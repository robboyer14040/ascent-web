"""routers/tours.py — Shared tour management with per-user stage completion tracking."""

import json
import math
import sqlite3
import time as _time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.auth import get_session_user_id
from app.routers.fitgpx import _haversine_m

router    = APIRouter()
db_getter: Callable = None

M_TO_MI    = 0.000621371
M_TO_FT    = 3.28084
_EXP_ALPHA = 0.18


# ── DB setup ──────────────────────────────────────────────────────────────────

def _ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS tours (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER NOT NULL,
            title      TEXT    NOT NULL,
            start_date TEXT    NOT NULL,
            end_date   TEXT    NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_stages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tour_id     INTEGER NOT NULL REFERENCES tours(id) ON DELETE CASCADE,
            stage_num   INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            distance_mi REAL    NOT NULL DEFAULT 0,
            climb_ft    REAL    NOT NULL DEFAULT 0,
            start_lat   REAL,
            start_lon   REAL
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tour_stages_tour ON tour_stages(tour_id)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_stage_points (
            stage_id INTEGER NOT NULL REFERENCES tour_stages(id) ON DELETE CASCADE,
            seq      INTEGER NOT NULL,
            lat      REAL    NOT NULL,
            lon      REAL    NOT NULL,
            alt_ft   REAL    NOT NULL DEFAULT 0,
            PRIMARY KEY (stage_id, seq)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tour_stage_pts ON tour_stage_points(stage_id)"
    )
    con.commit()


# ── GPX parsing for route (no activity creation) ──────────────────────────────

def _parse_gpx_route(data: bytes, filename: str) -> dict:
    """Parse a GPX file into a stage dict: name, distance_mi, climb_ft, points."""
    try:
        root = ET.fromstring(data)
    except Exception as e:
        raise ValueError(f"Invalid XML: {e}")

    def _tag(el):
        return el.tag.split("}")[-1] if "}" in el.tag else el.tag

    def _child(parent, local_name):
        for c in parent:
            if _tag(c) == local_name:
                return c
        return None

    trk = _child(root, "trk")
    if trk is None:
        raise ValueError("No <trk> element found")

    name_el = _child(trk, "name")
    name = (name_el.text or "").strip() if name_el is not None else ""
    if not name:
        base = filename.rsplit(".", 1)[0] if "." in filename else filename
        name = base.replace("_", " ").replace("-", " ").strip() or "Stage"

    raw: list[tuple[float, float, float]] = []
    for child in trk:
        if _tag(child) == "trkseg":
            for pt in child:
                if _tag(pt) != "trkpt":
                    continue
                try:
                    lat = float(pt.get("lat", 0) or 0)
                    lon = float(pt.get("lon", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                if lat == 0.0 and lon == 0.0:
                    continue
                ele_el = _child(pt, "ele")
                alt_m = float(ele_el.text) if ele_el is not None and ele_el.text else 0.0
                raw.append((lat, lon, alt_m))

    if not raw:
        raise ValueError("No valid track points found")

    # Compute distance and climb (exponentially smoothed altitude to suppress noise)
    cum_dist_mi  = 0.0
    climb_ft     = 0.0
    smooth_alt   = None
    prev_smooth  = None
    prev_lat = prev_lon = None

    for lat, lon, alt_m in raw:
        if prev_lat is not None:
            cum_dist_mi += _haversine_m(prev_lat, prev_lon, lat, lon) * M_TO_MI
        smooth_alt = (_EXP_ALPHA * alt_m + (1 - _EXP_ALPHA) * smooth_alt
                      if smooth_alt is not None else alt_m)
        if prev_smooth is not None and smooth_alt - prev_smooth > 0:
            climb_ft += (smooth_alt - prev_smooth) * M_TO_FT
        prev_smooth = smooth_alt
        prev_lat, prev_lon = lat, lon

    # Downsample to ≤800 points for storage
    step = max(1, len(raw) // 800)
    pts  = raw[::step]
    if raw[-1] not in pts:
        pts.append(raw[-1])

    return {
        "name":        name,
        "distance_mi": cum_dist_mi,
        "climb_ft":    climb_ft,
        "start_lat":   raw[0][0],
        "start_lon":   raw[0][1],
        # store alt in ft (matching DB convention)
        "points":      [(lat, lon, alt_m * M_TO_FT) for lat, lon, alt_m in pts],
    }


# ── Stage-to-activity matching ────────────────────────────────────────────────

def _match_stage_activity(
    con,
    user_id: int,
    start_date: str,
    end_date: str,
    start_lat: Optional[float],
    start_lon: Optional[float],
    stage_dist_mi: float,
) -> Optional[dict]:
    """Auto-pick the best activity for a stage: date-range + proximity + closest distance."""
    try:
        sd = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        ed = datetime.fromisoformat(end_date).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        start_ts = int(sd.timestamp())
        end_ts   = int(ed.timestamp())
    except Exception:
        return None

    dist_lo = stage_dist_mi * 0.65
    dist_hi = stage_dist_mi * 1.35

    base_where = (
        "user_id = ? "
        "AND COALESCE(creation_time_override_s, creation_time_s) BETWEEN ? AND ? "
        "AND distance_mi BETWEEN ? AND ?"
    )
    params: list = [user_id, start_ts, end_ts, dist_lo, dist_hi]

    # Proximity: ~2 km bounding box around stage start.
    # Include activities with no GPS (start_lat IS NULL) as fallback candidates.
    if start_lat is not None and start_lon is not None:
        lat_d = 2.0 / 111.0
        lon_d = 2.0 / (111.0 * math.cos(math.radians(start_lat)))
        prox_where = (
            " AND (start_lat IS NULL"
            "  OR (start_lat BETWEEN ? AND ? AND start_lon BETWEEN ? AND ?))"
        )
        base_where += prox_where
        params += [
            start_lat - lat_d, start_lat + lat_d,
            start_lon - lon_d, start_lon + lon_d,
        ]

    rows = con.execute(
        f"SELECT id, COALESCE(creation_time_override_s, creation_time_s), "
        f"distance_mi, attributes_json "
        f"FROM activities WHERE {base_where}",
        params,
    ).fetchall()

    if not rows:
        return None

    # Pick closest distance match
    best = min(rows, key=lambda r: abs((r[2] or 0) - stage_dist_mi))
    act_id, ts, dist_mi, attrs_json = best

    # Parse flat NSArray attributes_json
    attrs: dict = {}
    if attrs_json:
        try:
            flat = json.loads(attrs_json)
            if isinstance(flat, list):
                for i in range(0, len(flat) - 1, 2):
                    attrs[str(flat[i])] = flat[i + 1]
        except Exception:
            pass

    def _fa(key):
        v = attrs.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    return {
        "activity_id":          act_id,
        "date":                 date_str,
        "distance_mi":          dist_mi,
        "climb_ft":             _fa("totalClimb"),
        "duration_s":           _fa("durationAsFloat"),
        "moving_s":             _fa("movingDurationAsFloat"),
        "avg_moving_speed_mph": _fa("avgMovingSpeed"),
        "avg_hr":               _fa("avgHeartRate"),
        "max_hr":               _fa("maxHeartRate"),
        "avg_cadence":          _fa("avgCadence"),
        "avg_power":            _fa("avgPower"),
        "max_power":            _fa("maxPower"),
        "suffer_score":         _fa("sufferScore") or _fa("suffer_score"),
        "calories":             _fa("calories"),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/tours")
async def list_tours(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        rows = con.execute(
            "SELECT id, created_by, title, start_date, end_date, created_at "
            "FROM tours ORDER BY created_at DESC"
        ).fetchall()
        return JSONResponse([{
            "id":         r[0],
            "created_by": r[1],
            "title":      r[2],
            "start_date": r[3],
            "end_date":   r[4],
            "created_at": r[5],
            "is_mine":    r[1] == uid,
        } for r in rows])
    finally:
        con.close()


@router.post("/tours")
async def create_tour(
    request:    Request,
    title:      str               = Form(...),
    start_date: str               = Form(...),
    end_date:   str               = Form(...),
    files:      List[UploadFile]  = File(...),
):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    if not title.strip():
        raise HTTPException(400, "Title is required")
    try:
        from datetime import date as _date
        sd = _date.fromisoformat(start_date)
        ed = _date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format (use YYYY-MM-DD)")
    if ed < sd:
        raise HTTPException(400, "End date must be on or after start date")
    if not files:
        raise HTTPException(400, "At least one GPX file is required")

    # Parse all GPX files in upload order
    stages = []
    for i, f in enumerate(files):
        data = await f.read()
        try:
            stage = _parse_gpx_route(data, f.filename or f"Stage {i + 1}")
        except ValueError as e:
            raise HTTPException(400, f"File '{f.filename}': {e}")
        stage["stage_num"] = i + 1
        stages.append(stage)

    con = sqlite3.connect(db_getter().path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        _ensure_tables(con)

        cur = con.execute(
            "INSERT INTO tours (created_by, title, start_date, end_date, created_at) "
            "VALUES (?,?,?,?,?)",
            (uid, title.strip(), start_date, end_date, int(_time.time())),
        )
        tour_id = cur.lastrowid

        for s in stages:
            cur2 = con.execute(
                "INSERT INTO tour_stages "
                "(tour_id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon) "
                "VALUES (?,?,?,?,?,?,?)",
                (tour_id, s["stage_num"], s["name"],
                 s["distance_mi"], s["climb_ft"],
                 s["start_lat"], s["start_lon"]),
            )
            stage_id = cur2.lastrowid
            con.executemany(
                "INSERT INTO tour_stage_points (stage_id, seq, lat, lon, alt_ft) "
                "VALUES (?,?,?,?,?)",
                [(stage_id, seq, lat, lon, alt_ft)
                 for seq, (lat, lon, alt_ft) in enumerate(s["points"])],
            )

        con.commit()
        return JSONResponse({"id": tour_id, "title": title.strip()}, status_code=201)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.get("/tours/{tour_id}")
async def get_tour(tour_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)

        row = con.execute(
            "SELECT id, created_by, title, start_date, end_date FROM tours WHERE id=?",
            (tour_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")

        tour = {
            "id":         row[0],
            "created_by": row[1],
            "title":      row[2],
            "start_date": row[3],
            "end_date":   row[4],
            "is_mine":    row[1] == uid,
        }

        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()

        stages = []
        for sr in stage_rows:
            sid, snum, sname, sdist, sclimb, slat, slon = sr
            completion = _match_stage_activity(
                con, uid, tour["start_date"], tour["end_date"],
                slat, slon, sdist,
            )
            stages.append({
                "id":          sid,
                "stage_num":   snum,
                "name":        sname,
                "distance_mi": sdist,
                "climb_ft":    sclimb,
                "start_lat":   slat,
                "start_lon":   slon,
                "completion":  completion,
            })

        tour["stages"] = stages
        return JSONResponse(tour)
    finally:
        con.close()


@router.get("/tours/{tour_id}/points")
async def get_tour_points(tour_id: int, request: Request):
    """All stage route points grouped by stage_id — used for full-tour map rendering."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        if not con.execute("SELECT id FROM tours WHERE id=?", (tour_id,)).fetchone():
            raise HTTPException(404, "Tour not found")

        rows = con.execute(
            "SELECT ts.id, tp.lat, tp.lon "
            "FROM tour_stages ts "
            "JOIN tour_stage_points tp ON tp.stage_id = ts.id "
            "WHERE ts.tour_id = ? "
            "ORDER BY ts.stage_num, tp.seq",
            (tour_id,),
        ).fetchall()

        by_stage: dict = {}
        for stage_id, lat, lon in rows:
            key = str(stage_id)
            if key not in by_stage:
                by_stage[key] = []
            by_stage[key].append([lat, lon])

        return JSONResponse(by_stage)
    finally:
        con.close()


@router.delete("/tours/{tour_id}")
async def delete_tour(tour_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        _ensure_tables(con)

        row = con.execute(
            "SELECT created_by FROM tours WHERE id=?", (tour_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")
        if row[0] != uid:
            raise HTTPException(403, "Only the tour creator can delete it")

        con.execute("DELETE FROM tours WHERE id=?", (tour_id,))
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()
