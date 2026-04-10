"""routers/fitgpx.py — GPX export and GPX/FIT import"""

import io
import json
import math
import sqlite3
import time as _time
import uuid as uuid_mod
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, Optional

import zipfile

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.auth import get_session_user_id

router    = APIRouter()
db_getter: Callable = None

M_TO_MI    = 0.000621371
M_TO_FT    = 3.28084
FT_TO_M    = 0.3048
MPS_TO_MPH = 2.23694

_FIT_SPORT_MAP = {
    "cycling":    "Ride",
    "running":    "Run",
    "swimming":   "Swim",
    "walking":    "Walk",
    "hiking":     "Hike",
    "generic":    "Workout",
    "transition": "Workout",
    "fitness_equipment": "Workout",
    "training":   "Workout",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _build_attrs_json(name, activity_type, elapsed_s, moving_s,
                      dist_mi, climb_ft, descent_ft,
                      avg_hr, max_hr, avg_cad,
                      avg_speed_mph, max_speed_mph,
                      avg_power, max_power, calories) -> str:
    attrs: dict = {}
    if name:          attrs["name"]     = name
    if activity_type: attrs["activity"] = activity_type
    attrs["durationAsFloat"]       = elapsed_s or 0
    attrs["movingDurationAsFloat"] = moving_s or elapsed_s or 0
    attrs["distance"]              = round(dist_mi or 0, 4)
    if climb_ft:      attrs["totalClimb"]   = round(climb_ft, 1)
    if descent_ft:    attrs["totalDescent"] = round(descent_ft, 1)
    if avg_hr:        attrs["avgHeartRate"] = round(avg_hr, 1)
    if max_hr:        attrs["maxHeartRate"] = round(max_hr, 1)
    if avg_cad:       attrs["avgCadence"]   = round(avg_cad, 1)
    if avg_speed_mph:
        attrs["avgMovingSpeed"] = round(avg_speed_mph, 2)
        if avg_speed_mph > 0:
            mins = 60.0 / avg_speed_mph
            m = int(mins)
            s = int((mins - m) * 60)
            attrs["avgMovingPace"] = f"{m}:{s:02d}"
    if max_speed_mph: attrs["maxSpeed"] = round(max_speed_mph, 2)
    if avg_power:     attrs["avgPower"] = round(avg_power, 1)
    if max_power:     attrs["maxPower"] = round(max_power, 1)
    if calories:      attrs["calories"] = round(calories)
    flat = []
    for k, v in attrs.items():
        flat.append(k)
        flat.append(v)
    return json.dumps(flat)


def _check_duplicate(db, user_id: int, start_unix: int, dist_mi: float) -> Optional[int]:
    con = sqlite3.connect(db.path, timeout=10)
    try:
        row = con.execute(
            """SELECT id FROM activities
               WHERE user_id = ?
                 AND ABS(COALESCE(creation_time_override_s, creation_time_s) - ?) <= 30
                 AND ABS(distance_mi - ?) < 0.05
               LIMIT 1""",
            (user_id, start_unix, dist_mi),
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _insert_activity(db, user_id, name, activity_type, start_unix, dist_mi,
                     elapsed_s, moving_s, climb_ft, descent_ft,
                     avg_hr, max_hr, avg_cad, avg_speed_mph, max_speed_mph,
                     avg_power, max_power, calories,
                     start_lat, start_lon,
                     points_rows_no_tid) -> int:
    """
    Insert an imported activity + points into the DB.
    points_rows_no_tid: list of 12-tuples (no track_id column).
    Returns the new activity row id.
    """
    attrs_json = _build_attrs_json(
        name, activity_type, elapsed_s, moving_s, dist_mi,
        climb_ft, descent_ft, avg_hr, max_hr, avg_cad,
        avg_speed_mph, max_speed_mph, avg_power, max_power, calories,
    )
    act_uuid  = str(uuid_mod.uuid4()).upper()
    has_dist  = 1 if dist_mi else 0

    con = sqlite3.connect(db.path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        cur = con.execute("""
            INSERT INTO activities (
                uuid, name, creation_time_s, creation_time_override_s,
                distance_mi, attributes_json,
                strava_activity_id, user_id,
                src_distance, src_max_speed,
                src_avg_heartrate, src_max_heartrate,
                src_avg_temperature,
                src_max_elevation, src_min_elevation,
                src_avg_power, src_max_power,
                src_avg_cadence, src_total_climb,
                src_kilojoules, src_elapsed_time_s, src_moving_time_s,
                time_zone, seconds_from_gmt_at_sync,
                flags, device_id, firmware_version,
                has_distance_data, moving_speed_only,
                points_saved, points_count,
                weight_lb, altitude_smooth_factor, equipment_weight_lb, device_total_time_s,
                local_media_items_json, photo_urls_json,
                start_lat, start_lon,
                map_min_lat, map_max_lat, map_min_lon, map_max_lon,
                strava_visibility
            ) VALUES (
                ?,?,?,NULL,
                ?,?,
                NULL,?,
                ?,NULL,
                ?,?,
                NULL,
                NULL,NULL,
                ?,?,
                ?,?,
                NULL,?,?,
                NULL,0,
                0,0,0,
                ?,0,
                0,0,0,0,0,0,
                NULL,NULL,
                ?,?,
                NULL,NULL,NULL,NULL,
                NULL
            )
        """, (
            act_uuid, name, start_unix,
            dist_mi, attrs_json,
            user_id,
            dist_mi,               # src_distance
            avg_hr, max_hr,
            avg_power, max_power,
            avg_cad, climb_ft or 0,
            elapsed_s, moving_s or elapsed_s,
            has_dist,
            start_lat, start_lon,
        ))
        activity_id = cur.lastrowid

        if points_rows_no_tid:
            full_rows = [(activity_id,) + tuple(row) for row in points_rows_no_tid]
            con.executemany("""
                INSERT INTO points (
                    track_id, wall_clock_delta_s, active_time_delta_s,
                    latitude_e7, longitude_e7, orig_altitude_cm,
                    heartrate_bpm, cadence_rpm, temperature_c10,
                    speed_mps, power_w, orig_distance_m, flags
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, full_rows)
            con.execute(
                "UPDATE activities SET points_saved=1, points_count=? WHERE id=?",
                (len(full_rows), activity_id),
            )

        con.execute("UPDATE meta SET totalTracks = (SELECT COUNT(*) FROM activities)")
        con.commit()
        return activity_id
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── GPX EXPORT ────────────────────────────────────────────────────────────────

def _fmt_utc(unix_ts: int) -> str:
    """Format a Unix timestamp as ISO 8601 UTC without datetime overhead."""
    t = _time.gmtime(unix_ts)
    return f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d}T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"


def _gpx_chunks(act: dict, pts: list):
    """Generator yielding UTF-8 encoded GPX chunks — avoids building one giant string."""
    def _esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    start_ts = int(act.get("start_time") or 0)
    name  = _esc(act.get("name") or "Activity")
    atype = act.get("activity_type") or ""

    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Ascent Web"\n'
        '  xmlns="http://www.topografix.com/GPX/1/1"\n'
        '  xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"\n'
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '  xsi:schemaLocation="http://www.topografix.com/GPX/1/1'
        ' http://www.topografix.com/GPX/1/1/gpx.xsd">\n'
        f'  <trk>\n'
        f'    <name>{name}</name>\n'
    )
    if atype:
        header += f'    <type>{_esc(atype)}</type>\n'
    header += '    <trkseg>\n'
    yield header.encode("utf-8")

    BAD = 999.0
    buf = []
    FLUSH_EVERY = 500  # yield a chunk every N points

    for i, p in enumerate(pts):
        lat, lon = p["lat"], p["lon"]
        if lat == BAD or lon == BAD:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if lat == 0.0 and lon == 0.0:
            continue

        alt_m  = p["alt_m"]
        t_abs  = start_ts + int(p["t"] or 0)
        dt_str = _fmt_utc(t_abs)
        hr     = int(p["hr"])    if p["hr"]    else None
        cad    = int(p["cad"])   if p["cad"]   else None
        pwr    = int(p["power"]) if p["power"] else None
        tf     = p["temp_f"]
        temp_c = round((tf - 32) * 5 / 9, 1) if tf else None

        buf.append(f'      <trkpt lat="{lat}" lon="{lon}">\n'
                   f'        <ele>{alt_m}</ele>\n'
                   f'        <time>{dt_str}</time>\n')

        if hr is not None or cad is not None or pwr is not None or temp_c is not None:
            buf.append('        <extensions>\n          <gpxtpx:TrackPointExtension>\n')
            if temp_c is not None:
                buf.append(f'            <gpxtpx:atemp>{temp_c}</gpxtpx:atemp>\n')
            if hr is not None:
                buf.append(f'            <gpxtpx:hr>{hr}</gpxtpx:hr>\n')
            if cad is not None:
                buf.append(f'            <gpxtpx:cad>{cad}</gpxtpx:cad>\n')
            if pwr is not None:
                buf.append(f'            <gpxtpx:power>{pwr}</gpxtpx:power>\n')
            buf.append('          </gpxtpx:TrackPointExtension>\n        </extensions>\n')

        buf.append('      </trkpt>\n')

        if len(buf) >= FLUSH_EVERY * 10:  # ~10 lines per point × 500 points
            yield "".join(buf).encode("utf-8")
            buf.clear()

    if buf:
        yield "".join(buf).encode("utf-8")

    yield b'    </trkseg>\n  </trk>\n</gpx>\n'


@router.get("/activities/{activity_id}/export/gpx")
async def export_gpx(activity_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    pts  = db.get_track_points(activity_id)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (act.get("name") or "activity"))
    filename = f"{safe.strip() or 'activity'}.gpx"

    body = b"".join(_gpx_chunks(act, pts))
    return Response(
        content=body,
        media_type="application/gpx+xml",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(body)),
        },
    )


# ── GPX BATCH EXPORT (ZIP) ───────────────────────────────────────────────────

@router.post("/export/gpx/batch")
async def export_gpx_batch(request: Request, body: dict = Body(...)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    ids = body.get("ids") or []
    if not ids:
        raise HTTPException(400, "No activity IDs provided")
    if len(ids) > 200:
        raise HTTPException(400, "Too many activities (max 200)")

    db  = db_getter()
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen_names: dict[str, int] = {}
        for activity_id in ids:
            act = db.get_activity(int(activity_id))
            if not act:
                continue
            pts  = db.get_track_points(int(activity_id))
            gpx  = b"".join(_gpx_chunks(act, pts))
            base = "".join(c if c.isalnum() or c in "-_ " else "_"
                           for c in (act.get("name") or "activity")).strip() or "activity"
            # Deduplicate filenames within the ZIP
            count = seen_names.get(base, 0)
            seen_names[base] = count + 1
            fname = f"{base}.gpx" if count == 0 else f"{base} ({count}).gpx"
            zf.writestr(fname, gpx)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="activities.zip"'},
    )


# ── GPX IMPORT ────────────────────────────────────────────────────────────────

def _tag(el) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _child(parent, local_name):
    """Find first child with matching local tag name (ignores namespace)."""
    for c in parent:
        if _tag(c) == local_name:
            return c
    return None


def _parse_gpx_timestamp(text: str) -> Optional[int]:
    if not text:
        return None
    try:
        t = text.strip().replace("Z", "+00:00")
        return int(datetime.fromisoformat(t).timestamp())
    except Exception:
        return None


@router.post("/import/gpx")
async def import_gpx(request: Request, file: UploadFile = File(...)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    data = await file.read()
    try:
        root = ET.fromstring(data)
    except Exception as e:
        raise HTTPException(400, f"Invalid XML: {e}")

    # Find <trk>
    trk = _child(root, "trk")
    if trk is None:
        raise HTTPException(400, "No <trk> element found")

    name_el = _child(trk, "name")
    name    = (name_el.text or "").strip() if name_el is not None else ""
    name    = name or (file.filename or "Imported Activity")

    type_el       = _child(trk, "type")
    activity_type = (type_el.text or "").strip() if type_el is not None else "Workout"
    if not activity_type:
        activity_type = "Workout"

    # Collect all trkpt elements across all trkseg
    trkpts = []
    for child in trk:
        if _tag(child) == "trkseg":
            for pt in child:
                if _tag(pt) == "trkpt":
                    trkpts.append(pt)

    if not trkpts:
        raise HTTPException(400, "No track points found")

    # Parse track points
    start_unix = None
    raw_pts = []
    for pt in trkpts:
        try:
            lat = float(pt.get("lat", 0) or 0)
            lon = float(pt.get("lon", 0) or 0)
        except (TypeError, ValueError):
            continue

        ele_el = _child(pt, "ele")
        alt_m  = float(ele_el.text) if ele_el is not None and ele_el.text else 0.0

        time_el = _child(pt, "time")
        ts = _parse_gpx_timestamp(time_el.text if time_el is not None else None)
        if ts is not None and start_unix is None:
            start_unix = ts

        hr = cad = pwr = temp_c = None
        ext_el = _child(pt, "extensions")
        if ext_el is not None:
            for ext in ext_el:
                tag = _tag(ext)
                if tag == "TrackPointExtension":
                    for sub in ext:
                        st = _tag(sub)
                        val = (sub.text or "").strip()
                        if st == "hr" and val:
                            try: hr = int(float(val))
                            except: pass
                        elif st == "cad" and val:
                            try: cad = int(float(val))
                            except: pass
                        elif st == "power" and val:
                            try: pwr = int(float(val))
                            except: pass
                        elif st == "atemp" and val:
                            try: temp_c = float(val)
                            except: pass
                # Some devices put hr/cad/power directly under extensions
                elif tag == "hr" and (ext.text or "").strip():
                    try: hr = int(float(ext.text))
                    except: pass
                elif tag in ("cad", "cadence") and (ext.text or "").strip():
                    try: cad = int(float(ext.text))
                    except: pass
                elif tag == "power" and (ext.text or "").strip():
                    try: pwr = int(float(ext.text))
                    except: pass

        raw_pts.append({"lat": lat, "lon": lon, "alt_m": alt_m, "ts": ts,
                         "hr": hr, "cad": cad, "pwr": pwr, "temp_c": temp_c})

    if not raw_pts:
        raise HTTPException(400, "Could not parse any track points")

    if start_unix is None:
        start_unix = int(datetime.now(timezone.utc).timestamp())

    return _finish_import(request, uid, name, activity_type, start_unix, raw_pts)


# ── FIT IMPORT ────────────────────────────────────────────────────────────────

@router.post("/import/fit")
async def import_fit(request: Request, file: UploadFile = File(...)):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    try:
        import fitparse
    except ImportError:
        raise HTTPException(500, "fitparse library not installed")

    data = await file.read()
    try:
        ff = fitparse.FitFile(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(400, f"Invalid FIT file: {e}")

    name          = file.filename or "Imported Activity"
    activity_type = "Workout"
    start_unix    = None

    # Pull summary from session message
    for msg in ff.get_messages("session"):
        for field in msg:
            if field.name == "sport" and field.value:
                sport = str(field.value).lower()
                activity_type = _FIT_SPORT_MAP.get(sport, sport.capitalize())
            elif field.name == "start_time" and field.value:
                try:
                    start_unix = int(field.value.timestamp())
                except Exception:
                    pass

    # Activity name from activity message
    for msg in ff.get_messages("activity"):
        for field in msg:
            if field.name == "local_timestamp" and field.value:
                pass  # not a name field, skip
    # FIT files don't typically store a user-facing name; use filename minus extension
    if "." in name:
        name = name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    if not name:
        name = "Imported Activity"

    SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)

    raw_pts = []
    for msg in ff.get_messages("record"):
        d = {f.name: f.value for f in msg if f.value is not None}

        lat_sc = d.get("position_lat")
        lon_sc = d.get("position_long")
        if lat_sc is None or lon_sc is None:
            continue

        lat = float(lat_sc) * SEMICIRCLE_TO_DEG
        lon = float(lon_sc) * SEMICIRCLE_TO_DEG
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        ts_val = d.get("timestamp")
        ts = None
        if ts_val is not None:
            try:
                ts = int(ts_val.timestamp())
            except Exception:
                pass

        if ts is not None and start_unix is None:
            start_unix = ts

        # altitude in metres (fitparse already applies the offset/scale)
        alt_m = float(d["altitude"]) if "altitude" in d else 0.0
        hr    = int(d["heart_rate"]) if "heart_rate" in d else None
        cad   = int(d["cadence"])    if "cadence"    in d else None
        pwr   = int(d["power"])      if "power"      in d else None
        temp_c = float(d["temperature"]) if "temperature" in d else None
        # FIT distance is cumulative metres — we'll recompute from coords for consistency
        raw_pts.append({"lat": lat, "lon": lon, "alt_m": alt_m, "ts": ts,
                         "hr": hr, "cad": cad, "pwr": pwr, "temp_c": temp_c,
                         "fit_dist_m": float(d["distance"]) if "distance" in d else None,
                         "fit_speed_mps": float(d["speed"]) if "speed" in d else None})

    if not raw_pts:
        raise HTTPException(400, "No GPS track points found in FIT file")

    if start_unix is None:
        start_unix = int(datetime.now(timezone.utc).timestamp())

    return _finish_import(request, uid, name, activity_type, start_unix, raw_pts,
                          use_fit_fields=True)


# ── SHARED COMPUTE + INSERT ───────────────────────────────────────────────────

_EXP_ALPHA      = 0.18   # exponential smoothing for altitude (climb calculation only)
_MOVING_MPS     = 0.5    # m/s threshold for "moving" (~1.1 mph)


def _normalized_power(pwrs: list) -> float:
    """30-second rolling average raised to 4th power, averaged, then 4th-root.
    Falls back to simple mean when fewer than 30 samples."""
    if not pwrs:
        return 0.0
    if len(pwrs) < 30:
        return sum(pwrs) / len(pwrs)
    window = 30
    rolling = []
    for i in range(len(pwrs) - window + 1):
        rolling.append(sum(pwrs[i:i + window]) / window)
    return (sum(v ** 4 for v in rolling) / len(rolling)) ** 0.25


def _finish_import(request: Request, uid: int, name: str, activity_type: str,
                   start_unix: int, raw_pts: list,
                   use_fit_fields: bool = False) -> JSONResponse:
    """Compute stats, build points rows, insert, return JSON."""
    cum_dist_mi    = 0.0
    total_climb_ft = 0.0
    total_descent_ft = 0.0
    moving_s       = 0        # seconds actually moving
    smooth_alt     = None     # exponentially smoothed altitude for climb
    prev_smooth_alt = None
    prev_lat = prev_lon = prev_alt_m = prev_ts = None
    speeds_mps: list = []
    hrs: list  = []
    cads: list = []
    pwrs: list = []

    points_rows = []  # 12-tuples without track_id

    for p in raw_pts:
        lat, lon, alt_m, ts = p["lat"], p["lon"], p["alt_m"], p["ts"]

        # Distance
        dist_delta_m = 0.0
        if use_fit_fields and p.get("fit_dist_m") is not None:
            cum_dist_mi = p["fit_dist_m"] * M_TO_MI
        elif prev_lat is not None:
            dist_delta_m = _haversine_m(prev_lat, prev_lon, lat, lon)
            cum_dist_mi += dist_delta_m * M_TO_MI

        # Speed
        speed_mps = 0.0
        if use_fit_fields and p.get("fit_speed_mps") is not None:
            speed_mps = p["fit_speed_mps"]
        elif prev_ts and ts and ts > prev_ts and dist_delta_m > 0:
            speed_mps = dist_delta_m / (ts - prev_ts)

        if speed_mps > 0:
            speeds_mps.append(speed_mps)

        # Moving time — accumulate intervals where speed exceeds threshold
        if prev_ts and ts and ts > prev_ts and speed_mps >= _MOVING_MPS:
            moving_s += ts - prev_ts

        # Elevation gain/loss using exponentially smoothed altitude to suppress GPS noise
        smooth_alt = (_EXP_ALPHA * alt_m + (1 - _EXP_ALPHA) * smooth_alt
                      if smooth_alt is not None else alt_m)
        if prev_smooth_alt is not None:
            delta = smooth_alt - prev_smooth_alt
            if delta > 0:
                total_climb_ft += delta * M_TO_FT
            else:
                total_descent_ft += (-delta) * M_TO_FT
        prev_smooth_alt = smooth_alt

        # Biometric collections
        if p.get("hr"):  hrs.append(p["hr"])
        if p.get("cad"): cads.append(p["cad"])
        if p.get("pwr"): pwrs.append(p["pwr"])

        wall_delta = int(ts - start_unix) if ts else 0
        alt_ft  = alt_m * M_TO_FT   # orig_altitude_cm stores feet (CM_TO_FT = 1.0)
        temp_f10 = int(round((p["temp_c"] * 9 / 5 + 32) * 10)) if p.get("temp_c") is not None else None

        points_rows.append((
            wall_delta,        # wall_clock_delta_s
            wall_delta,        # active_time_delta_s
            lat,               # latitude_e7
            lon,               # longitude_e7
            alt_ft,            # orig_altitude_cm  (stores feet)
            p.get("hr"),       # heartrate_bpm
            p.get("cad"),      # cadence_rpm
            temp_f10,          # temperature_c10
            speed_mps,         # speed_mps
            p.get("pwr"),      # power_w
            cum_dist_mi,       # orig_distance_m  (stores miles despite name)
            0,                 # flags
        ))

        prev_lat, prev_lon, prev_alt_m, prev_ts = lat, lon, alt_m, ts

    elapsed_s = int(raw_pts[-1]["ts"] - start_unix) if raw_pts[-1].get("ts") else 0
    if moving_s == 0:
        moving_s = elapsed_s   # fallback if no speed data
    dist_mi = cum_dist_mi

    avg_hr  = round(sum(hrs)  / len(hrs),  1) if hrs  else None
    max_hr  = max(hrs)  if hrs  else None
    avg_cad = round(sum(cads) / len(cads), 1) if cads else None
    # Moving speed = distance / moving time (shown as "Mov Spd")
    avg_moving_speed_mph = (dist_mi / (moving_s / 3600)) if moving_s > 0 else 0
    max_speed_mph = max(speeds_mps) * MPS_TO_MPH if speeds_mps else 0
    # Simple mean — NP (158W) overshoots Strava's average_watts (134W) further than simple mean (143W)
    avg_power = round(sum(pwrs) / len(pwrs), 1) if pwrs else None
    max_power = max(pwrs) if pwrs else None

    db     = db_getter()
    dup_id = _check_duplicate(db, uid, start_unix, dist_mi)
    if dup_id:
        return JSONResponse({"error": "duplicate", "existing_id": dup_id,
                             "message": "An activity with the same start time and distance already exists."},
                            status_code=409)

    start_lat = raw_pts[0]["lat"] if raw_pts else None
    start_lon = raw_pts[0]["lon"] if raw_pts else None

    activity_id = _insert_activity(
        db, uid, name, activity_type, start_unix, dist_mi,
        elapsed_s, moving_s,
        total_climb_ft, total_descent_ft,
        avg_hr, max_hr, avg_cad,
        avg_moving_speed_mph, max_speed_mph,
        avg_power, max_power, None,
        start_lat, start_lon,
        points_rows,
    )

    return JSONResponse({"id": activity_id, "name": name,
                         "distance_mi": round(dist_mi, 2)}, status_code=201)
