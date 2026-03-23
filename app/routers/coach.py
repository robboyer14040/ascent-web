"""routers/coach.py — AI Coach endpoints for Ascent Web.

Provides:
  GET  /api/coach/state          — current goal + unread-activity flag
  GET  /api/coach/messages       — conversation history for active goal
  POST /api/coach/goal           — set a new goal (archives old conversation)
  POST /api/coach/chat           — send a user message, get AI response
  GET  /api/coach/goals/archived — list of archived goals
  GET  /api/coach/goals/{id}/messages — messages for an archived goal

DB tables created lazily (backward-compatible with old .ascentdb files):
  coach_goals    — one row per goal (active or archived)
  coach_messages — one row per message in a conversation
"""

import os
import json
import time
import sqlite3
from typing import Callable, Optional
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
db_getter: Callable = None

# ── SQL: table creation (always IF NOT EXISTS) ────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS coach_goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_text   TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,   -- unix timestamp
    archived_at INTEGER             -- NULL = currently active
);

CREATE TABLE IF NOT EXISTS coach_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id       INTEGER NOT NULL REFERENCES coach_goals(id) ON DELETE CASCADE,
    role          TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
    content       TEXT    NOT NULL,
    created_at    INTEGER NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_coach_msg_goal
    ON coach_messages(goal_id, created_at);
"""

# Migration: add token columns to existing DBs that predate this feature
_MIGRATIONS = [
    "ALTER TABLE coach_messages ADD COLUMN input_tokens  INTEGER DEFAULT 0",
    "ALTER TABLE coach_messages ADD COLUMN output_tokens INTEGER DEFAULT 0",
]


def _ensure_tables(con: sqlite3.Connection):
    """Idempotent: create coach tables and run pending column migrations."""
    for stmt in _DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            con.execute(s)
    # Apply migrations safely — SQLite has no IF NOT EXISTS for ALTER TABLE,
    # so we catch the duplicate-column error and continue.
    for migration in _MIGRATIONS:
        try:
            con.execute(migration)
        except Exception:
            pass  # column already exists
    con.commit()


def _get_con(db) -> sqlite3.Connection:
    """Open a fresh write connection to the DB file."""
    con = sqlite3.connect(db.path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    _ensure_tables(con)
    return con


# ── Activity summary builder ──────────────────────────────────────────────────

def _build_activity_summary(db) -> str:
    """
    Query the last ~90 days of activities and return a compact text summary
    suitable for inclusion in the Claude system prompt.
    """
    cutoff = int(time.time()) - 90 * 86400  # 90 days ago

    try:
        rows = db._con.execute("""
            SELECT
                COALESCE(creation_time_override_s, creation_time_s) AS ts,
                distance_mi,
                src_total_climb,
                src_moving_time_s,
                src_elapsed_time_s,
                src_avg_heartrate,
                json_extract(attributes_json, '$.activity')   AS act_type,
                json_extract(attributes_json, '$.name')       AS name,
                json_extract(attributes_json, '$.totalClimb') AS climb_attr
            FROM activities
            WHERE COALESCE(creation_time_override_s, creation_time_s) >= ?
            ORDER BY ts DESC
            LIMIT 200
        """, (cutoff,)).fetchall()
    except Exception:
        return "No activity data available."

    if not rows:
        return "No activities recorded in the past 90 days."

    # Per-activity lines
    lines = []
    for r in rows:
        ts      = r["ts"] or 0
        date    = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        atype   = r["act_type"] or "Activity"
        dist    = round(r["distance_mi"] or 0, 1)
        climb   = round(r["climb_attr"] or r["src_total_climb"] or 0)
        moving  = r["src_moving_time_s"] or r["src_elapsed_time_s"] or 0
        hrs     = round(moving / 3600, 1)
        hr      = round(r["src_avg_heartrate"] or 0)
        hr_str  = f", avg HR {hr}bpm" if hr else ""
        lines.append(
            f"  {date}: {atype} — {dist}mi, {climb}ft climb, {hrs}h moving{hr_str}"
        )

    # Weekly rollups (last 12 weeks)
    from collections import defaultdict
    import math

    week_dist  = defaultdict(float)
    week_climb = defaultdict(float)
    week_count = defaultdict(int)

    for r in rows:
        ts = r["ts"] or 0
        # ISO week key
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        wk = dt.strftime("%G-W%V")
        week_dist[wk]  += r["distance_mi"] or 0
        week_climb[wk] += r["climb_attr"] or r["src_total_climb"] or 0
        week_count[wk] += 1

    sorted_weeks = sorted(week_dist.keys())[-12:]
    week_lines = []
    for wk in sorted_weeks:
        week_lines.append(
            f"  {wk}: {round(week_dist[wk],1)}mi, {round(week_climb[wk])}ft climb, {week_count[wk]} activities"
        )

    summary = (
        f"TRAINING DATA SUMMARY (past 90 days)\n"
        f"Total activities: {len(rows)}\n"
        f"Total distance:   {round(sum(r['distance_mi'] or 0 for r in rows), 1)} miles\n"
        f"Total climb:      {round(sum((r['climb_attr'] or r['src_total_climb'] or 0) for r in rows))} ft\n\n"
        f"Weekly totals (most recent 12 weeks):\n"
        + "\n".join(week_lines) +
        f"\n\nIndividual activities (most recent first):\n"
        + "\n".join(lines[:60])  # cap at 60 most recent
    )
    return summary


def _build_system_prompt(goal_text: str, activity_summary: str) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    return f"""You are an expert endurance sports coach embedded in Ascent, a training log app. \
You have access to the athlete's real training data and a specific goal they're working toward.

Today's date: {today}

ATHLETE'S GOAL:
{goal_text}

{activity_summary}

YOUR ROLE:
- Analyse the athlete's actual training data in relation to their goal
- Give specific, data-driven coaching advice (reference their actual mileage, climbing, trends)
- Be proactive: notice patterns, gaps, or risks the athlete may not have spotted themselves
- Balance honest analysis with motivational coaching — be encouraging but honest
- When referencing activities, use dates and numbers from the data above
- Keep responses focused and actionable — avoid vague generalities
- If the goal has a timeline, reason explicitly about how much time is available

Respond conversationally. You're a knowledgeable coach who genuinely cares about this athlete's success."""


# ── Pydantic models ───────────────────────────────────────────────────────────

class GoalRequest(BaseModel):
    goal_text: str

class ChatRequest(BaseModel):
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_goal(con: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM coach_goals WHERE archived_at IS NULL ORDER BY created_at DESC LIMIT 1"
    ).fetchone()


def _goal_messages(con: sqlite3.Connection, goal_id: int, limit: int = 60) -> list:
    rows = con.execute(
        "SELECT role, content, created_at FROM coach_messages "
        "WHERE goal_id = ? ORDER BY created_at ASC LIMIT ?",
        (goal_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def _last_activity_ts(db) -> int:
    """Timestamp of the most recent activity in the DB."""
    row = db._con.execute(
        "SELECT MAX(COALESCE(creation_time_override_s, creation_time_s)) FROM activities"
    ).fetchone()
    return row[0] if row and row[0] else 0


def _last_coach_message_ts(con: sqlite3.Connection, goal_id: int) -> int:
    """Timestamp of the most recent coach message for this goal."""
    row = con.execute(
        "SELECT MAX(created_at) FROM coach_messages WHERE goal_id = ? AND role = 'assistant'",
        (goal_id,)
    ).fetchone()
    return row[0] if row and row[0] else 0


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/coach/state")
async def coach_state():
    """Return current goal info and whether there are new activities since last coach message."""
    db  = db_getter()
    con = _get_con(db)
    try:
        goal = _active_goal(con)
        if not goal:
            return {"has_goal": False, "goal": None, "has_new_activities": False}

        last_coach_ts    = _last_coach_message_ts(con, goal["id"])
        last_activity_ts = _last_activity_ts(db)
        has_new          = last_activity_ts > last_coach_ts if last_coach_ts else False

        msg_count = con.execute(
            "SELECT COUNT(*) FROM coach_messages WHERE goal_id=?", (goal["id"],)
        ).fetchone()[0]

        return {
            "has_goal":           True,
            "goal":               dict(goal),
            "has_new_activities": has_new,
            "message_count":      msg_count,
        }
    finally:
        con.close()


@router.get("/coach/messages")
async def coach_messages():
    """Return full conversation history for the active goal."""
    db  = db_getter()
    con = _get_con(db)
    try:
        goal = _active_goal(con)
        if not goal:
            return {"goal": None, "messages": []}
        msgs = _goal_messages(con, goal["id"])
        return {"goal": dict(goal), "messages": msgs}
    finally:
        con.close()


@router.post("/coach/goal")
async def set_goal(req: GoalRequest):
    """
    Set a new training goal. Archives any existing active goal first.
    Returns the new goal id and an initial AI assessment.
    """
    if not req.goal_text.strip():
        raise HTTPException(400, "Goal text cannot be empty")

    db  = db_getter()
    con = _get_con(db)
    now = int(time.time())

    try:
        # Archive existing active goal
        con.execute(
            "UPDATE coach_goals SET archived_at=? WHERE archived_at IS NULL",
            (now,)
        )

        # Insert new goal
        cur = con.execute(
            "INSERT INTO coach_goals (goal_text, created_at) VALUES (?,?)",
            (req.goal_text.strip(), now)
        )
        goal_id = cur.lastrowid
        con.commit()
    finally:
        con.close()

    # Generate initial coach response
    initial = await _call_claude(db, goal_id, req.goal_text.strip(), [], proactive=True)
    return {"goal_id": goal_id, "initial_message": initial}


@router.post("/coach/chat")
async def coach_chat(req: ChatRequest):
    """
    Send a user message. Optionally prepend a proactive activity observation.
    Returns the assistant reply.
    """
    if not req.message.strip():
        raise HTTPException(400, "Message cannot be empty")

    db  = db_getter()
    con = _get_con(db)
    now = int(time.time())

    try:
        goal = _active_goal(con)
        if not goal:
            raise HTTPException(400, "No active goal. Set a goal first.")

        goal_id   = goal["id"]
        goal_text = goal["goal_text"]

        # Check for new activities to surface proactively
        last_coach_ts    = _last_coach_message_ts(con, goal_id)
        last_activity_ts = _last_activity_ts(db)
        has_new = (last_activity_ts > last_coach_ts) if last_coach_ts else False

        # Save user message
        con.execute(
            "INSERT INTO coach_messages (goal_id, role, content, created_at) VALUES (?,?,?,?)",
            (goal_id, "user", req.message.strip(), now)
        )
        con.commit()

        # Load history (last 20 exchanges = 40 messages)
        history = _goal_messages(con, goal_id, limit=40)
    finally:
        con.close()

    # Call Claude
    reply = await _call_claude(db, goal_id, goal_text, history, proactive=has_new)
    return {"reply": reply}


@router.get("/coach/goals/archived")
async def archived_goals():
    db  = db_getter()
    con = _get_con(db)
    try:
        rows = con.execute(
            "SELECT * FROM coach_goals WHERE archived_at IS NOT NULL ORDER BY archived_at DESC"
        ).fetchall()
        return {"goals": [dict(r) for r in rows]}
    finally:
        con.close()


@router.get("/coach/goals/{goal_id}/messages")
async def goal_messages(goal_id: int):
    db  = db_getter()
    con = _get_con(db)
    try:
        goal = con.execute("SELECT * FROM coach_goals WHERE id=?", (goal_id,)).fetchone()
        if not goal:
            raise HTTPException(404, "Goal not found")
        msgs = _goal_messages(con, goal_id)
        return {"goal": dict(goal), "messages": msgs}
    finally:
        con.close()


# ── Usage endpoint ───────────────────────────────────────────────────────────

# Haiku pricing (per million tokens)
HAIKU_INPUT_COST_PER_M  = 0.25
HAIKU_OUTPUT_COST_PER_M = 1.25

@router.get("/coach/usage")
async def coach_usage():
    """
    Return aggregated token usage and estimated cost for the AI Coach feature.
    Covers all goals (active + archived).
    """
    db  = db_getter()
    con = _get_con(db)
    try:
        # All-time totals
        row = con.execute("""
            SELECT
                COUNT(*)                          AS total_queries,
                COALESCE(SUM(input_tokens),  0)   AS total_input,
                COALESCE(SUM(output_tokens), 0)   AS total_output
            FROM coach_messages
            WHERE role = 'assistant'
        """).fetchone()

        # This month
        import time as _time
        from datetime import datetime, timezone
        now_dt   = datetime.now(timezone.utc)
        month_ts = int(datetime(now_dt.year, now_dt.month, 1, tzinfo=timezone.utc).timestamp())

        month_row = con.execute("""
            SELECT
                COUNT(*)                          AS queries,
                COALESCE(SUM(input_tokens),  0)   AS input,
                COALESCE(SUM(output_tokens), 0)   AS output
            FROM coach_messages
            WHERE role = 'assistant' AND created_at >= ?
        """, (month_ts,)).fetchone()

        # Per-month breakdown (last 6 months)
        monthly = con.execute("""
            SELECT
                strftime('%Y-%m', datetime(created_at, 'unixepoch')) AS month,
                COUNT(*)                          AS queries,
                COALESCE(SUM(input_tokens),  0)   AS input_tokens,
                COALESCE(SUM(output_tokens), 0)   AS output_tokens
            FROM coach_messages
            WHERE role = 'assistant'
              AND created_at >= ?
            GROUP BY month
            ORDER BY month ASC
        """, (int(_time.time()) - 6 * 30 * 86400,)).fetchall()

    finally:
        con.close()

    def cost(inp, out):
        return round(
            (inp  / 1_000_000) * HAIKU_INPUT_COST_PER_M +
            (out / 1_000_000) * HAIKU_OUTPUT_COST_PER_M,
            4
        )

    total_in  = row["total_input"]
    total_out = row["total_output"]
    m_in      = month_row["input"]
    m_out     = month_row["output"]

    return {
        "model":          "claude-haiku-4-5",
        "input_price_pm":  HAIKU_INPUT_COST_PER_M,
        "output_price_pm": HAIKU_OUTPUT_COST_PER_M,
        "alltime": {
            "queries":       row["total_queries"],
            "input_tokens":  total_in,
            "output_tokens": total_out,
            "cost_usd":      cost(total_in, total_out),
        },
        "this_month": {
            "queries":       month_row["queries"],
            "input_tokens":  m_in,
            "output_tokens": m_out,
            "cost_usd":      cost(m_in, m_out),
        },
        "monthly_breakdown": [
            {
                "month":         r["month"],
                "queries":       r["queries"],
                "input_tokens":  r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cost_usd":      cost(r["input_tokens"], r["output_tokens"]),
            }
            for r in monthly
        ],
    }


# ── Claude API call ───────────────────────────────────────────────────────────

async def _call_claude(
    db,
    goal_id: int,
    goal_text: str,
    history: list,
    proactive: bool = False,
) -> str:
    """
    Call the Claude API and persist the assistant reply to the DB.
    `history` should be the messages already in the DB (including the latest user msg).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not set in .env")

    activity_summary = _build_activity_summary(db)
    system_prompt    = _build_system_prompt(goal_text, activity_summary)

    # Build messages array for the API (skip system-role rows)
    messages = []
    for m in history:
        if m["role"] in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m["content"]})

    # If proactive (new activities since last chat) AND this is a user-turn call,
    # prepend a note so Claude acknowledges new activities
    if proactive and messages and messages[-1]["role"] == "user":
        probe = (
            "[Note to coach: new activities have been logged since your last response. "
            "Briefly acknowledge the most relevant recent activity in your reply if appropriate, "
            "then address the athlete's question.]"
        )
        messages[-1]["content"] = probe + "\n\n" + messages[-1]["content"]

    # If no messages yet (initial goal set), send a synthetic first message
    if not messages:
        messages = [{"role": "user", "content":
            "I've just set my training goal. Please give me an initial assessment based on "
            "my recent training data and tell me what I should be focusing on right now."}]

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":      "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "system":     system_prompt,
                    "messages":   messages,
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(504, "Claude API timed out — please try again")

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error {resp.status_code}: {resp.text[:200]}")

    data    = resp.json()
    content = data.get("content", [])
    reply   = " ".join(b["text"] for b in content if b.get("type") == "text").strip()

    if not reply:
        raise HTTPException(502, "Claude returned an empty response")

    # Capture token usage from response
    usage         = data.get("usage", {})
    input_tokens  = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    # Persist reply with token counts
    now = int(time.time())
    con = _get_con(db)
    try:
        con.execute(
            "INSERT INTO coach_messages "
            "(goal_id, role, content, created_at, input_tokens, output_tokens) "
            "VALUES (?,?,?,?,?,?)",
            (goal_id, "assistant", reply, now, input_tokens, output_tokens)
        )
        con.commit()
    finally:
        con.close()

    return reply
