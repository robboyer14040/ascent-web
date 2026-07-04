"""Characterization tests for the tours.py backend logic the refactor touches.

These pin the behavior of the de-duplication refactor. Attribute parsing now
uses the shared db.parse_attrs (Phase 7 folded tours._parse_activity_attrs into
it). They also document the expected segment-grouping behavior the frontend port
must match.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.routers import tours
from app.db import parse_attrs


# ── parse_attrs (shared db.py parser used by tours._build_completion) ──────────

def test_parse_attrs_basic():
    attrs = parse_attrs(json.dumps(["totalClimb", 500, "avgHeartRate", 140, "calories", 900]))
    assert attrs == {"totalClimb": 500, "avgHeartRate": 140, "calories": 900}


def test_parse_attrs_odd_length_drops_trailing_key():
    # dict(zip(evens, odds)) drops a dangling final key (no value).
    assert parse_attrs(json.dumps(["a", 1, "b"])) == {"a": 1}


def test_parse_attrs_none_and_garbage():
    assert parse_attrs(None) == {}
    assert parse_attrs("") == {}
    assert parse_attrs("not json") == {}
    # A dict payload (defensive "shouldn't happen" case) is returned as-is.
    assert parse_attrs(json.dumps({"a": 1})) == {"a": 1}


# ── _fa (numeric coercion) ────────────────────────────────────────────────────

def test_fa_coercion():
    attrs = {"x": "3.5", "y": 4, "z": "nope", "n": None}
    assert tours._fa(attrs, "x") == 3.5
    assert tours._fa(attrs, "y") == 4.0
    assert tours._fa(attrs, "z") is None
    assert tours._fa(attrs, "n") is None
    assert tours._fa(attrs, "missing") is None


# ── _build_completion ─────────────────────────────────────────────────────────

def test_build_completion_maps_fields():
    ts = int(datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc).timestamp())
    attrs = ["totalClimb", 1200, "durationAsFloat", 7200, "movingDurationAsFloat", 6600,
             "avgMovingSpeed", 12.5, "avgHeartRate", 145, "maxHeartRate", 175,
             "avgCadence", 85, "avgPower", 210, "maxPower", 500,
             "sufferScore", 88, "calories", 950]
    row = (42, ts, 55.0, 40.0, -105.0, json.dumps(attrs))
    c = tours._build_completion(row)
    assert c["activity_id"] == 42
    assert c["date"] == "2026-07-02"
    assert c["distance_mi"] == 55.0
    assert c["climb_ft"] == 1200.0
    assert c["duration_s"] == 7200.0
    assert c["moving_s"] == 6600.0
    assert c["avg_moving_speed_mph"] == 12.5
    assert c["avg_hr"] == 145.0
    assert c["max_hr"] == 175.0
    assert c["avg_cadence"] == 85.0
    assert c["avg_power"] == 210.0
    assert c["max_power"] == 500.0
    assert c["suffer_score"] == 88.0
    assert c["calories"] == 950.0


def test_build_completion_suffer_score_fallback_and_missing_attrs():
    row = (1, 0, 10.0, None, None, json.dumps(["suffer_score", 30]))
    c = tours._build_completion(row)
    assert c["date"] == "1970-01-01"
    assert c["suffer_score"] == 30.0   # falls back to the snake_case key
    assert c["climb_ft"] is None       # missing attribute -> None


# ── _parse_gpx_route ──────────────────────────────────────────────────────────

GPX_TRK = """<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Test Stage</name><trkseg>
    <trkpt lat="40.0000" lon="-105.0000"><ele>1600</ele></trkpt>
    <trkpt lat="40.0100" lon="-105.0000"><ele>1650</ele></trkpt>
    <trkpt lat="40.0200" lon="-105.0000"><ele>1700</ele></trkpt>
    <trkpt lat="40.0300" lon="-105.0000"><ele>1680</ele></trkpt>
  </trkseg></trk>
</gpx>"""

GPX_RTE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1">
  <rte><name>Route Stage</name>
    <rtept lat="40.0" lon="-105.0"><ele>1600</ele></rtept>
    <rtept lat="40.01" lon="-105.0"><ele>1620</ele></rtept>
  </rte>
</gpx>"""


def test_parse_gpx_route_trk():
    s = tours._parse_gpx_route(GPX_TRK.encode(), "whatever.gpx")
    assert s["name"] == "Test Stage"
    assert s["start_lat"] == 40.0
    assert s["start_lon"] == -105.0
    assert s["distance_mi"] > 0
    assert s["climb_ft"] > 0
    assert len(s["points"]) == 4
    # Altitude is converted metres -> feet on the way in.
    assert s["points"][0][2] == pytest.approx(1600 * tours.M_TO_FT)


def test_parse_gpx_route_rte_fallback():
    s = tours._parse_gpx_route(GPX_RTE.encode(), "x.gpx")
    assert s["name"] == "Route Stage"
    assert len(s["points"]) == 2


def test_parse_gpx_route_name_from_filename():
    gpx = GPX_TRK.replace("<name>Test Stage</name>", "")
    s = tours._parse_gpx_route(gpx.encode(), "my_cool-ride.gpx")
    assert s["name"] == "my cool ride"


def test_parse_gpx_route_invalid_xml():
    with pytest.raises(ValueError):
        tours._parse_gpx_route(b"<gpx><unclosed>", "x.gpx")


def test_parse_gpx_route_no_points():
    empty = ('<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1">'
             '<trk><name>Empty</name><trkseg></trkseg></trk></gpx>')
    with pytest.raises(ValueError):
        tours._parse_gpx_route(empty.encode(), "x.gpx")


# ── DB-backed logic: segment grouping + stage matching ────────────────────────

@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    tours._ensure_tables(c)
    yield c
    c.close()


def _insert_points(con, stage_id, pts):
    con.executemany(
        "INSERT INTO tour_stage_points (stage_id, seq, lat, lon, alt_ft) VALUES (?,?,?,?,?)",
        [(stage_id, i, lat, lon, 0.0) for i, (lat, lon) in enumerate(pts)],
    )
    con.commit()


def _stage(id, pts):
    return {"id": id, "stage_num": id, "distance_mi": 10.0,
            "start_lat": pts[0][0], "start_lon": pts[0][1]}


def test_segment_groups_and_collapse(con):
    # Stages A(1) and C(3) share identical geometry (alternate routes);
    # B(2) is far away and stands alone.
    line_ac = [(40.0 + 0.001 * i, -105.0) for i in range(20)]
    line_b  = [(41.0 + 0.001 * i, -106.0) for i in range(20)]
    _insert_points(con, 1, line_ac)
    _insert_points(con, 2, line_b)
    _insert_points(con, 3, line_ac)
    stages = [_stage(1, line_ac), _stage(2, line_b), _stage(3, line_ac)]

    groups = tours._stage_segment_groups(con, stages)
    assert [[s["id"] for s in g] for g in groups] == [[1, 3], [2]]

    reps = tours._collapse_alternate_stages(con, stages)
    assert [s["id"] for s in reps] == [1, 2]

    # prefer_id keeps that alternate as its group's representative.
    reps_pref = tours._collapse_alternate_stages(con, stages, prefer_id=3)
    assert [s["id"] for s in reps_pref] == [3, 2]


def _make_activities_table(con):
    con.execute("""
        CREATE TABLE activities (
            id                       INTEGER PRIMARY KEY,
            user_id                  INTEGER,
            creation_time_s          INTEGER,
            creation_time_override_s INTEGER,
            distance_mi              REAL,
            start_lat                REAL,
            start_lon                REAL,
            attributes_json          TEXT
        )
    """)


def _insert_activity(con, id, ts, dist, lat, lon, attrs="[]", user_id=1):
    con.execute(
        "INSERT INTO activities (id, user_id, creation_time_s, creation_time_override_s, "
        "distance_mi, start_lat, start_lon, attributes_json) VALUES (?,?,?,?,?,?,?,?)",
        (id, user_id, ts, None, dist, lat, lon, attrs),
    )
    con.commit()


def _ts(y, m, d):
    return int(datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp())


ONE_STAGE = [{"id": 100, "stage_num": 1, "name": "S1", "distance_mi": 50.0,
              "start_lat": 40.0, "start_lon": -105.0}]


def test_global_stage_matching_single_match(con):
    _make_activities_table(con)
    _insert_activity(con, 7, _ts(2026, 7, 3), 52.0, 40.001, -105.001,
                     json.dumps(["totalClimb", 3000, "durationAsFloat", 10800]))
    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10", ONE_STAGE)
    assert result[100] is not None
    assert result[100]["activity_id"] == 7
    assert result[100]["distance_mi"] == 52.0
    assert result[100]["climb_ft"] == 3000.0


def test_global_stage_matching_distance_out_of_range_is_none(con):
    _make_activities_table(con)
    # 5mi activity is far too short to match a 50mi stage.
    _insert_activity(con, 8, _ts(2026, 7, 3), 5.0, 40.0, -105.0)
    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10", ONE_STAGE)
    assert result[100] is None


def test_global_stage_matching_gps_far_away_is_none(con):
    _make_activities_table(con)
    # Right distance, but start point is nowhere near the stage.
    _insert_activity(con, 9, _ts(2026, 7, 3), 50.0, 10.0, 10.0)
    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10", ONE_STAGE)
    assert result[100] is None


def test_global_stage_matching_outside_date_window_is_none(con):
    _make_activities_table(con)
    # 3 days after the tour end -> beyond the +1 day grace window.
    _insert_activity(con, 10, _ts(2026, 7, 13), 52.0, 40.001, -105.001)
    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10", ONE_STAGE)
    assert result[100] is None


def test_global_stage_matching_no_activities_all_none(con):
    _make_activities_table(con)
    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10", ONE_STAGE)
    assert result == {100: None}
