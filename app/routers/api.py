"""routers/api.py — JSON API endpoints consumed by the frontend JS."""

import os, json
from datetime import datetime
from app.auth import get_session_user_id
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import FileResponse
from typing import Callable, Optional

router = APIRouter()
db_getter: Callable = None



@router.get("/stats/recent")
async def recent_stats(request: Request, limit: int = Query(15)):
    uid = get_session_user_id(request)
    return db_getter().get_activities(limit=limit, sort_by="start_time", sort_dir="desc", user_id=uid)


@router.get("/stats/monthly")
async def monthly_stats(request: Request, year: Optional[int] = Query(None)):
    uid = get_session_user_id(request)
    return db_getter().get_monthly_totals(year=year, user_id=uid)

@router.get("/stats/weekly")
async def weekly_stats(request: Request, year: Optional[int] = Query(None), month: Optional[int] = Query(None)):
    uid = get_session_user_id(request)
    return db_getter().get_weekly_totals(year=year, month=month, user_id=uid)

@router.get("/stats/yearly")
async def yearly_stats(request: Request, year: Optional[int] = Query(None)):
    uid = get_session_user_id(request)
    return db_getter().get_yearly_totals(year=year, user_id=uid)

@router.get("/stats/daily")
async def daily_stats(request: Request, week_start: str = Query(...)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return db_getter().get_daily_totals(user_id=uid, week_start=week_start)

@router.get("/stats/daily-month")
async def daily_month_stats(request: Request, year: int = Query(...), month: int = Query(...)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return db_getter().get_daily_totals_for_month(user_id=uid, year=year, month=month)

@router.get("/stats/zones")
async def zone_stats(request: Request, year: Optional[int] = Query(None),
                     month: Optional[int] = Query(None), week_start: Optional[str] = Query(None)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return db_getter().get_zone_time(user_id=uid, year=year, month=month, week_start=week_start)

@router.get("/stats/fingerprint")
async def fingerprint_stats(request: Request, year: Optional[int] = Query(None),
                             month: Optional[int] = Query(None),
                             week_start: Optional[str] = Query(None),
                             skip_zones: bool = Query(False)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return db_getter().get_fingerprint_data(user_id=uid, year=year, month=month, week_start=week_start, skip_zones=skip_zones)

@router.get("/stats/hre")
async def hre_stats(request: Request, year: Optional[int] = Query(None),
                    month: Optional[int] = Query(None),
                    week_start: Optional[str] = Query(None)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return db_getter().get_hre_data(user_id=uid, year=year, month=month, week_start=week_start)

@router.get("/stats/missing-points")
async def missing_points(request: Request, year: Optional[int] = Query(None),
                         month: Optional[int] = Query(None), week_start: Optional[str] = Query(None)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return db_getter().get_activities_missing_points(user_id=uid, year=year, month=month, week_start=week_start)


@router.get("/activities/{activity_id}/geojson")
async def activity_geojson(activity_id: int):
    db = db_getter()
    if not db.get_activity(activity_id):
        raise HTTPException(404, "Activity not found")
    return db.get_track_points_geojson(activity_id)


@router.post("/activities/{activity_id}/save-as-route")
async def save_activity_as_route(activity_id: int, request: Request):
    """Save an activity's GPS track as a local route, optionally uploading to Strava."""
    import httpx, xml.etree.ElementTree as ET
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)

    body     = await request.json()
    name     = body.get("name", "Route").strip() or "Route"
    to_strava = bool(body.get("upload_to_strava", False))
    local_starred = bool(body.get("local_starred", False))

    db  = db_getter()
    geo = db.get_track_points_geojson(activity_id)
    coords = (geo.get("geometry") or {}).get("coordinates") or []
    if not coords:
        raise HTTPException(422, "No GPS points found for this activity")

    # GeoJSON coords are [lon, lat, alt] → route points are [lat, lon]
    points = [[c[1], c[0]] for c in coords]

    # Infer activity type for profile default
    act = db.get_activity(activity_id)
    act_type = ""
    if act:
        try:
            attrs = json.loads(act.get("attributes_json") or "[]")
            kv = dict(zip(attrs[::2], attrs[1::2]))
            act_type = (kv.get("activity") or "").lower()
        except Exception:
            pass
    profile = "pedestrian" if "run" in act_type or "hike" in act_type else "bicycle"

    route_id = db.save_route(
        user_id=uid,
        name=name,
        profile=profile,
        points=points,
        distance_km=act.get("distance_km") or None,
        duration_s=act.get("active_time") or None,
        climb_m=act.get("total_climb_m") or None,
        local_starred=local_starred,
    )

    strava_result = None
    if to_strava:
        try:
            from app.routers.strava import load_tokens, refresh_tokens, tokens_are_fresh, save_tokens
            tokens = load_tokens(user_id=uid)
            if not tokens.get("access_token"):
                strava_result = {"error": "Strava not connected — reconnect Strava to enable uploads"}
            else:
                if not tokens_are_fresh(tokens):
                    tokens = await refresh_tokens(tokens, user_id=uid)
                    save_tokens(tokens, user_id=uid)

                # Check scope
                scope = tokens.get("scope", "")
                if "route:write" not in scope:
                    strava_result = {"error": "Missing route:write scope — disconnect and reconnect Strava to grant route upload permission"}
                else:
                    # Build GPX
                    gpx = ET.Element("gpx", {
                        "version": "1.1", "creator": "Ascent",
                        "xmlns": "http://www.topografix.com/GPX/1/1",
                    })
                    trk = ET.SubElement(gpx, "trk")
                    ET.SubElement(trk, "name").text = name
                    seg = ET.SubElement(trk, "trkseg")
                    for lat, lon in points:
                        ET.SubElement(seg, "trkpt", {"lat": f"{lat:.7f}", "lon": f"{lon:.7f}"})
                    gpx_bytes = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                                 + ET.tostring(gpx, encoding="unicode")).encode()

                    # POST to Strava uploads — creates a new Strava activity from the GPX
                    import io
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(
                            "https://www.strava.com/api/v3/uploads",
                            headers={"Authorization": f"Bearer {tokens['access_token']}"},
                            files={"file": ("route.gpx", io.BytesIO(gpx_bytes), "application/gpx+xml")},
                            data={"data_type": "gpx", "name": name},
                        )
                    if resp.status_code in (200, 201):
                        strava_result = {"ok": True, "upload_id": resp.json().get("id")}
                    else:
                        strava_result = {"error": f"Strava upload failed: {resp.text[:200]}"}
        except Exception as e:
            strava_result = {"error": str(e)}

    return {"route_id": route_id, "strava": strava_result}


@router.get("/activities/{activity_id}/charts")
async def activity_charts(activity_id: int):
    db = db_getter()
    if not db.get_activity(activity_id):
        raise HTTPException(404, "Activity not found")
    return db.get_chart_data_for_points(activity_id)


@router.get("/activities/{activity_id}/laps")
async def activity_laps(activity_id: int):
    return db_getter().get_laps(activity_id)


from typing import List
from pydantic import BaseModel

class DeleteActivitiesRequest(BaseModel):
    ids: List[int]

@router.delete("/activities")
async def delete_activities(req: DeleteActivitiesRequest):
    db = db_getter()
    count = db.delete_activities(req.ids)
    return {"deleted": count}


@router.get("/me")
async def me(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    user = db_getter().get_user(uid)
    if not user:
        raise HTTPException(404, "User not found")
    has_key = bool(
        (user or {}).get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    )
    return {
        "id": user["id"],
        "username": user.get("username") or user.get("email", "?"),
        "has_anthropic_key": has_key,
    }


@router.post("/activities/{activity_id}/suggest-title")
async def suggest_activity_title(activity_id: int, request: Request):
    """Call Claude to generate a witty/humorous activity title based on stats."""
    import httpx

    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    db   = db_getter()
    act  = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    user    = db.get_user(uid)
    api_key = (user or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "No Anthropic API key configured")


    import secrets
    _SEED_WORDS = [
        "accordion", "badger", "Ptolemy", "stapler", "fjord", "mayonnaise",
        "trebuchet", "hamster", "Wellington", "kumquat", "dirigible", "platypus",
        "Rasputin", "cauliflower", "monocle", "catapult", "brisket", "Vesuvius",
        "yodeling", "wombat", "saxophone", "turnip", "Machiavelli", "marmalade",
        "penguin", "obelisk", "fondue", "Charlemagne", "kazoo", "spatula",
        "narwhal", "crouton", "bureaucracy", "corgi", "Copernicus", "jalapeño",
        "quokka", "periscope", "semaphore", "baguette", "Fibonacci", "tambourine",
        "walrus", "archipelago", "tiramisu", "zeppelin", "mongoose", "croissant",
    ]
    word_a = secrets.choice(_SEED_WORDS)
    word_b = secrets.choice([w for w in _SEED_WORDS if w != word_a])

    prompt  = (
        f"Write a short (2–6 words), absurd, funny activity title. "
        f"It MUST reference both: {word_a} and {word_b}. "
        "Nothing to do with exercise, cycling, running, or fitness. "
        "Be weird and unexpected. Reply with ONLY the title, no quotes, no explanation."
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
                "model":       "claude-haiku-4-5-20251001",
                "max_tokens":  60,
                "temperature": 1,
                "messages":    [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    title = resp.json()["content"][0]["text"].strip().strip('"').strip("'")
    return {"title": title}


_VALID_SUMMARY_MODELS = {
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
}

def _ensure_summary_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS activity_ai_summaries (
            activity_id INTEGER PRIMARY KEY,
            summary     TEXT    NOT NULL,
            model       TEXT,
            created_at  INTEGER NOT NULL
        )
    """)
    con.commit()

def _act_stats_str(a: dict) -> str:
    parts = []
    if a.get("distance_mi"):
        parts.append(f"{a['distance_mi']:.2f} miles")
    if a.get("active_time"):
        secs = int(a["active_time"])
        h, rem = divmod(secs, 3600)
        m = rem // 60
        parts.append(f"{h}h {m}m moving time" if h else f"{m}m moving time")
    if a.get("total_climb_ft"):
        parts.append(f"{int(a['total_climb_ft'])} ft gain")
    if a.get("avg_heartrate"):
        parts.append(f"avg HR {int(a['avg_heartrate'])} bpm")
    if a.get("max_heartrate"):
        parts.append(f"max HR {int(a['max_heartrate'])} bpm")
    if a.get("avg_speed_mph"):
        parts.append(f"avg {a['avg_speed_mph']:.1f} mph")
    if a.get("avg_power"):
        parts.append(f"avg power {int(a['avg_power'])} W")
    if a.get("suffer_score"):
        parts.append(f"suffer score {int(a['suffer_score'])}")
    return ", ".join(parts) or "no detailed stats"

@router.get("/activities/{activity_id}/ai-summary")
async def activity_ai_summary(activity_id: int, request: Request, model: str = "claude-haiku-4-5-20251001", refresh: bool = False):
    """Return a cached or freshly generated AI summary for an activity."""
    import httpx, sqlite3, time as _time

    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    user    = db.get_user(uid)
    api_key = (user or {}).get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "No Anthropic API key configured")

    is_owner   = act.get("user_id") == uid
    safe_model = model if model in _VALID_SUMMARY_MODELS else "claude-haiku-4-5-20251001"

    # ── Check persistent cache ────────────────────────────────────────────────
    con = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_summary_table(con)
        cached = con.execute(
            "SELECT summary FROM activity_ai_summaries WHERE activity_id = ?",
            (activity_id,)
        ).fetchone()
        if cached and not refresh:
            con.close()
            return {"summary": cached[0], "cached": True}
    except Exception:
        pass

    # ── Not the owner — return nothing rather than generating ─────────────────
    if not is_owner:
        con.close()
        raise HTTPException(403, "Summary not available")

    # ── Activity base info ────────────────────────────────────────────────────
    act_start = act.get("start_time") or 0
    act_type  = act.get("activity_type", "")

    # ── Fetch applicable coach goal ───────────────────────────────────────────
    # Apply the nearest goal whose target_date is within 6 months of this activity.
    # Goals more than 6 months away are too distant to be relevant — just summarize the ride.
    goal_text = None
    act_date_str = datetime.utcfromtimestamp(act_start).strftime("%Y-%m-%d") if act_start else None
    try:
        if act_date_str:
            act_date = datetime.strptime(act_date_str, "%Y-%m-%d")
            # 6-month cutoff: roughly 183 days
            from datetime import timedelta
            cutoff_date = act_date + timedelta(days=183)
            cutoff_str  = cutoff_date.strftime("%Y-%m-%d")
            rows = con.execute(
                "SELECT goal_text, target_date FROM coach_goals "
                "WHERE user_id=? AND target_date IS NOT NULL "
                "  AND target_date > ? "
                "  AND target_date <= ? "
                "ORDER BY target_date ASC LIMIT 1",
                (uid, act_date_str, cutoff_str)
            ).fetchall()
            if rows:
                goal_text = rows[0][0]
    except Exception:
        pass

    # ── Fetch recent past activities of the same type (before this one) ───────
    recent_lines = []
    try:
        rows = con.execute(
            """SELECT name, distance_mi, active_time, total_climb_ft, avg_heartrate, avg_speed_mph
               FROM activities
               WHERE user_id = ? AND start_time < ? AND activity_type = ?
               ORDER BY start_time DESC LIMIT 8""",
            (uid, act_start, act_type)
        ).fetchall()
        for r in rows:
            name, dist, atime, climb, hr, spd = r
            p = []
            if dist:   p.append(f"{dist:.1f} mi")
            if atime:
                h2, rem2 = divmod(int(atime), 3600); m2 = rem2 // 60
                p.append(f"{h2}h {m2}m" if h2 else f"{m2}m")
            if climb:  p.append(f"{int(climb)} ft gain")
            if hr:     p.append(f"avg HR {int(hr)} bpm")
            if spd:    p.append(f"{spd:.1f} mph")
            recent_lines.append(f'  • "{name}": {", ".join(p)}')
    except Exception:
        pass

    con.close()

    # ── Build prompt ──────────────────────────────────────────────────────────
    stats_str = _act_stats_str(act)
    recent_clause = (
        f"\nFor context, their {len(recent_lines)} most recent prior {act_type} activities:\n"
        + "\n".join(recent_lines)
    ) if recent_lines else ""

    # Sport-type context hints so Claude understands what each type implies
    _TYPE_HINTS = {
        "GravelRide":      "Gravel rides involve unpaved/rough terrain and are typically harder than equivalent road rides.",
        "MountainBikeRide":"Mountain biking involves technical off-road terrain and is very demanding.",
        "TrailRun":        "Trail runs involve varied terrain and elevation and are harder than equivalent road runs.",
        "VirtualRide":     "This was a virtual/indoor ride (e.g. on a trainer or Zwift).",
        "VirtualRun":      "This was a virtual/treadmill run.",
        "Swim":            "Open water or pool swimming.",
        "Rowing":          "Rowing workout (machine or on water).",
    }
    type_hint = _TYPE_HINTS.get(act_type, "")
    type_hint_clause = f" ({type_hint})" if type_hint else ""

    if goal_text:
        goal_clause = f"\nAthlete's upcoming training goal: {goal_text}."
        goal_instruction = (
            "Briefly note how this activity relates to the athlete's training goal "
            "if relevant. "
        )
    else:
        goal_clause = ""
        goal_instruction = (
            "Highlight any performance standouts — e.g. distance, climb, pace, or HR records. "
        )

    prompt = (
        f"Write a 1–2 sentence summary of this {act_type or 'activity'}{type_hint_clause}.\n"
        f"Name: \"{act.get('name', 'Unnamed')}\". Stats: {stats_str}."
        f"{goal_clause}{recent_clause}\n"
        f"Be specific and mention notable stats or how it compares to recent efforts if relevant. "
        f"{goal_instruction}"
        "No emojis. No markdown."
    )

    # ── Call Claude ───────────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      safe_model,
                "max_tokens": 150,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    summary = resp.json()["content"][0]["text"].strip()

    # ── Persist ───────────────────────────────────────────────────────────────
    try:
        con2 = sqlite3.connect(db.path, timeout=10)
        _ensure_summary_table(con2)
        con2.execute(
            "INSERT OR REPLACE INTO activity_ai_summaries (activity_id, summary, model, created_at) VALUES (?,?,?,?)",
            (activity_id, summary, safe_model, int(_time.time()))
        )
        con2.commit()
        con2.close()
    except Exception:
        pass

    return {"summary": summary, "cached": False}


@router.get("/users")
async def list_users(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    from pathlib import Path
    db = db_getter()
    users = db.list_users()
    result = []
    for u in users:
        avatar_url = None
        if u.get("avatar_path"):
            thumb_path = Path(u["avatar_path"]).parent / f"{u['id']}_thumb.jpg"
            if thumb_path.exists():
                avatar_url = f"/api/avatar/{u['id']}?thumb=1"
        result.append({
            "id":         u["id"],
            "username":   u.get("username") or u.get("email", "?"),
            "avatar_url": avatar_url,
        })
    return result


@router.get("/debug/activity-user-counts")
async def debug_activity_user_counts(request: Request):
    """Diagnostic: return count of activities per user_id value in the DB."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    import sqlite3
    db = db_getter()
    rows = db._con.execute(
        "SELECT user_id, COUNT(*) as cnt FROM activities GROUP BY user_id ORDER BY cnt DESC"
    ).fetchall()
    return [{"user_id": r[0], "count": r[1]} for r in rows]


@router.get("/schema")
async def schema_info():
    db = db_getter()
    tables = db.raw_tables()
    return {
        "tables": tables,
        "activities_columns": db.raw_columns("activities"),
        "points_columns": db.raw_columns("points") if "points" in tables else [],
    }


@router.post("/activities/{activity_id}/fetch-points")
async def fetch_points_from_strava(activity_id: int, request: Request):
    """
    Fetch GPS streams from Strava for an activity that has no local points,
    store them permanently in the points table, and return the point count.
    """
    import os, json, time
    from pathlib import Path
    import httpx
    from app.strava_importer import build_points_rows

    db = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        raise HTTPException(400, "Activity has no Strava ID — cannot fetch GPS points")

    # Get fresh token (per-user if request available, else legacy file)
    from app.routers.strava import load_tokens, tokens_are_fresh, refresh_tokens
    uid    = get_session_user_id(request) if request else None
    tokens = load_tokens(user_id=uid)
    if not tokens.get("refresh_token"):
        raise HTTPException(401, "Not connected to Strava")
    if not tokens_are_fresh(tokens):
        tokens = await refresh_tokens(tokens, user_id=uid)

    token = tokens["access_token"]
    stream_types = "latlng,heartrate,velocity_smooth,time,cadence,altitude,distance,watts,temp,moving"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}/streams",
            headers={"Authorization": f"Bearer {token}"},
            params={"keys": stream_types, "key_by_type": "true"},
        )
        if resp.status_code == 404:
            raise HTTPException(404, "Activity not found on Strava")
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid — reconnect Strava")
        resp.raise_for_status()
        streams = resp.json()

    # Convert streams to point rows
    point_rows_raw = build_points_rows(streams, activity_id)
    if not point_rows_raw:
        raise HTTPException(422, "No GPS data returned from Strava for this activity")

    # patch track_id (build_points_rows uses activity_id directly now)
    count = db.store_points(activity_id, point_rows_raw)

    # If description is missing, fetch full activity detail to save it
    # (the activities list API doesn't include description, but the detail endpoint does)
    if not act.get("notes"):
        try:
            async with httpx.AsyncClient(timeout=15) as dclient:
                detail_resp = await dclient.get(
                    f"https://www.strava.com/api/v3/activities/{strava_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"include_all_efforts": "false"},
                )
                if detail_resp.status_code == 200:
                    sa = detail_resp.json()
                    desc = (sa.get("description") or "").strip()
                    if desc:
                        db.update_activity_attrs(activity_id, {"notes": desc})
        except Exception:
            pass

    return {"status": "ok", "points_stored": count, "activity_id": activity_id}


# ── Local activity edit ───────────────────────────────────────────────────────

@router.post("/activities/{activity_id}/update")
async def update_activity_local(activity_id: int, req: dict, request: Request):
    """Save local edits (name, description, sport_type, gear) as pending — pushed to Strava on next resync."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    name        = (req.get("name") or "").strip() or None
    description = req.get("description")   # empty string is valid (clears description)
    visibility  = req.get("visibility")
    sport_type  = (req.get("sport_type") or "").strip() or None
    update_gear = "gear_id" in req
    gear_id     = req.get("gear_id")    # "" = clear gear; "bXXX" = set gear
    gear_name   = req.get("gear_name") or ""
    update_pe   = "perceived_exertion" in req
    pe_raw      = req.get("perceived_exertion")
    perceived_exertion = int(pe_raw) if pe_raw is not None else None
    if visibility is not None and visibility not in ("everyone", "followers_only", "only_me"):
        raise HTTPException(400, "visibility must be 'everyone' or 'only_me'")
    db_inst = db_getter()
    db_inst.update_activity_local(activity_id, uid,
                                  name=name, description=description, visibility=visibility,
                                  sport_type=sport_type, gear_id=gear_id, gear_name=gear_name,
                                  update_gear=update_gear,
                                  perceived_exertion=perceived_exertion,
                                  update_perceived_exertion=update_pe)
    return db_inst.get_activity(activity_id)


async def _push_activity_to_strava(strava_id: int, token: str,
                                   name: Optional[str],
                                   description: Optional[str],
                                   visibility: Optional[str],
                                   sport_type: Optional[str] = None,
                                   gear_id: Optional[str] = None,
                                   perceived_exertion: Optional[int] = None) -> dict:
    """PUT updated fields to Strava. Only sends fields that were explicitly changed.
    Returns the Strava response body."""
    import httpx, logging
    log = logging.getLogger("uvicorn")
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if sport_type:
        payload["sport_type"] = sport_type
    if gear_id is not None:           # "" clears gear on Strava; "bXXX" sets it
        payload["gear_id"] = gear_id
    if perceived_exertion is not None:
        payload["perceived_exertion"] = perceived_exertion
    # visibility intentionally omitted — Strava's API does not support changing it
    if not payload:
        return {}
    log.info(f"[push_strava] PUT activity {strava_id} payload={payload}")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"https://www.strava.com/api/v3/activities/{strava_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    log.info(f"[push_strava] response {resp.status_code}: visibility={resp.json().get('visibility') if resp.status_code == 200 else resp.text[:200]}")
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Strava push failed: {resp.text[:200]}")
    return resp.json()


@router.post("/activities/{activity_id}/resync")
async def resync_activity(activity_id: int, request: Request):
    """
    Re-fetch full activity metadata + photos/videos from Strava and update the DB.
    Returns the refreshed activity dict (same shape as the activity list).
    """
    import sqlite3, time, httpx
    from app.strava_importer import (
        build_attributes_json, _decode_polyline_bbox,
        iso_to_unix, parse_tz_name, parse_tz_offset,
        _f, M_TO_MI, M_TO_FT, MPS_TO_MPH,
    )
    from app.routers.photos import resolve_photos

    db = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        raise HTTPException(400, "Activity has no Strava ID")

    uid       = get_session_user_id(request)
    owner_uid = act.get("user_id") or uid

    # Prefer the activity owner's Strava token; fall back to the viewer's
    from app.routers.strava import get_fresh_token as _gft
    token = await _gft(user_id=owner_uid)
    if not token:
        token = await _gft(user_id=uid)
    if not token:
        raise HTTPException(401, "Not connected to Strava")

    # Push any pending local edits to Strava before re-fetching (owner only)
    if act.get("local_edited_at"):
        await _push_activity_to_strava(
            strava_id=strava_id,
            token=token,
            name=act.get("local_name"),
            description=act.get("local_description"),
            visibility=None,
            sport_type=act.get("local_sport_type"),
            gear_id=act.get("local_gear_id"),
            perceived_exertion=act.get("perceived_exertion"),
        )
        db.clear_activity_local_edits(activity_id)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"include_all_efforts": "false"},
        )
        if resp.status_code == 404:
            raise HTTPException(404, "Activity not found on Strava")
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid — reconnect Strava")
        resp.raise_for_status()
        sa = resp.json()

    # Build updated fields using the same logic as insert_activity_summary
    # Full activity detail includes a `gear` object with the name directly
    gear_name  = (sa.get("gear") or {}).get("name") or None
    attrs_json = build_attributes_json(sa, gear_name=gear_name)
    dist_mi         = _f(sa.get("distance"), 0) * M_TO_MI
    start_unix      = iso_to_unix(sa.get("start_date"))
    src_max_speed   = _f(sa.get("max_speed"),    0) * MPS_TO_MPH
    src_avg_hr      = _f(sa.get("average_heartrate"))
    src_max_hr      = _f(sa.get("max_heartrate"))
    src_avg_temp_f  = (_f(sa.get("average_temp"), 0) * 9/5 + 32) \
                      if sa.get("average_temp") is not None else None
    src_max_elev_ft = _f(sa.get("elev_high"), 0) * M_TO_FT
    src_min_elev_ft = _f(sa.get("elev_low"),  0) * M_TO_FT
    src_avg_power   = _f(sa.get("weighted_average_watts") or sa.get("average_watts"))
    src_max_power   = _f(sa.get("max_watts"))
    src_avg_cad     = _f(sa.get("average_cadence"))
    src_total_climb = _f(sa.get("total_elevation_gain"), 0) * M_TO_FT
    src_kj          = _f(sa.get("kilojoules"))
    src_elapsed     = _f(sa.get("elapsed_time"))
    src_moving      = _f(sa.get("moving_time"))

    start_latlng = sa.get("start_latlng") or []
    start_lat = float(start_latlng[0]) if len(start_latlng) >= 2 else None
    start_lon = float(start_latlng[1]) if len(start_latlng) >= 2 else None

    polyline = (sa.get("map") or {}).get("summary_polyline") or ""
    bbox = _decode_polyline_bbox(polyline)
    map_min_lat, map_max_lat, map_min_lon, map_max_lon = bbox if bbox else (None, None, None, None)

    db_path = os.environ.get("ASCENT_DB_PATH", "")
    con = sqlite3.connect(db_path, timeout=30)
    try:
        src_pe = sa.get("perceived_exertion")
        pe_val = int(src_pe) if src_pe is not None else None
        con.execute("""
            UPDATE activities SET
                name                    = ?,
                creation_time_s         = COALESCE(creation_time_s, ?),
                distance_mi             = ?,
                attributes_json         = ?,
                src_distance            = ?,
                src_max_speed           = ?,
                src_avg_heartrate       = ?,
                src_max_heartrate       = ?,
                src_avg_temperature     = ?,
                src_max_elevation       = ?,
                src_min_elevation       = ?,
                src_avg_power           = ?,
                src_max_power           = ?,
                src_avg_cadence         = ?,
                src_total_climb         = ?,
                src_kilojoules          = ?,
                src_elapsed_time_s      = ?,
                src_moving_time_s       = ?,
                time_zone               = ?,
                seconds_from_gmt_at_sync= ?,
                start_lat               = ?,
                start_lon               = ?,
                map_min_lat             = ?,
                map_max_lat             = ?,
                map_min_lon             = ?,
                map_max_lon             = ?,
                strava_visibility       = ?,
                perceived_exertion      = COALESCE(?, perceived_exertion),
                local_media_items_json  = NULL,
                local_video_urls_json   = NULL
            WHERE id = ?
        """, (
            sa.get("name", ""),
            start_unix,
            dist_mi,
            attrs_json,
            dist_mi, src_max_speed,
            src_avg_hr, src_max_hr,
            src_avg_temp_f,
            src_max_elev_ft, src_min_elev_ft,
            src_avg_power, src_max_power,
            src_avg_cad, src_total_climb,
            src_kj, src_elapsed, src_moving,
            parse_tz_name(sa), parse_tz_offset(sa),
            start_lat, start_lon,
            map_min_lat, map_max_lat, map_min_lon, map_max_lon,
            sa.get("visibility"),
            pe_val,
            activity_id,
        ))
        con.commit()
    finally:
        con.close()

    # Re-fetch photos and videos from Strava, bypassing any cached filenames
    await resolve_photos(activity_id, force=True)

    # Return the refreshed activity in the same shape the frontend expects
    return db.get_activity(activity_id)


@router.get("/strava/gear")
async def strava_gear(request: Request):
    """Return the authenticated user's bikes and shoes from Strava."""
    import httpx
    from app.routers.strava import load_tokens, tokens_are_fresh, refresh_tokens
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    tokens = load_tokens(user_id=uid)
    if not tokens.get("access_token"):
        return {"bikes": [], "shoes": []}
    if not tokens_are_fresh(tokens):
        tokens = await refresh_tokens(tokens, user_id=uid)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://www.strava.com/api/v3/athlete",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    if r.status_code != 200:
        return {"bikes": [], "shoes": []}
    athlete = r.json()
    bikes = [{"id": b["id"], "name": b.get("name") or b["id"]}
             for b in (athlete.get("bikes") or [])]
    shoes = [{"id": s["id"], "name": s.get("name") or s["id"]}
             for s in (athlete.get("shoes") or [])]
    return {"bikes": bikes, "shoes": shoes}


# ── SEGMENT COMPARE ──────────────────────────────────────────────────────────

class SegmentRequest(BaseModel):
    activity_id:     int
    start_idx:       int
    end_idx:         int
    max_results:     int        = 4
    radius_m:        float      = 150.0
    include_friends: bool       = False
    candidate_ids:   list[int]  = []  # if set, only compare against these activities


@router.post("/segment/compare")
async def segment_compare(req: SegmentRequest, request: Request):
    """
    Find activities that pass through the same segment.
    The reference activity is ALWAYS included.
    Other activities are filtered by:
      1. start_lat/lon proximity to segment bounding box (cheap, no points needed)
      2. GPS point proximity to segment start+end (requires points; fetched on demand)
    Returns up to req.max_results+1 fastest (elapsed time) including reference.
    """
    import math, asyncio

    db = db_getter()

    # ── Reference segment ────────────────────────────────────────────────────
    ref_pts = db.get_track_points(req.activity_id)
    if not ref_pts:
        raise HTTPException(404, "No GPS points for reference activity")

    n  = len(ref_pts)

    # req.start_idx/end_idx are indices into the FILTERED chart data array.
    # ref_pts from get_track_points may include sentinel rows (lat=999.0) that
    # shift indices. Filter ref_pts the same way the chart does.
    ref_pts = [p for p in ref_pts if p["lat"] != 999.0 and p["lon"] != 999.0]
    n  = len(ref_pts)
    if n < 2:
        raise HTTPException(400, "Not enough valid GPS points in reference activity")
    si = max(0, min(req.start_idx, n - 2))   # clamp to n-2 so ei always has room
    ei = max(si + 1, min(req.end_idx, n - 1))

    ref_start    = ref_pts[si]
    ref_end      = ref_pts[ei]
    start_lat    = ref_start["lat"]
    start_lon    = ref_start["lon"]
    end_lat      = ref_end["lat"]
    end_lon      = ref_end["lon"]
    ref_elapsed  = ref_pts[ei]["t"] - ref_pts[si]["t"]

    if ref_elapsed <= 0:
        raise HTTPException(400, "Invalid segment: zero elapsed time")

    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat/2)**2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon/2)**2)
        return R * 2 * math.asin(min(1, math.sqrt(a)))

    def haversine_m(lat1, lon1, lat2, lon2):
        return haversine_km(lat1, lon1, lat2, lon2) * 1000.0

    def point_to_segment_dist_km(p, a, b):
        """Distance from point p to line segment a-b in km.
        Uses cos-corrected Euclidean projection to handle lon scaling at non-equatorial lats."""
        lat_scale = math.cos(math.radians((a["lat"] + b["lat"]) / 2))
        ax, ay = a["lon"] * lat_scale, a["lat"]
        bx, by = b["lon"] * lat_scale, b["lat"]
        px, py = p["lon"] * lat_scale, p["lat"]
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return haversine_km(p["lat"], p["lon"], a["lat"], a["lon"])
        t = ((px - ax) * dx + (py - ay) * dy) / (dx*dx + dy*dy)
        t = max(0.0, min(1.0, t))
        # Convert projected point back to real lon
        cx = (ax + t * dx) / lat_scale
        cy = ay + t * dy
        return haversine_km(p["lat"], p["lon"], cy, cx)

    def seg_length_km(pts, si2, ei2):
        return sum(haversine_km(pts[i]["lat"], pts[i]["lon"],
                                pts[i+1]["lat"], pts[i+1]["lon"])
                   for i in range(si2, min(ei2, len(pts)-1)))

    def is_segment_similar(pts_a, si_a, ei_a, pts_b, si_b, ei_b, max_dev_km):
        """
        Bidirectional path deviation check (mirrors AscentPathFinder.isSegment).
        Every point in A must be within max_dev_km of the nearest segment in B, and vice versa.
        """
        # Check every point in A against segments of B
        for i in range(si_a, ei_a + 1):
            p = pts_a[i]
            min_d = float("inf")
            for j in range(si_b, ei_b):
                d = point_to_segment_dist_km(p, pts_b[j], pts_b[j+1])
                if d < min_d:
                    min_d = d
            if min_d > max_dev_km:
                return False
        # Check every point in B against segments of A
        for i in range(si_b, ei_b + 1):
            p = pts_b[i]
            min_d = float("inf")
            for j in range(si_a, ei_a):
                d = point_to_segment_dist_km(p, pts_a[j], pts_a[j+1])
                if d < min_d:
                    min_d = d
            if min_d > max_dev_km:
                return False
        return True

    def find_segment_start(pts, lat, lon, length_km, tol_km):
        """
        Find the index of the point in pts closest to (lat,lon).
        No hard radius cutoff — returns the closest point, caller checks distance.
        """
        best_i, best_d = 0, float("inf")
        for i, p in enumerate(pts):
            d = haversine_km(p["lat"], p["lon"], lat, lon)
            if d < best_d:
                best_d, best_i = d, i
        return best_i if best_d <= tol_km else -1

    def find_segment_end(pts, start_i, length_km, tol_km, end_lat, end_lon):
        """
        Walk forward from start_i accumulating distance.
        Search for the closest point to (end_lat, end_lon) within the window
        [length_km - tol_km, length_km + tol_km] of accumulated distance.
        Also does a global closest-to-end search as fallback.
        """
        accum = 0.0
        window_pts = []   # (index, dist_to_end) within the length window
        best_global_i, best_global_d = -1, float("inf")

        for i in range(start_i, len(pts) - 1):
            accum += haversine_km(pts[i]["lat"], pts[i]["lon"],
                                  pts[i+1]["lat"], pts[i+1]["lon"])
            if accum >= length_km - tol_km:
                d_end = haversine_km(pts[i+1]["lat"], pts[i+1]["lon"], end_lat, end_lon)
                window_pts.append((i+1, d_end))
                if d_end < best_global_d:
                    best_global_d, best_global_i = d_end, i+1
            if accum > length_km + tol_km:
                break

        if not window_pts:
            return -1

        # Pick the window point closest to end coords
        window_pts.sort(key=lambda x: x[1])
        best_i, best_d = window_pts[0]
        return best_i if best_d <= tol_km else -1

    def closest_idx(pts, lat, lon, start=0, end=None):
        best_i, best_d = start, float("inf")
        end = end if end is not None else len(pts)
        for i in range(start, end):
            d = haversine_m(pts[i]["lat"], pts[i]["lon"], lat, lon)
            if d < best_d:
                best_d, best_i = d, i
        return best_i, best_d

    def seg_points_sample(pts, si2, ei2):
        seg  = pts[si2:ei2 + 1]
        step = max(1, len(seg) // 500)
        out  = seg[::step]
        if seg and seg[-1] not in out:
            out = out + [seg[-1]]
        t0 = pts[si2]["t"]
        # Build cumulative distance in metres
        cum = 0.0
        dist_list = [0.0]
        for k in range(1, len(out)):
            p1, p2 = out[k-1], out[k]
            cum += haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"]) * 1000
            dist_list.append(cum)
        return [{"t": out[k]["t"] - t0, "lat": out[k]["lat"], "lon": out[k]["lon"],
                 "alt_ft": out[k]["alt_ft"], "hr": out[k]["hr"],
                 "speed_mph": out[k]["speed_mph"], "dist_m": dist_list[k]}
                for k in range(len(out))]

    def build_match(act_id, name, start_time, pts, si2, ei2, user_id=None):
        elapsed = pts[ei2]["t"] - pts[si2]["t"]
        if elapsed <= 0:
            return None
        return {
            "activity_id": act_id,
            "name":        name,
            "start_time":  start_time,
            "elapsed_s":   elapsed,
            "user_id":     user_id,
            "start_idx":   si2,
            "end_idx":     ei2,
            "points":      seg_points_sample(pts, si2, ei2),
        }

    # ── Build reference match (always included) ───────────────────────────────
    ref_act   = db.get_activity(req.activity_id)
    ref_match = {
        "activity_id": req.activity_id,
        "name":        ref_act.get("name", "(unnamed)") if ref_act else "(unnamed)",
        "start_time":  ref_act.get("start_time") if ref_act else None,
        "elapsed_s":   ref_elapsed,
        "user_id":     ref_act.get("user_id") if ref_act else None,
        "start_idx":   si,
        "end_idx":     ei,
        "points":      seg_points_sample(ref_pts, si, ei),
    }

    # Tolerances — faithful port of findTracks/findShortestTracks
    tolerance_pct  = 10.0
    ref_length_km  = seg_length_km(ref_pts, si, ei)
    tol_km         = ref_length_km * (tolerance_pct / 100.0)
    max_dev_km     = max(tol_km, 0.05)          # minimum 50m — same as ObjC (FLT_MAX default)
    min_length_km  = max(0.0, ref_length_km - tol_km)
    max_length_km  = ref_length_km + tol_km

    # Segment bbox covering ALL ref segment points + small pad for GPS jitter
    seg_lats = [p["lat"] for p in ref_pts[si:ei+1]]
    seg_lons = [p["lon"] for p in ref_pts[si:ei+1]]
    pad = max_dev_km / 111.0  # convert km tolerance to degrees
    seg_min_lat = min(seg_lats) - pad
    seg_max_lat = max(seg_lats) + pad
    seg_min_lon = min(seg_lons) - pad
    seg_max_lon = max(seg_lons) + pad

    # Start search tolerance = maxDeviationKM — faithful to ObjC FindPointIndexNearLocation
    start_tol_km = max_dev_km

    # Backfill map bbox from GPS points for activities that have points but no bbox.
    # Uses MIN/MAX which SQLite handles efficiently. One-time write per activity.
    db._con.execute("""
        UPDATE activities SET
            map_min_lat = (SELECT MIN(latitude_e7)  FROM points WHERE track_id=activities.id AND latitude_e7  != 999.0),
            map_max_lat = (SELECT MAX(latitude_e7)  FROM points WHERE track_id=activities.id AND latitude_e7  != 999.0),
            map_min_lon = (SELECT MIN(longitude_e7) FROM points WHERE track_id=activities.id AND longitude_e7 != 999.0),
            map_max_lon = (SELECT MAX(longitude_e7) FROM points WHERE track_id=activities.id AND longitude_e7 != 999.0)
        WHERE map_min_lat IS NULL
          AND points_saved = 1 AND points_count > 0
    """)
    db._con.commit()

    # Build user filter for candidate activities
    uid = get_session_user_id(request)

    # Lookback cutoff from user profile (0 = unlimited)
    import time as _time
    _lookback_cutoff = None
    if uid is not None:
        try:
            _profile = db.get_user_profile(uid)
            _lookback_years = _profile.get("compare_lookback_years") or 0
            if _lookback_years > 0:
                _lookback_cutoff = _time.time() - _lookback_years * 365.25 * 86400
        except Exception:
            pass

    lookback_filter = "AND ts >= ?" if _lookback_cutoff is not None else ""
    lookback_params = [_lookback_cutoff] if _lookback_cutoff is not None else []

    # When explicit candidate_ids are given (user manually selected activities),
    # bypass user/spatial filters entirely — trust the explicit selection.
    if req.candidate_ids:
        placeholders = ','.join('?' * len(req.candidate_ids))
        candidates = db._con.execute(f"""
            SELECT id, name,
                   COALESCE(creation_time_override_s, creation_time_s) AS ts,
                   points_saved, points_count, strava_activity_id, user_id
            FROM activities
            WHERE id IN ({placeholders})
            ORDER BY COALESCE(creation_time_override_s, creation_time_s) DESC
        """, req.candidate_ids).fetchall()
    else:
        if req.include_friends and uid is not None:
            user_filter = """AND (user_id = ? OR user_id IN (
                    SELECT id FROM users WHERE share_activities = 1 AND id != ?))"""
            user_params = [uid, uid]
        elif uid is not None:
            user_filter = "AND (user_id = ? OR user_id IS NULL)"
            user_params = [uid]
        else:
            user_filter = ""
            user_params = []

        # E-bike filter: match reference activity type
        EBIKE_TYPE = "EBikeRide"
        ref_type = (ref_act or {}).get("activity_type", "")
        _ebike_sql = """
            EXISTS (
                SELECT 1
                FROM json_each(activities.attributes_json) k
                JOIN json_each(activities.attributes_json) v ON v.key = k.key + 1
                WHERE k.value = 'activity' AND v.value = 'EBikeRide'
            )
        """
        if ref_type == EBIKE_TYPE:
            ebike_filter = f"AND ({_ebike_sql})"
        else:
            ebike_filter = f"AND NOT ({_ebike_sql})"

        candidates = db._con.execute(f"""
            SELECT id, name,
                   COALESCE(creation_time_override_s, creation_time_s) AS ts,
                   points_saved, points_count, strava_activity_id, user_id
            FROM activities
            WHERE id != ?
              {user_filter}
              {ebike_filter}
              {lookback_filter}
              AND (
                (points_saved = 1 AND points_count > 0
                 AND (map_min_lat IS NULL
                      OR (map_min_lat <= ? AND map_max_lat >= ?
                          AND map_min_lon <= ? AND map_max_lon >= ?)))
                OR (points_saved = 0
                    AND map_min_lat IS NOT NULL
                    AND map_min_lat <= ? AND map_max_lat >= ?
                    AND map_min_lon <= ? AND map_max_lon >= ?)
              )
            ORDER BY ts DESC
        """, [req.activity_id] + user_params + lookback_params +
             [seg_max_lat, seg_min_lat, seg_max_lon, seg_min_lon,
              seg_max_lat, seg_min_lat, seg_max_lon, seg_min_lon]
        ).fetchall()


    def max_dev_one_way(pts_a, pts_b):
        """Max of per-point min-distances from A to nearest segment in B (ObjC port)."""
        thr   = max_dev_km
        close = thr * 0.1
        max_d = 0.0
        for p in pts_a:
            min_d = float("inf")
            for j in range(len(pts_b)-1):
                d = point_to_segment_dist_km(p, pts_b[j], pts_b[j+1])
                if d < min_d:
                    min_d = d
                if min_d <= close:
                    break
            if min_d > max_d:
                max_d = min_d
            if max_d > thr:
                return max_d
        return max_d

    matches = []
    # Explicit candidate_ids: user chose specific activities — use lenient matching
    # (just find closest start/end, no deviation or tolerance checks).
    use_lenient = bool(req.candidate_ids)

    for row in candidates:
        act_id      = row[0]
        act_name    = row[1] or "(unnamed)"
        act_ts      = row[2]
        pts_saved   = row[3]
        pts_count   = row[4]
        strava_id   = row[5]
        act_user_id = row[6] if len(row) > 6 else None

        # ── Get points ──────────────────────────────────────────────────────
        def _valid_pt(p):
            lat, lon = p.get("lat"), p.get("lon")
            return (lat is not None and lon is not None
                    and lat != 999.0 and lon != 999.0
                    and -90 <= lat <= 90 and -180 <= lon <= 180
                    and not (lat == 0.0 and lon == 0.0))

        # Try precomputed summary first (lean lat/lon/cum_dist_m/t/orig_idx rows).
        # Fall back to full point dicts only if summary not built yet.
        sum_pts = db.get_points_summary(act_id)
        using_summary = bool(sum_pts)

        if using_summary:
            pts = sum_pts
        elif pts_saved and pts_count:
            pts = [p for p in db.get_track_points(act_id) if _valid_pt(p)]
        elif strava_id:
            # No points yet — fetch from Strava on demand
            try:
                import os, json, time, httpx
                from pathlib import Path
                from app.strava_importer import build_points_rows

                from app.routers.strava import load_tokens as _lt, tokens_are_fresh as _tf, refresh_tokens as _rt
                _uid    = get_session_user_id(request)
                tokens  = _lt(user_id=_uid)
                if not tokens.get("refresh_token"):
                    continue
                if tokens.get("expires_at", 0) <= time.time() + 60:
                    continue  # skip refresh during bulk scan for speed
                token = tokens["access_token"]
                stream_types = "latlng,time,altitude,heartrate,velocity_smooth,distance,watts"
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"https://www.strava.com/api/v3/activities/{strava_id}/streams",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"keys": stream_types, "key_by_type": "true"},
                    )
                if resp.status_code != 200:
                    continue
                rows = build_points_rows(resp.json(), act_id)
                if not rows:
                    continue
                db.store_points(act_id, rows)
                pts = [p for p in db.get_track_points(act_id) if _valid_pt(p)]
            except Exception:
                continue
        else:
            continue

        if len(pts) < 2:
            continue

        cos_lat = math.cos(math.radians(start_lat))

        if use_lenient:
            # Lenient: trust the user's selection — just find closest start/end points.
            # No tolerance threshold, no deviation check, no length window.
            best_i, best_dsq = 0, float("inf")
            for i, p in enumerate(pts):
                d2 = (p["lat"]-start_lat)**2 + ((p["lon"]-start_lon)*cos_lat)**2
                if d2 < best_dsq:
                    best_dsq, best_i = d2, i
            si2 = best_i

            best_j, best_dsq2 = si2 + 1, float("inf")
            for j in range(si2 + 1, len(pts)):
                d2 = (pts[j]["lat"]-end_lat)**2 + ((pts[j]["lon"]-end_lon)*cos_lat)**2
                if d2 < best_dsq2:
                    best_dsq2, best_j = d2, j
            ei2 = best_j

            if ei2 <= si2:
                continue
        else:
            # Faithful port of findTracks / findShortestTracks from AscentPathFinder.m
            # Uses same tolerances, same checks, same order.
            tol_deg_sq = (start_tol_km / 111.0) ** 2

            # a. FindPointIndexNearLocation — find closest point to segment start within tolerance
            best_i, best_dsq = -1, float("inf")
            for i, p in enumerate(pts):
                d2 = (p["lat"]-start_lat)**2 + ((p["lon"]-start_lon)*cos_lat)**2
                if d2 < best_dsq:
                    best_dsq, best_i = d2, i
            if best_i < 0 or best_dsq > tol_deg_sq:
                continue
            si2 = best_i

            # Quick sanity check: does the track get close to end coords within 1.5× length?
            max_walk = ref_length_km * 1.5
            min_end_q = float("inf")
            if using_summary:
                # Use precomputed cum_dist_m to walk without recomputing haversine
                si2_cum = pts[si2]["cum_dist_m"]
                max_walk_m = max_walk * 1000.0
                for p in pts[si2:]:
                    if p["cum_dist_m"] - si2_cum > max_walk_m:
                        break
                    d_q = haversine_km(p["lat"], p["lon"], end_lat, end_lon)
                    if d_q < min_end_q:
                        min_end_q = d_q
            else:
                accum_q = 0.0
                for _q in range(si2, min(si2 + 5000, len(pts) - 1)):
                    accum_q += haversine_km(pts[_q]["lat"], pts[_q]["lon"], pts[_q+1]["lat"], pts[_q+1]["lon"])
                    d_q = haversine_km(pts[_q+1]["lat"], pts[_q+1]["lon"], end_lat, end_lon)
                    if d_q < min_end_q:
                        min_end_q = d_q
                    if accum_q > max_walk:
                        break
            if min_end_q > max_dev_km * 3:
                continue

            # b. Find end index
            if using_summary:
                # Binary search on cum_dist_m — accurate because cum_dist reflects full-res path
                si2_cum = pts[si2]["cum_dist_m"]
                target_min_m = si2_cum + (ref_length_km - tol_km) * 1000.0
                target_max_m = si2_cum + (ref_length_km + tol_km) * 1000.0
                window_pts = []
                for _wi in range(si2 + 1, len(pts)):
                    c = pts[_wi]["cum_dist_m"]
                    if c < target_min_m:
                        continue
                    if c > target_max_m:
                        break
                    window_pts.append((_wi, haversine_km(pts[_wi]["lat"], pts[_wi]["lon"], end_lat, end_lon)))
                if not window_pts:
                    continue
                window_pts.sort(key=lambda x: x[1])
                _best_i, _best_d = window_pts[0]
                ei2 = _best_i if _best_d <= tol_km else -1
            else:
                ei2 = find_segment_end(pts, si2, ref_length_km, tol_km, end_lat, end_lon)
            if ei2 < 0 or ei2 <= si2:
                continue

            # c. Length check
            if using_summary:
                cand_len_km = (pts[ei2]["cum_dist_m"] - pts[si2]["cum_dist_m"]) / 1000.0
            else:
                cand_len_km = seg_length_km(pts, si2, ei2)
            if cand_len_km < min_length_km or cand_len_km > max_length_km:
                continue

            # d. isSegment:similarTo: — bidirectional max-deviation check
            step_a = max(1, (ei  - si)  // 300)
            step_b = max(1, (ei2 - si2) // 300)
            ref_sub  = ref_pts[si:ei+1:step_a]
            cand_sub = pts[si2:ei2+1:step_b]

            d_a2b = max_dev_one_way(ref_sub,  cand_sub)
            if d_a2b > max_dev_km:
                continue
            d_b2a = max_dev_one_way(cand_sub, ref_sub)
            if d_b2a > max_dev_km:
                continue

        # For summary-based matches, load full points to build the rich response.
        # Re-run the exact same start/end search on full_pts — summary orig_idx is
        # only approximate (step can be 10–30 for long activities), so a window
        # refinement isn't reliable. Full scan is O(n) on confirmed matches only (2–5).
        if using_summary:
            full_pts = [p for p in db.get_track_points(act_id) if _valid_pt(p)]

            if use_lenient:
                si2_full, best_dsq = 0, float("inf")
                for _k, p in enumerate(full_pts):
                    d2 = (p["lat"]-start_lat)**2 + ((p["lon"]-start_lon)*cos_lat)**2
                    if d2 < best_dsq:
                        best_dsq, si2_full = d2, _k
                ei2_full, best_dsq = si2_full + 1, float("inf")
                for _k in range(si2_full + 1, len(full_pts)):
                    d2 = (full_pts[_k]["lat"]-end_lat)**2 + ((full_pts[_k]["lon"]-end_lon)*cos_lat)**2
                    if d2 < best_dsq:
                        best_dsq, ei2_full = d2, _k
            else:
                _idx = _find_segment_indices(full_pts, start_lat, start_lon, end_lat, end_lon, ref_length_km, tol_km)
                if not _idx:
                    continue
                si2_full, ei2_full = _idx

            m = build_match(act_id, act_name, act_ts, full_pts, si2_full, ei2_full, user_id=act_user_id)
        else:
            m = build_match(act_id, act_name, act_ts, pts, si2, ei2, user_id=act_user_id)
        if m:
            matches.append(m)

    if not matches:
        if use_lenient:
            # Return just the reference; frontend will show candidates as "missing"
            return {"matches": [ref_match], "segment_name": ""}
        raise HTTPException(404,
            "No other activities found passing through this segment. "
            "Try a shorter or more common segment, or sync more activities.")

    # Sort by elapsed time fastest first
    matches.sort(key=lambda m: m["elapsed_s"])

    # Always include reference; fill remaining slots with fastest others
    # Target: max_results total including reference
    all_results = [ref_match]
    seen = {req.activity_id}
    for m in matches:
        if len(all_results) >= req.max_results:
            break
        if m["activity_id"] not in seen:
            seen.add(m["activity_id"])
            all_results.append(m)

    all_results.sort(key=lambda m: m["elapsed_s"])


    if len(all_results) < 2:
        raise HTTPException(404,
            "Only 1 matching activity found — need at least 2 to compare. "
            "Try a shorter segment or sync more activities with GPS data.")

    return {"matches": all_results}


# ── SEGMENT SAVE / LIST ───────────────────────────────────────────────────────

class SegmentSaveRequest(BaseModel):
    name:        str
    activity_id: int
    start_idx:   int
    end_idx:     int

@router.post("/segments")
async def save_segment(req: SegmentSaveRequest):
    import math, json as json_mod
    db = db_getter()

    # Use the same filtered point list as the chart to match the drag-selection indices
    chart = db.get_chart_data_for_points(req.activity_id)
    if not chart or not chart.get("alt_ft"):
        raise HTTPException(404, "No chart data for activity")

    # Build filtered pts list matching chart ordering
    all_pts = db.get_track_points(req.activity_id)
    pts = [p for p in all_pts if p["lat"] != 999.0 and p["lon"] != 999.0]
    if not pts:
        raise HTTPException(404, "No GPS points for activity")

    n  = len(pts)
    si = max(0, min(req.start_idx, n-1))
    ei = max(si+1, min(req.end_idx, n-1))

    def hav_km(lat1,lon1,lat2,lon2):
        R=6371.0; dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
        a=math.sin(dlat/2)**2+math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return R*2*math.asin(min(1,math.sqrt(a)))

    length_km = sum(hav_km(pts[i]["lat"],pts[i]["lon"],pts[i+1]["lat"],pts[i+1]["lon"])
                    for i in range(si, min(ei, n-2)))

    seg = pts[si:ei+1]
    lats = [p["lat"] for p in seg]
    lons = [p["lon"] for p in seg]

    # Subsample to ≤200 points for storage
    step = max(1, len(seg)//200)
    sampled = seg[::step]
    if seg[-1] not in sampled:
        sampled.append(seg[-1])

    points_json = json_mod.dumps([[p["lat"], p["lon"]] for p in sampled])

    seg_id = db.save_segment(
        name=req.name.strip() or "Unnamed Segment",
        activity_id=req.activity_id,
        start_idx=si, end_idx=ei,
        length_km=round(length_km, 4),
        min_lat=min(lats), max_lat=max(lats),
        min_lon=min(lons), max_lon=max(lons),
        points_json=points_json,
    )
    return {"id": seg_id, "name": req.name, "length_km": length_km,
            "start_lat": pts[si]["lat"], "start_lon": pts[si]["lon"],
            "end_lat": pts[ei]["lat"], "end_lon": pts[ei]["lon"]}


@router.put("/segments/{segment_id}")
async def update_segment_full(segment_id: int, req: SegmentSaveRequest):
    import math, json as json_mod
    db = db_getter()

    existing = db.get_segment(segment_id)
    if not existing:
        raise HTTPException(404, "Segment not found")

    chart = db.get_chart_data_for_points(req.activity_id)
    if not chart or not chart.get("alt_ft"):
        raise HTTPException(404, "No chart data for activity")

    all_pts = db.get_track_points(req.activity_id)
    pts = [p for p in all_pts if p["lat"] != 999.0 and p["lon"] != 999.0]
    if not pts:
        raise HTTPException(404, "No GPS points for activity")

    n  = len(pts)
    si = max(0, min(req.start_idx, n-1))
    ei = max(si+1, min(req.end_idx, n-1))

    def hav_km(lat1,lon1,lat2,lon2):
        R=6371.0; dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
        a=math.sin(dlat/2)**2+math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return R*2*math.asin(min(1,math.sqrt(a)))

    length_km = sum(hav_km(pts[i]["lat"],pts[i]["lon"],pts[i+1]["lat"],pts[i+1]["lon"])
                    for i in range(si, min(ei, n-2)))

    seg_pts = pts[si:ei+1]
    lats = [p["lat"] for p in seg_pts]
    lons = [p["lon"] for p in seg_pts]

    step = max(1, len(seg_pts)//200)
    sampled = seg_pts[::step]
    if seg_pts[-1] not in sampled:
        sampled.append(seg_pts[-1])

    points_json = json_mod.dumps([[p["lat"], p["lon"]] for p in sampled])

    db.update_segment(
        segment_id=segment_id,
        name=req.name.strip() or existing["name"],
        activity_id=req.activity_id,
        start_idx=si, end_idx=ei,
        length_km=round(length_km, 4),
        min_lat=min(lats), max_lat=max(lats),
        min_lon=min(lons), max_lon=max(lons),
        points_json=points_json,
    )
    return {"ok": True, "id": segment_id}


@router.patch("/segments/{segment_id}")
async def rename_segment(segment_id: int, body: dict):
    db = db_getter()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    seg = db.get_segment(segment_id)
    if not seg:
        raise HTTPException(404, "Segment not found")
    db.update_segment_name(segment_id, name)
    return {"ok": True}


@router.delete("/segments/{segment_id}")
async def delete_segment(segment_id: int):
    db = db_getter()
    db.delete_segment(segment_id)
    return {"ok": True}


@router.get("/segments/for-activity/{activity_id}")
async def segments_for_activity(request: Request, activity_id: int):
    db = db_getter()
    uid = get_session_user_id(request)
    segs = db.get_segments_for_activity(activity_id, current_user_id=uid)
    return {"segments": [{"id": s["id"], "name": s["name"],
                          "length_km": s["length_km"],
                          "start_idx": s["start_idx"],
                          "end_idx":   s["end_idx"],
                          "activity_id": s["activity_id"],
                          "matched_start_idx": s.get("matched_start_idx"),
                          "matched_end_idx":   s.get("matched_end_idx")}
                         for s in segs]}


class SegmentCompareByIdRequest(BaseModel):
    segment_id:  int
    activity_id: int   # the reference activity
    max_results: int = 4
    radius_m:    float = 150.0

@router.post("/segment/compare-saved")
async def segment_compare_saved(req: SegmentCompareByIdRequest):
    """Run compare using a previously saved segment definition."""
    db = db_getter()
    seg = db.get_segment(req.segment_id)
    if not seg:
        raise HTTPException(404, "Segment not found")
    # Delegate to the main compare endpoint using saved start/end indices
    # but with the reference activity provided by the caller
    from pydantic import BaseModel as BM
    class _R(BM):
        activity_id: int; start_idx: int; end_idx: int
        max_results: int = 4; radius_m: float = 150.0
    inner = SegmentRequest(
        activity_id=req.activity_id,
        start_idx=seg["start_idx"],
        end_idx=seg["end_idx"],
        max_results=req.max_results,
        radius_m=req.radius_m,
    )
    return await segment_compare(inner)


@router.post("/activities/backfill-bboxes")
async def backfill_bboxes(request: Request):
    """One-time: fetch Strava activity summaries to populate missing map bboxes.
    Call this once after deploying — subsequent Strava syncs store bbox automatically."""
    import os, json, time, httpx, logging
    from pathlib import Path
    from app.strava_importer import _decode_polyline_bbox

    db = db_getter()

    no_bbox = db._con.execute("""
        SELECT id, strava_activity_id FROM activities
        WHERE map_min_lat IS NULL AND strava_activity_id IS NOT NULL
        ORDER BY id ASC
    """).fetchall()

    if not no_bbox:
        return {"updated": 0, "remaining": 0, "message": "All activities already have bbox"}

    token_file = None  # not used
    from app.routers.strava import load_tokens as _lt3, tokens_are_fresh as _tf3, refresh_tokens as _rt3
    uid3   = get_session_user_id(request)
    tokens = _lt3(user_id=uid3)
    if not tokens.get("refresh_token"):
        raise HTTPException(401, "Not connected to Strava")
    if not _tf3(tokens):
        tokens = await _rt3(tokens, user_id=uid3)

    token   = tokens["access_token"]
    updated = 0
    skipped = 0
    last_error = None

    async with httpx.AsyncClient(timeout=30) as client:
        for db_id, strava_id in no_bbox:
            try:
                resp = await client.get(
                    f"https://www.strava.com/api/v3/activities/{strava_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"include_all_efforts": "false"},
                )
                if resp.status_code == 429:
                    last_error = "rate_limited"
                    break
                if resp.status_code != 200:
                    last_error = f"http_{resp.status_code}"
                    skipped += 1
                    continue
                act      = resp.json()
                polyline = (act.get("map") or {}).get("summary_polyline") or ""
                bbox     = _decode_polyline_bbox(polyline)
                if bbox:
                    db._con.execute(
                        "UPDATE activities SET map_min_lat=?,map_max_lat=?,map_min_lon=?,map_max_lon=? WHERE id=?",
                        (bbox[0], bbox[1], bbox[2], bbox[3], db_id)
                    )
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                last_error = str(e)[:80]
                skipped += 1
                continue

    if updated:
        db._con.commit()

    remaining = db._con.execute(
        "SELECT COUNT(*) FROM activities WHERE map_min_lat IS NULL AND strava_activity_id IS NOT NULL"
    ).fetchone()[0]

    return {"updated": updated, "skipped": skipped, "remaining": remaining, "last_error": last_error}


class MultiCompareRequest(BaseModel):
    activity_ids: list   # ordered list; first is reference
    segment_id:   int    # saved segment to use for timing

@router.post("/segment/compare-manual")
async def segment_compare_manual(req: MultiCompareRequest):
    """Compare specific activities on a saved segment. No matching — use exact IDs."""
    import math, json as json_mod
    db = db_getter()

    seg = db.get_segment(req.segment_id)
    if not seg:
        raise HTTPException(404, "Segment not found")

    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        dlat = math.radians(lat2-lat1); dlon = math.radians(lon2-lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return R * 2 * math.asin(min(1, math.sqrt(a)))

    def seg_points_sample(pts, si2, ei2):
        seg  = pts[si2:ei2+1]
        step = max(1, len(seg)//500)
        out  = seg[::step]
        if seg and seg[-1] not in out: out = out + [seg[-1]]
        t0 = pts[si2]["t"]
        cum = 0.0; dist_list = [0.0]
        for k in range(1, len(out)):
            p1, p2 = out[k-1], out[k]
            cum += haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"]) * 1000
            dist_list.append(cum)
        return [{"t": out[k]["t"]-t0, "lat": out[k]["lat"], "lon": out[k]["lon"],
                 "alt_ft": out[k]["alt_ft"], "hr": out[k]["hr"],
                 "speed_mph": out[k]["speed_mph"], "dist_m": dist_list[k]}
                for k in range(len(out))]

    # For each activity, find the segment using saved start/end coords
    start_lat = seg["min_lat"]  # use bbox center as approximate anchor
    start_lon = seg["min_lon"]
    # Better: decode the stored points_json to get actual start/end coords
    seg_pts = json_mod.loads(seg["points_json"]) if seg.get("points_json") else []
    if seg_pts:
        start_lat, start_lon = seg_pts[0][0], seg_pts[0][1]
        end_lat,   end_lon   = seg_pts[-1][0], seg_pts[-1][1]
    else:
        raise HTTPException(400, "Segment has no GPS points stored")

    ref_length_km = seg.get("length_km", 0)
    tol_km = ref_length_km * 0.10
    cos_lat = math.cos(math.radians(start_lat))

    matches = []
    for act_id in req.activity_ids[:5]:  # cap at 5
        act = db.get_activity(act_id)
        if not act: continue
        pts = [p for p in db.get_track_points(act_id)
               if p["lat"] != 999.0 and p["lon"] != 999.0]
        if not pts: continue

        # Find start
        tol_deg_sq = (max(tol_km, 0.05) * 5 / 111.0) ** 2
        best_i, best_dsq = 0, float("inf")
        for i, p in enumerate(pts):
            d2 = (p["lat"]-start_lat)**2 + ((p["lon"]-start_lon)*cos_lat)**2
            if d2 < best_dsq: best_dsq, best_i = d2, i
        si2 = best_i

        # Find end
        accum, window_pts = 0.0, []
        for i in range(si2, len(pts)-1):
            accum += haversine_km(pts[i]["lat"], pts[i]["lon"], pts[i+1]["lat"], pts[i+1]["lon"])
            if accum >= ref_length_km - tol_km:
                d_end = haversine_km(pts[i+1]["lat"], pts[i+1]["lon"], end_lat, end_lon)
                window_pts.append((i+1, d_end))
            if accum > ref_length_km + tol_km: break

        if not window_pts: continue
        window_pts.sort(key=lambda x: x[1])
        ei2 = window_pts[0][0]
        if ei2 <= si2: continue

        elapsed = pts[ei2]["t"] - pts[si2]["t"]
        if elapsed <= 0: continue

        matches.append({
            "activity_id": act_id,
            "name":        act.get("name", "(unnamed)"),
            "start_time":  act.get("start_time"),
            "elapsed_s":   elapsed,
            "user_id":     act.get("user_id"),
            "start_idx":   si2,
            "end_idx":     ei2,
            "points":      seg_points_sample(pts, si2, ei2),
        })

    if len(matches) < 1:
        raise HTTPException(404, "None of the selected activities contain this segment")

    return {"matches": matches, "segment_name": seg["name"]}


@router.get("/activities/{activity_id}/strava-kudos")
async def get_strava_kudos(activity_id: int, request: Request):
    """Return the kudos count for a Strava-linked activity."""
    import httpx
    from app.routers.strava import get_fresh_token

    uid = get_session_user_id(request)
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        return {"kudos_count": 0}

    owner_uid = act.get("user_id") or uid
    token = await get_fresh_token(user_id=owner_uid)
    if not token:
        token = await get_fresh_token(user_id=uid)
    if not token:
        raise HTTPException(401, "No Strava connection available for this activity")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"include_all_efforts": "false"},
        )
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid — reconnect Strava")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "Strava API error")
        data = resp.json()

    return {"kudos_count": data.get("kudos_count", 0)}


@router.get("/activities/{activity_id}/strava-kudos-list")
async def get_strava_kudos_list(activity_id: int, request: Request):
    """Return the list of athletes who gave kudos."""
    import httpx
    from app.routers.strava import get_fresh_token

    uid = get_session_user_id(request)
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        return {"athletes": []}

    owner_uid = act.get("user_id") or uid
    token = await get_fresh_token(user_id=owner_uid)
    if not token:
        token = await get_fresh_token(user_id=uid)
    if not token:
        raise HTTPException(401, "No Strava connection available for this activity")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}/kudos",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200, "page": 1},
        )
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid — reconnect Strava")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "Strava API error")
        raw = resp.json()

    athletes = [{"firstname": a.get("firstname", ""), "lastname": a.get("lastname", "")} for a in raw]
    return {"athletes": athletes}


@router.get("/activities/{activity_id}/strava-comments")
async def get_strava_comments(activity_id: int, request: Request):
    """Return the comment count for a Strava-linked activity."""
    import httpx
    from app.routers.strava import get_fresh_token

    uid = get_session_user_id(request)
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        return {"comment_count": 0}

    owner_uid = act.get("user_id") or uid
    token = await get_fresh_token(user_id=owner_uid)
    if not token:
        token = await get_fresh_token(user_id=uid)
    if not token:
        raise HTTPException(401, "No Strava connection available for this activity")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200, "page": 1},
        )
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid — reconnect Strava")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "Strava API error")
        raw = resp.json()

    return {"comment_count": len(raw)}


@router.get("/activities/{activity_id}/strava-comments-list")
async def get_strava_comments_list(activity_id: int, request: Request):
    """Return the list of comments on a Strava-linked activity."""
    import httpx
    from app.routers.strava import get_fresh_token

    uid = get_session_user_id(request)
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        return {"comments": []}

    owner_uid = act.get("user_id") or uid
    token = await get_fresh_token(user_id=owner_uid)
    if not token:
        token = await get_fresh_token(user_id=uid)
    if not token:
        raise HTTPException(401, "No Strava connection available for this activity")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200, "page": 1},
        )
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid — reconnect Strava")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "Strava API error")
        raw = resp.json()

    comments = []
    for c in raw:
        athlete = c.get("athlete") or {}
        firstname = athlete.get("firstname", "")
        lastname  = athlete.get("lastname", "")
        name = f"{firstname} {lastname}".strip() or "Unknown"
        comments.append({"athlete_name": name, "text": c.get("text", "")})

    return {"comments": comments}


# ── PERSONAL RECORDS ─────────────────────────────────────────────────────────

def _find_segment_indices(pts, start_lat, start_lon, end_lat, end_lon, ref_length_km, tol_km):
    """
    Find (start_index, end_index) of a segment pass in pts, or None if not found.
    Single canonical algorithm used by both segment compare and PR features.
    Uses degree-squared proximity for start, haversine accumulation for end.
    pts must be full track points (not summary) for accurate timing.
    """
    import math
    if len(pts) < 2:
        return None
    tol_km = max(tol_km, 0.05)
    cos_l = math.cos(math.radians(start_lat))
    tol_deg_sq = (tol_km / 111.0) ** 2

    # Find start: closest point within tolerance
    best_i, best_dsq = -1, float("inf")
    for i, p in enumerate(pts):
        d2 = (p["lat"] - start_lat) ** 2 + ((p["lon"] - start_lon) * cos_l) ** 2
        if d2 < best_dsq:
            best_dsq, best_i = d2, i
    if best_i < 0 or best_dsq > tol_deg_sq:
        return None
    si = best_i

    # Find end: use cum_dist_m if available (summary points), else haversine accumulation (full pts)
    R = 6371.0
    def _hav(lat1, lon1, lat2, lon2):
        dlat = math.radians(lat2 - lat1); dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(min(1.0, math.sqrt(a)))

    window_pts = []
    if "cum_dist_m" in pts[si]:
        si_cum = pts[si]["cum_dist_m"]
        target_min_m = si_cum + (ref_length_km - tol_km) * 1000.0
        target_max_m = si_cum + (ref_length_km + tol_km) * 1000.0
        for i in range(si + 1, len(pts)):
            c = pts[i]["cum_dist_m"]
            if c < target_min_m:
                continue
            if c > target_max_m:
                break
            d_end = _hav(pts[i]["lat"], pts[i]["lon"], end_lat, end_lon)
            window_pts.append((i, d_end))
    else:
        accum = 0.0
        for i in range(si, len(pts) - 1):
            accum += _hav(pts[i]["lat"], pts[i]["lon"], pts[i + 1]["lat"], pts[i + 1]["lon"])
            if accum >= ref_length_km - tol_km:
                d_end = _hav(pts[i + 1]["lat"], pts[i + 1]["lon"], end_lat, end_lon)
                window_pts.append((i + 1, d_end))
            if accum > ref_length_km + tol_km:
                break

    if not window_pts:
        return None
    window_pts.sort(key=lambda x: x[1])
    ei, best_d = window_pts[0]
    if best_d > tol_km or ei <= si:
        return None
    return (si, ei)


def _activity_type_group(act_type: str):
    """Map Strava activity type to a PR comparison group. Returns None for excluded types."""
    if not act_type:
        return None
    if act_type in ("EBikeRide", "VirtualRide"):
        return None
    if act_type in ("Ride", "GravelRide"):
        return "Ride"
    return act_type


def _get_pr_windows():
    """PR time windows: (label, years_back). years_back=None means all-time."""
    return [("All-Time", None), ("5-Year", 5), ("3-Year", 3), ("1-Year", 1)]



def _valid_gps(p):
    lat, lon = p.get("lat"), p.get("lon")
    return (lat is not None and lon is not None
            and lat != 999.0 and lon != 999.0
            and -90 <= lat <= 90 and -180 <= lon <= 180
            and not (lat == 0.0 and lon == 0.0))


def _match_segment_elapsed(db, act_id, start_lat, start_lon, end_lat, end_lon, ref_length_km, tol_km):
    """
    Screen with summary points (fast O(300)), refine on full track points (accurate timing).
    Returns elapsed_s or None. Same two-phase approach as segment_compare.
    """
    # Phase 1: fast screening with summary (avoid loading full pts for non-matches)
    sum_pts = db.get_points_summary(act_id)
    if sum_pts:
        if not _find_segment_indices(sum_pts, start_lat, start_lon, end_lat, end_lon, ref_length_km, tol_km):
            return None

    # Phase 2: accurate timing with full track points
    full_pts = [p for p in db.get_track_points(act_id) if _valid_gps(p)]
    if len(full_pts) < 2:
        return None
    idx = _find_segment_indices(full_pts, start_lat, start_lon, end_lat, end_lon, ref_length_km, tol_km)
    if idx is None:
        return None
    elapsed = full_pts[idx[1]]["t"] - full_pts[idx[0]]["t"]
    return elapsed if elapsed > 0 else None


@router.post("/activities/check-prs")
async def check_prs(body: dict, request: Request):
    """Check if newly synced activities set PRs on saved segments."""
    import json as json_mod, time as _time
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    db = db_getter()
    activity_ids = body.get("activity_ids", [])
    if not activity_ids:
        return {"prs": []}

    results = []
    now = _time.time()

    for target_id in activity_ids:
        act = db.get_activity(target_id)
        if not act:
            continue

        # Ensure GPS points exist — fetch from Strava if needed
        if not (act.get("points_saved") and act.get("points_count", 0) > 0):
            strava_id = act.get("strava_activity_id")
            if strava_id:
                try:
                    import httpx
                    from app.strava_importer import build_points_rows
                    from app.routers.strava import load_tokens, tokens_are_fresh, refresh_tokens
                    tokens = load_tokens(user_id=uid)
                    if tokens.get("refresh_token"):
                        if not tokens_are_fresh(tokens):
                            tokens = await refresh_tokens(tokens, user_id=uid)
                        token = tokens["access_token"]
                        stream_types = "latlng,time,altitude,heartrate,velocity_smooth,distance"
                        async with httpx.AsyncClient(timeout=30) as client:
                            resp = await client.get(
                                f"https://www.strava.com/api/v3/activities/{strava_id}/streams",
                                headers={"Authorization": f"Bearer {token}"},
                                params={"keys": stream_types, "key_by_type": "true"},
                            )
                        if resp.status_code == 200:
                            rows = build_points_rows(resp.json(), target_id)
                            if rows:
                                db.store_points(target_id, rows)
                                act = db.get_activity(target_id)  # refresh
                except Exception:
                    pass

        target_type = act.get("activity_type", "")
        target_group = _activity_type_group(target_type)
        if target_group is None:
            continue

        # Get target's full points (newly synced — may not have summary yet)
        target_pts = [p for p in db.get_track_points(target_id) if _valid_gps(p)]
        if len(target_pts) < 2:
            continue

        segs = db.get_segments_for_activity(target_id)
        if not segs:
            continue

        for seg in segs:
            seg_pts_json = json_mod.loads(seg["points_json"]) if seg.get("points_json") else []
            if len(seg_pts_json) < 2:
                continue
            length_km = seg.get("length_km", 0)
            if length_km <= 0:
                continue
            tol_km = length_km * 0.10

            start_lat, start_lon = seg_pts_json[0][0], seg_pts_json[0][1]
            end_lat, end_lon = seg_pts_json[-1][0], seg_pts_json[-1][1]

            _idx = _find_segment_indices(target_pts, start_lat, start_lon, end_lat, end_lon, length_km, tol_km)
            if not _idx:
                continue
            target_elapsed = target_pts[_idx[1]]["t"] - target_pts[_idx[0]]["t"]
            if target_elapsed <= 0:
                continue

            # Collect elapsed times from all compatible user activities
            all_times = [(target_elapsed, act.get("start_time") or now, target_id)]

            candidates = db._con.execute("""
                SELECT id, COALESCE(creation_time_override_s, creation_time_s) AS ts,
                       local_sport_type, attributes_json
                FROM activities
                WHERE user_id = ? AND id != ?
                  AND points_saved = 1 AND points_count > 0
                ORDER BY ts DESC
            """, (uid, target_id)).fetchall()

            for row in candidates:
                cand_id  = row[0]
                cand_ts  = row[1]
                cand_type = row[2]
                if not cand_type:
                    try:
                        attrs_raw = json_mod.loads(row[3]) if row[3] else []
                        attrs_d   = dict(zip(attrs_raw[::2], attrs_raw[1::2]))
                        cand_type = attrs_d.get("activity", "")
                    except Exception:
                        cand_type = ""
                if _activity_type_group(cand_type) != target_group:
                    continue

                elapsed = _match_segment_elapsed(db, cand_id, start_lat, start_lon, end_lat, end_lon, length_km, tol_km)
                if elapsed is not None:
                    all_times.append((elapsed, cand_ts, cand_id))

            # Check each PR window
            pr_cats = []
            for label, years_back in _get_pr_windows():
                cutoff = now - years_back * 365.25 * 86400 if years_back else None
                window = [(e, ts, aid) for e, ts, aid in all_times if cutoff is None or ts >= cutoff]
                if len(window) < 1:
                    continue
                fastest = min(window, key=lambda x: x[0])
                if fastest[2] == target_id:
                    pr_cats.append(label)

            if pr_cats:
                results.append({
                    "segment_name": seg["name"],
                    "segment_id":   seg["id"],
                    "activity_id":  target_id,
                    "elapsed_s":    target_elapsed,
                    "categories":   pr_cats,
                })

    return {"prs": results}


@router.get("/segments/{segment_id}/best-efforts")
async def segment_best_efforts(segment_id: int, request: Request,
                               activity_id: int = Query(...)):
    """Return PR holders per time window for a saved segment."""
    import json as json_mod, time as _time, datetime
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    db = db_getter()
    seg = db.get_segment(segment_id)
    if not seg:
        raise HTTPException(404, "Segment not found")

    ref_act = db.get_activity(activity_id)
    if not ref_act:
        raise HTTPException(404, "Reference activity not found")
    ref_group = _activity_type_group(ref_act.get("activity_type", ""))
    if ref_group is None:
        return {"efforts": [], "segment_name": seg["name"]}

    seg_pts_json = json_mod.loads(seg["points_json"]) if seg.get("points_json") else []
    if len(seg_pts_json) < 2:
        raise HTTPException(400, "Segment has no GPS points")

    length_km = seg.get("length_km", 0)
    if length_km <= 0:
        raise HTTPException(400, "Invalid segment length")
    tol_km = length_km * 0.10
    start_lat, start_lon = seg_pts_json[0][0], seg_pts_json[0][1]
    end_lat, end_lon     = seg_pts_json[-1][0], seg_pts_json[-1][1]

    candidates = db._con.execute("""
        SELECT id, name, COALESCE(creation_time_override_s, creation_time_s) AS ts,
               local_sport_type, attributes_json, local_gear_name
        FROM activities
        WHERE user_id = ?
          AND points_saved = 1 AND points_count > 0
        ORDER BY ts DESC
    """, (uid,)).fetchall()

    all_times = []  # (elapsed_s, ts, act_id, act_name, date_str, equipment)
    for row in candidates:
        cand_id   = row[0]
        cand_name = row[1] or "(unnamed)"
        cand_ts   = row[2]
        cand_type = row[3]
        attrs_d   = {}
        try:
            attrs_raw = json_mod.loads(row[4]) if row[4] else []
            attrs_d   = dict(zip(attrs_raw[::2], attrs_raw[1::2]))
        except Exception:
            pass
        if not cand_type:
            cand_type = attrs_d.get("activity", "")
        if _activity_type_group(cand_type) != ref_group:
            continue
        # local_gear_name overrides attributes_json equipment (mirrors build_activity logic)
        equipment = row[5] if row[5] is not None else attrs_d.get("equipment", "")

        elapsed = _match_segment_elapsed(db, cand_id, start_lat, start_lon, end_lat, end_lon, length_km, tol_km)
        if elapsed is not None:
            date_str = datetime.datetime.utcfromtimestamp(cand_ts).strftime("%Y-%m-%d") if cand_ts else ""
            all_times.append((elapsed, cand_ts, cand_id, cand_name, date_str, equipment))

    now    = _time.time()
    efforts = []
    for label, years_back in _get_pr_windows():
        cutoff  = now - years_back * 365.25 * 86400 if years_back else None
        window  = [(e, ts, aid, nm, dt, eq) for e, ts, aid, nm, dt, eq in all_times if cutoff is None or ts >= cutoff]
        if len(window) < 1:
            continue
        fastest = min(window, key=lambda x: x[0])
        efforts.append({
            "category":      label,
            "activity_id":   fastest[2],
            "activity_name": fastest[3],
            "date":          fastest[4],
            "equipment":     fastest[5],
            "elapsed_s":     fastest[0],
        })

    all_efforts_sorted = sorted(all_times, key=lambda x: x[0])
    all_efforts = [
        {"activity_id": e[2], "activity_name": e[3], "date": e[4], "equipment": e[5], "elapsed_s": e[0]}
        for e in all_efforts_sorted
    ]
    return {"efforts": efforts, "all_efforts": all_efforts, "segment_name": seg["name"]}


