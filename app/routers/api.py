"""routers/api.py — JSON API endpoints consumed by the frontend JS."""

import os, json
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
async def weekly_stats(request: Request, year: Optional[int] = Query(None)):
    uid = get_session_user_id(request)
    return db_getter().get_weekly_totals(year=year, user_id=uid)

@router.get("/stats/yearly")
async def yearly_stats(request: Request, year: Optional[int] = Query(None)):
    uid = get_session_user_id(request)
    return db_getter().get_yearly_totals(year=year, user_id=uid)


@router.get("/activities/{activity_id}/geojson")
async def activity_geojson(activity_id: int):
    db = db_getter()
    if not db.get_activity(activity_id):
        raise HTTPException(404, "Activity not found")
    return db.get_track_points_geojson(activity_id)


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
    return {"id": user["id"], "username": user.get("username") or user.get("email", "?")}


@router.get("/users")
async def list_users(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    users = db_getter().list_users()
    return [{"id": u["id"], "username": u.get("username") or u.get("email", "?")} for u in users]


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
    from app.routers.strava import load_tokens, tokens_are_fresh, refresh_tokens
    from app.routers.photos import resolve_photos

    db = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        raise HTTPException(400, "Activity has no Strava ID")

    uid    = get_session_user_id(request)
    tokens = load_tokens(user_id=uid)
    if not tokens.get("refresh_token"):
        raise HTTPException(401, "Not connected to Strava")
    if not tokens_are_fresh(tokens):
        tokens = await refresh_tokens(tokens, user_id=uid)
    token = tokens["access_token"]

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
                local_media_items_json  = NULL
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
            activity_id,
        ))
        con.commit()
    finally:
        con.close()

    # Re-fetch photos and videos from Strava (local_media_items_json was cleared above)
    await resolve_photos(activity_id)

    # Return the refreshed activity in the same shape the frontend expects
    updated = db.get_activity(activity_id)
    return updated


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
    si = max(0, min(req.start_idx, n - 1))
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
        """, [req.activity_id] + user_params +
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

        if pts_saved and pts_count:
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
            accum_q = 0.0
            min_end_q = float("inf")
            for _q in range(si2, min(si2 + 5000, len(pts) - 1)):
                accum_q += haversine_km(pts[_q]["lat"], pts[_q]["lon"], pts[_q+1]["lat"], pts[_q+1]["lon"])
                d_q = haversine_km(pts[_q+1]["lat"], pts[_q+1]["lon"], end_lat, end_lon)
                if d_q < min_end_q:
                    min_end_q = d_q
                if accum_q > max_walk:
                    break
            if min_end_q > max_dev_km * 3:
                continue

            # b. Walk forward from si2 accumulating distance to find end index
            ei2 = find_segment_end(pts, si2, ref_length_km, tol_km, end_lat, end_lon)
            if ei2 < 0 or ei2 <= si2:
                continue

            # c. Length check
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
async def segments_for_activity(activity_id: int):
    db = db_getter()
    segs = db.get_segments_for_activity(activity_id)
    return {"segments": [{"id": s["id"], "name": s["name"],
                          "length_km": s["length_km"],
                          "start_idx": s["start_idx"],
                          "end_idx":   s["end_idx"],
                          "activity_id": s["activity_id"]}
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
            "points":      seg_points_sample(pts, si2, ei2),
        })

    if len(matches) < 1:
        raise HTTPException(404, "None of the selected activities contain this segment")

    return {"matches": matches, "segment_name": seg["name"]}




