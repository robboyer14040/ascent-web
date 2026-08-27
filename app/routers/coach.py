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
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.db import parse_attrs

router = APIRouter()
db_getter: Callable = None
templates = None          # set in main.py; used by the PDF export

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
    "ALTER TABLE coach_messages ADD COLUMN model TEXT",      # model that produced this reply
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


# ── Athlete-local dates ───────────────────────────────────────────────────────
#
# Claude has no clock — it only knows what the prompt tells it. Activity
# timestamps are UTC epochs, but the athlete trains (and thinks) in local time,
# so every date we render is converted into the athlete's own zone, derived from
# the most recent activity their device recorded.

def _athlete_tz(db, user_id: Optional[int]) -> timezone:
    """The athlete's UTC offset, taken from their latest activity. UTC if unknown."""
    try:
        user_filter = "AND user_id = ?" if user_id is not None else ""
        params = [user_id] if user_id is not None else []
        row = db._con.execute(f"""
            SELECT seconds_from_gmt_at_sync AS off
            FROM activities
            WHERE seconds_from_gmt_at_sync IS NOT NULL {user_filter}
            ORDER BY COALESCE(creation_time_override_s, creation_time_s) DESC
            LIMIT 1
        """, params).fetchone()
        if row and row["off"] is not None:
            # Sanity-check the offset before trusting it (valid range is ±14h).
            off = int(row["off"])
            if -50400 <= off <= 50400:
                return timezone(timedelta(seconds=off))
    except Exception:
        pass
    return timezone.utc


def _resolve_type(row) -> str:
    """Activity type, preferring a local override, then attributes_json.

    `attributes_json` is a flat ["key", "value", ...] array, so json_extract()
    cannot read it — parse_attrs() is the only correct reader.
    """
    try:
        local = row["local_sport_type"]
    except (KeyError, IndexError):
        local = None
    if local:
        return str(local)
    try:
        attrs = parse_attrs(row["attributes_json"])
    except Exception:
        return "Activity"
    return str(attrs.get("activity") or "Activity")


def _activity_line(r, today, tz, with_id: bool = False) -> str:
    """One activity rendered with an absolute date AND a relative day count."""
    ts    = r["ts"] or 0
    dt    = datetime.fromtimestamp(ts, tz=tz)
    days  = (today - dt.date()).days
    when  = "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")
    attrs = parse_attrs(r["attributes_json"]) if r["attributes_json"] else {}

    dist   = round(r["distance_mi"] or 0, 1)
    climb  = round(attrs.get("totalClimb") or r["src_total_climb"] or 0)
    moving = r["src_moving_time_s"] or r["src_elapsed_time_s"] or 0
    hrs    = round(moving / 3600, 1)

    bits = [f"{dist}mi", f"{climb}ft climb", f"{hrs}h moving"]
    for label, val, unit in (
        ("avg HR",  r["src_avg_heartrate"], "bpm"),
        ("max HR",  r["src_max_heartrate"], "bpm"),
        ("avg",     r["src_avg_power"],     "W"),
        ("max",     r["src_max_power"],     "W"),
        ("avg cad", r["src_avg_cadence"],   "rpm"),
    ):
        if val:
            bits.append(f"{label} {round(val)}{unit}")

    name    = attrs.get("name") or ""
    name_s  = f' "{name}"' if name else ""
    id_s    = f"[id={r['id']}] " if with_id else ""
    gear    = f" · gear: {r['local_gear_name']}" if r["local_gear_name"] else ""
    return (f"  {id_s}{dt.strftime('%Y-%m-%d')} ({when}, {dt.strftime('%a')}): "
            f"{_resolve_type(r)}{name_s} — " + ", ".join(bits) + gear)


_RECENT_SELECT = """
    SELECT
        id,
        COALESCE(creation_time_override_s, creation_time_s) AS ts,
        distance_mi, src_total_climb, src_moving_time_s, src_elapsed_time_s,
        src_avg_heartrate, src_max_heartrate, src_avg_power, src_max_power,
        src_avg_cadence, attributes_json, local_sport_type, local_gear_name
    FROM activities
    WHERE COALESCE(creation_time_override_s, creation_time_s) >= ?
    {user_filter}
    ORDER BY ts DESC
    LIMIT 60
"""


def _build_zone_block(db, user_id: Optional[int], today) -> str:
    """HR + power zone minutes for the current and previous month.

    Month-scoped get_zone_time() runs in ~0.03s; the all-time variant scans the
    whole points table (7.5s+), so deep zone history is a tool, not prompt text.
    """
    if user_id is None:
        return ""
    prev = (today.replace(day=1) - timedelta(days=1))
    out  = []
    for label, y, m in (
        (today.strftime("%B %Y"), today.year, today.month),
        (prev.strftime("%B %Y"),  prev.year,  prev.month),
    ):
        try:
            z = db.get_zone_time(user_id, year=y, month=m)
        except Exception:
            continue
        hr = z.get("hr_zones_min") or []
        pw = z.get("power_zones_min") or []
        if not any(hr) and not any(pw):
            continue
        out.append(f"  {label}:")
        if any(hr):
            total = sum(hr)
            parts = [f"Z{i+1} {v:.0f}min ({v / total * 100:.0f}%)" for i, v in enumerate(hr)]
            out.append(f"    HR zones (max {z.get('max_hr')}): " + ", ".join(parts))
        if any(pw):
            total = sum(pw)
            parts = [f"Z{i+1} {v:.0f}min ({v / total * 100:.0f}%)" for i, v in enumerate(pw)]
            out.append(f"    Power zones (FTP {z.get('ftp')}W): " + ", ".join(parts))
            if any(hr) and sum(pw) < sum(hr) * 0.5:
                out.append("    NOTE: power-zone minutes here cover far less time than the HR "
                           "zones because the per-second power stream is missing for some "
                           "rides. This is a data-sync gap, NOT the athlete riding without a "
                           "power meter — their per-activity average and max power (in the "
                           "activity list above) are complete and reliable. Read the power-zone "
                           "SHAPE, not its absolute minutes, and use per-activity power for "
                           "anything quantitative.")
    if not out:
        return ""
    return "TIME IN ZONES (from recorded per-second data)\n" + "\n".join(out)


def _build_activity_summary(db, user_id: Optional[int] = None) -> str:
    """Recency-weighted training context for the system prompt.

    Three tiers, most recent first — detail where it matters, rollups where it
    doesn't, and everything else reachable through the coach's tools:
      1. Last 14 days   — every activity, full detail, with ids to drill into
      2. Last 12 weeks  — weekly rollups from the real all-history aggregate
      3. Time in zones  — current + previous month
    """
    tz     = _athlete_tz(db, user_id)
    today  = datetime.now(tz).date()
    cutoff = int(time.time()) - 90 * 86400

    user_filter = "AND user_id = ?" if user_id is not None else ""
    params = [cutoff] + ([user_id] if user_id is not None else [])
    try:
        rows = db._con.execute(
            _RECENT_SELECT.format(user_filter=user_filter), params
        ).fetchall()
    except Exception:
        return "No activity data available."

    if not rows:
        return "No activities recorded in the past 90 days."

    recent_cutoff = int(time.time()) - 14 * 86400
    recent = [r for r in rows if (r["ts"] or 0) >= recent_cutoff]

    sections = []

    # Tier 1 — the last two weeks, in full.
    if recent:
        sections.append(
            "RECENT ACTIVITIES (last 14 days — the most important window; "
            "use [id=N] with get_activity_detail or get_activity_streams to drill in)\n"
            + "\n".join(_activity_line(r, today, tz, with_id=True) for r in recent)
        )
    else:
        sections.append("RECENT ACTIVITIES (last 14 days)\n  None — no activities logged in the last 14 days.")

    # Tier 2 — weekly rollups from the real aggregate (all history, not just 90 days).
    if user_id is not None:
        try:
            weeks = db.get_weekly_totals(user_id=user_id)[-12:]
        except Exception:
            weeks = []
        if weeks:
            wl = [
                f"  {w['week']}: {round(w['dist_mi'], 1)}mi, {round(w['climb_ft'])}ft climb, "
                f"{w['active_h']:.1f}h, {w['count']} activities, {w['active_days']} active days"
                + (f", avg HR {round(w['avg_hr'])}bpm" if w.get("avg_hr") else "")
                + (f", avg {round(w['avg_power_w'])}W" if w.get("avg_power_w") else "")
                for w in weeks
            ]
            sections.append(
                "WEEKLY TOTALS (last 12 weeks, week starting Monday)\n" + "\n".join(wl)
            )

    # Tier 3 — how that time actually distributed across intensity.
    zone_block = _build_zone_block(db, user_id, today)
    if zone_block:
        sections.append(zone_block)

    return "\n\n".join(sections)


def _build_date_anchor(db, user_id: Optional[int], target_date: Optional[str]) -> str:
    """The single authoritative statement of 'now'.

    Sent as the last message of every turn so it outranks any stale date the
    model wrote earlier in the conversation, and so it sits adjacent to the
    question rather than buried at the top of a long system prompt.
    """
    tz    = _athlete_tz(db, user_id)
    now   = datetime.now(tz)
    today = now.date()

    lines = [
        "CURRENT DATE ANCHOR — authoritative.",
        f"Today is {now.strftime('%A, %B %-d, %Y')}.",
    ]

    if target_date:
        try:
            tgt  = datetime.strptime(target_date, "%Y-%m-%d").date()
            diff = (tgt - today).days
            if diff > 0:
                lines.append(
                    f"The athlete's target date is {tgt.strftime('%A, %B %-d, %Y')} — "
                    f"{diff} days from today ({diff / 7:.1f} weeks)."
                )
            elif diff == 0:
                lines.append(f"The athlete's target date is TODAY ({tgt.isoformat()}).")
            else:
                lines.append(
                    f"The athlete's target date ({tgt.isoformat()}) passed {abs(diff)} days ago."
                )
        except ValueError:
            pass

    last_ts = _last_activity_ts(db, user_id=user_id)
    if last_ts:
        days = (today - datetime.fromtimestamp(last_ts, tz=tz).date()).days
        lines.append(
            "The athlete's most recent logged activity was "
            + ("today." if days == 0 else "yesterday." if days == 1 else f"{days} days ago.")
        )

    lines.append(
        "This anchor reflects the real current date. Any date stated earlier in this "
        "conversation is from a past session and is out of date — ignore it and use "
        "this anchor for every date calculation."
    )
    return "\n".join(lines)


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


def _build_system_prompt(goal_text: str, activity_summary: str, tour_summary: str = "",
                         target_date: Optional[str] = None, profile_block: str = "",
                         analysis_block: str = "") -> str:
    """The static (cacheable) half of the prompt.

    Deliberately contains no 'today' — the date lives in the date anchor sent as
    the last message of every turn, so there is exactly one source of truth for
    it and this block stays byte-identical across turns for prompt caching.
    """
    target_line = f"\nTarget date: {target_date}" if target_date else ""
    tour_section    = f"\n{tour_summary}\n"    if tour_summary    else ""
    profile_section = f"\n{profile_block}\n"   if profile_block   else ""
    analysis_section = f"\n{analysis_block}\n" if analysis_block  else ""
    return f"""You are an expert endurance sports coach embedded in Ascent, a training log app. \
You have access to the athlete's real training data, physiology, and a specific goal they're working toward.

ATHLETE'S GOAL:
{goal_text}{target_line}
{profile_section}{analysis_section}
{activity_summary}
{tour_section}
INVESTIGATE BEFORE YOU ADVISE:
The context above is a summary — recent activities, weekly totals, and recent zone
distribution. You also have tools that read the athlete's full training database. Use them.
- Before prescribing intensity or writing any training plan, call get_zone_distribution to
  see how their time has ACTUALLY distributed across zones. Most athletes' real intensity
  distribution differs from what they assume, and a plan that ignores it is guesswork.
- Before making claims about progression, call get_training_totals to compare against the
  same period in previous months or years, and get_personal_records for their real ceiling.
- When a specific workout matters to your reasoning, open it with get_activity_detail, or
  get_activity_streams to examine pacing, HR drift, or interval structure within the ride.
- Prefer recent data. The last 14 days say more about current form than anything older;
  weight the last 4-6 weeks most heavily and use older history only for context and trend.
- Two or three well-chosen tool calls beat a dozen. Investigate what your recommendation
  actually depends on, then answer.

YOUR ROLE:
- Ground every recommendation in real numbers. Cite specific dates, mileage, climbing, heart
  rate, power, and zone minutes — never speak in generalities.
- Establish the athlete's current baseline first, then state whether each key metric is
  trending up, flat, or down, and by how much.
- Read heart rate and power TOGETHER: rising HR at similar power/pace signals fatigue, heat, or
  dehydration; rising power (or efficiency factor) at similar HR signals improving aerobic fitness.
  Call out what the trend actually implies for this athlete.
- Use the acute:chronic load ratio to judge freshness vs overreaching. If load is spiking or the
  athlete looks fatigued, prescribe SPECIFIC recovery — e.g. "take 2 easy days then reassess" —
  and scale the number of rest days to their age (older athletes recover slower).
- When you prescribe rest or a change in load, say exactly how much and why, tied to the data.
- Be proactive: surface patterns, gaps, or risks the athlete may not have noticed.
- Reason explicitly about how much time remains before the target date and whether the current
  trajectory gets them there. Take the current date from the date anchor, never from memory.
- Do NOT offer generic platitudes ("stay hydrated", "listen to your body", "take it easy")
  unless directly tied to a specific observation in this athlete's data.

Format your answer in Markdown — it is rendered as Markdown for the athlete. Use headings and
tables where they aid scanning (a week-by-week plan is a table), prose where they don't.
Respond conversationally and encouragingly, but stay honest and quantitative. You're a knowledgeable \
coach who genuinely cares about this athlete's success."""


# ── Model config (must be defined before Pydantic models) ────────────────────

# Available models and their pricing (per million tokens).
# NOTE: the keys here must match the <option value=...> entries in
# _html_overlays.html — a value absent from this dict is silently downgraded to
# DEFAULT_MODEL, which is exactly the bug that made the coach feel weak.
MODELS = {
    "claude-opus-5": {
        "label":      "Opus 5",
        "input_pm":   5.00,
        "output_pm":  25.00,
        "display":    "claude-opus-5",
    },
    "claude-sonnet-5": {
        "label":      "Sonnet 5",
        "input_pm":   3.00,
        "output_pm":  15.00,
        "display":    "claude-sonnet-5",
    },
    "claude-haiku-4-5": {
        "label":      "Haiku 4.5",
        "input_pm":   1.00,
        "output_pm":  5.00,
        "display":    "claude-haiku-4-5",
    },
}
DEFAULT_MODEL = "claude-opus-5"

# Retired ids that may still be stored in user UI prefs or old coach_messages rows.
_MODEL_ALIASES = {
    "claude-haiku-4-5-20251001":  "claude-haiku-4-5",
    "claude-sonnet-4-5-20250929": "claude-sonnet-5",
    "claude-sonnet-4-20250514":   "claude-sonnet-5",
    "claude-sonnet-4-6":          "claude-sonnet-5",
}


def _resolve_model(model_id: Optional[str]) -> str:
    """Map any stored/submitted model id onto a currently-supported one."""
    if not model_id:
        return DEFAULT_MODEL
    if model_id in MODELS:
        return model_id
    return _MODEL_ALIASES.get(model_id, DEFAULT_MODEL)


def first_text(resp_json: dict) -> str:
    """The reply text from a Messages response.

    Opus 5 thinks by default, so content[0] is often a thinking block with no
    "text" key — the reply is the first block that actually is text.
    """
    for block in resp_json.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            return block["text"].strip()
    return ""


def _model_info(model_id: str) -> dict:
    return MODELS[_resolve_model(model_id)]


# Mid-conversation system messages (role:"system" inside messages[]) are how the
# date anchor stays authoritative without invalidating the cached prefix.
# Only the newest models accept them; older ones 400.
_SUPPORTS_SYSTEM_MESSAGES = {"claude-opus-5"}


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
        "SELECT id, role, content, created_at FROM coach_messages "
        "WHERE goal_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (goal_id, limit)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


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
    from app.auth import get_session_user_id, require_user
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
    initial, message_id = await _call_claude(
        db, goal_id, req.goal_text.strip(), [], proactive=True, user_id=_uid,
        model=_resolve_model(req.model), target_date=target_date)
    return {"goal_id": goal_id, "initial_message": initial, "message_id": message_id}


@router.post("/coach/chat")
async def coach_chat(req: ChatRequest, request: Request):
    """
    Send a user message and stream the coach's reply back as Server-Sent Events.

    Events:
      tool  — {"label": "..."} a database tool the coach is consulting
      text  — {"delta": "..."} a chunk of the reply
      done  — {"model": ..., "input_tokens": ...} reply complete and persisted
      error — {"message": "..."} the turn failed
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

    model = _resolve_model(req.model)

    async def events():
        def sse(kind: str, payload: dict) -> str:
            return f"event: {kind}\ndata: {json.dumps(payload)}\n\n"
        try:
            async for kind, payload in _stream_coach_reply(
                db, goal_id, goal_text, history, proactive=has_new,
                model=model, user_id=uid, target_date=target_date,
            ):
                if kind == "text":
                    yield sse("text", {"delta": payload})
                elif kind == "tool":
                    yield sse("tool", {"label": payload})
                else:
                    yield sse("done", payload)
        except HTTPException as e:
            yield sse("error", {"message": e.detail})
        except Exception as e:  # never leave the client hanging on an open stream
            yield sse("error", {"message": f"Coach failed: {e}"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/coach/today")
async def coach_today(request: Request, model: str = DEFAULT_MODEL):
    """
    Generate 'what should I do today?' advice based on recent activities and goal.
    Returns advice text + up to 3 candidate activity IDs with simplified track coords.
    """
    from app.auth import require_user
    from app.coach_analysis import build_athlete_profile_block, build_training_analysis
    uid = require_user(request)

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

    safe_model = _resolve_model(model)

    # Fetch recent activities (last 60 days) with IDs and stats
    cutoff = int(time.time()) - 60 * 86400
    try:
        rows = db._con.execute("""
            SELECT
                id,
                COALESCE(creation_time_override_s, creation_time_s) AS ts,
                distance_mi, src_total_climb, src_moving_time_s,
                src_avg_heartrate, src_avg_power,
                attributes_json, local_sport_type
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

    tz        = _athlete_tz(db, uid)
    now_local = datetime.now(tz)
    today     = now_local.date()
    today_str = now_local.strftime("%A, %B %-d, %Y")
    goal_section = f"\nATHLETE'S GOAL:\n{goal_text}\n" if goal_text else ""

    act_lines = []
    for r in rows:
        ts    = r["ts"] or 0
        dt    = datetime.fromtimestamp(ts, tz=tz)
        days  = (today - dt.date()).days
        when  = "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")
        attrs = parse_attrs(r["attributes_json"]) or {}
        atype = _resolve_type(r)
        name  = attrs.get("name") or "(unnamed)"
        dist  = round(r["distance_mi"] or 0, 1)
        climb = round(attrs.get("totalClimb") or r["src_total_climb"] or 0)
        moving = r["src_moving_time_s"] or 0
        hrs   = round(moving / 3600, 1)
        hr    = round(r["src_avg_heartrate"] or 0)
        hr_str = f", avg HR {hr}bpm" if hr else ""
        pw    = round(r["src_avg_power"] or 0)
        pw_str = f", avg {pw}W" if pw else ""
        act_lines.append(
            f"  id={r['id']}: {dt.strftime('%Y-%m-%d')} ({when}) {atype} \"{name}\" — "
            f"{dist}mi, {climb}ft climb, {hrs}h{hr_str}{pw_str}"
        )

    profile_block  = build_athlete_profile_block(db, uid)
    analysis_block = build_training_analysis(db, uid)
    profile_section  = f"\n{profile_block}\n"  if profile_block  else ""
    analysis_section = f"\n{analysis_block}\n" if analysis_block else ""

    prompt = (
        f"Today is {today_str}.{goal_section}{profile_section}{analysis_section}\n"
        f"RECENT ACTIVITIES (last 60 days, most recent first):\n"
        + "\n".join(act_lines) +
        "\n\nBased on this athlete's baseline, recent HR/power trends, training-load balance, age, "
        "and goal, recommend what they should do TODAY. Reference specific numbers from the data. "
        "If their load is spiking or they're on a long active streak, recommend appropriate recovery "
        "(scaled to their age) rather than more volume. "
        "Also, from the list above, identify up to 3 activity IDs that are the best examples or templates "
        "for what you're recommending — routes or workouts they've done before that fit well.\n\n"
        "Respond ONLY with valid JSON:\n"
        '{"advice": "2-4 sentence recommendation citing their recent training numbers and trend", "activity_ids": [id1, id2]}\n'
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
                # Covers thinking + reply; Opus 5 thinks by default.
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    raw = first_text(resp.json())

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
        # Group by model so each row is priced at the rate it actually cost.
        # Rows written before the `model` column existed are priced as Haiku,
        # which is what they in fact ran as.
        rows = con.execute(f"""
            SELECT
                strftime('%Y-%m', datetime(coach_messages.created_at, 'unixepoch')) AS month,
                coach_messages.created_at        AS created_at,
                COALESCE(model, '')              AS model,
                COALESCE(input_tokens,  0)       AS input_tokens,
                COALESCE(output_tokens, 0)       AS output_tokens
            FROM coach_messages
            {user_join}
            WHERE role = 'assistant' {user_where}
        """, user_params).fetchall()
    finally:
        con.close()

    import time as _time
    from datetime import datetime, timezone
    now_dt   = datetime.now(timezone.utc)
    month_ts = int(datetime(now_dt.year, now_dt.month, 1, tzinfo=timezone.utc).timestamp())
    six_mo   = int(_time.time()) - 6 * 30 * 86400

    def row_cost(r) -> float:
        info = _model_info(r["model"] or "claude-haiku-4-5")
        return ((r["input_tokens"] / 1_000_000) * info["input_pm"] +
                (r["output_tokens"] / 1_000_000) * info["output_pm"])

    def summarize(subset) -> dict:
        return {
            "queries":       len(subset),
            "input_tokens":  sum(r["input_tokens"] for r in subset),
            "output_tokens": sum(r["output_tokens"] for r in subset),
            "cost_usd":      round(sum(row_cost(r) for r in subset), 4),
        }

    by_month = {}
    for r in rows:
        if r["created_at"] >= six_mo:
            by_month.setdefault(r["month"], []).append(r)

    default_info = _model_info(DEFAULT_MODEL)
    return {
        "models":          {k: v["label"] for k, v in MODELS.items()},
        "pricing":         {k: {"label": v["label"], "input_pm": v["input_pm"],
                                "output_pm": v["output_pm"]}
                            for k, v in MODELS.items()},
        "default_model":   DEFAULT_MODEL,
        "input_price_pm":  default_info["input_pm"],
        "output_price_pm": default_info["output_pm"],
        "alltime":         summarize(rows),
        "this_month":      summarize([r for r in rows if r["created_at"] >= month_ts]),
        "monthly_breakdown": [
            dict(summarize(subset), month=month)
            for month, subset in sorted(by_month.items())
        ],
    }


# ── Claude API call ───────────────────────────────────────────────────────────

MAX_TOOL_ITERATIONS = 8   # generous ceiling; typical answers use 2-4

# Models that accept adaptive thinking + the effort parameter.
_MODERN_MODELS = {"claude-opus-5", "claude-sonnet-5"}


def _api_key_for(db, user_id: Optional[int]) -> str:
    """The user's own key takes priority over the global env key."""
    api_key = ""
    if user_id:
        user = db.get_user(user_id)
        api_key = (user or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "No Anthropic API key set. Add your key in Settings.")
    return api_key


def _build_request_messages(db, history: list, proactive: bool, user_id: Optional[int],
                            target_date: Optional[str], model: str) -> list:
    """History as API messages, terminated by the authoritative date anchor."""
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history if m["role"] in ("user", "assistant")
    ]

    # New activities since the last reply — ask for an acknowledgement.
    if proactive and messages and messages[-1]["role"] == "user":
        probe = (
            "[Note to coach: new activities have been logged since your last response. "
            "Briefly acknowledge the most relevant recent activity in your reply if appropriate, "
            "then address the athlete's question.]"
        )
        messages[-1]["content"] = probe + "\n\n" + messages[-1]["content"]

    # Cold start: the goal was just set and there is nothing to reply to yet.
    if not messages:
        messages = [{"role": "user", "content":
            "I've just set my training goal. Please give me an initial assessment based on "
            "my recent training data and tell me what I should be focusing on right now."}]

    anchor = _build_date_anchor(db, user_id, target_date)
    if model in _SUPPORTS_SYSTEM_MESSAGES:
        # An operator-authority turn placed after the cached history, so it wins
        # over stale dates in the conversation without invalidating the cache.
        messages.append({"role": "system", "content": anchor})
    else:
        # Older models reject role:"system" inside messages[] — fold it into the
        # last user turn instead, which keeps it adjacent to the question.
        messages[-1] = dict(messages[-1])
        messages[-1]["content"] = anchor + "\n\n" + messages[-1]["content"]
    return messages


def _model_kwargs(model: str) -> dict:
    """Thinking/effort settings, which only the current-generation models accept."""
    if model in _MODERN_MODELS:
        return {
            "thinking":      {"type": "adaptive"},
            "output_config": {"effort": "high"},
        }
    return {}


async def _stream_coach_reply(
    db,
    goal_id: int,
    goal_text: str,
    history: list,
    proactive: bool = False,
    model: str = DEFAULT_MODEL,
    user_id: Optional[int] = None,
    target_date: Optional[str] = None,
):
    """Run the agentic coaching turn, yielding UI events as it goes.

    Yields ("tool", label) when a database tool fires, ("text", delta) for each
    chunk of the reply, and finally ("done", meta) once the assistant message has
    been persisted. Raises HTTPException on a hard API failure.
    """
    import anthropic
    from app.coach_analysis import build_athlete_profile_block, build_training_analysis
    from app.coach_tools import TOOL_SCHEMAS, describe_call, run_tool

    model  = _resolve_model(model)
    client = anthropic.AsyncAnthropic(api_key=_api_key_for(db, user_id), max_retries=3)

    system_prompt = _build_system_prompt(
        goal_text,
        _build_activity_summary(db, user_id=user_id),
        tour_summary=_build_tour_summary(db, user_id=user_id),
        target_date=target_date,
        profile_block=build_athlete_profile_block(db, user_id),
        analysis_block=build_training_analysis(db, user_id),
    )
    # One cache breakpoint at the end of the static block. Tools render before
    # the system prompt, so they are cached along with it; the date anchor and
    # the athlete's question sit after it and never invalidate it.
    system = [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]

    messages = _build_request_messages(db, history, proactive, user_id, target_date, model)

    reply_parts = []
    in_tok = out_tok = cache_read = cache_write = 0
    persisted = False

    saved_id = None

    def _persist(text: str, interrupted: bool = False):
        """Write the assistant reply. Called once, even on an aborted stream."""
        nonlocal saved_id
        body = text + ("\n\n_(reply interrupted)_" if interrupted else "")
        con = _get_con(db)
        try:
            cur = con.execute(
                "INSERT INTO coach_messages "
                "(goal_id, role, content, created_at, input_tokens, output_tokens, model) "
                "VALUES (?,?,?,?,?,?,?)",
                (goal_id, "assistant", body, int(time.time()), in_tok, out_tok, model)
            )
            saved_id = cur.lastrowid
            con.commit()
        finally:
            con.close()

    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            async with client.messages.stream(
                model=model,
                max_tokens=16000,
                system=system,
                messages=messages,
                tools=TOOL_SCHEMAS,
                **_model_kwargs(model),
            ) as stream:
                async for event in stream:
                    if (event.type == "content_block_delta"
                            and event.delta.type == "text_delta"):
                        reply_parts.append(event.delta.text)
                        yield ("text", event.delta.text)
                final = await stream.get_final_message()

            u = final.usage
            in_tok      += u.input_tokens or 0
            out_tok     += u.output_tokens or 0
            cache_read  += getattr(u, "cache_read_input_tokens", 0) or 0
            cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

            if final.stop_reason == "refusal":
                raise HTTPException(502, "Claude declined to answer that request.")
            if final.stop_reason != "tool_use":
                break

            # Echo the assistant turn back verbatim — thinking and tool_use
            # blocks must be preserved unmodified.
            messages.append({"role": "assistant", "content": final.content})

            results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                yield ("tool", describe_call(block.name, block.input))
                result = run_tool(db, user_id, block.name, block.input)
                results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(result, default=str)[:60000],
                })
            # All results for one assistant turn go back in a single user message.
            messages.append({"role": "user", "content": results})

            # Text the model wrote before calling tools runs straight into the
            # text it writes after them; keep them visually separate.
            if reply_parts and not reply_parts[-1].endswith("\n"):
                reply_parts.append("\n\n")
                yield ("text", "\n\n")
        else:
            yield ("tool", "Reached the tool-call limit — answering with what I have")

        reply = "".join(reply_parts).strip()
        if not reply:
            raise HTTPException(502, "Claude returned an empty response")
        _persist(reply)
        persisted = True

    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            raise HTTPException(503, "Claude is currently overloaded — please try again in a moment")
        raise HTTPException(502, f"Claude API error {e.status_code}: {str(e)[:300]}")
    except anthropic.APIConnectionError:
        raise HTTPException(504, "Could not reach Claude — please try again")
    finally:
        # The athlete may close the panel or drop connection mid-reply, and a
        # long Opus turn can run for minutes. Save whatever was generated rather
        # than leaving their question in the conversation with no answer at all.
        if not persisted:
            partial = "".join(reply_parts).strip()
            if partial:
                _persist(partial, interrupted=True)
                persisted = True

    yield ("done", {
        "message_id":        saved_id,   # lets the UI wire up copy / PDF actions
        "model":             model,
        "input_tokens":      in_tok,
        "output_tokens":     out_tok,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    })


async def _call_claude(
    db,
    goal_id: int,
    goal_text: str,
    history: list,
    proactive: bool = False,
    model: str = DEFAULT_MODEL,
    user_id: int = None,
    target_date: Optional[str] = None,
) -> tuple:
    """Non-streaming wrapper: run the turn, return (reply_text, message_id).

    The id lets the UI attach copy/PDF actions to the reply without a reload.
    """
    parts, message_id = [], None
    async for kind, payload in _stream_coach_reply(
        db, goal_id, goal_text, history, proactive=proactive, model=model,
        user_id=user_id, target_date=target_date,
    ):
        if kind == "text":
            parts.append(payload)
        elif kind == "done":
            message_id = payload.get("message_id")
    return "".join(parts).strip(), message_id


# ── Export: PDF + plain text ─────────────────────────────────────────────────

def _markdown_to_html(text: str) -> str:
    """Render a coach reply's Markdown for the PDF.

    Mirrors coachMarkdown() in coach.js (headings, lists, tables, code, links).
    markdown-it-py escapes HTML by default, so model output cannot inject markup.
    """
    from markdown_it import MarkdownIt
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable("table")
    return md.render(text or "")


def _owned_goal(con: sqlite3.Connection, goal_id: int, uid: Optional[int]):
    """Fetch a goal, refusing goals that belong to somebody else."""
    goal = con.execute("SELECT * FROM coach_goals WHERE id=?", (goal_id,)).fetchone()
    if not goal:
        raise HTTPException(404, "Conversation not found")
    if goal["user_id"] is not None and goal["user_id"] != uid:
        raise HTTPException(403, "Not your conversation")
    return goal


def _exchanges(msgs: list) -> list:
    """Pair each assistant reply with the question that prompted it.

    Consecutive user messages are joined — the athlete can send a follow-up
    before the coach has answered, and both belong to the reply that follows.
    """
    out, pending = [], []
    for m in msgs:
        if m["role"] == "user":
            pending.append(m)
        elif m["role"] == "assistant":
            out.append({"prompt": pending, "reply": m})
            pending = []
    if pending:                       # question still awaiting an answer
        out.append({"prompt": pending, "reply": None})
    return out


def _export_context(goal, exchanges: list, db, uid: Optional[int]) -> dict:
    """Template context shared by the single-exchange and full-conversation PDFs."""
    tz = _athlete_tz(db, uid)

    def when(ts):
        return datetime.fromtimestamp(ts or 0, tz=tz).strftime("%b %-d, %Y at %-I:%M %p")

    user = db.get_user(uid) if uid else None
    rendered = [
        {
            "prompt_texts": [p["content"] for p in ex["prompt"]],
            "prompt_when":  when(ex["prompt"][0]["created_at"]) if ex["prompt"] else "",
            "reply_html":   _markdown_to_html(ex["reply"]["content"]) if ex["reply"] else "",
            "reply_when":   when(ex["reply"]["created_at"]) if ex["reply"] else "",
            "model":        (ex["reply"] or {}).get("model") or "",
        }
        for ex in exchanges
    ]
    target = goal["target_date"] if "target_date" in goal.keys() else None
    meta = [b for b in (
        (user or {}).get("username") or "",
        f"Target: {target}" if target else "",
        datetime.now(tz).strftime("Exported %B %-d, %Y"),
    ) if b]
    return {
        "goal_text": goal["goal_text"],
        "meta":      meta,               # joined in the template, not via CSS
        "exchanges": rendered,
    }


def _render_coach_pdf(ctx: dict, filename: str) -> Response:
    """Render the export template to a PDF response.

    A coach conversation is text-only, so unlike the photo-heavy tour book this
    renders synchronously — no background job needed.
    """
    try:
        import weasyprint          # lazy — heavy native deps
    except Exception:
        raise HTTPException(501, "PDF export is unavailable on this server "
                                 "(WeasyPrint not installed).")
    html_str  = templates.env.get_template("coach_pdf.html").render(**ctx)
    pdf_bytes = weasyprint.HTML(string=html_str).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _pdf_filename(goal, suffix: str) -> str:
    import re
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", (goal["goal_text"] or "coaching")[:40])
    return f"{stem.strip('_') or 'coaching'}_{suffix}.pdf"


@router.get("/coach/messages/{message_id}/pdf")
async def coach_message_pdf(message_id: int, request: Request):
    """One exchange — the coach's reply plus the question that prompted it."""
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    db  = db_getter()
    con = _get_con(db)
    try:
        msg = con.execute("SELECT * FROM coach_messages WHERE id=?", (message_id,)).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found")
        goal = _owned_goal(con, msg["goal_id"], uid)

        msgs = _goal_messages(con, msg["goal_id"], limit=10000)
        match = [e for e in _exchanges(msgs)
                 if e["reply"] and e["reply"]["id"] == message_id]
        if not match:
            raise HTTPException(400, "That message is not a coach reply")
        ctx = _export_context(goal, match, db, uid)
    finally:
        con.close()

    ctx["subtitle"] = "One exchange"
    return _render_coach_pdf(ctx, _pdf_filename(goal, f"exchange_{message_id}"))


@router.get("/coach/goals/{goal_id}/pdf")
async def coach_goal_pdf(goal_id: int, request: Request):
    """The whole conversation for one goal."""
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    db  = db_getter()
    con = _get_con(db)
    try:
        goal = _owned_goal(con, goal_id, uid)
        msgs = _goal_messages(con, goal_id, limit=10000)
        if not msgs:
            raise HTTPException(404, "This conversation has no messages yet")
        ctx = _export_context(goal, _exchanges(msgs), db, uid)
    finally:
        con.close()

    n = len(ctx["exchanges"])
    ctx["subtitle"] = f"Full conversation · {n} exchange{'s' if n != 1 else ''}"
    return _render_coach_pdf(ctx, _pdf_filename(goal, "conversation"))


# ── Compare Analysis ──────────────────────────────────────────────────────────

class CompareAnalysisRequest(BaseModel):
    activity_ids: list
    elapsed_times: dict          # {str(activity_id): elapsed_s int or null}
    segment_name: str = "Segment"
    model: str = DEFAULT_MODEL


@router.post("/coach/compare-analysis")
async def compare_analysis(req: CompareAnalysisRequest, request: Request):
    """
    Generate a one-shot AI analysis of a segment comparison.
    Does not persist to the coaching conversation.
    """
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)

    db = db_getter()

    # Get API key (user key > env key)
    api_key = ""
    if uid:
        user = db.get_user(uid)
        api_key = (user or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "No Anthropic API key set. Add your key in Settings.")

    model = _resolve_model(req.model)

    # Fetch activity details for each id
    activities = []
    for aid in req.activity_ids:
        act = db.get_activity(int(aid))
        if act:
            elapsed = req.elapsed_times.get(str(aid))
            activities.append({
                "id":          aid,
                "name":        act.get("name", f"Activity {aid}"),
                "date":        datetime.fromtimestamp(act["start_time"], tz=timezone.utc).strftime("%b %d, %Y") if act.get("start_time") else "unknown",
                "elapsed_s":   elapsed,
                "avg_hr":      act.get("avg_heartrate") or 0,
                "max_hr":      act.get("max_heartrate") or 0,
                "avg_power":   act.get("avg_power") or 0,
                "avg_speed":   act.get("avg_speed_mph") or 0,
                "equipment":   act.get("equipment") or "",
                "weather":     act.get("weather") or "",
                "notes":       act.get("notes") or "",
                "activity_type": act.get("activity_type") or "",
            })

    if len(activities) < 2:
        raise HTTPException(400, "Need at least 2 activities to analyze")

    # Find fastest
    timed = [a for a in activities if a["elapsed_s"] is not None]
    if not timed:
        raise HTTPException(400, "No timed efforts to analyze")
    fastest = min(timed, key=lambda a: a["elapsed_s"])

    def fmt_elapsed(s):
        if s is None:
            return "N/A"
        m, sec = divmod(int(s), 60)
        return f"{m}:{sec:02d}"

    # Build effort summaries
    lines = []
    for a in activities:
        parts = [f'**{a["name"]}** ({a["date"]})']
        parts.append(f'Segment time: {fmt_elapsed(a["elapsed_s"])}')
        if a["avg_hr"]:
            parts.append(f'Avg HR: {a["avg_hr"]} bpm')
        if a["max_hr"]:
            parts.append(f'Max HR: {a["max_hr"]} bpm')
        if a["avg_power"]:
            parts.append(f'Avg power: {a["avg_power"]}W')
        if a["avg_speed"]:
            parts.append(f'Avg speed: {a["avg_speed"]} mph')
        if a["equipment"]:
            parts.append(f'Equipment: {a["equipment"]}')
        if a["weather"]:
            parts.append(f'Weather: {a["weather"]}')
        lines.append(" | ".join(parts))

    efforts_text = "\n".join(f"- {l}" for l in lines)

    prompt = f"""You are an expert endurance sports analyst. Analyze the following segment efforts and explain what drove the differences in performance. The segment is called "{req.segment_name}".

Efforts (sorted by activity):
{efforts_text}

Fastest effort: {fastest["name"]} ({fmt_elapsed(fastest["elapsed_s"])})

In 3–5 short paragraphs:
1. Identify the fastest effort and by how much it beat the others
2. Discuss what physical factors (heart rate, power, etc.) explain the difference
3. Note any equipment, weather, or external factors that may have contributed
4. Give one specific, actionable tip for improving future efforts on this segment

Be concise, specific, and reference actual numbers from the data. Do not use markdown headers."""

    # Call Anthropic API directly (no DB persistence)
    max_retries = 3
    base_delay  = 2.0
    resp = None

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
                        "model":      model,
                        # max_tokens covers thinking + reply, and Opus 5 thinks by
                        # default — 1024 truncated the analysis mid-sentence.
                        "max_tokens": 4096,
                        "messages":   [{"role": "user", "content": prompt}],
                    },
                )
        except httpx.TimeoutException:
            raise HTTPException(504, "Claude API timed out")

        if resp.status_code == 200:
            break
        if resp.status_code in (529, 500, 502, 503) and attempt < max_retries - 1:
            await __import__("asyncio").sleep(base_delay * (2 ** attempt))
            continue
        break

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp else 0
        body   = resp.text[:300] if resp else "no response"
        raise HTTPException(502, f"Claude API error {status}: {body}")

    data  = resp.json()
    reply = " ".join(b["text"] for b in data.get("content", []) if b.get("type") == "text").strip()
    if not reply:
        raise HTTPException(502, "Claude returned an empty response")

    return {"analysis": reply}

