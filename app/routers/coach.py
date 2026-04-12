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
from fastapi import APIRouter, Request, HTTPException
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
    "ALTER TABLE coach_goals ADD COLUMN user_id INTEGER",
    "ALTER TABLE coach_goals ADD COLUMN target_date TEXT",   # ISO date YYYY-MM-DD, optional
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

def _build_activity_summary(db, user_id: Optional[int] = None) -> str:
    """
    Query the last ~90 days of activities and return a compact text summary
    suitable for inclusion in the Claude system prompt.
    """
    cutoff = int(time.time()) - 90 * 86400  # 90 days ago

    user_filter = "AND user_id = ?" if user_id is not None else ""
    params = [cutoff] + ([user_id] if user_id is not None else [])

    try:
        rows = db._con.execute(f"""
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
            {user_filter}
            ORDER BY ts DESC
            LIMIT 200
        """, params).fetchall()
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


def _build_tour_summary(db, user_id: Optional[int] = None) -> str:
    """Query tours accessible to the user and return a compact text summary for the coach prompt."""
    if user_id is None:
        return ""
    try:
        from app.routers.tours import _global_stage_matching, _ensure_tables
        con = sqlite3.connect(db.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            _ensure_tables(con)
            tour_rows = con.execute(
                "SELECT id, title, start_date, end_date FROM tours "
                "WHERE created_by=? OR shared=1 ORDER BY start_date DESC LIMIT 5",
                (user_id,),
            ).fetchall()
            if not tour_rows:
                return ""
            sections = []
            for tour in tour_rows:
                stage_rows = con.execute(
                    "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
                    "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
                    (tour["id"],),
                ).fetchall()
                if not stage_rows:
                    continue
                stages = [
                    {"id": r["id"], "stage_num": r["stage_num"], "name": r["name"],
                     "distance_mi": r["distance_mi"], "climb_ft": r["climb_ft"],
                     "start_lat": r["start_lat"], "start_lon": r["start_lon"]}
                    for r in stage_rows
                ]
                completions = _global_stage_matching(
                    con, user_id, tour["start_date"], tour["end_date"], stages
                )
                n_done      = sum(1 for s in stages if completions.get(s["id"]))
                total_dist  = sum(s["distance_mi"] for s in stages)
                total_climb = sum(s["climb_ft"] for s in stages)
                lines = [
                    f'Tour: "{tour["title"]}" ({tour["start_date"]} to {tour["end_date"]})',
                    f'Progress: {n_done}/{len(stages)} stages complete',
                    f'Total route: {round(total_dist, 1)} mi, {round(total_climb):,} ft climb',
                    'Stages:',
                ]
                for s in stages:
                    comp   = completions.get(s["id"])
                    status = f'✓ completed {comp["date"]}' if comp else '○ pending'
                    lines.append(
                        f'  Stage {s["stage_num"]}: {s["name"]} — '
                        f'{round(s["distance_mi"], 1)} mi, {round(s["climb_ft"]):,} ft — {status}'
                    )
                sections.append('\n'.join(lines))
            return ("TOUR / MULTI-STAGE EVENT DATA\n" + "\n\n".join(sections)) if sections else ""
        finally:
            con.close()
    except Exception:
        return ""


def _build_system_prompt(goal_text: str, activity_summary: str, tour_summary: str = "", target_date: Optional[str] = None) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    target_line = f"\nTarget date: {target_date}" if target_date else ""
    tour_section = f"\n{tour_summary}\n" if tour_summary else ""
    return f"""You are an expert endurance sports coach embedded in Ascent, a training log app. \
You have access to the athlete's real training data and a specific goal they're working toward.

Today's date: {today}

ATHLETE'S GOAL:
{goal_text}{target_line}

{activity_summary}
{tour_section}
YOUR ROLE:
- Analyse the athlete's actual training data in relation to their goal
- Give specific, data-driven coaching advice (reference their actual mileage, climbing, trends)
- Be proactive: notice patterns, gaps, or risks the athlete may not have spotted themselves
- Balance honest analysis with motivational coaching — be encouraging but honest
- When referencing activities, use dates and numbers from the data above
- Keep responses focused and actionable — avoid vague generalities
- If the goal has a timeline, reason explicitly about how much time is available

Respond conversationally. You're a knowledgeable coach who genuinely cares about this athlete's success."""


# ── Model config (must be defined before Pydantic models) ────────────────────

# Available models and their pricing (per million tokens)
MODELS = {
    "claude-haiku-4-5-20251001": {
        "label":      "Haiku 4.5",
        "input_pm":   0.25,
        "output_pm":  1.25,
        "display":    "claude-haiku-4-5",
    },
    "claude-sonnet-4-5-20250929": {
        "label":      "Sonnet 4.5",
        "input_pm":   3.00,
        "output_pm":  15.00,
        "display":    "claude-sonnet-4-5",
    },
    "claude-sonnet-4-20250514": {
        "label":      "Sonnet 4",
        "input_pm":   3.00,
        "output_pm":  15.00,
        "display":    "claude-sonnet-4",
    },
}
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

def _model_info(model_id: str) -> dict:
    return MODELS.get(model_id, MODELS[DEFAULT_MODEL])


# ── Pydantic models ───────────────────────────────────────────────────────────

class GoalRequest(BaseModel):
    goal_text: str
    model: str = DEFAULT_MODEL
    target_date: Optional[str] = None  # ISO date YYYY-MM-DD

class ChatRequest(BaseModel):
    message: str
    model: str = DEFAULT_MODEL


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_goal(con: sqlite3.Connection, user_id: Optional[int] = None) -> Optional[sqlite3.Row]:
    if user_id is not None:
        return con.execute(
            "SELECT * FROM coach_goals WHERE archived_at IS NULL AND user_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
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


def _last_activity_ts(db, user_id: Optional[int] = None) -> int:
    """Timestamp of the most recent activity in the DB."""
    if user_id is not None:
        row = db._con.execute(
            "SELECT MAX(COALESCE(creation_time_override_s, creation_time_s)) FROM activities WHERE user_id=?",
            (user_id,)
        ).fetchone()
    else:
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
async def coach_state(request: Request):
    """Return current goal info and whether there are new activities since last coach message."""
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    db  = db_getter()
    con = _get_con(db)
    try:
        goal = _active_goal(con, user_id=uid)
        if not goal:
            return {"has_goal": False, "goal": None, "has_new_activities": False}

        last_coach_ts    = _last_coach_message_ts(con, goal["id"])
        last_activity_ts = _last_activity_ts(db, user_id=uid)
        has_new          = last_activity_ts > last_coach_ts if last_coach_ts else False

        msg_count = con.execute(
            "SELECT COUNT(*) FROM coach_messages WHERE goal_id=?", (goal["id"],)
        ).fetchone()[0]

        goal_dict = dict(goal)
        return {
            "has_goal":           True,
            "goal":               goal_dict,
            "has_new_activities": has_new,
            "message_count":      msg_count,
        }
    finally:
        con.close()


@router.get("/coach/messages")
async def coach_messages(request: Request):
    """Return full conversation history for the active goal."""
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    db  = db_getter()
    con = _get_con(db)
    try:
        goal = _active_goal(con, user_id=uid)
        if not goal:
            return {"goal": None, "messages": []}
        msgs = _goal_messages(con, goal["id"])
        return {"goal": dict(goal), "messages": msgs}
    finally:
        con.close()


@router.post("/coach/goal")
async def set_goal(req: GoalRequest, request: Request):
    """
    Set a new training goal. Archives any existing active goal first.
    Returns the new goal id and an initial AI assessment.
    """
    if not req.goal_text.strip():
        raise HTTPException(400, "Goal text cannot be empty")

    from app.auth import get_session_user_id as _gsu
    _uid = _gsu(request) if hasattr(request, 'cookies') else None

    db  = db_getter()
    con = _get_con(db)
    now = int(time.time())

    try:
        # Archive existing active goal for this user only
        con.execute(
            "UPDATE coach_goals SET archived_at=? WHERE archived_at IS NULL AND user_id=?",
            (now, _uid)
        )

        # Validate and sanitize target_date
        target_date = None
        if req.target_date:
            import re
            if re.match(r'^\d{4}-\d{2}-\d{2}$', req.target_date):
                target_date = req.target_date

        # Insert new goal with user_id and optional target_date
        cur = con.execute(
            "INSERT INTO coach_goals (goal_text, created_at, user_id, target_date) VALUES (?,?,?,?)",
            (req.goal_text.strip(), now, _uid, target_date)
        )
        goal_id = cur.lastrowid
        con.commit()
    finally:
        con.close()

    # Generate initial coach response
    initial = await _call_claude(db, goal_id, req.goal_text.strip(), [], proactive=True, user_id=_uid,
                                  model=req.model if req.model in MODELS else DEFAULT_MODEL,
                                  target_date=target_date)
    return {"goal_id": goal_id, "initial_message": initial}


@router.post("/coach/chat")
async def coach_chat(req: ChatRequest, request: Request):
    """
    Send a user message. Optionally prepend a proactive activity observation.
    Returns the assistant reply.
    """
    if not req.message.strip():
        raise HTTPException(400, "Message cannot be empty")

    from app.auth import get_session_user_id
    uid = get_session_user_id(request)

    db  = db_getter()
    con = _get_con(db)
    now = int(time.time())

    try:
        goal = _active_goal(con, user_id=uid)
        if not goal:
            raise HTTPException(400, "No active goal. Set a goal first.")

        goal_id     = goal["id"]
        goal_text   = goal["goal_text"]
        target_date = goal["target_date"] if "target_date" in goal.keys() else None

        # Check for new activities to surface proactively
        last_coach_ts    = _last_coach_message_ts(con, goal_id)
        last_activity_ts = _last_activity_ts(db, user_id=uid)
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
    model = req.model if req.model in MODELS else DEFAULT_MODEL
    reply = await _call_claude(db, goal_id, goal_text, history, proactive=has_new, model=model, user_id=uid,
                               target_date=target_date)
    return {"reply": reply}


@router.get("/coach/today")
async def coach_today(request: Request, model: str = DEFAULT_MODEL):
    """
    Generate 'what should I do today?' advice based on recent activities and goal.
    Returns advice text + up to 3 candidate activity IDs with simplified track coords.
    """
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    db  = db_getter()
    con = _get_con(db)
    try:
        goal = _active_goal(con, user_id=uid)
        goal_text = goal["goal_text"] if goal else None
    finally:
        con.close()

    # Get user's API key
    api_key = ""
    user = db.get_user(uid)
    api_key = (user or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "No Anthropic API key configured")

    safe_model = model if model in MODELS else DEFAULT_MODEL

    # Fetch recent activities (last 60 days) with IDs and stats
    cutoff = int(time.time()) - 60 * 86400
    try:
        rows = db._con.execute("""
            SELECT
                id,
                COALESCE(creation_time_override_s, creation_time_s) AS ts,
                distance_mi, src_total_climb, src_moving_time_s,
                src_avg_heartrate,
                json_extract(attributes_json, '$.activity')   AS act_type,
                json_extract(attributes_json, '$.name')       AS name,
                json_extract(attributes_json, '$.totalClimb') AS climb_attr
            FROM activities
            WHERE COALESCE(creation_time_override_s, creation_time_s) >= ?
              AND user_id = ?
            ORDER BY ts DESC
            LIMIT 30
        """, (cutoff, uid)).fetchall()
    except Exception:
        rows = []

    if not rows:
        raise HTTPException(404, "No recent activities found to base advice on")

    today_str = datetime.now().strftime("%A, %B %d, %Y")
    goal_section = f"\nATHLETE'S GOAL:\n{goal_text}\n" if goal_text else ""

    act_lines = []
    for r in rows:
        ts    = r["ts"] or 0
        date  = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        atype = r["act_type"] or "Activity"
        name  = r["name"] or "(unnamed)"
        dist  = round(r["distance_mi"] or 0, 1)
        climb = round(r["climb_attr"] or r["src_total_climb"] or 0)
        moving = r["src_moving_time_s"] or 0
        hrs   = round(moving / 3600, 1)
        hr    = round(r["src_avg_heartrate"] or 0)
        hr_str = f", avg HR {hr}bpm" if hr else ""
        act_lines.append(
            f"  id={r['id']}: {date} {atype} \"{name}\" — {dist}mi, {climb}ft climb, {hrs}h{hr_str}"
        )

    prompt = (
        f"Today is {today_str}.{goal_section}\n"
        f"RECENT ACTIVITIES (last 60 days, most recent first):\n"
        + "\n".join(act_lines) +
        "\n\nBased on this athlete's recent training load and goal, recommend what they should do TODAY. "
        "Also, from the list above, identify up to 3 activity IDs that are the best examples or templates "
        "for what you're recommending — routes or workouts they've done before that fit well.\n\n"
        "Respond ONLY with valid JSON:\n"
        '{"advice": "2-3 sentence recommendation referencing their recent training", "activity_ids": [id1, id2]}\n'
        "activity_ids must be integer IDs from the list. Include fewer than 3 if fewer match."
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":    safe_model,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    raw = resp.json()["content"][0]["text"].strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        advice = str(data.get("advice", "")).strip()
        activity_ids = [int(i) for i in (data.get("activity_ids") or [])[:3]]
    except Exception:
        advice = raw
        activity_ids = []

    # Fetch simplified track coords for each suggested activity
    valid_ids = {r["id"] for r in rows}
    activity_cards = []
    for act_id in activity_ids:
        if act_id not in valid_ids:
            continue
        act = db.get_activity(act_id)
        if not act:
            continue
        try:
            pts = db.get_track_points(act_id)
            coords = [
                [p["lon"], p["lat"]]
                for p in pts
                if p["lat"] != 999.0 and p["lon"] != 999.0
                and -90.0 <= p["lat"] <= 90.0
                and -180.0 <= p["lon"] <= 180.0
                and not (p["lat"] == 0.0 and p["lon"] == 0.0)
            ]
            # Downsample to ~120 points max
            if len(coords) > 120:
                step = max(1, len(coords) // 120)
                coords = coords[::step]
        except Exception:
            coords = []
        activity_cards.append({
            "id":          act_id,
            "name":        act.get("name", "(unnamed)"),
            "type":        act.get("activity_type", ""),
            "distance_mi": act.get("distance_mi"),
            "coords":      coords,
        })

    if activity_cards:
        advice = advice.rstrip() + "\n\nHere are some examples from your activities that would be a great fit for today:"

    return {"advice": advice, "activities": activity_cards}


@router.get("/coach/goals/archived")
async def archived_goals(request: Request):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    db  = db_getter()
    con = _get_con(db)
    try:
        if uid is not None:
            rows = con.execute(
                "SELECT * FROM coach_goals WHERE archived_at IS NOT NULL AND user_id=? ORDER BY archived_at DESC",
                (uid,)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM coach_goals WHERE archived_at IS NOT NULL ORDER BY archived_at DESC"
            ).fetchall()
        return {"goals": [dict(r) for r in rows]}
    finally:
        con.close()


@router.get("/coach/goals/{goal_id}/messages")
async def goal_messages(goal_id: int, request: Request):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    db  = db_getter()
    con = _get_con(db)
    try:
        goal = con.execute("SELECT * FROM coach_goals WHERE id=?", (goal_id,)).fetchone()
        if not goal:
            raise HTTPException(404, "Goal not found")
        if uid is not None and goal["user_id"] is not None and goal["user_id"] != uid:
            raise HTTPException(403, "Not your goal")
        msgs = _goal_messages(con, goal_id)
        return {"goal": dict(goal), "messages": msgs}
    finally:
        con.close()


# ── Usage endpoint ───────────────────────────────────────────────────────────

@router.get("/coach/usage")
async def coach_usage(request: Request):
    """
    Return aggregated token usage and estimated cost for the AI Coach feature.
    Covers all goals (active + archived) for the current user.
    """
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    db  = db_getter()
    con = _get_con(db)

    # Build user filter for joining coach_goals
    user_join  = "JOIN coach_goals g ON coach_messages.goal_id = g.id" if uid else ""
    user_where = "AND g.user_id = ?" if uid else ""
    user_params = [uid] if uid else []

    try:
        # All-time totals
        row = con.execute(f"""
            SELECT
                COUNT(*)                          AS total_queries,
                COALESCE(SUM(input_tokens),  0)   AS total_input,
                COALESCE(SUM(output_tokens), 0)   AS total_output
            FROM coach_messages
            {user_join}
            WHERE role = 'assistant' {user_where}
        """, user_params).fetchone()

        import time as _time
        from datetime import datetime, timezone
        now_dt   = datetime.now(timezone.utc)
        month_ts = int(datetime(now_dt.year, now_dt.month, 1, tzinfo=timezone.utc).timestamp())

        month_row = con.execute(f"""
            SELECT
                COUNT(*)                          AS queries,
                COALESCE(SUM(input_tokens),  0)   AS input,
                COALESCE(SUM(output_tokens), 0)   AS output
            FROM coach_messages
            {user_join}
            WHERE role = 'assistant' AND coach_messages.created_at >= ? {user_where}
        """, [month_ts] + user_params).fetchone()

        monthly = con.execute(f"""
            SELECT
                strftime('%Y-%m', datetime(coach_messages.created_at, 'unixepoch')) AS month,
                COUNT(*)                          AS queries,
                COALESCE(SUM(input_tokens),  0)   AS input_tokens,
                COALESCE(SUM(output_tokens), 0)   AS output_tokens
            FROM coach_messages
            {user_join}
            WHERE role = 'assistant'
              AND coach_messages.created_at >= ? {user_where}
            GROUP BY month
            ORDER BY month ASC
        """, [int(_time.time()) - 6 * 30 * 86400] + user_params).fetchall()

    finally:
        con.close()

    # Use Sonnet pricing for cost estimate if any Sonnet was used
    # (conservative: use average of Haiku/Sonnet if mixed, or just Haiku rates)
    # Since we don't store which model was used per-message, we use Haiku rates
    # as the floor estimate. User sees the model selector so they know the cost.
    haiku_info  = _model_info("claude-haiku-4-5-20251001")
    sonnet_info = _model_info("claude-sonnet-4-5-20250929")

    def cost(inp, out):
        # Use Haiku rates as minimum estimate (messages may be Sonnet)
        return round(
            (inp  / 1_000_000) * haiku_info["input_pm"] +
            (out / 1_000_000) * haiku_info["output_pm"],
            4
        )

    total_in  = row["total_input"]
    total_out = row["total_output"]
    m_in      = month_row["input"]
    m_out     = month_row["output"]

    return {
        "models":         {k: v["label"] for k, v in MODELS.items()},
        "input_price_pm":  haiku_info["input_pm"],
        "output_price_pm": haiku_info["output_pm"],
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
    model: str = DEFAULT_MODEL,
    user_id: int = None,
    target_date: Optional[str] = None,
) -> str:
    """
    Call the Claude API and persist the assistant reply to the DB.
    `history` should be the messages already in the DB (including the latest user msg).
    `model` should be a key in MODELS dict.
    """
    # User's own key takes priority over global env key
    api_key = ""
    if user_id:
        user = db.get_user(user_id)
        api_key = (user or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "No Anthropic API key set. Add your key in Settings.")

    activity_summary = _build_activity_summary(db, user_id=user_id)
    tour_summary     = _build_tour_summary(db, user_id=user_id)
    system_prompt    = _build_system_prompt(goal_text, activity_summary, tour_summary=tour_summary, target_date=target_date)

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

    # Retry with exponential backoff on 529 (overloaded) and 529-adjacent errors
    max_retries = 4
    base_delay  = 2.0  # seconds

    resp = None
    last_error = None

    for attempt in range(max_retries):
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
                        "model":      model if model in MODELS else DEFAULT_MODEL,
                        "max_tokens": 2048,
                        "system":     system_prompt,
                        "messages":   messages,
                    },
                )
        except httpx.TimeoutException:
            raise HTTPException(504, "Claude API timed out — please try again")

        # Success
        if resp.status_code == 200:
            break

        # Retryable: 529 overloaded or 529-style server errors
        if resp.status_code in (529, 500, 502, 503) and attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)  # 2s, 4s, 8s
            await __import__("asyncio").sleep(delay)
            continue

        # Non-retryable error
        last_error = resp
        break

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp is not None else 0
        body   = resp.text[:300] if resp is not None else "no response"
        if status == 529:
            raise HTTPException(503, "Claude is currently overloaded — please try again in a moment")
        raise HTTPException(502, f"Claude API error {status}: {body}")

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


