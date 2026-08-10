"""coach_tools.py — read-only database tools the AI Coach can call.

Thin wrappers over app/db.py so the coach can investigate the athlete's real
training history instead of reasoning from a fixed prompt summary. Everything
here is read-only and scoped to one user.

SECURITY: several db.py accessors (get_activity, get_laps,
get_chart_data_for_points) take an activity_id with no user scoping, so every
activity-keyed tool below must call _own() first — same pattern as
routers/api.py's activity endpoints.

Each tool returns a JSON-serialisable dict. Errors come back as
{"error": "..."} rather than raising, so a bad tool call costs the model one
turn instead of failing the whole request.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

# Tool order is fixed and deterministic — the tools block renders ahead of the
# system prompt, so reordering it would invalidate the prompt cache.
TOOL_NAMES = [
    "list_activities",
    "get_activity_detail",
    "get_activity_streams",
    "get_zone_distribution",
    "get_training_totals",
    "get_personal_records",
    "get_fitness_scores",
    "get_hr_efficiency_trend",
]

_MAX_ROWS = 100


def _own(db, activity_id: int, user_id: int) -> Optional[dict]:
    """Return the activity only if it belongs to this user."""
    act = db.get_activity(activity_id)
    if not act:
        return None
    if act.get("user_id") != user_id:
        return None
    return act


def _epoch(day: str, end: bool = False) -> int:
    """YYYY-MM-DD -> UTC epoch seconds (end=True gives end-of-day)."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int((d + timedelta(days=1)).timestamp()) - 1 if end else int(d.timestamp())


# ── Tools ─────────────────────────────────────────────────────────────────────

def list_activities(db, user_id: int, start_date: str, end_date: str,
                    activity_type: str = "", limit: int = 50) -> dict:
    """Activities in a date range, newest first."""
    from app.db import parse_attrs
    try:
        lo, hi = _epoch(start_date), _epoch(end_date, end=True)
    except ValueError:
        return {"error": "start_date and end_date must be YYYY-MM-DD"}

    limit = max(1, min(int(limit or 50), _MAX_ROWS))
    rows = db._con.execute("""
        SELECT id, COALESCE(creation_time_override_s, creation_time_s) AS ts,
               distance_mi, src_total_climb, src_moving_time_s, src_elapsed_time_s,
               src_avg_heartrate, src_max_heartrate, src_avg_power, src_max_power,
               attributes_json, local_sport_type
        FROM activities
        WHERE user_id = ? AND COALESCE(creation_time_override_s, creation_time_s)
              BETWEEN ? AND ?
        ORDER BY ts DESC LIMIT ?
    """, (user_id, lo, hi, limit)).fetchall()

    want = (activity_type or "").strip().lower()
    out = []
    for r in rows:
        attrs = parse_attrs(r["attributes_json"]) or {}
        atype = r["local_sport_type"] or attrs.get("activity") or "Activity"
        if want and want not in str(atype).lower():
            continue
        moving = r["src_moving_time_s"] or r["src_elapsed_time_s"] or 0
        out.append({
            "activity_id":   r["id"],
            "date":          datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
            "type":          atype,
            "name":          attrs.get("name"),
            "distance_mi":   round(r["distance_mi"] or 0, 2),
            "climb_ft":      round(attrs.get("totalClimb") or r["src_total_climb"] or 0),
            "moving_hours":  round(moving / 3600, 2),
            "avg_hr":        round(r["src_avg_heartrate"]) if r["src_avg_heartrate"] else None,
            "max_hr":        round(r["src_max_heartrate"]) if r["src_max_heartrate"] else None,
            "avg_power_w":   round(r["src_avg_power"]) if r["src_avg_power"] else None,
            "max_power_w":   round(r["src_max_power"]) if r["src_max_power"] else None,
        })
    return {"count": len(out), "activities": out}


def get_activity_detail(db, user_id: int, activity_id: int) -> dict:
    """Everything recorded for one activity, plus laps if the device saved them."""
    act = _own(db, int(activity_id), user_id)
    if not act:
        return {"error": f"Activity {activity_id} not found or not yours"}

    keep = (
        "id", "name", "notes", "weather", "location", "effort", "disposition",
        "activity_type", "equipment", "device", "event_type", "distance_mi",
        "duration_hms", "active_time_hms", "total_climb_ft", "total_descent_ft",
        "avg_speed_mph", "max_speed_mph", "avg_pace", "avg_heartrate",
        "max_heartrate", "avg_cadence", "max_cadence", "avg_power", "max_power",
        "work_kj", "calories", "suffer_score", "perceived_exertion",
        "max_altitude_ft", "min_altitude_ft", "avg_temp_f", "max_temp_f",
        "local_gear_name",
    )
    out = {k: act.get(k) for k in keep if act.get(k) not in (None, "", 0)}
    out["date"] = datetime.fromtimestamp(
        act.get("start_time") or 0, tz=timezone.utc
    ).strftime("%Y-%m-%d")

    try:
        laps = db.get_laps(int(activity_id))
    except Exception:
        laps = []
    if laps:
        out["laps"] = [dict(l) for l in laps[:50]]
    else:
        out["laps"] = []
        out["laps_note"] = ("No lap data recorded for this activity. Use "
                            "get_activity_streams to analyse its structure instead.")
    return out


def get_activity_streams(db, user_id: int, activity_id: int, samples: int = 120) -> dict:
    """Downsampled per-second series — for pacing, HR drift, and interval structure."""
    if not _own(db, int(activity_id), user_id):
        return {"error": f"Activity {activity_id} not found or not yours"}
    try:
        data = db.get_chart_data_for_points(int(activity_id))
    except Exception:
        return {"error": "No recorded stream data for this activity"}

    time_s = data.get("time") or []
    if not time_s:
        return {"error": "No recorded stream data for this activity"}

    n    = max(10, min(int(samples or 120), 300))
    step = max(1, len(time_s) // n)

    def thin(key, digits=0, scale=1.0):
        vals = data.get(key) or []
        if not vals or not any(v for v in vals):
            return None
        return [
            round(v * scale, digits) if isinstance(v, (int, float)) else None
            for v in vals[::step]
        ]

    return {
        "activity_id":   int(activity_id),
        "sample_count":  len(time_s[::step]),
        "total_seconds": time_s[-1] if time_s else 0,
        "note":          (f"Downsampled to ~1 point per {step} recorded samples. "
                          "Arrays are parallel — index i of each is the same moment."),
        "elapsed_s":     [round(t) for t in time_s[::step]],
        # get_chart_data_for_points returns dist_m in metres despite the source
        # column storing miles; convert back for the coach.
        "distance_mi":   thin("dist_m", 2, scale=1 / 1609.344),
        "altitude_ft":   thin("alt_ft"),
        "heartrate":     thin("hr"),
        "speed_mph":     thin("speed", 1),
        "power_w":       thin("power"),
        "cadence":       thin("cadence"),
    }


def get_zone_distribution(db, user_id: int, period: str = "month",
                          year: Optional[int] = None, month: Optional[int] = None,
                          week_start: Optional[str] = None) -> dict:
    """HR and power zone minutes, computed from raw per-second data."""
    kwargs = {}
    if period == "week":
        if not week_start:
            monday = date.today() - timedelta(days=date.today().weekday())
            week_start = monday.isoformat()
        kwargs["week_start"] = week_start
    elif period == "month":
        today = date.today()
        kwargs["year"]  = int(year or today.year)
        kwargs["month"] = int(month or today.month)
    elif period == "year":
        kwargs["year"] = int(year or date.today().year)
    elif period != "all":
        return {"error": "period must be one of: week, month, year, all"}

    try:
        z = db.get_zone_time(user_id, **kwargs)
    except Exception as e:
        return {"error": f"Could not compute zone time: {e}"}

    hr = z.get("hr_zones_min") or []
    pw = z.get("power_zones_min") or []
    hr_total, pw_total = sum(hr), sum(pw)

    out = {
        "period":  period,
        "max_hr":  z.get("max_hr"),
        "ftp":     z.get("ftp"),
        "hr_zones": [
            {"zone": f"Z{i+1}", "minutes": round(v, 1),
             "pct": round(v / hr_total * 100, 1) if hr_total else 0}
            for i, v in enumerate(hr)
        ],
        "hr_total_minutes": round(hr_total, 1),
        "power_zones": [
            {"zone": f"Z{i+1}", "minutes": round(v, 1),
             "pct": round(v / pw_total * 100, 1) if pw_total else 0}
            for i, v in enumerate(pw)
        ],
        "power_total_minutes": round(pw_total, 1),
    }
    out.update({k: v for k, v in kwargs.items()})
    if hr_total and pw_total < hr_total * 0.5:
        out["note"] = ("Power is recorded on only some activities, so power-zone "
                       "minutes cover far less time than HR zones. Judge intensity "
                       "distribution from the HR zones.")
    return out


def get_training_totals(db, user_id: int, granularity: str = "weekly",
                        year: Optional[int] = None, month: Optional[int] = None,
                        limit: int = 26) -> dict:
    """Weekly, monthly, or yearly rollups across the athlete's whole history."""
    limit = max(1, min(int(limit or 26), _MAX_ROWS))
    try:
        if granularity == "weekly":
            rows = db.get_weekly_totals(year=year, month=month, user_id=user_id)
        elif granularity == "monthly":
            rows = db.get_monthly_totals(year=year, user_id=user_id)
        elif granularity == "yearly":
            rows = db.get_yearly_totals(year=year, user_id=user_id)
        else:
            return {"error": "granularity must be one of: weekly, monthly, yearly"}
    except Exception as e:
        return {"error": f"Could not compute totals: {e}"}

    rows = [dict(r) for r in rows][-limit:]
    for r in rows:
        for k in ("dist_mi", "active_h"):
            if r.get(k) is not None:
                r[k] = round(r[k], 1)
        for k in ("climb_ft", "max_climb_ft", "avg_power_w", "avg_hr"):
            if r.get(k):
                r[k] = round(r[k])
    return {"granularity": granularity, "count": len(rows), "periods": rows}


def get_personal_records(db, user_id: int) -> dict:
    """Lifetime totals and bests — the athlete's demonstrated ceiling."""
    try:
        s = db.get_dashboard_stats(user_id=user_id)
    except Exception as e:
        return {"error": f"Could not load records: {e}"}
    return {
        "total_activities":  s.get("total_activities"),
        "total_distance_mi": s.get("total_distance_mi"),
        "total_climb_ft":    s.get("total_climb_ft"),
        "total_active_time": s.get("total_active_hms"),
        "avg_heartrate":     s.get("avg_heartrate"),
        "longest_ride_mi":   s.get("longest_mi"),
        "most_climb_ft":     s.get("most_climb_ft"),
        "note": "longest_ride_mi and most_climb_ft are single-activity lifetime bests.",
    }


def get_fitness_scores(db, user_id: int, year: Optional[int] = None,
                       month: Optional[int] = None) -> dict:
    """0-100 scores across six training dimensions, graded against personal bests."""
    # NOTE: do not pass skip_zones=True — it silently reports endurance and
    # intensity as 0 with has_hr False, which reads as "this athlete has no HR
    # data". Computing zones costs a few seconds; wrong numbers cost more.
    try:
        f = db.get_fingerprint_data(user_id, year=year, month=month)
    except Exception as e:
        return {"error": f"Could not compute fitness scores: {e}"}
    if not f.get("has_data"):
        return {"error": "Not enough data to compute fitness scores"}
    out = {
        "scale": "0-100, scored against this athlete's own personal bests",
        "volume":      f.get("volume"),
        "climbing":    f.get("climbing"),
        "speed":       f.get("speed"),
        "consistency": f.get("consistency"),
    }
    if f.get("has_hr"):
        out["endurance"] = f.get("endurance")
        out["intensity"] = f.get("intensity")
        out["endurance_intensity_note"] = (
            "endurance = % of HR-zone time in Z1-Z2; intensity = % in Z4-Z5.")
    else:
        out["note"] = ("No usable heart-rate data for this period, so endurance and "
                       "intensity could not be scored.")
    return out


def get_hr_efficiency_trend(db, user_id: int, year: Optional[int] = None,
                            month: Optional[int] = None, limit: int = 40) -> dict:
    """Per-activity heart-rate efficiency — speed or power delivered per heartbeat.

    Rising efficiency at a steady HR is the clearest signal of aerobic progress.
    """
    try:
        rows = db.get_hre_data(user_id=user_id, year=year, month=month)
    except Exception as e:
        return {"error": f"Could not compute HR efficiency: {e}"}
    if not rows:
        return {"error": "No heart-rate data available for this period"}

    limit = max(1, min(int(limit or 40), _MAX_ROWS))
    out = []
    for r in rows[-limit:]:
        d = dict(r)
        out.append({
            "activity_id":  d.get("id"),
            "date":         datetime.fromtimestamp(d.get("ts") or 0, tz=timezone.utc)
                                    .strftime("%Y-%m-%d"),
            "type":         d.get("activity_type"),
            "avg_hr":       round(d["hr"]) if d.get("hr") else None,
            "speed_mph":    round(d["speed_mph"], 1) if d.get("speed_mph") else None,
            "power_w":      round(d["power_w"]) if d.get("power_w") else None,
            "hre_speed":    round(d["hre_speed"], 3) if d.get("hre_speed") else None,
            "hre_power":    round(d["hre_power"], 3) if d.get("hre_power") else None,
        })
    return {
        "count": len(out),
        "note": ("hre_speed = mph per bpm, hre_power = watts per bpm. "
                 "Higher is better — more output for the same cardiac cost."),
        "activities": out,
    }


# ── Tool schemas (Anthropic tool-use format) ─────────────────────────────────
#
# Descriptions are prescriptive about WHEN to call, not just what the tool does
# — that measurably raises should-call rate.

_DATE = {"type": "string", "description": "Date as YYYY-MM-DD"}

TOOL_SCHEMAS = [
    {
        "name": "list_activities",
        "description": (
            "List the athlete's activities in a date range, newest first. Call this to "
            "look beyond the 14 days already in your context — for example to compare "
            "this month against the same month last year, to find every long ride "
            "before a past event, or to check what a build block actually contained."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": _DATE,
                "end_date": _DATE,
                "activity_type": {
                    "type": "string",
                    "description": "Optional filter, matched loosely (e.g. 'Ride', 'Run', 'Hike').",
                },
                "limit": {"type": "integer", "description": "Max activities (default 50, cap 100)."},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_activity_detail",
        "description": (
            "Open one activity in full: every recorded metric plus laps. Call this when a "
            "specific workout matters to your reasoning — a race, a breakthrough ride, or "
            "an activity whose numbers look anomalous and you want to explain why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"activity_id": {"type": "integer", "description": "Activity id."}},
            "required": ["activity_id"],
        },
    },
    {
        "name": "get_activity_streams",
        "description": (
            "Downsampled per-second data within one activity (time, distance, altitude, HR, "
            "speed, power, cadence). Call this to analyse what happened INSIDE a ride: "
            "pacing, whether HR drifted upward at constant power (aerobic decoupling — a "
            "fatigue or heat signal), how climbs were paced, or interval structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {"type": "integer", "description": "Activity id."},
                "samples": {"type": "integer", "description": "Points to return (default 120, cap 300)."},
            },
            "required": ["activity_id"],
        },
    },
    {
        "name": "get_zone_distribution",
        "description": (
            "Time spent in each heart-rate and power zone, computed from raw per-second "
            "data. CALL THIS BEFORE PRESCRIBING INTENSITY OR WRITING ANY TRAINING PLAN — "
            "an athlete's real intensity distribution is usually not what they assume, and "
            "a plan that ignores it is guesswork. Use period='month' or 'week' for current "
            "form; period='all' scans the entire history and is slow, so avoid it unless "
            "the question genuinely spans all time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["week", "month", "year", "all"],
                    "description": "Window to summarise. Defaults to the current month.",
                },
                "year": {"type": "integer", "description": "Year, for period 'month' or 'year'."},
                "month": {"type": "integer", "description": "Month 1-12, for period 'month'."},
                "week_start": {"type": "string", "description": "Monday of the week, YYYY-MM-DD."},
            },
        },
    },
    {
        "name": "get_training_totals",
        "description": (
            "Distance, climbing, hours, and active days rolled up by week, month, or year "
            "across the athlete's entire history. Call this to judge whether current volume "
            "is genuinely high or low FOR THIS ATHLETE, and to compare against the same "
            "period in previous years, before claiming any progression or decline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "granularity": {
                    "type": "string",
                    "enum": ["weekly", "monthly", "yearly"],
                    "description": "Rollup level. Defaults to weekly.",
                },
                "year": {"type": "integer", "description": "Restrict to one year."},
                "month": {"type": "integer", "description": "Restrict to one month (weekly only)."},
                "limit": {"type": "integer", "description": "Most recent N periods (default 26)."},
            },
        },
    },
    {
        "name": "get_personal_records",
        "description": (
            "Lifetime totals and single-activity bests (longest ride, most climbing). Call "
            "this before telling the athlete a target is ambitious or achievable — it shows "
            "what they have actually already done."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_fitness_scores",
        "description": (
            "Six 0-100 scores (volume, climbing, speed, consistency, endurance, intensity), "
            "each graded against this athlete's own personal bests. Call this for a quick "
            "read on which dimension is currently the weakest link limiting their goal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Restrict to one year."},
                "month": {"type": "integer", "description": "Restrict to one month."},
            },
        },
    },
    {
        "name": "get_hr_efficiency_trend",
        "description": (
            "Per-activity heart-rate efficiency over time — speed and watts delivered per "
            "heartbeat. Call this to answer 'am I actually getting fitter?': rising "
            "efficiency at steady HR means aerobic improvement, falling efficiency means "
            "accumulating fatigue or lost fitness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Restrict to one year."},
                "month": {"type": "integer", "description": "Restrict to one month."},
                "limit": {"type": "integer", "description": "Most recent N activities (default 40)."},
            },
        },
    },
]

_DISPATCH = {
    "list_activities":         list_activities,
    "get_activity_detail":     get_activity_detail,
    "get_activity_streams":    get_activity_streams,
    "get_zone_distribution":   get_zone_distribution,
    "get_training_totals":     get_training_totals,
    "get_personal_records":    get_personal_records,
    "get_fitness_scores":      get_fitness_scores,
    "get_hr_efficiency_trend": get_hr_efficiency_trend,
}


def run_tool(db, user_id: int, name: str, args: dict) -> dict:
    """Execute a tool by name. Never raises — errors come back as data."""
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    if user_id is None:
        return {"error": "No user context — cannot read training data"}
    try:
        return fn(db, user_id, **(args or {}))
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}


def describe_call(name: str, args: dict) -> str:
    """Short human-readable line for the UI's live tool trace."""
    a = args or {}
    if name == "list_activities":
        span = f"{a.get('start_date', '?')} to {a.get('end_date', '?')}"
        kind = f" {a['activity_type']}" if a.get("activity_type") else ""
        return f"Looking up{kind} activities from {span}"
    if name == "get_activity_detail":
        return f"Opening activity #{a.get('activity_id')}"
    if name == "get_activity_streams":
        return f"Analysing the ride data inside activity #{a.get('activity_id')}"
    if name == "get_zone_distribution":
        return f"Reading time-in-zone for the {a.get('period', 'month')}"
    if name == "get_training_totals":
        return f"Pulling {a.get('granularity', 'weekly')} training totals"
    if name == "get_personal_records":
        return "Checking personal records"
    if name == "get_fitness_scores":
        return "Scoring current fitness against personal bests"
    if name == "get_hr_efficiency_trend":
        return "Checking heart-rate efficiency trend"
    return f"Running {name}"
