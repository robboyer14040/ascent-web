"""routers/tours.py — Shared tour management with per-user stage completion tracking."""

import asyncio
import json
import math
import os
import re
import sqlite3
import time as _time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth import get_session_user_id, require_user
from app.routers.fitgpx import _haversine_m
from app.db import build_activity, parse_attrs

router    = APIRouter()
db_getter: Callable = None
templates = None

# Stage summaries write prose from route facts already supplied in the prompt,
# so they don't need the coach's model — Sonnet is markedly faster to first byte.
STAGE_SUMMARY_MODEL = "claude-sonnet-5"

# (stage_id, user_id) currently generating, so repeated polls don't pile up
# duplicate generations for the same card. _summary_tasks holds a strong
# reference to each running task — asyncio only keeps a weak one, so a task
# without one can be garbage-collected mid-flight.
_summary_jobs: set = set()
_summary_tasks: set = set()

# Generation reverse-geocodes through Nominatim, whose policy is one request a
# second for the whole application. A page showing every stage at once would
# otherwise start dozens of generations together and get the app blocked, so
# only one summary is ever built at a time.
_summary_gate: Optional[asyncio.Semaphore] = None


def _gate() -> asyncio.Semaphore:
    global _summary_gate
    if _summary_gate is None:
        _summary_gate = asyncio.Semaphore(1)
    return _summary_gate

M_TO_MI    = 0.000621371
M_TO_FT    = 3.28084
_EXP_ALPHA = 0.18


def _wx_tile_url() -> str:
    """Dark tile URL for the weather-forecast route thumbnail (Stadia if keyed)."""
    key = os.environ.get("STADIA_API_KEY", "")
    if key:
        return ("https://tiles.stadiamaps.com/tiles/alidade_smooth_dark"
                "/{z}/{x}/{y}.png?api_key=" + key)
    return "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _load_stage_route(con, tour_id: int, stage_id: int):
    """Return (stage_name, points) for a stage, where points are
    [{lat, lon, alt_m}]. Raises HTTPException if the stage/route is missing."""
    stage = con.execute(
        "SELECT name FROM tour_stages WHERE id=? AND tour_id=?", (stage_id, tour_id)
    ).fetchone()
    if not stage:
        raise HTTPException(404, "Stage not found")
    rows = con.execute(
        "SELECT lat, lon, alt_ft FROM tour_stage_points WHERE stage_id=? ORDER BY seq",
        (stage_id,),
    ).fetchall()
    if len(rows) < 2:
        raise HTTPException(400, "Stage has no route to forecast")
    pts = [{"lat": r[0], "lon": r[1], "alt_m": (r[2] * 0.3048 if r[2] else None)}
           for r in rows]
    return stage[0] or "Stage", pts


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
    # A share link now targets a specific attempt (tracking window) of its user.
    try:
        con.execute("ALTER TABLE tour_shares ADD COLUMN attempt_id INTEGER")
    except Exception:
        pass
    # Per-user tracking windows ("attempts"). A user with no attempt row for a
    # tour is "unsubscribed": no stage/activity matching runs for them.
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_attempts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tour_id    INTEGER NOT NULL REFERENCES tours(id) ON DELETE CASCADE,
            user_id    INTEGER NOT NULL,
            start_date TEXT    NOT NULL,
            end_date   TEXT,
            created_at INTEGER NOT NULL
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tour_attempts ON tour_attempts(tour_id, user_id)"
    )
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
            stage_id  INTEGER NOT NULL,
            user_id   INTEGER NOT NULL DEFAULT 0,
            summary   TEXT    NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (stage_id, user_id)
        )
    """)
    # Older DBs keyed summaries by stage alone, and their text predates the
    # route/terrain context. Both make them wrong to keep, so rebuild empty.
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(tour_stage_ai_summary)")}
        if "user_id" not in cols:
            con.execute("DROP TABLE tour_stage_ai_summary")
            con.execute("""
                CREATE TABLE tour_stage_ai_summary (
                    stage_id  INTEGER NOT NULL,
                    user_id   INTEGER NOT NULL DEFAULT 0,
                    summary   TEXT    NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (stage_id, user_id)
                )
            """)
            con.commit()
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE tour_stage_ai_summary "
                    "ADD COLUMN completed INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
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
    # Per-stage alternate-route override: NULL = auto (geometry heuristic),
    # 0 = force standalone (never an alternate), 1 = force alternate of the
    # preceding stage. Added after the fact for DBs that pre-date it.
    try:
        con.execute("ALTER TABLE tour_stages ADD COLUMN alt_override INTEGER")
    except Exception:
        pass
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
    # Public share-page followers. A subscriber is "whoever holds this share
    # link", so rows are keyed by the tour_shares token and die with it.
    # confirmed_at NULL means signed up but not yet confirmed — only confirmed
    # rows are ever mailed.
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_subscribers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            token         TEXT    NOT NULL,
            email         TEXT    NOT NULL,
            confirm_token TEXT    NOT NULL,
            unsub_token   TEXT    NOT NULL,
            confirmed_at  INTEGER,
            created_at    INTEGER NOT NULL
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_subs "
                "ON tour_subscribers(token, email)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_subs_confirm "
                "ON tour_subscribers(confirm_token)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_subs_unsub "
                "ON tour_subscribers(unsub_token)")
    # One row per stage announced. Mail cannot be recalled, so a stage that has
    # been sent about is never sent about again.
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_stage_notified (
            token      TEXT    NOT NULL,
            stage_id   INTEGER NOT NULL,
            sent_at    INTEGER NOT NULL,
            sent_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (token, stage_id)
        )
    """)
    _migrate_legacy_tours_to_attempts(con)
    con.commit()


def _migrate_legacy_tours_to_attempts(con):
    """One-time, idempotent: convert legacy tours (which carried a single global
    start_date/end_date) into the per-user attempts model. For each such tour we
    mark it public and create one attempt spanning the old dates for the creator
    and for every other user with an activity inside that window. Tours created
    under the new model store empty dates and are skipped."""
    try:
        legacy = con.execute(
            "SELECT id, created_by, start_date, end_date FROM tours "
            "WHERE start_date IS NOT NULL AND start_date != '' "
            "AND end_date IS NOT NULL AND end_date != ''"
        ).fetchall()
    except Exception:
        return

    for tour_id, creator, sd, ed in legacy:
        # Skip if already migrated (an attempt already exists for this tour).
        if con.execute(
            "SELECT 1 FROM tour_attempts WHERE tour_id=? LIMIT 1", (tour_id,)
        ).fetchone():
            continue

        con.execute("UPDATE tours SET shared=1 WHERE id=?", (tour_id,))

        users = {creator}
        try:
            sd_ts = int(datetime.fromisoformat(sd).replace(tzinfo=timezone.utc).timestamp())
            ed_ts = int(datetime.fromisoformat(ed).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp())
            for (u,) in con.execute(
                "SELECT DISTINCT user_id FROM activities "
                "WHERE COALESCE(creation_time_override_s, creation_time_s) BETWEEN ? AND ?",
                (sd_ts, ed_ts),
            ).fetchall():
                if u is not None:
                    users.add(u)
        except Exception:
            pass  # e.g. no activities table (unit-test connections)

        for u in users:
            cur = con.execute(
                "INSERT INTO tour_attempts (tour_id, user_id, start_date, end_date, created_at) "
                "VALUES (?,?,?,?,?)",
                (tour_id, u, sd, ed, int(_time.time())),
            )
            con.execute(
                "UPDATE tour_shares SET attempt_id=? "
                "WHERE tour_id=? AND user_id=? AND attempt_id IS NULL",
                (cur.lastrowid, tour_id, u),
            )


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

    def _pt_coords(pt):
        """Return (lat, lon, alt_m) for a trkpt/rtept, or None if invalid."""
        try:
            lat = float(pt.get("lat", 0) or 0)
            lon = float(pt.get("lon", 0) or 0)
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        if lat == 0.0 and lon == 0.0:
            return None
        ele_el = _child(pt, "ele")
        alt_m = float(ele_el.text) if ele_el is not None and ele_el.text else 0.0
        return (lat, lon, alt_m)

    raw: list[tuple[float, float, float]] = []

    # Prefer <trk> (track); fall back to <rte> (route). Some exporters (e.g.
    # gpx.py route exports) emit <rte>/<rtept> instead of <trk>/<trkseg>/<trkpt>.
    trk = _child(root, "trk")
    if trk is not None:
        name_el = _child(trk, "name")
        for child in trk:
            if _tag(child) == "trkseg":
                for pt in child:
                    if _tag(pt) == "trkpt":
                        c = _pt_coords(pt)
                        if c is not None:
                            raw.append(c)
    else:
        rte = _child(root, "rte")
        if rte is None:
            raise ValueError("No <trk> or <rte> element found")
        name_el = _child(rte, "name")
        for pt in rte:
            if _tag(pt) == "rtept":
                c = _pt_coords(pt)
                if c is not None:
                    raw.append(c)

    name = (name_el.text or "").strip() if name_el is not None else ""
    if not name:
        base = filename.rsplit(".", 1)[0] if "." in filename else filename
        name = base.replace("_", " ").replace("-", " ").strip() or "Stage"

    if not raw:
        raise ValueError("No valid track points found")

    return {"name": name, **_compute_route_stats(raw)}


def _compute_route_stats(raw: list) -> dict:
    """Reduce a list of (lat, lon, alt_m) track points to a stage's route stats:
    {distance_mi, climb_ft, start_lat, start_lon, points}. Altitude is
    exponentially smoothed to suppress noise; points are downsampled to ≤800 and
    stored with altitude in feet (matching the DB convention). Assumes raw is
    non-empty."""
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
        "distance_mi": cum_dist_mi,
        "climb_ft":    climb_ft,
        "start_lat":   raw[0][0],
        "start_lon":   raw[0][1],
        # store alt in ft (matching DB convention)
        "points":      [(lat, lon, alt_m * M_TO_FT) for lat, lon, alt_m in pts],
    }


# ── Stage-to-activity matching ────────────────────────────────────────────────

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
    attrs = parse_attrs(attrs_json)
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


def _stage_segment_groups(con, stages: list) -> list:
    """Group stages that share BOTH their start and end location — i.e. alternate
    routes of the same segment. The first stage (in list order) with a given
    [start, end] anchors the group; any later stage matching it is an alternate,
    regardless of length. Returns a list of groups (each a list of stage dicts);
    most groups hold a single stage.

    Mirrors the frontend `_stageSegmentGroups`. Alternate routes often diverge
    for a short stretch at the start or finish (a different exit out of town)
    before rejoining the shared path, so rather than compare only each route's
    single first/last point we match a terminal point against a small WINDOW of
    the other route's early/late points — the tracks converge, so this matches
    even when the recorded start points are a couple hundred metres apart.
    Sharing endpoints alone isn't enough, though: an alternate must also RETRACE
    at least half of the original (>= 50% of the anchor's points lie on the
    candidate), so two genuinely different stages that merely begin and end near
    the same place stay separate.
    `stages` is a list of dicts (in stage order) each with id, distance_mi,
    start_lat, start_lon.

    A stage's stored `alt_override` forces the call regardless of geometry:
    0 = always its own segment, 1 = always an alternate of the preceding stage.
    """
    _WIN = 15   # points at each end to scan for convergence
    _OVERLAP_MIN = 0.5  # alternate must retrace >= this fraction of the original
    _fcache: dict = {}
    _lcache: dict = {}
    _acache: dict = {}
    _ocache: dict = {}

    def _first_pts(sid):
        if sid not in _fcache:
            rows = con.execute(
                "SELECT lat, lon FROM tour_stage_points WHERE stage_id=? ORDER BY seq ASC LIMIT ?",
                (sid, _WIN),
            ).fetchall()
            _fcache[sid] = [(r[0], r[1]) for r in rows]
        return _fcache[sid]

    def _last_pts(sid):
        if sid not in _lcache:
            rows = con.execute(
                "SELECT lat, lon FROM tour_stage_points WHERE stage_id=? ORDER BY seq DESC LIMIT ?",
                (sid, _WIN),
            ).fetchall()
            _lcache[sid] = [(r[0], r[1]) for r in rows]
        return _lcache[sid]

    def _all_pts(sid):
        if sid not in _acache:
            rows = con.execute(
                "SELECT lat, lon FROM tour_stage_points WHERE stage_id=? ORDER BY seq ASC",
                (sid,),
            ).fetchall()
            pts = [(r[0], r[1]) for r in rows]
            step = max(1, len(pts) // 150)   # cap the pairwise work
            _acache[sid] = pts[::step]
        return _acache[sid]

    def _override(sid):
        if sid not in _ocache:
            r = con.execute(
                "SELECT alt_override FROM tour_stages WHERE id=?", (sid,)
            ).fetchone()
            _ocache[sid] = r[0] if r else None
        return _ocache[sid]

    def _start_pt(s):
        if s.get("start_lat") is not None and s.get("start_lon") is not None:
            return (s["start_lat"], s["start_lon"])
        fp = _first_pts(s["id"])
        return fp[0] if fp else None

    def _end_pt(sid):
        lp = _last_pts(sid)          # ordered DESC → first element is the true last point
        return lp[0] if lp else None

    def _near(a, b):
        if not a or not b:
            return False
        d_lat = (a[0] - b[0]) * 111000
        d_lon = (a[1] - b[1]) * 111000 * math.cos(math.radians(a[0]))
        return math.hypot(d_lat, d_lon) < 150   # metres

    def _near_any(pt, arr):
        return pt is not None and any(_near(pt, q) for q in arr)

    def _overlap_frac(orig_sid, cand_sid):
        """Fraction of the ORIGINAL route's points that lie on (near) the candidate."""
        orig, cand = _all_pts(orig_sid), _all_pts(cand_sid)
        if not orig or not cand:
            return 0.0
        return sum(1 for p in orig if _near_any(p, cand)) / len(orig)

    def _same_seg(a, b):
        starts = _near_any(_start_pt(a), _first_pts(b["id"])) or _near_any(_start_pt(b), _first_pts(a["id"]))
        ends   = _near_any(_end_pt(a["id"]), _last_pts(b["id"])) or _near_any(_end_pt(b["id"]), _last_pts(a["id"]))
        if not (starts and ends):
            return False
        # a is the later candidate, b the group anchor (original); the alternate
        # (a) must retrace at least half of the original (b).
        return _overlap_frac(b["id"], a["id"]) >= _OVERLAP_MIN

    # Anchor each group on the first occurrence of its [start, end]; a later
    # stage matching that anchor (and retracing it) is an alternate route.
    # `alt_override` forces the call: 0 → own group, 1 → join the previous
    # stage's group.
    groups: list = []
    group_of: dict = {}
    prev = None
    for s in stages:
        ov = _override(s["id"])
        if ov == 0:
            grp = None
        elif ov == 1 and prev is not None:
            grp = group_of.get(prev["id"])
        else:
            grp = next((g for g in groups if _same_seg(s, g[0])), None)
        if grp is not None:
            grp.append(s)
        else:
            grp = [s]
            groups.append(grp)
        group_of[s["id"]] = grp
        prev = s
    return groups


import re as _re

_LEADING_NUM_RE = _re.compile(r"^\s*(\d+)")               # "3 Alpe d'Huez"
_STAGE_WORD_RE  = _re.compile(r"\bstage\s*(\d+)", _re.I)  # "Stage 3" / "stage 3"
_CODE_PREFIX_RE = _re.compile(r"^\s*([A-Za-z]{1,4})\s*(\d+)")  # "CdP 3", "TdF15"


def _stage_points_raw(con, stage_id: int) -> list:
    """[(lat, lon, alt_ft)] for a stage, in route order."""
    return con.execute(
        "SELECT lat, lon, alt_ft FROM tour_stage_points WHERE stage_id=? ORDER BY seq",
        (stage_id,),
    ).fetchall()


def _format_route_block(profile: dict, places: list) -> str:
    """Render route terrain + place names as prompt context. Empty when the
    stage has no usable route, so the prompt simply omits the section."""
    lines = []
    if places:
        lines.append("Places along the route, in order: "
                     + " -> ".join(p["name"] for p in places))
    if profile.get("high_ft") is not None:
        lines.append(
            f"Elevation: starts {profile['start_ft']:.0f}ft, ends {profile['end_ft']:.0f}ft, "
            f"low {profile['low_ft']:.0f}ft, high {profile['high_ft']:.0f}ft "
            f"reached at mile {profile['high_at_mi']:.1f}"
        )
    climbs = profile.get("climbs") or []
    if climbs:
        lines.append("Significant climbs (sustained gains of 300ft or more):")
        for c in climbs:
            lines.append(
                f"  - starts at mile {c['start_mi']:.1f}, {c['len_mi']:.1f}mi long, "
                f"{c['gain_ft']:.0f}ft gain, averaging {c['grade_pct']:.1f}% grade, "
                f"topping out at {c['top_ft']:.0f}ft"
            )
    elif profile.get("distance_mi"):
        lines.append("No sustained climbs over 300ft — the route is comparatively steady.")
    return "\n".join(lines)


async def _stage_route_context(db_path: str, stage_id: int) -> str:
    """Terrain profile + reverse-geocoded place names for a stage route.

    Geocoding is a rate-limited network call, so this is only ever run on a
    cache miss, immediately before generating a summary.
    """
    con = sqlite3.connect(db_path, timeout=10)
    try:
        pts = _stage_points_raw(con, stage_id)
    finally:
        con.close()
    if len(pts) < 2:
        return ""

    profile = _stage_route_profile(pts)
    places = []
    try:
        from app.routers.weather import fetch_location_points
        places = await fetch_location_points(
            [{"lat": p[0], "lon": p[1]} for p in pts if p[0] is not None]
        )
    except Exception:
        pass
    return _format_route_block(profile, places)


def _stage_route_profile(pts: list) -> dict:
    """Terrain facts for a stage route, derived from its own points.

    pts is [(lat, lon, alt_ft)]. Returns distance, elevation extremes, and the
    significant climbs (>=300ft sustained gain), each with its distance from the
    start so a summary can say *where* on the stage the hard part falls.
    """
    clean = [p for p in pts if p[0] is not None and p[1] is not None]
    if len(clean) < 2:
        return {}

    # Cumulative distance at each point, in miles.
    cum, total_m = [0.0], 0.0
    for a, b in zip(clean, clean[1:]):
        total_m += _haversine_m(a[0], a[1], b[0], b[1])
        cum.append(total_m * M_TO_MI)

    alts = [p[2] for p in clean]
    if not any(a is not None for a in alts):
        return {"distance_mi": cum[-1], "climbs": []}

    # Carry the last known elevation across gaps so a dropout isn't a cliff.
    filled, last = [], None
    for a in alts:
        last = a if a is not None else last
        filled.append(last if last is not None else 0.0)

    # Smooth lightly — raw GPS elevation is noisy enough to invent climbs.
    smooth = []
    for i in range(len(filled)):
        w = filled[max(0, i - 2):i + 3]
        smooth.append(sum(w) / len(w))

    hi = max(range(len(smooth)), key=lambda i: smooth[i])
    lo = min(range(len(smooth)), key=lambda i: smooth[i])

    # Walk the profile, banking a climb whenever a sustained rise ends. A dip of
    # more than 100ft closes the current climb; smaller ones are noise.
    climbs, start_i, peak_i = [], 0, 0
    for i in range(1, len(smooth)):
        if smooth[i] <= smooth[start_i]:
            # At or below the base — the climb hasn't started yet, so don't let
            # flat or descending ground inflate its length and dilute its grade.
            start_i = peak_i = i
        elif smooth[i] >= smooth[peak_i]:
            peak_i = i
        elif smooth[peak_i] - smooth[i] > 100:
            gain = smooth[peak_i] - smooth[start_i]
            if gain >= 300:
                climbs.append(_climb_dict(cum, smooth, start_i, peak_i))
            start_i = peak_i = i
    gain = smooth[peak_i] - smooth[start_i]
    if gain >= 300:
        climbs.append(_climb_dict(cum, smooth, start_i, peak_i))

    return {
        "distance_mi": cum[-1],
        "high_ft":     smooth[hi],
        "high_at_mi":  cum[hi],
        "low_ft":      smooth[lo],
        "start_ft":    smooth[0],
        "end_ft":      smooth[-1],
        "climbs":      sorted(climbs, key=lambda c: c["gain_ft"], reverse=True)[:4],
    }


def _climb_dict(cum: list, smooth: list, i0: int, i1: int) -> dict:
    gain_ft = smooth[i1] - smooth[i0]
    len_mi  = cum[i1] - cum[i0]
    return {
        "start_mi": cum[i0],
        "len_mi":   len_mi,
        "gain_ft":  gain_ft,
        "top_ft":   smooth[i1],
        # Average gradient over the climb; 5280 ft per mile.
        "grade_pct": (gain_ft / (len_mi * 5280) * 100) if len_mi > 0 else 0.0,
    }


def _stage_display_num(stages: list) -> Callable[[int], int]:
    """Return a fn mapping a stage's stage_num to the number to *display* for it.
    When one naming scheme matches EVERY stage name the embedded number is used;
    otherwise it falls back to list order (stage_num). Schemes, tried in order:
      1. a leading number ("3 Alpe d'Huez");
      2. a "Stage N" reference anywhere ("Stage 3");
      3. the SAME 1-4 letter code + a number at the start of every name
         ("CdP 1", "CdP 2" → 1, 2; mixed codes don't qualify).
    Keeps AI-summary/advice stage references aligned with the tour pages, which
    apply the same rule client-side (see tour_stage.js — keep the two in sync)."""
    def _by_regex(pat):
        ms = [pat.search(s.get("name") or "") for s in stages]
        if not (stages and all(ms)):
            return None
        return {s["stage_num"]: int(m.group(1)) for s, m in zip(stages, ms)}

    def _by_code_prefix():
        ms = [_CODE_PREFIX_RE.match(s.get("name") or "") for s in stages]
        if not (stages and all(ms)):
            return None
        if len({m.group(1).lower() for m in ms}) != 1:   # code must be identical
            return None
        return {s["stage_num"]: int(m.group(2)) for s, m in zip(stages, ms)}

    for scheme in (lambda: _by_regex(_LEADING_NUM_RE),
                   lambda: _by_regex(_STAGE_WORD_RE),
                   _by_code_prefix):
        parsed = scheme()
        if parsed is not None:
            return lambda n, _p=parsed: _p.get(n, n)
    return lambda n: n


def _collapse_alternate_stages(con, stages: list, prefer_id: Optional[int] = None) -> list:
    """Collapse each same-segment group of stages down to a single representative,
    so alternate routes count once. The representative is the first (original)
    stage of the group, unless `prefer_id` matches a stage in the group (then that
    one is kept — used so a stage never disappears from its own summary/advice).
    Returns the kept stages, in order.
    """
    reps: list = []
    for g in _stage_segment_groups(con, stages):
        rep = next((s for s in g if s["id"] == prefer_id), None) or g[0]
        reps.append(rep)
    return reps


# ── Attempts (per-user tracking windows) ──────────────────────────────────────

def _list_attempts(con, tour_id: int, user_id: int) -> list:
    """A user's attempts for a tour, ordered by start date ascending."""
    rows = con.execute(
        "SELECT id, start_date, end_date FROM tour_attempts "
        "WHERE tour_id=? AND user_id=? ORDER BY start_date ASC, id ASC",
        (tour_id, user_id),
    ).fetchall()
    return [{"id": r[0], "start_date": r[1], "end_date": r[2]} for r in rows]


def _resolve_attempt(attempts: list, requested_id: Optional[int]) -> Optional[dict]:
    """Pick the active attempt: the requested one if it belongs to the user,
    otherwise the most recent (latest start date). None if unsubscribed."""
    if not attempts:
        return None
    if requested_id is not None:
        for a in attempts:
            if a["id"] == requested_id:
                return a
    return attempts[-1]


def _attempt_window(attempts: list, attempt: dict) -> tuple:
    """Return (start_date, end_date) strings bounding an attempt's activities,
    per the Section-4 matching rules. `attempts` is the user's full ordered list
    (needed to find the subsequent attempt for open-ended windows)."""
    from datetime import date as _date, timedelta

    start = attempt["start_date"]
    if attempt.get("end_date"):
        return start, attempt["end_date"]

    # Open-ended: bounded by the next attempt's start (exclusive), if any.
    later = [a for a in attempts
             if a["start_date"] > start or (a["start_date"] == start and a["id"] > attempt["id"])]
    if later:
        nxt = min(later, key=lambda a: (a["start_date"], a["id"]))
        try:
            cap = _date.fromisoformat(nxt["start_date"]) - timedelta(days=1)
            return start, cap.isoformat()
        except ValueError:
            return start, start
    # No end and no subsequent attempt: match everything up to and incl. start.
    return "1900-01-01", start


def _estimate_stage_date(attempt: dict, attempts: list, stage_num: int, total_stages: int):
    """Best-guess calendar date a rider reaches a stage, anchored on their
    selected attempt. Interpolates evenly across the attempt's window when an end
    is known (explicit end, or capped by a later attempt); otherwise falls back
    to one-stage-per-day from the start. Returns a date, or None if unparseable."""
    from datetime import date as _date, timedelta
    try:
        sd = _date.fromisoformat(attempt["start_date"])
    except (ValueError, TypeError):
        return None

    ed = None
    if attempt.get("end_date"):
        try:
            ed = _date.fromisoformat(attempt["end_date"])
        except ValueError:
            ed = None
    else:
        later = [a for a in attempts
                 if a["start_date"] > attempt["start_date"]
                 or (a["start_date"] == attempt["start_date"] and a["id"] > attempt["id"])]
        if later:
            nxt = min(later, key=lambda a: (a["start_date"], a["id"]))
            try:
                ed = _date.fromisoformat(nxt["start_date"]) - timedelta(days=1)
            except ValueError:
                ed = None

    if ed is not None:
        tour_days = (ed - sd).days
        offset = round((stage_num - 1) * tour_days / max(total_stages - 1, 1)) if total_stages > 1 else 0
        return sd + timedelta(days=offset)
    return sd + timedelta(days=(stage_num - 1))


def _completions_for_user(con, tour_id: int, user_id: int, stages: list,
                          requested_attempt_id: Optional[int]) -> tuple:
    """Compute stage completions for a user's selected attempt. Returns
    (completions, attempts, selected_attempt_id). Unsubscribed users (no
    attempts) get all-None completions and no matching."""
    attempts = _list_attempts(con, tour_id, user_id)
    attempt  = _resolve_attempt(attempts, requested_attempt_id)
    if attempt is None:
        return {s["id"]: None for s in stages}, attempts, None
    sd, ed = _attempt_window(attempts, attempt)
    completions = _global_stage_matching(con, user_id, sd, ed, stages)
    return completions, attempts, attempt["id"]


def _place_attempt(others: list, start: str, end: Optional[str]) -> tuple:
    """Validate a proposed attempt (start/end ISO strings) against the user's
    OTHER attempts and apply the overlap-resolution rules. Returns
    (error_or_None, cap) where cap is (attempt_id, new_end_iso) for a prior
    open-ended attempt that must be auto-capped (Rule 2), or None."""
    from datetime import date as _date, timedelta

    try:
        s = _date.fromisoformat(start)
    except (ValueError, TypeError):
        return "Invalid start date.", None
    e = None
    if end:
        try:
            e = _date.fromisoformat(end)
        except ValueError:
            return "Invalid end date.", None
        if e < s:
            return "End date must be on or after the start date.", None

    parsed = []
    for a in others:
        try:
            a_s = _date.fromisoformat(a["start_date"])
        except (ValueError, TypeError):
            continue
        a_e = None
        if a.get("end_date"):
            try:
                a_e = _date.fromisoformat(a["end_date"])
            except ValueError:
                a_e = None
        parsed.append((a_s, a_e))

    for a_s, a_e in parsed:
        if a_s == s:
            return "An attempt already starts on that date.", None
        # Rule 1 — the intersect reject (against attempts with an explicit end).
        if a_e is not None and a_s <= s <= a_e:
            return ("That start date falls inside an existing attempt "
                    f"({a_s.isoformat()} to {a_e.isoformat()})."), None

    nxt = min((a_s for a_s, _ in parsed if a_s > s), default=None)
    prev = max(((a_s, a_e) for a_s, a_e in parsed if a_s < s), default=None,
               key=lambda p: p[0])

    # Rule 4 — future collision: an explicit end may not reach the next start.
    if e is not None and nxt is not None and e >= nxt:
        return (f"End date must be before the next attempt's start ({nxt.isoformat()})."), None

    # Rule 2 — auto-cap a prior open-ended attempt at (newStart - 1 day).
    cap = None
    if prev is not None and prev[1] is None:
        cap_date = (s - timedelta(days=1)).isoformat()
        for a in others:
            if a["start_date"] == prev[0].isoformat() and not a.get("end_date"):
                cap = (a["id"], cap_date)
                break
    return None, cap


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
    shared:     int               = Form(default=0),
    files:      List[UploadFile]  = File(...),
):
    uid = require_user(request)

    if not title.strip():
        raise HTTPException(400, "Title is required")
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
            (uid, title.strip(), "", "", int(_time.time()), shared),
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


def _activity_to_stage(con, uid: int, activity_id: int) -> dict:
    """Build a tour-stage dict from an existing activity's GPS track. The stage
    name is the activity's title; distance/climb/route come from its points (same
    computation as a GPX-derived stage). Activities without a usable track fall
    back to their stored distance and totalClimb, with no route points."""
    row = con.execute(
        "SELECT name, local_name, distance_mi, start_lat, start_lon, attributes_json "
        "FROM activities WHERE id=? AND user_id=?",
        (activity_id, uid),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Activity {activity_id} not found")
    name_col, local_name, distance_mi, start_lat, start_lon, attrs_json = row
    attrs = parse_attrs(attrs_json)
    name = (local_name or attrs.get("name") or name_col or "Stage").strip() or "Stage"

    rows = con.execute(
        f"SELECT latitude_e7, longitude_e7, orig_altitude_cm "
        f"FROM points WHERE track_id=? AND {_VALID_GPS} "
        f"ORDER BY wall_clock_delta_s ASC, active_time_delta_s ASC",
        (activity_id,),
    ).fetchall()
    raw = [(r[0], r[1], (r[2] or 0) / 100.0) for r in rows]  # orig_altitude_cm → m

    if len(raw) >= 2:
        return {"name": name, **_compute_route_stats(raw)}

    # No usable GPS track — keep the stage but carry only stored stats.
    return {
        "name":        name,
        "distance_mi": float(distance_mi or 0.0),
        "climb_ft":    _fa(attrs, "totalClimb") or 0.0,
        "start_lat":   start_lat,
        "start_lon":   start_lon,
        "points":      [],
    }


@router.post("/tours/from-activities")
async def create_tour_from_activities(
    request:      Request,
    title:        str = Form(...),
    shared:       int = Form(default=0),
    activity_ids: str = Form(...),
):
    """Create a tour whose stages are existing activities, in the given order.
    Stages are stored as uncompleted routes (derived from each activity's GPS
    track); per-user completion still comes from the normal attempt/matching
    flow once a tracking window is created via Manage."""
    uid = require_user(request)

    if not title.strip():
        raise HTTPException(400, "Title is required")
    try:
        ids = json.loads(activity_ids)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid activity_ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "At least one activity is required")
    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid activity_ids")

    con = sqlite3.connect(db_getter().path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        _ensure_tables(con)

        stages = [_activity_to_stage(con, uid, aid) for aid in ids]

        cur = con.execute(
            "INSERT INTO tours (created_by, title, start_date, end_date, created_at, shared) "
            "VALUES (?,?,?,?,?,?)",
            (uid, title.strip(), "", "", int(_time.time()), shared),
        )
        tour_id = cur.lastrowid

        for i, s in enumerate(stages):
            cur2 = con.execute(
                "INSERT INTO tour_stages "
                "(tour_id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon) "
                "VALUES (?,?,?,?,?,?,?)",
                (tour_id, i + 1, s["name"],
                 s["distance_mi"], s["climb_ft"],
                 s["start_lat"], s["start_lon"]),
            )
            stage_id = cur2.lastrowid
            if s["points"]:
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


@router.post("/tours/{tour_id}/stages")
async def add_tour_stages(
    tour_id: int,
    request: Request,
    files:   List[UploadFile] = File(...),
):
    """Append a batch of GPX files as new stages, continuing the stage_num
    sequence. Used by the batched uploader so large tours don't require one
    giant multipart POST (which iPad Safari can fail to build)."""
    uid = require_user(request)

    real_files = [f for f in (files or []) if f.filename]
    if not real_files:
        raise HTTPException(400, "At least one GPX file is required")

    stages = []
    for i, f in enumerate(real_files):
        data = await f.read()
        try:
            s = _parse_gpx_route(data, f.filename or f"Stage {i + 1}")
        except ValueError as e:
            raise HTTPException(400, f"File '{f.filename}': {e}")
        stages.append(s)

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
            raise HTTPException(403, "Only the tour creator can add stages")

        base = con.execute(
            "SELECT COALESCE(MAX(stage_num), 0) FROM tour_stages WHERE tour_id=?",
            (tour_id,),
        ).fetchone()[0]

        for j, s in enumerate(stages):
            cur = con.execute(
                "INSERT INTO tour_stages "
                "(tour_id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon) "
                "VALUES (?,?,?,?,?,?,?)",
                (tour_id, base + j + 1, s["name"],
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
        return JSONResponse({"added": len(stages)}, status_code=201)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.get("/tours/{tour_id}")
async def get_tour(tour_id: int, request: Request,
                   match_user_id: Optional[int] = Query(default=None),
                   attempt_id: Optional[int] = Query(default=None)):
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
        # Private tours are visible only to their creator.
        if row[1] != uid and not row[5]:
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
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon, alt_override "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()

        stages = [{
            "id":           sr[0],
            "stage_num":    sr[1],
            "name":         sr[2],
            "distance_mi":  sr[3],
            "climb_ft":     sr[4],
            "start_lat":    sr[5],
            "start_lon":    sr[6],
            "alt_override": sr[7],
            "completion":   None,
        } for sr in stage_rows]

        match_uid = match_user_id if match_user_id is not None else uid
        completions, attempts, sel_attempt_id = _completions_for_user(
            con, tour_id, match_uid, stages, attempt_id,
        )
        for stage in stages:
            stage["completion"] = completions.get(stage["id"])

        tour["stages"]      = stages
        tour["attempts"]    = attempts
        tour["attempt_id"]  = sel_attempt_id
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
    attempt_id: Optional[int] = Query(default=None),
):
    """GPS points from matched actual activities for completed stages, keyed by stage_id."""
    uid = require_user(request)
    actual_uid = match_user_id if match_user_id is not None else uid

    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        if not con.execute("SELECT 1 FROM tours WHERE id=?", (tour_id,)).fetchone():
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

        completions, _, _ = _completions_for_user(con, tour_id, actual_uid, stages, attempt_id)
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

        # Hard-delete the tour and every user's attempts + share links for it.
        con.execute("DELETE FROM tour_attempts WHERE tour_id=?", (tour_id,))
        con.execute(
            "DELETE FROM tour_subscribers WHERE token IN "
            "(SELECT token FROM tour_shares WHERE tour_id=?)", (tour_id,))
        con.execute(
            "DELETE FROM tour_stage_notified WHERE token IN "
            "(SELECT token FROM tour_shares WHERE tour_id=?)", (tour_id,))
        con.execute("DELETE FROM tour_shares WHERE tour_id=?", (tour_id,))
        con.execute("DELETE FROM tours WHERE id=?", (tour_id,))
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()


def _tour_visible_or_404(con, tour_id: int, uid: int) -> tuple:
    """Return (created_by, shared) for a tour the user may see, else raise."""
    row = con.execute(
        "SELECT created_by, shared FROM tours WHERE id=?", (tour_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Tour not found")
    created_by, shared = row[0], bool(row[1])
    if created_by != uid and not shared:
        raise HTTPException(404, "Tour not found")
    return created_by, shared


@router.get("/tours/{tour_id}/attempts")
async def list_tour_attempts(tour_id: int, request: Request,
                             user_id: Optional[int] = Query(default=None)):
    """List a user's attempts for a tour. Peers' attempts are only visible on
    public tours (used for read-only peer browsing)."""
    uid = require_user(request)
    target = user_id if user_id is not None else uid
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        created_by, shared = _tour_visible_or_404(con, tour_id, uid)
        if target != uid and not shared:
            raise HTTPException(403, "Not authorised")
        return JSONResponse({
            "attempts":  _list_attempts(con, tour_id, target),
            "read_only": target != uid,
        })
    finally:
        con.close()


@router.post("/tours/{tour_id}/attempts")
async def add_tour_attempt(tour_id: int, request: Request,
                           start_date: str = Form(...),
                           end_date:   str = Form(default="")):
    """Subscribe / add an attempt for the current user (overlap engine applies)."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        _ensure_tables(con)
        _tour_visible_or_404(con, tour_id, uid)

        others = _list_attempts(con, tour_id, uid)
        err, cap = _place_attempt(others, start_date, end_date or None)
        if err:
            raise HTTPException(400, err)
        if cap:
            con.execute("UPDATE tour_attempts SET end_date=? WHERE id=?", (cap[1], cap[0]))
        cur = con.execute(
            "INSERT INTO tour_attempts (tour_id, user_id, start_date, end_date, created_at) "
            "VALUES (?,?,?,?,?)",
            (tour_id, uid, start_date, end_date or None, int(_time.time())),
        )
        con.commit()
        return JSONResponse({"id": cur.lastrowid}, status_code=201)
    finally:
        con.close()


@router.put("/tours/{tour_id}/attempts/{attempt_id}")
async def edit_tour_attempt(tour_id: int, attempt_id: int, request: Request,
                            start_date: str = Form(...),
                            end_date:   str = Form(default="")):
    """Edit the current user's attempt dates (overlap engine applies)."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT user_id FROM tour_attempts WHERE id=? AND tour_id=?",
            (attempt_id, tour_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Attempt not found")
        if row[0] != uid:
            raise HTTPException(403, "Not authorised")

        others = [a for a in _list_attempts(con, tour_id, uid) if a["id"] != attempt_id]
        err, cap = _place_attempt(others, start_date, end_date or None)
        if err:
            raise HTTPException(400, err)
        if cap:
            con.execute("UPDATE tour_attempts SET end_date=? WHERE id=?", (cap[1], cap[0]))
        con.execute(
            "UPDATE tour_attempts SET start_date=?, end_date=? WHERE id=?",
            (start_date, end_date or None, attempt_id),
        )
        con.commit()
        return JSONResponse({"id": attempt_id})
    finally:
        con.close()


@router.delete("/tours/{tour_id}/attempts/{attempt_id}")
async def delete_tour_attempt(tour_id: int, attempt_id: int, request: Request):
    """Hard-delete the current user's attempt (and any share link to it)."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT user_id FROM tour_attempts WHERE id=? AND tour_id=?",
            (attempt_id, tour_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Attempt not found")
        if row[0] != uid:
            raise HTTPException(403, "Not authorised")
        con.execute("DELETE FROM tour_attempts WHERE id=?", (attempt_id,))
        con.execute(
            "DELETE FROM tour_shares WHERE tour_id=? AND user_id=? AND attempt_id=?",
            (tour_id, uid, attempt_id),
        )
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()


@router.get("/tours/{tour_id}/subscriber-count")
async def tour_subscriber_count(tour_id: int, request: Request):
    """Number of OTHER users with at least one attempt (for delete warnings)."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _tour_visible_or_404(con, tour_id, uid)
        n = con.execute(
            "SELECT COUNT(DISTINCT user_id) FROM tour_attempts WHERE tour_id=? AND user_id!=?",
            (tour_id, uid),
        ).fetchone()[0]
        return JSONResponse({"others": n})
    finally:
        con.close()


@router.patch("/tours/{tour_id}/visibility")
async def set_tour_visibility(tour_id: int, request: Request, body: dict = Body(...)):
    """Creator-only: toggle a tour public/private."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute("SELECT created_by FROM tours WHERE id=?", (tour_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")
        if row[0] != uid:
            raise HTTPException(403, "Only the tour creator can change visibility")
        shared = 1 if body.get("public") else 0
        con.execute("UPDATE tours SET shared=? WHERE id=?", (shared, tour_id))
        con.commit()
        return JSONResponse({"public": bool(shared)})
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
    shared:      int                       = Form(default=0),
    stage_order: str                       = Form(default="[]"),
    files:       List[UploadFile]           = File(default=[]),
):
    """Edit an existing tour: update metadata, reorder/remove/add stages."""
    uid = require_user(request)

    if not title.strip():
        raise HTTPException(400, "Title is required")

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
            "UPDATE tours SET title=?, shared=?, ai_summary=NULL WHERE id=?",
            (title.strip(), shared, tour_id),
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

        # Normalise the per-stage alternate-route override to NULL / 0 / 1.
        def _alt_ov(item):
            v = item.get("alt_override")
            return v if v in (0, 1) else None

        # Apply the new ordering: renumber existing stages, insert new ones
        for pos, item in enumerate(order):
            stage_num = pos + 1
            if item.get("type") == "existing":
                con.execute(
                    "UPDATE tour_stages SET stage_num=?, alt_override=? WHERE id=? AND tour_id=?",
                    (stage_num, _alt_ov(item), int(item["id"]), tour_id),
                )
            elif item.get("type") == "new":
                idx = int(item.get("idx", 0))
                if idx >= len(new_stages):
                    continue
                s = new_stages[idx]
                cur = con.execute(
                    "INSERT INTO tour_stages "
                    "(tour_id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon, alt_override) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (tour_id, stage_num, s["name"],
                     s["distance_mi"], s["climb_ft"],
                     s["start_lat"], s["start_lon"], _alt_ov(item)),
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
async def get_stage_forecast(tour_id: int, stage_id: int, request: Request,
                             match_user_id: Optional[int] = Query(default=None),
                             attempt_id: Optional[int] = Query(default=None)):
    """Return an Open-Meteo forecast for the estimated date of an uncompleted
    tour stage. The date is anchored on the viewing user's selected attempt."""
    uid = require_user(request)
    view_uid = match_user_id if match_user_id is not None else uid

    import httpx

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        stage_row = con.execute(
            "SELECT stage_num, start_lat, start_lon "
            "FROM tour_stages WHERE id=? AND tour_id=?",
            (stage_id, tour_id),
        ).fetchone()
        if not stage_row:
            raise HTTPException(404, "Stage not found")
        stage_num, start_lat, start_lon = stage_row
        total_stages = con.execute(
            "SELECT COUNT(*) FROM tour_stages WHERE tour_id=?", (tour_id,)
        ).fetchone()[0]
        attempts = _list_attempts(con, tour_id, view_uid)
        attempt  = _resolve_attempt(attempts, attempt_id)
    finally:
        con.close()

    if start_lat is None or start_lon is None or attempt is None:
        return JSONResponse({"forecast": None, "out_of_range": False})

    stage_date = _estimate_stage_date(attempt, attempts, stage_num, total_stages)
    if stage_date is None:
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


@router.post("/tours/{tour_id}/stages/{stage_id}/route-forecast")
async def stage_route_forecast(tour_id: int, stage_id: int, request: Request):
    """Weather-along-route forecast for a tour stage at a user-chosen start time.
    Reuses the shared route-forecast builder (same engine as /api/forecast)."""
    require_user(request)
    body = await request.json()

    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        name, pts = _load_stage_route(con, tour_id, stage_id)
    finally:
        con.close()

    from app.routers.forecast import build_route_forecast, parse_start_time
    start_dt = parse_start_time(body.get("start_time"))
    return JSONResponse(await build_route_forecast(pts, start_dt, name))


@router.get("/tours/{tour_id}/ai-summary")
async def get_tour_ai_summary(tour_id: int, request: Request, model: Optional[str] = Query(default=None), force: bool = Query(default=False)):
    """Return an AI-generated summary of the entire tour (structure only, no activity data)."""
    import os, httpx
    from app.routers.coach import MODELS, DEFAULT_MODEL, _resolve_model, first_text
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
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
        stages = [{
            "id": r[0], "stage_num": r[1], "name": r[2],
            "distance_mi": r[3], "climb_ft": r[4],
            "start_lat": r[5], "start_lon": r[6],
        } for r in stage_rows]
        disp = _stage_display_num(stages)
        # Exclude alternative routes (adjacent stages sharing both endpoints) so
        # the summary counts one route per segment, matching the tour pages.
        stages = _collapse_alternate_stages(con, stages)
    finally:
        con.close()

    if not stages:
        raise HTTPException(404, "No stages found")

    total_dist  = sum(s["distance_mi"] for s in stages)
    total_climb = sum(s["climb_ft"] for s in stages)
    avg_dist    = total_dist  / len(stages)
    avg_climb   = total_climb / len(stages)

    stage_lines = [
        f"  Stage {disp(s['stage_num'])}: {s['name']} — {s['distance_mi']:.1f}mi, {s['climb_ft']:.0f}ft climb"
        for s in stages
    ]

    prompt = (
        f"Tour: {tour_title}\n"
        f"Dates: {start_date} to {end_date}\n"
        f"Number of stages: {len(stages)}\n"
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
                "model":      _resolve_model(model),
                # Covers thinking + reply; Opus 5 thinks by default.
                "max_tokens": 2000,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    summary = first_text(resp.json())

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
    attempt_id: Optional[int] = Query(default=None),
    model: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
    readonly: bool = Query(default=False),
):
    """Return AI coach advice for an uncompleted tour stage.
    readonly=true returns cached advice only ({"advice": null} if none exists).
    """
    import os, httpx
    from app.routers.coach import MODELS, DEFAULT_MODEL, _resolve_model, first_text
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
    disp = _stage_display_num(stages)
    con2 = sqlite3.connect(db.path, timeout=15)
    try:
        completions, _, _ = _completions_for_user(con2, tour_id, actual_uid, stages, attempt_id)
        # Count one route per segment (exclude alternates); keep THIS stage even
        # if it is itself an alternate. Matching still runs over the full list.
        stat_stages = _collapse_alternate_stages(con2, stages, prefer_id=target["id"])
    finally:
        con2.close()

    n_done = sum(1 for s in stat_stages if completions.get(s["id"]))

    stage_lines = []
    for s in stat_stages:
        comp = completions.get(s["id"])
        marker = " ← UPCOMING" if s["id"] == target["id"] else ""
        done_str = ""
        if comp:
            dur_h = round((comp.get("duration_s") or 0) / 3600, 1)
            clb   = round(comp.get("climb_ft") or 0)
            done_str = f" [DONE: {comp['distance_mi']:.1f}mi, {clb}ft climb, {dur_h}h]"
        stage_lines.append(
            f"  Stage {disp(s['stage_num'])}: {s['name']} — {s['distance_mi']:.1f}mi, {s['climb_ft']:.0f}ft climb{done_str}{marker}"
        )

    # Stages after the target — advice must account for what's still to come.
    upcoming = [s for s in stat_stages if s["stage_num"] > target["stage_num"]][:3]
    if upcoming:
        upcoming_block = (
            "\n\nStages that come AFTER this one (plan energy and recovery accordingly):\n"
            + "\n".join(
                f"  Stage {disp(s['stage_num'])}: {s['name']} — {s['distance_mi']:.1f}mi, {s['climb_ft']:.0f}ft climb"
                for s in upcoming
            )
        )
    else:
        upcoming_block = "\n\n(This is the final stage — the athlete can empty the tank.)"

    # Athlete physiology + recent training trends (age, HR/power, load, recovery).
    from app.coach_analysis import build_athlete_profile_block, build_training_analysis
    profile_block    = build_athlete_profile_block(db, actual_uid)
    analysis_block   = build_training_analysis(db, actual_uid)
    profile_section  = f"\n{profile_block}\n"  if profile_block  else ""
    analysis_section = f"\n{analysis_block}\n" if analysis_block else ""

    prompt = (
        f"Tour: {tour_title} ({start_date} to {end_date})\n"
        f"Progress: {n_done} of {len(stat_stages)} stages completed"
        f"{profile_section}{analysis_section}\n"
        "All stages:\n" + "\n".join(stage_lines) + "\n\n"
        f"The athlete is preparing for Stage {disp(target['stage_num'])}: {target['name']} "
        f"({target['distance_mi']:.1f}mi, {target['climb_ft']:.0f}ft climb)."
        + upcoming_block + "\n\n"
        "Provide 3-6 sentences of specific, actionable coach advice for this UPCOMING stage.\n"
        "- Use the athlete's profile and recent training trends to judge whether their current fitness and "
        "training-load balance leave them fresh or fatigued for this stage.\n"
        "- Factor their age into pacing and how much recovery they need.\n"
        "- Weigh the stage's difficulty against completed stages and cumulative fatigue, AND against the "
        "stages that come afterward, so they don't over-spend today.\n"
        "- Give concrete pacing, nutrition, and effort-management guidance. If they should take an easy or "
        "rest day before or after, say so and how many.\n"
        "Reference specific numbers from the data. Avoid generic platitudes."
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
                "model":      _resolve_model(model),
                # Covers thinking + reply; Opus 5 thinks by default.
                "max_tokens": 2500,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    advice = first_text(resp.json())

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


def _cached_stage_summary(db, tour_id: int, stage_id: int, user_id: int, attempt_id):
    """The cached summary for this stage/user if it is still current, else None.

    "Current" means it was generated against the same completion state — a
    summary written before the ride has nothing to say about how it went.
    """
    con = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT summary, completed FROM tour_stage_ai_summary "
            "WHERE stage_id=? AND user_id=?",
            (stage_id, user_id),
        ).fetchone()
        if not row:
            return None
        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
        stages = [{
            "id": r[0], "stage_num": r[1], "name": r[2],
            "distance_mi": r[3], "climb_ft": r[4],
            "start_lat": r[5], "start_lon": r[6],
        } for r in stage_rows]
        stages = _collapse_alternate_stages(con, stages, prefer_id=stage_id)
        completions, _, _ = _completions_for_user(con, tour_id, user_id, stages, attempt_id)
        is_done = 1 if completions.get(stage_id) else 0
        return row[0] if row[1] == is_done else None
    except Exception:
        return None
    finally:
        con.close()


def _start_summary_job(db, tour_id: int, stage_id: int, user_id: int,
                       attempt_id, api_key: str, model, force: bool) -> None:
    """Generate a stage summary in the background, at most one per stage/user.

    The request that asks for it returns straight away — generation runs for
    tens of seconds, and holding the connection open for that long risks the
    machine scaling to zero underneath it.
    """
    job = (stage_id, user_id)
    if job in _summary_jobs:
        return
    _summary_jobs.add(job)

    async def _run():
        try:
            async with _gate():
                await _generate_stage_summary(
                    db, tour_id, stage_id, user_id, attempt_id, api_key, model, force
                )
        except Exception:
            pass          # the next poll simply asks again
        finally:
            _summary_jobs.discard(job)

    task = asyncio.create_task(_run())
    _summary_tasks.add(task)
    task.add_done_callback(_summary_tasks.discard)


async def _generate_stage_summary(db, tour_id: int, stage_id: int, user_id: int,
                                  attempt_id, api_key: str, model, force: bool) -> str:
    """Summary of one stage for one user, generated on a cache miss and cached.

    The summary leads with the route itself — where it goes, its terrain and
    climbs — and only reports performance once the user has ridden the stage.
    """
    import httpx
    from app.routers.coach import _resolve_model, first_text

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
            "SELECT summary, completed FROM tour_stage_ai_summary "
            "WHERE stage_id=? AND user_id=?",
            (stage_id, user_id),
        ).fetchone()

        all_stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
        all_stages = [{
            "id": r[0], "stage_num": r[1], "name": r[2],
            "distance_mi": r[3], "climb_ft": r[4],
            "start_lat": r[5], "start_lon": r[6],
        } for r in all_stage_rows]
        disp = _stage_display_num(all_stages)
        # Exclude alternative routes so counts/totals reflect one route per
        # segment; keep THIS stage even if it is itself an alternate.
        stages = _collapse_alternate_stages(con, all_stages, prefer_id=stage_id)
        completions, _, _ = _completions_for_user(con, tour_id, user_id, stages, attempt_id)
    finally:
        con.close()

    # A summary written before the rider finished the stage says nothing about
    # how it went, so completing the stage has to invalidate it.
    is_done = 1 if completions.get(stage_id) else 0
    if cache_row and not force and cache_row[1] == is_done:
        return cache_row[0]

    stage_num, stage_name, dist_mi, climb_ft = target_row
    total_stages = len(stages)
    total_dist   = sum(s["distance_mi"] for s in stages)
    total_climb  = sum(s["climb_ft"] for s in stages)
    avg_dist     = total_dist  / total_stages if total_stages else 0
    avg_climb    = total_climb / total_stages if total_stages else 0

    stage_lines = [
        f"  Stage {disp(s['stage_num'])}: {s['name']} — {s['distance_mi']:.1f}mi, {s['climb_ft']:.0f}ft climb"
        + (" ← THIS STAGE" if s["id"] == stage_id else "")
        for s in stages
    ]

    route_block = await _stage_route_context(db.path, stage_id)
    route_section = f"\n\nRoute detail for this stage:\n{route_block}" if route_block else ""

    # Performance only enters the picture once this user has ridden the stage.
    comp = completions.get(stage_id)
    if comp:
        dur_h = (comp.get("duration_s") or 0) / 3600
        perf_bits = [
            f"completed on {comp.get('date')}",
            f"{comp.get('distance_mi') or 0:.1f}mi",
            f"{comp.get('climb_ft') or 0:.0f}ft climb",
        ]
        if dur_h:
            perf_bits.append(f"{dur_h:.1f}h elapsed")
        if comp.get("avg_moving_speed_mph"):
            perf_bits.append(f"{comp['avg_moving_speed_mph']:.1f}mph moving average")
        if comp.get("avg_hr"):
            perf_bits.append(f"avg HR {round(comp['avg_hr'])}bpm")
        if comp.get("avg_power"):
            perf_bits.append(f"avg power {round(comp['avg_power'])}W")
        perf_section = (
            "\n\nThe rider has COMPLETED this stage: " + ", ".join(perf_bits) + "."
            "\nClose with one or two sentences on how that ride went against the "
            "terrain above — where the climbs would have bitten, how the pace reads "
            "for this profile."
        )
        closing = ""
    else:
        perf_section = ""
        closing = (
            "\nThis stage has NOT been ridden yet, so do not invent or imply performance "
            "data — describe the route as it awaits the rider.\n"
        )

    prompt = (
        f"Tour: {tour_title} ({start_date} to {end_date})\n"
        f"Total: {total_stages} stages, {total_dist:.1f}mi, {total_climb:.0f}ft climb\n"
        f"Average per stage: {avg_dist:.1f}mi, {avg_climb:.0f}ft climb\n\n"
        "All stages:\n" + "\n".join(stage_lines)
        + route_section + perf_section + "\n\n"
        f"Describe Stage {disp(stage_num)}: {stage_name} ({dist_mi:.1f}mi, {climb_ft:.0f}ft climb) "
        "for someone about to ride or follow it.\n"
        "Lead with what makes THIS route interesting: the places it passes through, the "
        "terrain and scenery those names imply, where the hard climbing falls and how steep "
        "it gets, the high point, and any standout feature of its shape (a summit finish, a "
        "long valley run-in, a sting in the tail).\n"
        "Name real places and real numbers from the route detail above — miles, gradients, "
        "elevations. Prefer concrete specifics over adjectives.\n"
        "Mention how it compares to the tour average, and where it sits in the tour arc, but "
        "keep that to a single clause — it is context, not the point.\n"
        + closing +
        "Write 4-6 sentences of flowing prose. No bullet points, no headings, no training "
        "or coaching advice."
    )

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      _resolve_model(model or STAGE_SUMMARY_MODEL),
                # Covers thinking + reply; Opus 5 thinks by default.
                "max_tokens": 2500,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )

    if resp.status_code != 200:
        raise HTTPException(502, f"Claude API error: {resp.status_code}")

    summary = first_text(resp.json())
    if not summary:
        # No text block at all — cache nothing rather than an empty card.
        raise HTTPException(502, "Claude API returned no text")

    con2 = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_tables(con2)
        con2.execute(
            "INSERT OR REPLACE INTO tour_stage_ai_summary "
            "(stage_id, user_id, summary, completed) VALUES (?,?,?,?)",
            (stage_id, user_id, summary, is_done),
        )
        con2.commit()
    finally:
        con2.close()

    return summary


@router.get("/tours/{tour_id}/stages/{stage_id}/ai-summary")
async def get_stage_ai_summary(
    tour_id: int,
    stage_id: int,
    request: Request,
    model: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
    attempt_id: Optional[int] = Query(default=None),
    match_user_id: Optional[int] = Query(default=None),
):
    """Return an AI-generated summary of a tour stage in the context of the overall tour."""
    uid = require_user(request)
    # The tour page can view another rider's data; the summary follows suit.
    actual_uid = match_user_id if match_user_id is not None else uid
    db = db_getter()
    api_key = (db.get_user(uid) or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "No Anthropic API key configured")

    if not force:
        cached = _cached_stage_summary(db, tour_id, stage_id, actual_uid, attempt_id)
        if cached:
            return JSONResponse({"summary": cached})

    _start_summary_job(db, tour_id, stage_id, actual_uid, attempt_id, api_key, model, force)
    return JSONResponse({"pending": True}, status_code=202)


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
    """Generate (or return existing) share token for one of this user's attempts.
    The chosen attempt fixes the tracking window shown on the public page."""
    import secrets
    uid = require_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    req_attempt_id = body.get("attempt_id")

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT id FROM tours WHERE id=? AND (created_by=? OR shared=1)", (tour_id, uid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Tour not found")

        attempts = _list_attempts(con, tour_id, uid)
        if not attempts:
            raise HTTPException(400, "Subscribe to this tour before sharing it.")
        attempt = _resolve_attempt(attempts, req_attempt_id)

        existing = con.execute(
            "SELECT token FROM tour_shares WHERE tour_id=? AND user_id=?", (tour_id, uid)
        ).fetchone()
        token = existing[0] if existing else secrets.token_hex(20)
        con.execute(
            "INSERT OR REPLACE INTO tour_shares (tour_id, user_id, token, attempt_id) "
            "VALUES (?,?,?,?)",
            (tour_id, uid, token, attempt["id"]),
        )
        con.commit()
        return JSONResponse({"token": token, "attempt_id": attempt["id"]})
    finally:
        con.close()


@router.delete("/tours/{tour_id}/publish")
async def revoke_tour_publish(tour_id: int, request: Request):
    """Revoke this user's share token for a tour."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        # Subscribers followed this link, so revoking it unsubscribes them.
        con.execute(
            "DELETE FROM tour_subscribers WHERE token IN "
            "(SELECT token FROM tour_shares WHERE tour_id=? AND user_id=?)",
            (tour_id, uid),
        )
        con.execute(
            "DELETE FROM tour_stage_notified WHERE token IN "
            "(SELECT token FROM tour_shares WHERE tour_id=? AND user_id=?)",
            (tour_id, uid),
        )
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
        "wx_tile_url":  _wx_tile_url(),
    })


@router.get("/tours/share/{token}/data")
async def tour_share_data(token: str):
    """Public endpoint — tour info + stages + completions for the share user."""
    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT t.id, t.title, ts.user_id, ts.attempt_id, t.ai_summary "
            "FROM tour_shares ts JOIN tours t ON t.id=ts.tour_id WHERE ts.token=?",
            (token,),
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id, title, share_uid, share_attempt_id, ai_summary = tour_row

        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon, alt_override "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
            (tour_id,),
        ).fetchall()
        stages = [{
            "id":           sr[0],
            "stage_num":    sr[1],
            "name":         sr[2],
            "distance_mi":  sr[3],
            "climb_ft":     sr[4],
            "start_lat":    sr[5],
            "start_lon":    sr[6],
            "alt_override": sr[7],
            "completion":   None,
        } for sr in stage_rows]

        completions, _, _ = _completions_for_user(con, tour_id, share_uid, stages, share_attempt_id)
        for stage in stages:
            stage["completion"] = completions.get(stage["id"])

        # Date range for display: the shared attempt's own window.
        attempts    = _list_attempts(con, tour_id, share_uid)
        attempt     = _resolve_attempt(attempts, share_attempt_id)
        disp_start  = attempt["start_date"] if attempt else ""
        disp_end    = ""
        if attempt:
            win_lo, win_hi = _attempt_window(attempts, attempt)
            disp_end = attempt["end_date"] or (win_hi if win_lo != "1900-01-01" else "")

        return JSONResponse({
            "id":         tour_id,
            "title":      title,
            "start_date": disp_start,
            "end_date":   disp_end,
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
            "SELECT t.id, ts.user_id, ts.attempt_id "
            "FROM tour_shares ts JOIN tours t ON t.id=ts.tour_id WHERE ts.token=?",
            (token,),
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id, share_uid, share_attempt_id = tour_row

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

        completions, _, _ = _completions_for_user(con, tour_id, share_uid, stages, share_attempt_id)
        return JSONResponse(_activity_pts_for_stages(con, stages, completions))
    finally:
        con.close()


@router.get("/tours/share/{token}/stages/{stage_id}/forecast")
async def tour_share_forecast(token: str, stage_id: int):
    """Public endpoint — forecast for an uncompleted stage on a shared tour,
    anchored on the shared attempt's window."""
    import httpx

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT tour_id, user_id, attempt_id FROM tour_shares WHERE token=?", (token,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id, share_uid, share_attempt_id = tour_row

        stage_row = con.execute(
            "SELECT stage_num, start_lat, start_lon "
            "FROM tour_stages WHERE id=? AND tour_id=?",
            (stage_id, tour_id),
        ).fetchone()
        if not stage_row:
            raise HTTPException(404, "Stage not found")
        stage_num, start_lat, start_lon = stage_row
        total_stages = con.execute(
            "SELECT COUNT(*) FROM tour_stages WHERE tour_id=?", (tour_id,)
        ).fetchone()[0]
        attempts = _list_attempts(con, tour_id, share_uid)
        attempt  = _resolve_attempt(attempts, share_attempt_id)
    finally:
        con.close()

    if start_lat is None or start_lon is None or attempt is None:
        return JSONResponse({"forecast": None, "out_of_range": False})
    stage_date = _estimate_stage_date(attempt, attempts, stage_num, total_stages)
    if stage_date is None:
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


@router.post("/tours/share/{token}/stages/{stage_id}/route-forecast")
async def tour_share_route_forecast(token: str, stage_id: int, request: Request):
    """Public — weather-along-route forecast for a shared tour stage."""
    body = await request.json()

    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT tour_id FROM tour_shares WHERE token=?", (token,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        name, pts = _load_stage_route(con, tour_row[0], stage_id)
    finally:
        con.close()

    from app.routers.forecast import build_route_forecast, parse_start_time
    start_dt = parse_start_time(body.get("start_time"))
    return JSONResponse(await build_route_forecast(pts, start_dt, name))


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
    """Public endpoint — AI stage summary for the shared rider's attempt.

    Generated on a cache miss using the share owner's key, so stages the owner
    never opened are not blank for visitors. Generation is once per stage.
    """
    db = db_getter()
    con = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_tables(con)
        tour_row = con.execute(
            "SELECT tour_id, user_id, attempt_id FROM tour_shares WHERE token=?", (token,)
        ).fetchone()
        if not tour_row:
            raise HTTPException(404, "Share link not found or revoked")
        tour_id, owner_uid, attempt_id = tour_row
        stage_row = con.execute(
            "SELECT id FROM tour_stages WHERE id=? AND tour_id=?", (stage_id, tour_id)
        ).fetchone()
        if not stage_row:
            raise HTTPException(404, "Stage not found")
    finally:
        con.close()

    cached = _cached_stage_summary(db, tour_id, stage_id, owner_uid, attempt_id)
    if cached:
        return JSONResponse({"summary": cached})

    api_key = (db.get_user(owner_uid) or {}).get("anthropic_api_key") or ""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(404, "No cached summary")

    _start_summary_job(db, tour_id, stage_id, owner_uid, attempt_id, api_key, None, False)
    return JSONResponse({"pending": True}, status_code=202)


# ── Share-page followers: opt-in email updates ────────────────────────────────
# The share page has no login, so the only thing we know about a follower is the
# address they typed. Subscriptions are therefore keyed by the share token and
# confirmed by email before anything is ever sent to them.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The subscribe endpoint is public and causes outbound mail, so it is capped per
# client. In-process only — good enough for a single machine, which is what runs.
_SUB_HITS: dict = {}
_SUB_MAX    = 5
_SUB_WINDOW = 3600


def _sub_rate_ok(ip: str) -> bool:
    now  = _time.time()
    # The endpoint is public, so the table itself must stay bounded: sweep out
    # clients whose window has fully expired once it grows past a sane size.
    if len(_SUB_HITS) > 1000:
        for k in [k for k, v in _SUB_HITS.items()
                  if not v or now - v[-1] >= _SUB_WINDOW]:
            del _SUB_HITS[k]
    hits = [t for t in _SUB_HITS.get(ip, []) if now - t < _SUB_WINDOW]
    _SUB_HITS[ip] = hits
    if len(hits) >= _SUB_MAX:
        return False
    hits.append(now)
    return True


def _sub_result(request: Request, icon: str, message: str, sub: str = "",
                back_url: str = ""):
    return templates.TemplateResponse("tour_subscribe_result.html", {
        "request":  request,
        "icon":     icon,
        "message":  message,
        "sub":      sub,
        "back_url": back_url,
        "title":    message,
    })


def _stats_line(stage: tuple, comp: dict, use_metric: bool) -> str:
    """"68.4 mi · 4,200 ft · 5h 12m" from a stage row and its completion."""
    _num, _name, plan_mi, plan_ft = stage
    dist_mi = (comp or {}).get("distance_mi") or plan_mi or 0
    climb_ft = (comp or {}).get("climb_ft")
    if climb_ft is None:
        climb_ft = plan_ft or 0
    parts = []
    if use_metric:
        parts.append(f"{dist_mi * 1.60934:,.1f} km")
        parts.append(f"{climb_ft * 0.3048:,.0f} m")
    else:
        parts.append(f"{dist_mi:,.1f} mi")
        parts.append(f"{climb_ft:,.0f} ft")
    secs = (comp or {}).get("moving_s") or (comp or {}).get("duration_s")
    if secs:
        h, m = int(secs) // 3600, (int(secs) % 3600) // 60
        parts.append(f"{h}h {m:02d}m" if h else f"{m}m")
    return " · ".join(parts)


@router.post("/tours/share/{token}/subscribe")
async def tour_share_subscribe(token: str, request: Request, body: dict = Body(...)):
    """Public: ask for stage updates by email. Sends a confirmation link.

    Always answers {"ok": true} whether or not the address is already on the
    list — the same non-enumeration stance as the password-reset flow.
    """
    import logging
    import secrets
    from app.mailer import smtp_configured, send_tour_confirm_email

    email = (body.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, "Enter a valid email address.")
    ip = request.client.host if request.client else "?"
    if not _sub_rate_ok(ip):
        raise HTTPException(429, "Too many sign-ups from here. Try again later.")

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT ts.user_id, t.title FROM tour_shares ts "
            "JOIN tours t ON t.id=ts.tour_id WHERE ts.token=?", (token,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Share link not found or revoked")
        owner_uid, title = row
        owner_row = con.execute(
            "SELECT username FROM users WHERE id=?", (owner_uid,)
        ).fetchone()
        owner_name = owner_row[0] if owner_row else "Someone"

        existing = con.execute(
            "SELECT confirm_token, confirmed_at FROM tour_subscribers "
            "WHERE token=? AND email=?", (token, email)
        ).fetchone()
        if existing and existing[1]:
            return JSONResponse({"ok": True})   # already confirmed — nothing to do
        if existing:
            confirm_token = existing[0]         # resend the same link
        else:
            confirm_token = secrets.token_urlsafe(32)
            con.execute(
                "INSERT INTO tour_subscribers "
                "(token, email, confirm_token, unsub_token, confirmed_at, created_at) "
                "VALUES (?,?,?,?,NULL,?)",
                (token, email, confirm_token, secrets.token_urlsafe(32),
                 int(_time.time())),
            )
            con.commit()
    finally:
        con.close()

    base        = str(request.base_url).rstrip("/")
    confirm_url = f"{base}/tours/share/{token}/confirm/{confirm_token}"
    # Logged so the flow is testable locally without SMTP, as the reset flow does.
    logging.getLogger("uvicorn").info(f"[tour-sub] confirm URL for {email}: {confirm_url}")
    if smtp_configured():
        try:
            send_tour_confirm_email(email, confirm_url, title, owner_name)
        except Exception:
            pass   # the caller still sees "check your email"
    return JSONResponse({"ok": True})


@router.get("/tours/share/{token}/confirm/{confirm_token}", response_class=HTMLResponse)
async def tour_share_confirm(token: str, confirm_token: str, request: Request):
    """Public: complete a double opt-in."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        row = con.execute(
            "SELECT id, confirmed_at FROM tour_subscribers "
            "WHERE token=? AND confirm_token=?", (token, confirm_token)
        ).fetchone()
        if not row:
            return _sub_result(
                request, "⚠️", "That confirmation link is no longer valid.",
                "It may have already been used, or the tour's share link was revoked.")
        if not row[1]:
            con.execute("UPDATE tour_subscribers SET confirmed_at=? WHERE id=?",
                        (int(_time.time()), row[0]))
            con.commit()
    finally:
        con.close()
    return _sub_result(
        request, "📬", "You're subscribed.",
        "You'll get an email each time a new stage is completed. "
        "Every message has an unsubscribe link.",
        f"/tours/share/{token}")


@router.get("/tours/share/{token}/unsubscribe/{unsub_token}", response_class=HTMLResponse)
async def tour_share_unsubscribe(token: str, unsub_token: str, request: Request):
    """Public: one-click unsubscribe, no confirmation step."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        con.execute("DELETE FROM tour_subscribers WHERE token=? AND unsub_token=?",
                    (token, unsub_token))
        con.commit()
    finally:
        con.close()
    return _sub_result(
        request, "👋", "Unsubscribed.",
        "You won't get any more stage updates for this tour.",
        f"/tours/share/{token}")


@router.get("/tours/{tour_id}/notify-status")
async def tour_notify_status(tour_id: int, request: Request):
    """Owner: follower count and which stages have already been announced."""
    uid = require_user(request)
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _tour_visible_or_404(con, tour_id, uid)
        row = con.execute(
            "SELECT token FROM tour_shares WHERE tour_id=? AND user_id=?", (tour_id, uid)
        ).fetchone()
        if not row:
            return JSONResponse({"subscribers": 0, "notified": []})
        token = row[0]
        n = con.execute(
            "SELECT COUNT(*) FROM tour_subscribers "
            "WHERE token=? AND confirmed_at IS NOT NULL", (token,)
        ).fetchone()[0]
        notified = [r[0] for r in con.execute(
            "SELECT stage_id FROM tour_stage_notified WHERE token=?", (token,))]
        return JSONResponse({"subscribers": n, "notified": notified})
    finally:
        con.close()


@router.post("/tours/{tour_id}/notify")
async def tour_notify_stage(tour_id: int, request: Request, body: dict = Body(...)):
    """Owner: announce one completed stage to that share link's followers.

    Sending is deliberately manual. Stage completion is inferred by matching
    activities to stages, and a match can shift as later rides land — mail cannot
    be recalled, so the owner confirms each one.
    """
    from app.mailer import smtp_configured, send_stage_update_emails

    uid = require_user(request)
    stage_id = body.get("stage_id")
    if not isinstance(stage_id, int):
        raise HTTPException(400, "stage_id is required")

    db = db_getter()
    con = sqlite3.connect(db.path, timeout=15)
    try:
        _ensure_tables(con)
        _tour_visible_or_404(con, tour_id, uid)
        share = con.execute(
            "SELECT ts.token, ts.attempt_id, t.title FROM tour_shares ts "
            "JOIN tours t ON t.id=ts.tour_id WHERE ts.tour_id=? AND ts.user_id=?",
            (tour_id, uid),
        ).fetchone()
        if not share:
            raise HTTPException(400, "Share this tour before notifying followers.")
        token, attempt_id, tour_title = share

        already = con.execute(
            "SELECT 1 FROM tour_stage_notified WHERE token=? AND stage_id=?",
            (token, stage_id),
        ).fetchone()
        if already:
            raise HTTPException(409, "Followers have already been told about this stage.")

        stage = con.execute(
            "SELECT stage_num, name, distance_mi, climb_ft FROM tour_stages "
            "WHERE id=? AND tour_id=?", (stage_id, tour_id)
        ).fetchone()
        if not stage:
            raise HTTPException(404, "Stage not found")

        # Confirm the stage really is complete for the shared attempt.
        stage_rows = con.execute(
            "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon "
            "FROM tour_stages WHERE tour_id=? ORDER BY stage_num", (tour_id,)
        ).fetchall()
        stages = [{"id": r[0], "stage_num": r[1], "name": r[2],
                   "distance_mi": r[3], "climb_ft": r[4],
                   "start_lat": r[5], "start_lon": r[6]} for r in stage_rows]
        stages = _collapse_alternate_stages(con, stages, prefer_id=stage_id)
        completions, _, _ = _completions_for_user(con, tour_id, uid, stages, attempt_id)
        comp = completions.get(stage_id)
        if not comp:
            raise HTTPException(400, "That stage isn't completed yet.")

        recipients = con.execute(
            "SELECT email, unsub_token FROM tour_subscribers "
            "WHERE token=? AND confirmed_at IS NOT NULL", (token,)
        ).fetchall()

        owner_row = con.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        owner_name = owner_row[0] if owner_row else "Someone"
    finally:
        con.close()

    if not recipients:
        return JSONResponse({"sent": 0})
    if not smtp_configured():
        raise HTTPException(503, "Email is not configured on this server.")

    try:
        use_metric = (db.get_user_profile(uid) or {}).get("use_metric", False)
    except Exception:
        use_metric = False

    # An uncached summary would take tens of seconds to generate; the mail is
    # worth more than the prose, so send without it.
    summary = _cached_stage_summary(db, tour_id, stage_id, uid, attempt_id) or ""

    base        = str(request.base_url).rstrip("/")
    stage_url   = f"{base}/tours/share/{token}?stage={stage_id}"
    stage_title = f"Stage {stage[0]} — {stage[1]}"
    stats_line  = _stats_line(stage, comp, use_metric)

    sent = send_stage_update_emails(
        [(e, f"{base}/tours/share/{token}/unsubscribe/{u}") for e, u in recipients],
        tour_title, owner_name, stage_title, stats_line, summary, stage_url,
    )

    con = sqlite3.connect(db.path, timeout=10)
    try:
        con.execute(
            "INSERT OR REPLACE INTO tour_stage_notified "
            "(token, stage_id, sent_at, sent_count) VALUES (?,?,?,?)",
            (token, stage_id, int(_time.time()), sent),
        )
        con.commit()
    finally:
        con.close()
    return JSONResponse({"sent": sent})


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
        "wx_tile_url": _wx_tile_url(),
    })
