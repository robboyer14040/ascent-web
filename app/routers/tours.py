"""routers/tours.py — Shared tour management with per-user stage completion tracking."""

import json
import math
import os
import sqlite3
import time as _time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth import get_session_user_id, require_user
from app.routers.fitgpx import _haversine_m
from app.db import build_activity

router    = APIRouter()
db_getter: Callable = None
templates = None

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
            created_at INTEGER NOT NULL,
            shared     INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Migrate existing DBs that pre-date the shared column
    try:
        con.execute("ALTER TABLE tours ADD COLUMN shared INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE tours ADD COLUMN ai_summary TEXT")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE tours ADD COLUMN share_token TEXT")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE tours ADD COLUMN share_user_id INTEGER")
    except Exception:
        pass
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_shares (
            tour_id  INTEGER NOT NULL,
            user_id  INTEGER NOT NULL,
            token    TEXT    NOT NULL,
            PRIMARY KEY (tour_id, user_id)
        )
    """)
    try:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_shares_token ON tour_shares(token)")
    except Exception:
        pass
    # Migrate existing share_token / share_user_id rows into tour_shares
    try:
        con.execute("""
            INSERT OR IGNORE INTO tour_shares (tour_id, user_id, token)
            SELECT id, share_user_id, share_token FROM tours
            WHERE share_token IS NOT NULL AND share_user_id IS NOT NULL
        """)
        con.commit()
    except Exception:
        pass
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_stage_ai_advice (
            stage_id INTEGER NOT NULL,
            user_id  INTEGER NOT NULL,
            advice   TEXT    NOT NULL,
            PRIMARY KEY (stage_id, user_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_stage_ai_summary (
            stage_id INTEGER NOT NULL PRIMARY KEY,
            summary  TEXT    NOT NULL
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

def _parse_activity_attrs(attrs_json: Optional[str]) -> dict:
    """Parse Ascent's flat NSArray attributes_json into a dict."""
    attrs: dict = {}
    if attrs_json:
        try:
            flat = json.loads(attrs_json)
            if isinstance(flat, list):
                for i in range(0, len(flat) - 1, 2):
                    attrs[str(flat[i])] = flat[i + 1]
        except Exception:
            pass
    return attrs


def _fa(attrs: dict, key: str) -> Optional[float]:
    v = attrs.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_completion(act_row: tuple) -> dict:
    """Build a completion dict from a DB activity row (id, ts, dist, lat, lon, attrs_json)."""
    act_id, ts, dist_mi, _lat, _lon, attrs_json = act_row
    attrs = _parse_activity_attrs(attrs_json)
    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return {
        "activity_id":          act_id,
        "date":                 date_str,
        "distance_mi":          dist_mi,
        "climb_ft":             _fa(attrs, "totalClimb"),
        "duration_s":           _fa(attrs, "durationAsFloat"),
        "moving_s":             _fa(attrs, "movingDurationAsFloat"),
        "avg_moving_speed_mph": _fa(attrs, "avgMovingSpeed"),
        "avg_hr":               _fa(attrs, "avgHeartRate"),
        "max_hr":               _fa(attrs, "maxHeartRate"),
        "avg_cadence":          _fa(attrs, "avgCadence"),
        "avg_power":            _fa(attrs, "avgPower"),
        "max_power":            _fa(attrs, "maxPower"),
        "suffer_score":         _fa(attrs, "sufferScore") or _fa(attrs, "suffer_score"),
        "calories":             _fa(attrs, "calories"),
    }


def _global_stage_matching(con, uid: int, start_date: str, end_date: str, stages: list) -> dict:
    """
    Assign activities to stages using global greedy scoring.

    Supports one activity covering multiple consecutive stages (e.g. riding stages 1+2
    in a single activity). Candidates include both single-stage and multi-stage groups.

    Scores each (stage-group, activity) pair on three factors:
      - GPS proximity  (weight 2.0) — checks activity start vs first stage's start
      - Distance match (weight 1.0) — activity distance vs sum of stages in group
      - Date order     (weight 0.5) — activity rank aligns with first stage's rank

    The tour date window is extended by ±1 day to catch activities recorded the day
    before/after the official tour start/end (common when tour dates are approximate).

    Returns dict: stage_id -> completion dict (or None if no match).
    """
    from datetime import timedelta

    try:
        sd = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        ed = datetime.fromisoformat(end_date).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        start_ts = int((sd - timedelta(days=1)).timestamp())
        end_ts   = int((ed + timedelta(days=1)).timestamp())
    except Exception:
        return {s["id"]: None for s in stages}

    rows = con.execute(
        "SELECT id, COALESCE(creation_time_override_s, creation_time_s), "
        "distance_mi, start_lat, start_lon, attributes_json "
        "FROM activities WHERE user_id=? "
        "AND COALESCE(creation_time_override_s, creation_time_s) BETWEEN ? AND ? "
        "ORDER BY COALESCE(creation_time_override_s, creation_time_s)",
        (uid, start_ts, end_ts),
    ).fetchall()

    if not rows:
        return {s["id"]: None for s in stages}

    n_acts   = len(rows)
    n_stages = len(stages)

    def _score_group(si_list: list, ai: int) -> Optional[float]:
        """Score an activity against a consecutive group of stages. Returns None if no match."""
        first_stage   = stages[si_list[0]]
        combined_dist = sum(stages[si]["distance_mi"] for si in si_list)
        slat          = first_stage["start_lat"]
        slon          = first_stage["start_lon"]

        act_id, act_ts, act_dist, act_lat, act_lon, _ = rows[ai]
        act_dist_v = act_dist or 0.0

        if not (combined_dist * 0.65 <= act_dist_v <= combined_dist * 1.35):
            return None

        # GPS proximity — anchored to first stage's start point
        gps_score = 0.0
        if act_lat is not None and act_lon is not None:
            if slat is not None and slon is not None:
                lat_d = 5.0 / 111.0
                lon_d = 5.0 / (111.0 * math.cos(math.radians(slat)))
                if (slat - lat_d <= act_lat <= slat + lat_d and
                        slon - lon_d <= act_lon <= slon + lon_d):
                    gps_score = 2.0   # confirmed GPS match
                else:
                    return None       # activity GPS outside this stage's area — skip
            else:
                gps_score = 0.3       # activity has GPS but stage doesn't — mild bonus

        # Distance score: 1.0 = perfect, 0.0 = at the ±35% edge
        dist_score = 1.0 - abs(act_dist_v - combined_dist) / (combined_dist * 0.35)

        # Date-order score: activity rank vs first stage's positional rank
        stage_rank = (first_stage["stage_num"] - 1) / max(n_stages - 1, 1)
        act_rank   = ai / max(n_acts - 1, 1)
        date_score = 1.0 - abs(act_rank - stage_rank)

        return gps_score + dist_score + 0.5 * date_score

    # Build candidates: (score, [si_list], ai)
    # Include single-stage and multi-stage (up to 4 consecutive) groups
    candidates: list = []
    for ai in range(len(rows)):
        for group_size in range(1, min(5, n_stages + 1)):
            for start_si in range(n_stages - group_size + 1):
                si_list = list(range(start_si, start_si + group_size))
                score   = _score_group(si_list, ai)
                if score is not None:
                    candidates.append((score, si_list, ai))

    # Greedy assignment — highest score first
    # An activity assigned to multiple stages consumes all of them at once
    candidates.sort(key=lambda x: -x[0])
    used_stages: set = set()
    used_acts:   set = set()
    assignments: dict = {}  # stage_id -> row index into `rows`

    for _score, si_list, ai in candidates:
        if any(si in used_stages for si in si_list) or ai in used_acts:
            continue
        for si in si_list:
            assignments[stages[si]["id"]] = ai
            used_stages.add(si)
        used_acts.add(ai)

    # ── Repair pass ───────────────────────────────────────────────────────────
    # When a single-stage match wins over a multi-stage group (e.g. the
    # activity distance is slightly closer to one stage's distance than to the
    # combined distance), adjacent stages may be left orphaned.  Walk through
    # orphaned stages and extend a neighbouring assignment when the neighbour's
    # activity distance is also compatible with the combined 2-stage distance.
    # Repeat until no more extensions are possible (handles 3+ consecutive
    # orphaned stages in a single long activity).
    repair_changed = True
    while repair_changed:
        repair_changed = False
        for si in range(n_stages):
            if stages[si]["id"] in assignments:
                continue  # already matched
            orphan_dist = stages[si].get("distance_mi") or 0.0
            for adj_si in (si - 1, si + 1):
                if not (0 <= adj_si < n_stages):
                    continue
                if stages[adj_si]["id"] not in assignments:
                    continue  # neighbour also unmatched
                ai         = assignments[stages[adj_si]["id"]]
                nbr_dist   = stages[adj_si].get("distance_mi") or 0.0
                combined   = orphan_dist + nbr_dist
                act_dist_v = rows[ai][2] or 0.0
                if combined > 0 and combined * 0.65 <= act_dist_v <= combined * 1.35:
                    assignments[stages[si]["id"]] = ai
                    repair_changed = True
                    break

    return {
        stage["id"]: (_build_completion(rows[assignments[stage["id"]]]) if stage["id"] in assignments else None)
        for stage in stages
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/tours")
async def list_tours(request: Request):
    uid = require_user(request)

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        rows = con.execute(
            "SELECT id, created_by, title, start_date, end_date, created_at, shared "
            "FROM tours WHERE created_by=? OR shared=1 ORDER BY start_date DESC",
            (uid,),
        ).fetchall()
        return JSONResponse([{
            "id":         r[0],
            "created_by": r[1],
            "title":      r[2],
            "start_date": r[3],
            "end_date":   r[4],
            "created_at": r[5],
            "shared":     bool(r[6]),
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
    shared:     int               = Form(default=1),
    files:      List[UploadFile]  = File(...),
):
    uid = require_user(request)

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
            "INSERT INTO tours (created_by, title, start_date, end_date, created_at, shared) "
            "VALUES (?,?,?,?,?,?)",
            (uid, title.strip(), start_date, end_date, int(_time.time()), shared),
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
async def get_tour(tour_id: int, request: Request, match_user_id: Optional[int] = Query(default=None)):
    uid = require_user(request)

    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)

        row = con.execute(
            "SELECT id, created_by, title, start_date, end_date, shared FROM tours WHERE id=?",
            (tour_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")

        share_row = con.execute(
            "SELECT token FROM tour_shares WHERE tour_id=? AND user_id=?", (tour_id, uid)
        ).fetchone()

        tour = {
            "id":          row[0],
            "created_by":  row[1],
            "title":       row[2],
            "start_date":  row[3],
            "end_date":    row[4],
            "shared":      bool(row[5]) if row[5] is not None else True,
            "is_mine":     row[1] == uid,
            "share_token": share_row[0] if share_row else None,
        }

        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()

        stages = [{
            "id":          sr[0],
            "stage_num":   sr[1],
            "name":        sr[2],
            "distance_mi": sr[3],
            "climb_ft":    sr[4],
            "start_lat":   sr[5],
            "start_lon":   sr[6],
            "completion":  None,
        } for sr in stage_rows]

        completions = _global_stage_matching(
            con, match_user_id if match_user_id is not None else uid,
            tour["start_date"], tour["end_date"], stages,
        )
        for stage in stages:
            stage["completion"] = completions.get(stage["id"])

        tour["stages"] = stages
        return JSONResponse(tour)
    finally:
        con.close()


@router.get("/tours/{tour_id}/points")
async def get_tour_points(tour_id: int, request: Request):
    """All stage route points grouped by stage_id — used for full-tour map rendering."""
    uid = require_user(request)

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        if not con.execute("SELECT id FROM tours WHERE id=?", (tour_id,)).fetchone():
            raise HTTPException(404, "Tour not found")

        rows = con.execute(
            "SELECT ts.id, tp.lat, tp.lon, tp.alt_ft "
            "FROM tour_stages ts "
            "JOIN tour_stage_points tp ON tp.stage_id = ts.id "
            "WHERE ts.tour_id = ? "
            "ORDER BY ts.stage_num, tp.seq",
            (tour_id,),
        ).fetchall()

        by_stage: dict = {}
        for stage_id, lat, lon, alt_ft in rows:
            key = str(stage_id)
            if key not in by_stage:
                by_stage[key] = []
            by_stage[key].append([lat, lon, alt_ft])

        return JSONResponse(by_stage)
    finally:
        con.close()


_VALID_GPS = (
    "latitude_e7 != 999.0 AND longitude_e7 != 999.0"
    " AND latitude_e7 BETWEEN -90 AND 90"
    " AND longitude_e7 BETWEEN -180 AND 180"
    " AND NOT (latitude_e7 = 0.0 AND longitude_e7 = 0.0)"
)


def _activity_pts_for_stages(con, stages: list, completions: dict) -> dict:
    """Return {stage_id: [[lat, lon, alt_ft], ...]} from actual activity GPS for completed stages."""
    result: dict = {}
    for stage in stages:
        comp = completions.get(stage["id"])
        if not comp or not comp.get("activity_id"):
            continue
        rows = con.execute(
            f"SELECT latitude_e7, longitude_e7, orig_altitude_cm "
            f"FROM points WHERE track_id=? AND {_VALID_GPS} "
            f"ORDER BY wall_clock_delta_s ASC, active_time_delta_s ASC",
            (comp["activity_id"],),
        ).fetchall()
        if not rows:
            continue
        step = max(1, len(rows) // 800)
        sampled = list(rows[::step])
        if rows[-1] not in sampled:
            sampled.append(rows[-1])
        result[str(stage["id"])] = [
            [lat, lon, round(float(alt or 0), 1)]
            for lat, lon, alt in sampled
        ]
    return result


@router.get("/tours/{tour_id}/activity-points")
async def get_tour_activity_points(
    tour_id: int, request: Request,
    match_user_id: Optional[int] = Query(default=None),
):
    """GPS points from matched actual activities for completed stages, keyed by stage_id."""
    uid = require_user(request)
    actual_uid = match_user_id if match_user_id is not None else uid

    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT start_date, end_date FROM tours WHERE id=?", (tour_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")

        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
        stages = [{
            "id": sr[0], "stage_num": sr[1], "name": sr[2],
            "distance_mi": sr[3], "climb_ft": sr[4],
            "start_lat": sr[5], "start_lon": sr[6],
        } for sr in stage_rows]

        completions = _global_stage_matching(con, actual_uid, row[0], row[1], stages)
        return JSONResponse(_activity_pts_for_stages(con, stages, completions))
    finally:
        con.close()


@router.delete("/tours/{tour_id}")
async def delete_tour(tour_id: int, request: Request):
    uid = require_user(request)

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


@router.patch("/tours/{tour_id}/stages/reorder")
async def reorder_stages(tour_id: int, request: Request, body: dict = Body(...)):
    uid = require_user(request)

    stage_ids = body.get("stage_ids") or []
    if not stage_ids:
        raise HTTPException(400, "stage_ids required")

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
            raise HTTPException(403, "Only the tour creator can reorder stages")

        for i, sid in enumerate(stage_ids):
            con.execute(
                "UPDATE tour_stages SET stage_num=? WHERE id=? AND tour_id=?",
                (i + 1, sid, tour_id),
            )
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()


@router.put("/tours/{tour_id}")
async def update_tour(
    tour_id:     int,
    request:     Request,
    title:       str                       = Form(...),
    start_date:  str                       = Form(...),
    end_date:    str                       = Form(...),
    shared:      int                       = Form(default=1),
    stage_order: str                       = Form(default="[]"),
    files:       Optional[List[UploadFile]] = File(default=None),
):
    """Edit an existing tour: update metadata, reorder/remove/add stages."""
    uid = require_user(request)

    if not title.strip():
        raise HTTPException(400, "Title is required")
    try:
        from datetime import date as _date
        sd = _date.fromisoformat(start_date)
        ed = _date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format")
    if ed < sd:
        raise HTTPException(400, "End date must be on or after start date")

    # Parse stage_order JSON
    try:
        order = json.loads(stage_order) if stage_order else []
    except Exception:
        raise HTTPException(400, "Invalid stage_order JSON")

    # Parse any new GPX files (filter out empty uploads FastAPI may inject)
    real_files = [f for f in (files or []) if f.filename]
    new_stages: list = []
    for i, f in enumerate(real_files):
        data = await f.read()
        try:
            s = _parse_gpx_route(data, f.filename or f"Stage {i + 1}")
        except ValueError as e:
            raise HTTPException(400, f"File '{f.filename}': {e}")
        new_stages.append(s)

    con = sqlite3.connect(db_getter().path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        _ensure_tables(con)

        row = con.execute(
            "SELECT created_by FROM tours WHERE id=?", (tour_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")
        if row[0] != uid:
            raise HTTPException(403, "Only the tour creator can edit it")

        # Update metadata and clear cached AI content
        con.execute(
            "UPDATE tours SET title=?, start_date=?, end_date=?, shared=?, ai_summary=NULL WHERE id=?",
            (title.strip(), start_date, end_date, shared, tour_id),
        )
        con.execute(
            "DELETE FROM tour_stage_ai_advice WHERE stage_id IN "
            "(SELECT id FROM tour_stages WHERE tour_id=?)",
            (tour_id,),
        )
        con.execute(
            "DELETE FROM tour_stage_ai_summary WHERE stage_id IN "
            "(SELECT id FROM tour_stages WHERE tour_id=?)",
            (tour_id,),
        )

        # Which existing stage IDs to keep
        keep_ids = {int(item["id"]) for item in order if item.get("type") == "existing"}

        # Delete removed stages (cascades to tour_stage_points)
        for (sid,) in con.execute(
            "SELECT id FROM tour_stages WHERE tour_id=?", (tour_id,)
        ).fetchall():
            if sid not in keep_ids:
                con.execute("DELETE FROM tour_stages WHERE id=?", (sid,))

        # Apply the new ordering: renumber existing stages, insert new ones
        for pos, item in enumerate(order):
            stage_num = pos + 1
            if item.get("type") == "existing":
                con.execute(
                    "UPDATE tour_stages SET stage_num=? WHERE id=? AND tour_id=?",
                    (stage_num, int(item["id"]), tour_id),
                )
            elif item.get("type") == "new":
                idx = int(item.get("idx", 0))
                if idx >= len(new_stages):
                    continue
                s = new_stages[idx]
                cur = con.execute(
                    "INSERT INTO tour_stages "
                    "(tour_id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (tour_id, stage_num, s["name"],
                     s["distance_mi"], s["climb_ft"],
                     s["start_lat"], s["start_lon"]),
                )
                stage_id = cur.lastrowid
                con.executemany(
                    "INSERT INTO tour_stage_points (stage_id, seq, lat, lon, alt_ft) "
                    "VALUES (?,?,?,?,?)",
                    [(stage_id, seq, lat, lon, alt_ft)
                     for seq, (lat, lon, alt_ft) in enumerate(s["points"])],
                )

        con.commit()
        return JSONResponse({"id": tour_id, "title": title.strip()})
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.get("/tours/{tour_id}/stages/{stage_id}/locations")
async def get_stage_locations(tour_id: int, stage_id: int, request: Request):
    """Sample points along a tour stage and reverse-geocode to a location string."""
    uid = require_user(request)

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        if not con.execute(
            "SELECT 1 FROM tour_stages WHERE id=? AND tour_id=?", (stage_id, tour_id)
        ).fetchone():
            raise HTTPException(404, "Stage not found")
        pts_rows = con.execute(
            "SELECT lat, lon FROM tour_stage_points WHERE stage_id=? ORDER BY seq",
            (stage_id,),
        ).fetchall()
    finally:
        con.close()

    if not pts_rows:
        return JSONResponse({"locations": None})

    from app.routers.weather import fetch_locations
    pts = [{"lat": r[0], "lon": r[1]} for r in pts_rows]
    locations = await fetch_locations(pts)
    return JSONResponse({"locations": locations})


@router.get("/tours/{tour_id}/stages/{stage_id}/forecast")
async def get_stage_forecast(tour_id: int, stage_id: int, request: Request):
    """Return an Open-Meteo forecast for the estimated date of an uncompleted tour stage."""
    uid = require_user(request)

    from datetime import date as _date, timedelta
    import httpx

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        stage_row = con.execute(
            "SELECT ts.stage_num, ts.start_lat, ts.start_lon, t.start_date, t.end_date "
            "FROM tour_stages ts JOIN tours t ON t.id = ts.tour_id "
            "WHERE ts.id=? AND ts.tour_id=?",
            (stage_id, tour_id),
        ).fetchone()
        if not stage_row:
            raise HTTPException(404, "Stage not found")
        stage_num, start_lat, start_lon, tour_start, tour_end = stage_row
        total_stages = con.execute(
            "SELECT COUNT(*) FROM tour_stages WHERE tour_id=?", (tour_id,)
        ).fetchone()[0]
    finally:
        con.close()

    if start_lat is None or start_lon is None:
        return JSONResponse({"forecast": None, "out_of_range": False})

    try:
        sd = _date.fromisoformat(tour_start)
        ed = _date.fromisoformat(tour_end)
        tour_days = (ed - sd).days
        offset = round((stage_num - 1) * tour_days / max(total_stages - 1, 1)) if total_stages > 1 else 0
        stage_date = sd + timedelta(days=offset)
    except Exception:
        return JSONResponse({"forecast": None, "out_of_range": False})

    today = datetime.now(timezone.utc).date()
    delta = (stage_date - today).days
    if not (0 <= delta <= 16):
        return JSONResponse({"forecast": None, "out_of_range": True})

    params = {
        "latitude":  round(start_lat, 4),
        "longitude": round(start_lon, 4),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "start_date": stage_date.isoformat(),
        "end_date":   stage_date.isoformat(),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit":  "kmh",
        "timezone": "UTC",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            if r.status_code != 200:
                return JSONResponse({"forecast": None})
            data = r.json()
    except Exception:
        return JSONResponse({"forecast": None})

    daily  = data.get("daily", {})
    codes  = daily.get("weather_code",        [None])
    t_max  = daily.get("temperature_2m_max",  [None])
    t_min  = daily.get("temperature_2m_min",  [None])
    precip = daily.get("precipitation_sum",   [None])
    wind   = daily.get("wind_speed_10m_max",  [None])

    from app.routers.weather import wmo_desc
    return JSONResponse({"forecast": {
        "stage_date":  stage_date.isoformat(),
        "description": wmo_desc(codes[0]) if codes[0] is not None else None,
        "temp_max_f":  round(t_max[0],  1) if t_max[0]  is not None else None,
        "temp_min_f":  round(t_min[0],  1) if t_min[0]  is not None else None,
        "precip_mm":   round(precip[0], 1) if precip[0] is not None else None,
        "wind_kph":    round(wind[0],   1) if wind[0]   is not None else None,
    }})


@router.get("/tours/{tour_id}/ai-summary")
async def get_tour_ai_summary(tour_id: int, request: Request, model: Optional[str] = Query(default=None), force: bool = Query(default=False)):
    """Return an AI-generated summary of the entire tour (structure only, no activity data)."""
    import os, httpx
    from app.routers.coach import MODELS, DEFAULT_MODEL
    uid = require_user(request)

    db = db_getter()
    api_key = (db.get_user(uid) or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "No Anthropic API key configured")

    con = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT title, start_date, end_date, ai_summary FROM tours WHERE id=?", (tour_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")
        tour_title, start_date, end_date, cached_summary = row
        if cached_summary and not force:
            return JSONResponse({"summary": cached_summary})
        stage_rows = con.execute(
            "SELECT stage_num, name, distance_mi, climb_ft FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
    finally:
        con.close()

    if not stage_rows:
        raise HTTPException(404, "No stages found")

    total_dist  = sum(r[2] for r in stage_rows)
    total_climb = sum(r[3] for r in stage_rows)
    avg_dist    = total_dist  / len(stage_rows)
    avg_climb   = total_climb / len(stage_rows)

    stage_lines = [
        f"  Stage {r[0]}: {r[1]} — {r[2]:.1f}mi, {r[3]:.0f}ft climb"
        for r in stage_rows
    ]

    prompt = (
        f"Tour: {tour_title}\n"
        f"Dates: {start_date} to {end_date}\n"
        f"Number of stages: {len(stage_rows)}\n"
        f"Total: {total_dist:.1f}mi, {total_climb:.0f}ft climb\n"
        f"Average per stage: {avg_dist:.1f}mi, {avg_climb:.0f}ft climb\n\n"
        "Stages:\n" + "\n".join(stage_lines) + "\n\n"
        "Provide a concise summary of this tour for an endurance cyclist or hiker. Include:\n"
        "- Overall character of the tour (total distance, total climbing, number of stages)\n"
        "- Which stages are the most difficult and why they stand out\n"
        "- Any notable patterns (e.g. back-to-back hard stages, easier transition stages, progressive difficulty)\n"
        "Keep it to 3-5 sentences. Do not include training goals, training advice, or recent activity references."
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
                "model":      model if model in MODELS else DEFAULT_MODEL,
                "max_tokens": 350,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    summary = resp.json()["content"][0]["text"].strip()

    # Persist the generated summary
    con2 = sqlite3.connect(db.path, timeout=10)
    try:
        con2.execute("UPDATE tours SET ai_summary=? WHERE id=?", (summary, tour_id))
        con2.commit()
    finally:
        con2.close()

    return JSONResponse({"summary": summary})


@router.get("/tours/{tour_id}/stages/{stage_id}/ai-advice")
async def get_stage_ai_advice(
    tour_id: int,
    stage_id: int,
    request: Request,
    match_user_id: Optional[int] = Query(default=None),
    model: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
    readonly: bool = Query(default=False),
):
    """Return AI coach advice for an uncompleted tour stage.
    readonly=true returns cached advice only ({"advice": null} if none exists).
    """
    import os, httpx
    from app.routers.coach import MODELS, DEFAULT_MODEL
    uid = require_user(request)

    actual_uid = match_user_id if match_user_id is not None else uid

    # Readonly: return cache only, never generate
    if readonly:
        con_ro = sqlite3.connect(db_getter().path, timeout=10)
        try:
            _ensure_tables(con_ro)
            row = con_ro.execute(
                "SELECT advice FROM tour_stage_ai_advice WHERE stage_id=? AND user_id=?",
                (stage_id, actual_uid),
            ).fetchone()
            return JSONResponse({"advice": row[0] if row else None})
        finally:
            con_ro.close()

    db = db_getter()
    api_key = (db.get_user(uid) or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "No Anthropic API key configured")

    con = sqlite3.connect(db.path, timeout=15)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT title, start_date, end_date FROM tours WHERE id=?", (tour_id,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Tour not found")
        tour_title, start_date, end_date = tour_row

        target_row = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft "
            "FROM tour_stages WHERE id=? AND tour_id=?",
            (stage_id, tour_id),
        ).fetchone()
        if not target_row:
            raise HTTPException(404, "Stage not found")

        all_stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
    finally:
        con.close()

    # Check per-user cache
    con_cache = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_tables(con_cache)
        cache_row = con_cache.execute(
            "SELECT advice FROM tour_stage_ai_advice WHERE stage_id=? AND user_id=?",
            (stage_id, actual_uid),
        ).fetchone()
        if cache_row and not force:
            return JSONResponse({"advice": cache_row[0]})
    finally:
        con_cache.close()

    stages = [
        {"id": r[0], "stage_num": r[1], "name": r[2], "distance_mi": r[3], "climb_ft": r[4],
         "start_lat": r[5], "start_lon": r[6]}
        for r in all_stage_rows
    ]
    target = next(s for s in stages if s["id"] == target_row[0])
    con2 = sqlite3.connect(db.path, timeout=15)
    try:
        completions = _global_stage_matching(con2, actual_uid, start_date, end_date, stages)
    finally:
        con2.close()

    n_done = sum(1 for s in stages if completions.get(s["id"]))

    stage_lines = []
    for s in stages:
        comp = completions.get(s["id"])
        marker = " ← UPCOMING" if s["id"] == target["id"] else ""
        done_str = ""
        if comp:
            dur_h = round((comp.get("duration_s") or 0) / 3600, 1)
            clb   = round(comp.get("climb_ft") or 0)
            done_str = f" [DONE: {comp['distance_mi']:.1f}mi, {clb}ft climb, {dur_h}h]"
        stage_lines.append(
            f"  Stage {s['stage_num']}: {s['name']} — {s['distance_mi']:.1f}mi, {s['climb_ft']:.0f}ft climb{done_str}{marker}"
        )

    prompt = (
        f"Tour: {tour_title} ({start_date} to {end_date})\n"
        f"Progress: {n_done} of {len(stages)} stages completed\n\n"
        "All stages:\n" + "\n".join(stage_lines) + "\n\n"
        f"The athlete is preparing for Stage {target['stage_num']}: {target['name']} "
        f"({target['distance_mi']:.1f}mi, {target['climb_ft']:.0f}ft climb).\n\n"
        "Provide 2-4 sentences of specific coach advice for this upcoming stage. "
        "Consider: the stage difficulty relative to completed stages, cumulative fatigue from prior stages, "
        "and tactical tips (pacing, nutrition, effort management). "
        "Do not mention training goals or recent training outside the tour. Be specific and actionable."
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
                "model":      model if model in MODELS else DEFAULT_MODEL,
                "max_tokens": 300,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    advice = resp.json()["content"][0]["text"].strip()

    con3 = sqlite3.connect(db.path, timeout=10)
    try:
        con3.execute(
            "INSERT OR REPLACE INTO tour_stage_ai_advice (stage_id, user_id, advice) VALUES (?,?,?)",
            (stage_id, actual_uid, advice),
        )
        con3.commit()
    finally:
        con3.close()

    return JSONResponse({"advice": advice})


@router.get("/tours/{tour_id}/stages/{stage_id}/ai-summary")
async def get_stage_ai_summary(
    tour_id: int,
    stage_id: int,
    request: Request,
    model: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
):
    """Return an AI-generated summary of a tour stage in the context of the overall tour."""
    import os, httpx
    from app.routers.coach import MODELS, DEFAULT_MODEL
    uid = require_user(request)

    db = db_getter()
    api_key = (db.get_user(uid) or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "No Anthropic API key configured")

    con = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT title, start_date, end_date FROM tours WHERE id=?", (tour_id,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Tour not found")
        tour_title, start_date, end_date = tour_row

        target_row = con.execute(
            "SELECT stage_num, name, distance_mi, climb_ft "
            "FROM tour_stages WHERE id=? AND tour_id=?",
            (stage_id, tour_id),
        ).fetchone()
        if not target_row:
            raise HTTPException(404, "Stage not found")

        cache_row = con.execute(
            "SELECT summary FROM tour_stage_ai_summary WHERE stage_id=?", (stage_id,)
        ).fetchone()
        if cache_row and not force:
            return JSONResponse({"summary": cache_row[0]})

        all_stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
    finally:
        con.close()

    stage_num, stage_name, dist_mi, climb_ft = target_row
    total_stages = len(all_stage_rows)
    total_dist   = sum(r[3] for r in all_stage_rows)
    total_climb  = sum(r[4] for r in all_stage_rows)
    avg_dist     = total_dist  / total_stages if total_stages else 0
    avg_climb    = total_climb / total_stages if total_stages else 0

    stage_lines = [
        f"  Stage {r[1]}: {r[2]} — {r[3]:.1f}mi, {r[4]:.0f}ft climb"
        + (" ← THIS STAGE" if r[0] == stage_id else "")
        for r in all_stage_rows
    ]

    prompt = (
        f"Tour: {tour_title} ({start_date} to {end_date})\n"
        f"Total: {total_stages} stages, {total_dist:.1f}mi, {total_climb:.0f}ft climb\n"
        f"Average per stage: {avg_dist:.1f}mi, {avg_climb:.0f}ft climb\n\n"
        "All stages:\n" + "\n".join(stage_lines) + "\n\n"
        f"Describe Stage {stage_num}: {stage_name} ({dist_mi:.1f}mi, {climb_ft:.0f}ft climb) "
        "in the context of the overall tour. "
        "In 2-3 sentences: characterize what kind of stage it is (e.g. short/long, flat/hilly, "
        "hard/moderate relative to the tour average), and where it sits in the tour arc "
        "(early/mid/late, after or before harder stages, etc.). "
        "Be specific and factual. Do not give training advice or coaching tips."
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
                "model":      model if model in MODELS else DEFAULT_MODEL,
                "max_tokens": 250,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    summary = resp.json()["content"][0]["text"].strip()

    con2 = sqlite3.connect(db.path, timeout=10)
    try:
        con2.execute(
            "INSERT OR REPLACE INTO tour_stage_ai_summary (stage_id, summary) VALUES (?,?)",
            (stage_id, summary),
        )
        con2.commit()
    finally:
        con2.close()

    return JSONResponse({"summary": summary})


def _resolve_share_token(con, token: str):
    """Return (tour_id, share_user_id) for a valid token, or raise 404."""
    row = con.execute(
        "SELECT tour_id, user_id FROM tour_shares WHERE token=?", (token,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Share link not found or revoked")
    return row[0], row[1]


@router.post("/tours/{tour_id}/publish")
async def publish_tour(tour_id: int, request: Request):
    """Generate (or return existing) share token for this user on a tour."""
    import secrets
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT id FROM tours WHERE id=? AND (created_by=? OR shared=1)", (tour_id, uid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")
        existing = con.execute(
            "SELECT token FROM tour_shares WHERE tour_id=? AND user_id=?", (tour_id, uid)
        ).fetchone()
        token = existing[0] if existing else secrets.token_hex(20)
        con.execute(
            "INSERT OR REPLACE INTO tour_shares (tour_id, user_id, token) VALUES (?,?,?)",
            (tour_id, uid, token),
        )
        con.commit()
        return JSONResponse({"token": token})
    finally:
        con.close()


@router.delete("/tours/{tour_id}/publish")
async def revoke_tour_publish(tour_id: int, request: Request):
    """Revoke this user's share token for a tour."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        con.execute(
            "DELETE FROM tour_shares WHERE tour_id=? AND user_id=?", (tour_id, uid)
        )
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()


@router.get("/tours/share/{token}", response_class=HTMLResponse)
async def tour_share_page(token: str, request: Request):
    """Public share page — no auth required."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        share_row = con.execute(
            "SELECT ts.tour_id, ts.user_id, t.title FROM tour_shares ts JOIN tours t ON t.id=ts.tour_id WHERE ts.token=?",
            (token,),
        ).fetchone()
        if not share_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id, share_uid, title = share_row
        user_row = con.execute(
            "SELECT username FROM users WHERE id=?", (share_uid,)
        ).fetchone()
        display_name = user_row[0] if user_row else "Unknown"
    finally:
        con.close()
    try:
        owner_profile = db_getter().get_user_profile(share_uid)
        use_metric = owner_profile.get("use_metric", False)
    except Exception:
        use_metric = False
    return templates.TemplateResponse("tour_share.html", {
        "request":      request,
        "token":        token,
        "tour_title":   title,
        "display_name": display_name,
        "share_uid":    share_uid,
        "use_metric":   use_metric,
    })


@router.get("/tours/share/{token}/data")
async def tour_share_data(token: str):
    """Public endpoint — tour info + stages + completions for the share user."""
    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT t.id, t.title, t.start_date, t.end_date, ts.user_id, t.ai_summary "
            "FROM tour_shares ts JOIN tours t ON t.id=ts.tour_id WHERE ts.token=?",
            (token,),
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id, title, start_date, end_date, share_uid, ai_summary = tour_row

        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
        stages = [{
            "id":          sr[0],
            "stage_num":   sr[1],
            "name":        sr[2],
            "distance_mi": sr[3],
            "climb_ft":    sr[4],
            "start_lat":   sr[5],
            "start_lon":   sr[6],
            "completion":  None,
        } for sr in stage_rows]

        completions = _global_stage_matching(con, share_uid, start_date, end_date, stages)
        for stage in stages:
            stage["completion"] = completions.get(stage["id"])

        return JSONResponse({
            "id":         tour_id,
            "title":      title,
            "start_date": start_date,
            "end_date":   end_date,
            "ai_summary": ai_summary,
            "stages":     stages,
        })
    finally:
        con.close()


@router.get("/tours/share/{token}/points")
async def tour_share_points(token: str):
    """Public endpoint — stage route points for the shared tour."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT tour_id FROM tour_shares WHERE token=?", (token,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id = tour_row[0]

        rows = con.execute(
            "SELECT ts.id, tp.lat, tp.lon, tp.alt_ft "
            "FROM tour_stages ts "
            "JOIN tour_stage_points tp ON tp.stage_id = ts.id "
            "WHERE ts.tour_id=? ORDER BY ts.stage_num, tp.seq",
            (tour_id,),
        ).fetchall()
        by_stage: dict = {}
        for stage_id, lat, lon, alt_ft in rows:
            key = str(stage_id)
            if key not in by_stage:
                by_stage[key] = []
            by_stage[key].append([lat, lon, alt_ft])
        return JSONResponse(by_stage)
    finally:
        con.close()


@router.get("/tours/share/{token}/activity-points")
async def tour_share_activity_points(token: str):
    """Public endpoint — GPS points from actual completed activities for a shared tour."""
    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT t.id, t.start_date, t.end_date, ts.user_id "
            "FROM tour_shares ts JOIN tours t ON t.id=ts.tour_id WHERE ts.token=?",
            (token,),
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id, start_date, end_date, share_uid = tour_row

        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
        stages = [{
            "id": sr[0], "stage_num": sr[1], "name": sr[2],
            "distance_mi": sr[3], "climb_ft": sr[4],
            "start_lat": sr[5], "start_lon": sr[6],
        } for sr in stage_rows]

        completions = _global_stage_matching(con, share_uid, start_date, end_date, stages)
        return JSONResponse(_activity_pts_for_stages(con, stages, completions))
    finally:
        con.close()


@router.get("/tours/share/{token}/stages/{stage_id}/forecast")
async def tour_share_forecast(token: str, stage_id: int):
    """Public endpoint — forecast for an uncompleted stage on a shared tour."""
    from datetime import date as _date, timedelta
    import httpx

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT tour_id FROM tour_shares WHERE token=?", (token,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id = tour_row[0]

        stage_row = con.execute(
            "SELECT ts.stage_num, ts.start_lat, ts.start_lon, t.start_date, t.end_date "
            "FROM tour_stages ts JOIN tours t ON t.id = ts.tour_id "
            "WHERE ts.id=? AND ts.tour_id=?",
            (stage_id, tour_id),
        ).fetchone()
        if not stage_row:
            raise HTTPException(404, "Stage not found")
        stage_num, start_lat, start_lon, tour_start, tour_end = stage_row
        total_stages = con.execute(
            "SELECT COUNT(*) FROM tour_stages WHERE tour_id=?", (tour_id,)
        ).fetchone()[0]
    finally:
        con.close()

    if start_lat is None or start_lon is None:
        return JSONResponse({"forecast": None, "out_of_range": False})
    try:
        sd = _date.fromisoformat(tour_start)
        ed = _date.fromisoformat(tour_end)
        tour_days  = (ed - sd).days
        offset     = round((stage_num - 1) * tour_days / max(total_stages - 1, 1)) if total_stages > 1 else 0
        stage_date = sd + timedelta(days=offset)
    except Exception:
        return JSONResponse({"forecast": None, "out_of_range": False})

    today = datetime.now(timezone.utc).date()
    delta = (stage_date - today).days
    if not (0 <= delta <= 16):
        return JSONResponse({"forecast": None, "out_of_range": True})

    params = {
        "latitude": round(start_lat, 4), "longitude": round(start_lon, 4),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "start_date": stage_date.isoformat(), "end_date": stage_date.isoformat(),
        "temperature_unit": "fahrenheit", "wind_speed_unit": "kmh", "timezone": "UTC",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            if r.status_code != 200:
                return JSONResponse({"forecast": None})
            data = r.json()
    except Exception:
        return JSONResponse({"forecast": None})

    daily  = data.get("daily", {})
    codes  = daily.get("weather_code",       [None])
    t_max  = daily.get("temperature_2m_max", [None])
    t_min  = daily.get("temperature_2m_min", [None])
    precip = daily.get("precipitation_sum",  [None])
    wind   = daily.get("wind_speed_10m_max", [None])
    from app.routers.weather import wmo_desc
    return JSONResponse({"forecast": {
        "stage_date":  stage_date.isoformat(),
        "description": wmo_desc(codes[0]) if codes[0] is not None else None,
        "temp_max_f":  round(t_max[0],  1) if t_max[0]  is not None else None,
        "temp_min_f":  round(t_min[0],  1) if t_min[0]  is not None else None,
        "precip_mm":   round(precip[0], 1) if precip[0] is not None else None,
        "wind_kph":    round(wind[0],   1) if wind[0]   is not None else None,
    }})


def _stage_gpx_response(stage_name: str, pts: list) -> bytes:
    """Build a GPX route from stage points. pts = [(lat, lon, alt_ft), ...]"""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<gpx version="1.1" creator="Ascent Web"\n',
        '  xmlns="http://www.topografix.com/GPX/1/1"\n',
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n',
        '  xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">\n',
        f'  <rte>\n',
        f'    <name>{stage_name}</name>\n',
    ]
    for lat, lon, alt_ft in pts:
        alt_m = round(alt_ft * 0.3048, 1)
        lines.append(f'    <rtept lat="{lat}" lon="{lon}"><ele>{alt_m}</ele></rtept>\n')
    lines.append('  </rte>\n</gpx>\n')
    return ''.join(lines).encode('utf-8')


@router.get("/tours/stages/{stage_id}/export/gpx")
async def tour_stage_export_gpx(stage_id: int, request: Request):
    """Authenticated — download route GPX for a tour stage."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT ts.name, t.created_by, t.shared "
            "FROM tour_stages ts JOIN tours t ON t.id = ts.tour_id "
            "WHERE ts.id=?", (stage_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Stage not found")
        stage_name, created_by, shared = row
        if created_by != uid and not shared:
            raise HTTPException(403, "Not authorised")
        pts = con.execute(
            "SELECT lat, lon, alt_ft FROM tour_stage_points WHERE stage_id=? ORDER BY seq",
            (stage_id,)
        ).fetchall()
    finally:
        con.close()
    if not pts:
        raise HTTPException(404, "No route points for this stage")
    safe = "".join(c for c in stage_name if c.isalnum() or c in " -_")
    from fastapi.responses import Response
    return Response(
        content=_stage_gpx_response(stage_name, pts),
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{safe.strip() or "stage"}.gpx"'},
    )


@router.get("/tours/share/{token}/stages/{stage_id}/export/gpx")
async def tour_share_stage_export_gpx(token: str, stage_id: int):
    """Public — download route GPX for a stage on a shared tour."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT tour_id FROM tour_shares WHERE token=?", (token,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id = tour_row[0]
        row = con.execute(
            "SELECT ts.name FROM tour_stages ts WHERE ts.id=? AND ts.tour_id=?",
            (stage_id, tour_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Stage not found")
        stage_name = row[0]
        pts = con.execute(
            "SELECT lat, lon, alt_ft FROM tour_stage_points WHERE stage_id=? ORDER BY seq",
            (stage_id,)
        ).fetchall()
    finally:
        con.close()
    if not pts:
        raise HTTPException(404, "No route points for this stage")
    safe = "".join(c for c in stage_name if c.isalnum() or c in " -_")
    from fastapi.responses import Response
    return Response(
        content=_stage_gpx_response(stage_name, pts),
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{safe.strip() or "stage"}.gpx"'},
    )


@router.get("/tours/share/{token}/activities/{activity_id}")
async def tour_share_activity(token: str, activity_id: int):
    """Public endpoint — full activity detail for a completed stage on a shared tour."""
    import sqlite3 as _sq
    con = _sq.connect(db_getter().path, timeout=10)
    con.row_factory = _sq.Row
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT ts.tour_id, ts.user_id FROM tour_shares ts WHERE ts.token=?", (token,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        share_uid = tour_row["user_id"]

        act_row = con.execute(
            "SELECT * FROM activities WHERE id=? AND user_id=?", (activity_id, share_uid)
        ).fetchone()
        if not act_row:
            raise HTTPException(404, "Activity not found")

        act = build_activity(act_row)
        ai_row = con.execute(
            "SELECT summary FROM activity_ai_summaries WHERE activity_id=?", (activity_id,)
        ).fetchone()
        return JSONResponse({
            "id":                    act["id"],
            "name":                  act.get("name") or "",
            "notes":                 act.get("notes") or "",
            "start_time":            act.get("start_time"),
            "activity_type":         act.get("activity_type") or "",
            "equipment":             act.get("equipment") or "",
            "strava_activity_id":    act.get("strava_activity_id"),
            "perceived_exertion":    act.get("perceived_exertion"),
            "distance_mi":           act.get("distance_mi", 0),
            "total_climb_ft":        act.get("total_climb_ft", 0),
            "total_descent_ft":      act.get("total_descent_ft", 0),
            "duration":              act.get("duration", 0),
            "active_time":           act.get("active_time", 0),
            "avg_speed_mph":         act.get("avg_speed_mph", 0),
            "avg_overall_speed_mph": act.get("avg_overall_speed_mph", 0),
            "avg_heartrate":         act.get("avg_heartrate", 0),
            "max_heartrate":         act.get("max_heartrate", 0),
            "calories":              act.get("calories", 0),
            "avg_cadence":           act.get("avg_cadence", 0),
            "avg_power":             act.get("avg_power", 0),
            "max_power":             act.get("max_power", 0),
            "suffer_score":          act.get("suffer_score", 0),
            "ai_summary":            ai_row[0] if ai_row else None,
        })
    finally:
        con.close()


@router.post("/tours/share/{token}/activities/{activity_id}/sync")
async def tour_share_activity_sync(token: str, activity_id: int):
    """Public endpoint — re-sync a completed stage activity from Strava."""
    import httpx
    from app.strava_importer import apply_strava_update
    from app.routers.photos import resolve_photos
    from app.routers.strava import get_fresh_token as _gft

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        share_row = con.execute(
            "SELECT tour_id, user_id FROM tour_shares WHERE token=?", (token,)
        ).fetchone()
        if not share_row:
            raise HTTPException(404, "Share link not found or revoked")
        share_uid = share_row[1]
    finally:
        con.close()

    db = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")
    if act.get("user_id") != share_uid:
        raise HTTPException(403, "Activity not accessible")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        raise HTTPException(400, "Activity has no Strava ID")

    strava_token = await _gft(user_id=share_uid)
    if not strava_token:
        raise HTTPException(503, "Share user not connected to Strava")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}",
            headers={"Authorization": f"Bearer {strava_token}"},
            params={"include_all_efforts": "false"},
        )
        if resp.status_code == 404:
            raise HTTPException(404, "Activity not found on Strava")
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid")
        resp.raise_for_status()
        sa = resp.json()

    import sqlite3 as _sq2, os as _os
    db_path = _os.environ.get("ASCENT_DB_PATH", "")
    con2 = _sq2.connect(db_path, timeout=30)
    try:
        apply_strava_update(con2, activity_id, sa)
        con2.commit()
    finally:
        con2.close()

    await resolve_photos(activity_id, force=True)

    # Return in the same format as tour_share_activity
    import sqlite3 as _sq3
    con3 = _sq3.connect(db_getter().path, timeout=10)
    con3.row_factory = _sq3.Row
    try:
        act_row = con3.execute(
            "SELECT * FROM activities WHERE id=? AND user_id=?", (activity_id, share_uid)
        ).fetchone()
        if not act_row:
            raise HTTPException(404, "Activity not found after sync")
        act2 = build_activity(act_row)
    finally:
        con3.close()

    return JSONResponse({
        "id":                    act2["id"],
        "name":                  act2.get("name") or "",
        "notes":                 act2.get("notes") or "",
        "start_time":            act2.get("start_time"),
        "activity_type":         act2.get("activity_type") or "",
        "equipment":             act2.get("equipment") or "",
        "strava_activity_id":    act2.get("strava_activity_id"),
        "perceived_exertion":    act2.get("perceived_exertion"),
        "distance_mi":           act2.get("distance_mi", 0),
        "total_climb_ft":        act2.get("total_climb_ft", 0),
        "total_descent_ft":      act2.get("total_descent_ft", 0),
        "duration":              act2.get("duration", 0),
        "active_time":           act2.get("active_time", 0),
        "avg_speed_mph":         act2.get("avg_speed_mph", 0),
        "avg_overall_speed_mph": act2.get("avg_overall_speed_mph", 0),
        "avg_heartrate":         act2.get("avg_heartrate", 0),
        "max_heartrate":         act2.get("max_heartrate", 0),
        "calories":              act2.get("calories", 0),
        "avg_cadence":           act2.get("avg_cadence", 0),
        "avg_power":             act2.get("avg_power", 0),
        "max_power":             act2.get("max_power", 0),
        "suffer_score":          act2.get("suffer_score", 0),
    })


@router.get("/tours/share/{token}/stages/{stage_id}/ai-summary")
async def tour_share_stage_ai_summary(token: str, stage_id: int):
    """Public endpoint — cached AI stage summary (read-only, no generation)."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        tour_row = con.execute("SELECT tour_id FROM tour_shares WHERE token=?", (token,)).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        stage_row = con.execute(
            "SELECT id FROM tour_stages WHERE id=? AND tour_id=?", (stage_id, tour_row[0])
        ).fetchone()
        if not stage_row:
            raise HTTPException(404, "Stage not found")
        cache_row = con.execute(
            "SELECT summary FROM tour_stage_ai_summary WHERE stage_id=?", (stage_id,)
        ).fetchone()
        if not cache_row:
            raise HTTPException(404, "No cached summary")
        return JSONResponse({"summary": cache_row[0]})
    finally:
        con.close()


@router.get("/tour", response_class=HTMLResponse)
async def tour_page(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        return RedirectResponse("/login?next=/tour", status_code=303)
    user = db_getter().get_user(uid)
    is_admin = bool(user and user.get("is_admin"))
    try:
        ui_prefs = db_getter().get_ui_prefs(uid)
        profile  = db_getter().get_user_profile(uid)
        ui_prefs["use_metric"] = profile.get("use_metric", False)
    except Exception:
        ui_prefs = {"use_metric": False}
    return templates.TemplateResponse("tour.html", {
        "request": request,
        "current_user_id": uid,
        "is_admin": is_admin,
        "has_anthropic_key": bool((user or {}).get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")),
        "ui_prefs": ui_prefs,
    })
