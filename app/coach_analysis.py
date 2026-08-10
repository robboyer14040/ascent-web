"""coach_analysis.py — athlete profile + training baseline/trend analysis.

Shared by the AI Coach (routers/coach.py) and Tour stage advice (routers/tours.py)
so both features reason from the same real training data instead of platitudes:

  * build_athlete_profile_block  — age, max HR, FTP, weight, HR/power zones,
                                   and an age-aware recovery note.
  * build_training_analysis      — baseline performance, recent-vs-prior trends
                                   in heart rate / power / efficiency, training
                                   load balance (acute:chronic), and recovery state.

Both return plain text blocks ready to drop into a Claude prompt, or "" when the
underlying data is unavailable.
"""

import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

# Columns pulled once and reused by every window calculation below.
_ACTIVITY_SELECT = """
    SELECT
        COALESCE(creation_time_override_s, creation_time_s) AS ts,
        distance_mi,
        src_total_climb,
        src_moving_time_s,
        src_elapsed_time_s,
        src_avg_heartrate,
        src_max_heartrate,
        src_avg_power,
        src_avg_cadence,
        attributes_json,
        local_sport_type
    FROM activities
    WHERE COALESCE(creation_time_override_s, creation_time_s) >= ?
    {user_filter}
    ORDER BY ts DESC
    LIMIT 500
"""


def compute_age(profile: dict) -> Optional[int]:
    """Age in years from an explicit `age`, else derived from `birthday` (YYYY-MM-DD)."""
    age = profile.get("age")
    if age and int(age) > 0:
        return int(age)
    bday = profile.get("birthday")
    if bday:
        try:
            b = date.fromisoformat(str(bday)[:10])
            today = date.today()
            return today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        except Exception:
            return None
    return None


def build_athlete_profile_block(db, user_id: Optional[int]) -> str:
    """Physiology + training zones. Returns '' if nothing is configured."""
    if user_id is None:
        return ""
    try:
        profile = db.get_user_profile(user_id)
    except Exception:
        return ""
    if not profile:
        return ""

    age      = compute_age(profile)
    max_hr   = profile.get("max_hr")
    ftp      = profile.get("ftp_watts")
    weight   = profile.get("weight_lb")

    # Estimate max HR from age (Tanaka 2001: 208 − 0.7·age) when not measured.
    est_max_hr = round(208 - 0.7 * age) if (not max_hr and age) else None

    lines = ["ATHLETE PROFILE"]
    if age:
        lines.append(f"Age: {age}")
    if max_hr:
        lines.append(f"Max HR: {max_hr} bpm (configured)")
    elif est_max_hr:
        lines.append(f"Max HR: ~{est_max_hr} bpm (estimated from age — not measured)")
    if ftp:
        wkg = f" ({ftp / (weight / 2.20462):.2f} W/kg)" if weight else ""
        lines.append(f"FTP: {ftp} W{wkg}")
    if weight:
        lines.append(f"Weight: {round(weight)} lb")

    mhr = max_hr or est_max_hr
    if mhr:
        z = [round(mhr * f) for f in (0.60, 0.70, 0.80, 0.90)]
        src = "configured" if max_hr else "age-estimated"
        lines.append(
            f"HR zones ({src} max {mhr}): "
            f"Z1 <{z[0]}, Z2 {z[0]}–{z[1]}, Z3 {z[1]}–{z[2]}, "
            f"Z4 {z[2]}–{z[3]}, Z5 >{z[3]} bpm"
        )
    if ftp:
        p = [round(ftp * f) for f in (0.55, 0.75, 0.90, 1.05, 1.20)]
        lines.append(
            f"Power zones (FTP {ftp}W): "
            f"Z1 <{p[0]}, Z2 {p[0]}–{p[1]}, Z3 {p[1]}–{p[2]}, "
            f"Z4 {p[2]}–{p[3]}, Z5 {p[3]}–{p[4]}, Z6 >{p[4]} W"
        )

    if len(lines) == 1:
        return ""  # profile exists but is entirely blank

    if age:
        lines.append(
            f"NOTE: Recovery capacity declines with age. For a {age}-year-old, weight "
            "recovery-day recommendations and load ramps more conservatively than for a "
            "younger athlete."
        )
    return "\n".join(lines)


def _fetch_rows(db, user_id: Optional[int], days: int):
    """Rows as plain dicts, with attribute-backed fields already resolved.

    `attributes_json` is a flat ["key", "value", ...] array, so it can only be
    read with parse_attrs() — json_extract() returns NULL for every row.
    """
    from app.db import parse_attrs

    cutoff = int(time.time()) - days * 86400
    user_filter = "AND user_id = ?" if user_id is not None else ""
    params = [cutoff] + ([user_id] if user_id is not None else [])
    try:
        rows = db._con.execute(
            _ACTIVITY_SELECT.format(user_filter=user_filter), params
        ).fetchall()
    except Exception:
        return []

    out = []
    for r in rows:
        d = dict(r)
        attrs = parse_attrs(d.pop("attributes_json", None)) or {}
        d["act_type"]   = d.pop("local_sport_type", None) or attrs.get("activity") or "Activity"
        d["name"]       = attrs.get("name")
        d["climb_attr"] = attrs.get("totalClimb")
        out.append(d)
    return out


def _mean(vals):
    vals = [v for v in vals if v]
    return sum(vals) / len(vals) if vals else 0.0


def _window(rows, lo: int, hi: int) -> dict:
    """Aggregate the activities whose timestamp falls in [lo, hi)."""
    sel = [r for r in rows if lo <= (r["ts"] or 0) < hi]
    secs   = sum((r["src_moving_time_s"] or r["src_elapsed_time_s"] or 0) for r in sel)
    hours  = secs / 3600
    dist   = sum((r["distance_mi"] or 0) for r in sel)
    climb  = sum((r["climb_attr"] or r["src_total_climb"] or 0) for r in sel)
    avg_hr = _mean([r["src_avg_heartrate"] for r in sel if (r["src_avg_heartrate"] or 0) > 0])
    avg_pw = _mean([r["src_avg_power"]     for r in sel if (r["src_avg_power"]     or 0) > 0])
    speed  = dist / hours if hours else 0
    # Efficiency factor: watts per heartbeat (power sports) or speed per bpm.
    ef_pw  = (avg_pw / avg_hr) if (avg_hr and avg_pw) else 0
    ef_sp  = (speed / avg_hr * 1000) if (avg_hr and speed) else 0  # mph per bpm ×1000
    return {
        "n": len(sel), "hours": hours, "dist": dist, "climb": climb,
        "avg_hr": avg_hr, "avg_pw": avg_pw, "speed": speed,
        "ef_pw": ef_pw, "ef_sp": ef_sp,
    }


def _delta_str(recent: float, prior: float, unit: str = "", pct: bool = True) -> str:
    if not prior:
        return f"{recent:.0f}{unit}" if recent else "n/a"
    change = (recent - prior) / prior * 100
    arrow = "↑" if change > 3 else ("↓" if change < -3 else "→")
    tail = f" ({change:+.0f}%)" if pct else ""
    return f"{recent:.0f}{unit} {arrow} vs {prior:.0f}{unit}{tail}"


def build_training_analysis(db, user_id: Optional[int]) -> str:
    """Baseline, HR/power trends, load balance, and recovery state as prompt text."""
    rows = _fetch_rows(db, user_id, days=90)
    if not rows:
        return ""

    now = int(time.time())
    day = 86400
    recent = _window(rows, now - 28 * day, now + day)          # last 4 weeks
    prior  = _window(rows, now - 56 * day, now - 28 * day)      # the 4 weeks before that

    # ── Acute:chronic training-load balance (duration-based proxy) ────────────
    acute   = _window(rows, now - 7 * day,  now + day)["hours"]
    chronic = _window(rows, now - 28 * day, now + day)["hours"] / 4.0
    acwr    = (acute / chronic) if chronic else 0
    if not acwr:
        acwr_note = "insufficient history"
    elif acwr > 1.5:
        acwr_note = "load spiking — elevated overreaching/injury risk"
    elif acwr > 1.3:
        acwr_note = "ramping quickly — monitor fatigue"
    elif acwr < 0.8:
        acwr_note = "load dropping — detraining or taper/rest"
    else:
        acwr_note = "balanced"

    # ── Recovery state ────────────────────────────────────────────────────────
    last_ts    = max((r["ts"] or 0) for r in rows)
    days_since = (now - last_ts) / day if last_ts else None
    active_days = {
        datetime.fromtimestamp(r["ts"], tz=timezone.utc).date()
        for r in rows if r["ts"]
    }
    streak = 0
    cur = datetime.fromtimestamp(last_ts, tz=timezone.utc).date() if last_ts else None
    while cur and cur in active_days:
        streak += 1
        cur = cur.fromordinal(cur.toordinal() - 1)

    lines = ["TRAINING ANALYSIS (baseline, trends & load)"]

    # Baseline
    lines.append(
        "Recent 4-week baseline: "
        f"{recent['n']} activities, {recent['dist']:.0f} mi, "
        f"{round(recent['climb']):,} ft climb, {recent['hours']:.1f} h "
        f"(~{recent['hours'] / 4:.1f} h/week)."
    )

    # Trends vs the previous 4 weeks
    lines.append("Trend vs previous 4 weeks:")
    lines.append(f"  Volume (h/4wk):  {_delta_str(recent['hours'], prior['hours'], 'h')}")
    lines.append(f"  Distance (mi):   {_delta_str(recent['dist'],  prior['dist'],  'mi')}")
    if recent["avg_hr"] or prior["avg_hr"]:
        lines.append(f"  Avg HR (bpm):    {_delta_str(recent['avg_hr'], prior['avg_hr'], '')}")
    if recent["avg_pw"] or prior["avg_pw"]:
        lines.append(f"  Avg power (W):   {_delta_str(recent['avg_pw'], prior['avg_pw'], 'W')}")
    if recent["ef_pw"] or prior["ef_pw"]:
        # Efficiency factor: higher = more power per heartbeat = improving aerobic fitness.
        lines.append(
            f"  Efficiency (W/bpm): {recent['ef_pw']:.2f}"
            + (f" vs {prior['ef_pw']:.2f} prior" if prior["ef_pw"] else "")
            + " — higher means more power per heartbeat (improving fitness)."
        )
    elif recent["ef_sp"] or prior["ef_sp"]:
        lines.append(
            f"  Efficiency (speed/HR): {recent['ef_sp']:.2f}"
            + (f" vs {prior['ef_sp']:.2f} prior" if prior["ef_sp"] else "")
            + " — higher means more speed per heartbeat (improving fitness)."
        )

    # Load + recovery
    lines.append(
        f"Load balance (acute:chronic): {acwr:.2f} — {acwr_note}. "
        f"(Last 7 days {acute:.1f} h vs {chronic:.1f} h/week chronic average.)"
    )
    if days_since is not None:
        if days_since < 1:
            rec = "trained today"
        elif days_since < 2:
            rec = "trained yesterday"
        else:
            rec = f"{days_since:.0f} days since last activity"
        lines.append(f"Recovery: {rec}; {streak} consecutive active day(s) in the latest streak.")

    return "\n".join(lines)
