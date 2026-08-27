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


# ── Create/edit HTTP endpoints (multipart GPX upload) ─────────────────────────

def _gpx_file(name):
    return ("files", (name, GPX_TRK.encode(), "application/gpx+xml"))


def test_edit_tour_add_new_stage_via_upload(authed_client):
    """Editing a tour with a new GPX file must accept the upload.

    Regression: an `Optional[List[UploadFile]]` signature made FastAPI reject any
    uploaded file on PUT with a 422 ("Input should be a valid list"), which the UI
    surfaced as "[object Object]" and blocked adding stages to an existing tour.
    """
    c = authed_client
    r = c.post(
        "/tours",
        data={"title": "T", "start_date": "2026-07-01", "end_date": "2026-07-26", "shared": "1"},
        files=[_gpx_file("a.gpx")],
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    order = [{"type": "existing", "id": 1}, {"type": "new", "idx": 0}]
    r2 = c.put(
        f"/tours/{tid}",
        data={"title": "T2", "start_date": "2026-07-01", "end_date": "2026-07-26",
              "shared": "1", "stage_order": json.dumps(order)},
        files=[_gpx_file("b.gpx")],
    )
    assert r2.status_code == 200, r2.text


def test_edit_tour_reorder_only_no_files(authed_client):
    """Editing with no new files (reorder/remove only) still works."""
    c = authed_client
    r = c.post(
        "/tours",
        data={"title": "T", "start_date": "2026-07-01", "end_date": "2026-07-26", "shared": "1"},
        files=[_gpx_file("a.gpx")],
    )
    tid = r.json()["id"]
    r2 = c.put(
        f"/tours/{tid}",
        data={"title": "T", "start_date": "2026-07-01", "end_date": "2026-07-26",
              "shared": "1", "stage_order": json.dumps([{"type": "existing", "id": 1}])},
    )
    assert r2.status_code == 200, r2.text


def test_add_tour_stages_appends_in_sequence(authed_client):
    """POST /tours/{id}/stages appends new stages, continuing stage_num.

    Backs the batched uploader: large tours are created with a first batch then
    grown in chunks so iPad Safari never has to build one giant multipart body.
    """
    c = authed_client
    r = c.post(
        "/tours",
        data={"title": "T", "start_date": "2026-07-01", "end_date": "2026-07-26", "shared": "1"},
        files=[_gpx_file("1.gpx"), _gpx_file("2.gpx")],
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    r2 = c.post(f"/tours/{tid}/stages", files=[_gpx_file("3.gpx"), _gpx_file("4.gpx")])
    assert r2.status_code == 201, r2.text
    assert r2.json()["added"] == 2

    tour = c.get(f"/tours/{tid}").json()
    nums = [s["stage_num"] for s in tour["stages"]]
    assert nums == [1, 2, 3, 4]


def test_add_tour_stages_rejects_non_creator(authed_client, make_user):
    """Only the tour creator may append stages."""
    from app.auth import create_session_token, SESSION_COOKIE
    c = authed_client
    r = c.post(
        "/tours",
        data={"title": "T", "start_date": "2026-07-01", "end_date": "2026-07-26", "shared": "1"},
        files=[_gpx_file("1.gpx")],
    )
    tid = r.json()["id"]

    other = make_user()
    c.cookies.set(SESSION_COOKIE, create_session_token(other))
    r2 = c.post(f"/tours/{tid}/stages", files=[_gpx_file("2.gpx")])
    assert r2.status_code == 403, r2.text


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


def test_segment_overlap_requires_shared_path(con):
    # A and C share start+end but C detours far away in between (<50% overlap):
    # sharing endpoints alone is no longer enough to call C an alternate.
    line_a = [(40.0 + 0.001 * i, -105.0) for i in range(20)]
    line_c = [line_a[0]] + [(40.0 + 0.001 * i, -105.0 + 0.03) for i in range(1, 19)] + [line_a[-1]]
    _insert_points(con, 1, line_a)
    _insert_points(con, 2, line_c)
    stages = [_stage(1, line_a), _stage(2, line_c)]

    groups = tours._stage_segment_groups(con, stages)
    assert [[s["id"] for s in g] for g in groups] == [[1], [2]]


def test_alt_override_forces_classification(con):
    line = [(40.0 + 0.001 * i, -105.0) for i in range(20)]
    far  = [(41.0 + 0.001 * i, -106.0) for i in range(20)]
    _insert_points(con, 1, line)
    _insert_points(con, 2, line)   # identical geometry → would auto-group with 1
    _insert_points(con, 3, far)    # unrelated geometry
    con.executemany(
        "INSERT INTO tour_stages (id, tour_id, stage_num, name, alt_override) VALUES (?,?,?,?,?)",
        [(1, 1, 1, "A", None), (2, 1, 2, "B", 0), (3, 1, 3, "C", 1)],
    )
    con.commit()
    stages = [_stage(1, line), _stage(2, line), _stage(3, far)]

    groups = tours._stage_segment_groups(con, stages)
    # 2 is forced standalone (override 0) despite identical geometry;
    # 3 is forced into 2's group (override 1) despite unrelated geometry.
    assert [[s["id"] for s in g] for g in groups] == [[1], [2, 3]]


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


# ── Attempt window (Section 4 matching bounds) ────────────────────────────────

def test_attempt_window_explicit_end():
    a = {"id": 1, "start_date": "2026-07-01", "end_date": "2026-07-10"}
    assert tours._attempt_window([a], a) == ("2026-07-01", "2026-07-10")


def test_attempt_window_open_with_subsequent_caps_day_before():
    a = {"id": 1, "start_date": "2026-07-01", "end_date": None}
    b = {"id": 2, "start_date": "2026-08-01", "end_date": None}
    assert tours._attempt_window([a, b], a) == ("2026-07-01", "2026-07-31")


def test_attempt_window_open_no_subsequent_is_open_start():
    a = {"id": 1, "start_date": "2026-07-01", "end_date": None}
    assert tours._attempt_window([a], a) == ("1900-01-01", "2026-07-01")


# ── Forecast stage-date estimate (attempt-anchored) ──────────────────────────

def test_estimate_stage_date_explicit_end_interpolates():
    a = {"id": 1, "start_date": "2026-07-01", "end_date": "2026-07-11"}
    # 6 stages over 10 days -> stage 4 lands 6 days in (round(3*10/5)).
    assert tours._estimate_stage_date(a, [a], 4, 6).isoformat() == "2026-07-07"


def test_estimate_stage_date_open_with_subsequent_caps_window():
    a = {"id": 1, "start_date": "2026-07-01", "end_date": None}
    b = {"id": 2, "start_date": "2026-07-11", "end_date": None}
    # Capped end is 2026-07-10 -> 9-day window; stage 2 of 3 = +4 days (round(1*9/2)=4).
    assert tours._estimate_stage_date(a, [a, b], 2, 3).isoformat() == "2026-07-05"


def test_estimate_stage_date_open_no_subsequent_one_per_day():
    a = {"id": 1, "start_date": "2026-07-01", "end_date": None}
    assert tours._estimate_stage_date(a, [a], 3, 10).isoformat() == "2026-07-03"


# ── Overlap-resolution engine (_place_attempt, Rules 1–4) ─────────────────────

def test_place_attempt_rule1_intersect_reject():
    others = [{"id": 1, "start_date": "2026-07-01", "end_date": "2026-07-10"}]
    err, cap = tours._place_attempt(others, "2026-07-05", None)
    assert err is not None and cap is None


def test_place_attempt_duplicate_start_reject():
    others = [{"id": 1, "start_date": "2026-07-01", "end_date": "2026-07-10"}]
    err, cap = tours._place_attempt(others, "2026-07-01", None)
    assert err is not None and cap is None


def test_place_attempt_rule2_open_prev_auto_cap():
    others = [{"id": 1, "start_date": "2026-07-01", "end_date": None}]
    err, cap = tours._place_attempt(others, "2026-08-01", None)
    assert err is None
    assert cap == (1, "2026-07-31")


def test_place_attempt_rule3_historical_before_existing_ok():
    others = [{"id": 2, "start_date": "2026-08-01", "end_date": "2026-08-10"}]
    err, cap = tours._place_attempt(others, "2026-07-01", None)
    assert err is None and cap is None


def test_place_attempt_rule4_future_collision_reject():
    others = [{"id": 2, "start_date": "2026-08-01", "end_date": None}]
    err, cap = tours._place_attempt(others, "2026-07-01", "2026-08-01")
    assert err is not None and cap is None


def test_place_attempt_end_before_start_reject():
    err, cap = tours._place_attempt([], "2026-07-10", "2026-07-01")
    assert err is not None and cap is None


# ── Attempt CRUD + subscription HTTP endpoints ────────────────────────────────

def _make_tour(client):
    r = client.post("/tours", data={"title": "T", "shared": "1"},
                    files=[_gpx_file("a.gpx")])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_subscribe_then_add_attempt_autocaps_previous(authed_client):
    c = authed_client
    tid = _make_tour(c)

    # Unsubscribed: get_tour reports no attempts.
    info = c.get(f"/tours/{tid}").json()
    assert info["attempts"] == [] and info["attempt_id"] is None

    # Subscribe with an open-ended attempt.
    r = c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-01"})
    assert r.status_code == 201, r.text

    # Add a later attempt -> the open one is auto-capped the day before.
    r2 = c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-08-01"})
    assert r2.status_code == 201, r2.text

    attempts = c.get(f"/tours/{tid}/attempts").json()["attempts"]
    assert [a["start_date"] for a in attempts] == ["2026-07-01", "2026-08-01"]
    assert attempts[0]["end_date"] == "2026-07-31"


def test_add_attempt_intersect_rejected(authed_client):
    c = authed_client
    tid = _make_tour(c)
    c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-01", "end_date": "2026-07-10"})
    r = c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-05"})
    assert r.status_code == 400


def test_edit_and_delete_attempt(authed_client):
    c = authed_client
    tid = _make_tour(c)
    aid = c.post(f"/tours/{tid}/attempts",
                 data={"start_date": "2026-07-01", "end_date": "2026-07-10"}).json()["id"]

    r = c.put(f"/tours/{tid}/attempts/{aid}",
              data={"start_date": "2026-07-02", "end_date": "2026-07-12"})
    assert r.status_code == 200, r.text
    assert c.get(f"/tours/{tid}/attempts").json()["attempts"][0]["start_date"] == "2026-07-02"

    assert c.delete(f"/tours/{tid}/attempts/{aid}").status_code == 200
    assert c.get(f"/tours/{tid}/attempts").json()["attempts"] == []


def test_subscriber_count_counts_other_users(authed_client, make_user, test_db):
    from app.auth import create_session_token, SESSION_COOKIE
    c = authed_client
    tid = _make_tour(c)
    c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-01"})
    assert c.get(f"/tours/{tid}/subscriber-count").json()["others"] == 0

    # A second user subscribes -> creator sees one other subscriber.
    other = make_user()
    from starlette.testclient import TestClient
    import app.main as main_mod
    oc = TestClient(main_mod.app)
    oc.cookies.set(SESSION_COOKIE, create_session_token(other))
    oc.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-05"})
    assert c.get(f"/tours/{tid}/subscriber-count").json()["others"] == 1


def test_private_tour_hidden_from_peer_and_visibility_toggle(authed_client, make_user):
    from app.auth import create_session_token, SESSION_COOKIE
    from starlette.testclient import TestClient
    import app.main as main_mod
    c = authed_client
    # Private tour.
    r = c.post("/tours", data={"title": "P", "shared": "0"}, files=[_gpx_file("a.gpx")])
    tid = r.json()["id"]

    other = make_user()
    oc = TestClient(main_mod.app)
    oc.cookies.set(SESSION_COOKIE, create_session_token(other))
    # Peer cannot see a private tour.
    assert oc.get(f"/tours/{tid}").status_code == 404
    # Creator flips it public.
    assert c.patch(f"/tours/{tid}/visibility", json={"public": True}).status_code == 200
    assert oc.get(f"/tours/{tid}").status_code == 200
    # Peer cannot change visibility.
    assert oc.patch(f"/tours/{tid}/visibility", json={"public": False}).status_code == 403


def test_publish_targets_attempt_and_share_uses_its_window(authed_client):
    c = authed_client
    tid = _make_tour(c)
    aid = c.post(f"/tours/{tid}/attempts",
                 data={"start_date": "2026-07-01", "end_date": "2026-07-10"}).json()["id"]

    pub = c.post(f"/tours/{tid}/publish", json={"attempt_id": aid}).json()
    assert pub["attempt_id"] == aid and pub["token"]

    data = c.get(f"/tours/share/{pub['token']}/data").json()
    # The public page's window reflects the shared attempt, not any tour-level date.
    assert data["start_date"] == "2026-07-01"
    assert data["end_date"] == "2026-07-10"
    assert len(data["stages"]) == 1


def test_legacy_tour_migrates_to_attempts(authed_client, make_user, add_activity, test_db):
    creator = authed_client.user_id
    other = make_user()
    # `other` has an activity inside the legacy window; creator has none.
    add_activity(user_id=other,
                 creation_time_s=int(datetime(2026, 7, 3, 12, tzinfo=timezone.utc).timestamp()))

    con = sqlite3.connect(test_db.path)
    tours._ensure_tables(con)
    con.execute(
        "INSERT INTO tours (created_by, title, start_date, end_date, created_at, shared) "
        "VALUES (?,?,?,?,?,0)",
        (creator, "Legacy", "2026-07-01", "2026-07-10", 0),
    )
    tid = con.execute("SELECT id FROM tours WHERE title='Legacy'").fetchone()[0]
    con.commit()
    con.close()

    # Re-opening runs the one-time migration inside _ensure_tables.
    con2 = sqlite3.connect(test_db.path)
    tours._ensure_tables(con2)
    shared = con2.execute("SELECT shared FROM tours WHERE id=?", (tid,)).fetchone()[0]
    users = {r[0] for r in con2.execute(
        "SELECT user_id FROM tour_attempts WHERE tour_id=?", (tid,)).fetchall()}
    # Idempotent: a second pass must not duplicate attempts.
    tours._ensure_tables(con2)
    n = con2.execute("SELECT COUNT(*) FROM tour_attempts WHERE tour_id=?", (tid,)).fetchone()[0]
    con2.close()

    assert shared == 1                       # legacy tours become public
    assert creator in users and other in users
    assert n == len(users)                   # no duplication on re-run


def test_publish_without_subscription_rejected(authed_client):
    c = authed_client
    tid = _make_tour(c)
    r = c.post(f"/tours/{tid}/publish", json={})
    assert r.status_code == 400


def test_forecast_none_when_unsubscribed(authed_client):
    # No attempt -> no window -> no forecast (and no outbound network call).
    c = authed_client
    tid = _make_tour(c)
    sid = c.get(f"/tours/{tid}").json()["stages"][0]["id"]
    body = c.get(f"/tours/{tid}/stages/{sid}/forecast").json()
    assert body["forecast"] is None and body["out_of_range"] is False


def test_unsubscribed_user_gets_no_completions(authed_client, add_activity):
    c = authed_client
    uid = c.user_id
    # An activity that WOULD match, but the user has no attempt -> no matching.
    add_activity(user_id=uid, distance_mi=10.0, creation_time_s=_ts(2026, 7, 3),
                 start_lat=40.0, start_lon=-105.0,
                 points=[(40.0, -105.0), (40.03, -105.0)])
    tid = _make_tour(c)
    info = c.get(f"/tours/{tid}").json()
    assert all(s["completion"] is None for s in info["stages"])


# ══════════════════════════════════════════════════════════════════════════════
# Visibility, Manage-modal control contract, Delete Tour, cross-user attempt
# permissions, and date-ordered stage matching.
#
# `_make_tour` (above) creates a PUBLIC tour (shared=1). Helpers below add a
# private variant and a peer TestClient (a second logged-in "browser").
# ══════════════════════════════════════════════════════════════════════════════

def _client_for(uid):
    """A fresh TestClient carrying `uid`'s session cookie — a distinct browser."""
    from starlette.testclient import TestClient
    from app.auth import create_session_token, SESSION_COOKIE
    import app.main as main_mod
    c = TestClient(main_mod.app)
    c.cookies.set(SESSION_COOKIE, create_session_token(uid))
    return c


def _private_tour(client, title="Priv"):
    r = client.post("/tours", data={"title": title, "shared": "0"},
                    files=[_gpx_file("a.gpx")])
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Public / Private visibility ───────────────────────────────────────────────

def test_public_tour_visible_to_peer_in_listing_and_detail(authed_client, make_user):
    """A public tour shows up in a peer's list (as not theirs) and is readable."""
    c = authed_client
    tid = _make_tour(c)                      # public

    peer = _client_for(make_user())
    listing = peer.get("/tours").json()
    entry = next((t for t in listing if t["id"] == tid), None)
    assert entry is not None
    assert entry["is_mine"] is False and entry["shared"] is True

    detail = peer.get(f"/tours/{tid}")
    assert detail.status_code == 200
    assert detail.json()["is_mine"] is False


def test_private_tour_excluded_from_peer_listing_and_detail(authed_client, make_user):
    """A private tour is hidden from peers in BOTH the list and detail, while the
    creator still sees their own."""
    c = authed_client
    tid = _private_tour(c)

    peer = _client_for(make_user())
    assert tid not in {t["id"] for t in peer.get("/tours").json()}
    assert peer.get(f"/tours/{tid}").status_code == 404

    assert tid in {t["id"] for t in c.get("/tours").json()}


def test_visibility_toggle_flips_peer_access(authed_client, make_user):
    """Flipping shared on/off adds/removes the tour from a peer's world."""
    c = authed_client
    tid = _private_tour(c)
    peer = _client_for(make_user())
    assert peer.get(f"/tours/{tid}").status_code == 404

    assert c.patch(f"/tours/{tid}/visibility", json={"public": True}).status_code == 200
    assert peer.get(f"/tours/{tid}").status_code == 200
    assert tid in {t["id"] for t in peer.get("/tours").json()}

    assert c.patch(f"/tours/{tid}/visibility", json={"public": False}).status_code == 200
    assert peer.get(f"/tours/{tid}").status_code == 404


# ── Manage-modal control contract ─────────────────────────────────────────────
# The modal's control states are driven entirely by data the backend returns:
#   • is_mine  → the creator-only block (Public toggle / Edit Stages / Delete Tour)
#   • attempts → Subscribe… (empty) vs Add Attempt… + edit/delete-attempt controls
#   • read_only (list_tour_attempts) → a peer's attempts are view-only
# These tests pin those contracts rather than the DOM the JS builds from them.

def test_manage_creator_block_only_for_creator(authed_client, make_user):
    c = authed_client
    tid = _make_tour(c)
    assert c.get(f"/tours/{tid}").json()["is_mine"] is True        # creator block shown

    peer = _client_for(make_user())
    assert peer.get(f"/tours/{tid}").json()["is_mine"] is False    # creator block hidden


def test_manage_subscribe_vs_add_attempt_state(authed_client):
    c = authed_client
    tid = _make_tour(c)
    info = c.get(f"/tours/{tid}").json()
    assert info["attempts"] == [] and info["attempt_id"] is None   # → "Subscribe…"

    c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-01"})
    info2 = c.get(f"/tours/{tid}").json()
    assert len(info2["attempts"]) == 1 and info2["attempt_id"] is not None  # → "Add Attempt…"


def test_manage_peer_attempts_are_read_only(authed_client, make_user):
    c = authed_client
    tid = _make_tour(c)
    c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-01"})
    creator = c.user_id

    # Creator viewing their own attempts: editable.
    assert c.get(f"/tours/{tid}/attempts").json()["read_only"] is False

    # Peer browsing the creator's attempts on a public tour: read-only.
    peer = _client_for(make_user())
    r = peer.get(f"/tours/{tid}/attempts", params={"user_id": creator})
    assert r.status_code == 200 and r.json()["read_only"] is True


def test_peer_cannot_toggle_visibility(authed_client, make_user):
    """The Public toggle is creator-only; a peer PATCH is rejected."""
    c = authed_client
    tid = _make_tour(c)
    peer = _client_for(make_user())
    assert peer.patch(f"/tours/{tid}/visibility", json={"public": False}).status_code == 403
    assert c.get(f"/tours/{tid}").json()["shared"] is True   # unchanged


# ── Cross-user attempt permissions ────────────────────────────────────────────

def test_delete_attempt_rejects_other_users_attempt(authed_client, make_user):
    c = authed_client
    tid = _make_tour(c)
    aid = c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-01"}).json()["id"]

    peer = _client_for(make_user())
    assert peer.delete(f"/tours/{tid}/attempts/{aid}").status_code == 403
    assert len(c.get(f"/tours/{tid}/attempts").json()["attempts"]) == 1   # survives


def test_edit_attempt_rejects_other_users_attempt(authed_client, make_user):
    c = authed_client
    tid = _make_tour(c)
    aid = c.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-01"}).json()["id"]

    peer = _client_for(make_user())
    r = peer.put(f"/tours/{tid}/attempts/{aid}", data={"start_date": "2026-07-02"})
    assert r.status_code == 403


def test_peer_cannot_subscribe_to_private_tour(authed_client, make_user):
    c = authed_client
    tid = _private_tour(c)
    peer = _client_for(make_user())
    # Private tour is invisible → subscribing (adding an attempt) 404s.
    assert peer.post(f"/tours/{tid}/attempts",
                     data={"start_date": "2026-07-01"}).status_code == 404


# ── Delete Tour (private + public cascade) ────────────────────────────────────

def test_delete_private_tour(authed_client):
    c = authed_client
    tid = _private_tour(c)
    assert c.delete(f"/tours/{tid}").status_code == 200
    assert c.get(f"/tours/{tid}").status_code == 404


def test_delete_tour_rejected_for_non_creator(authed_client, make_user):
    c = authed_client
    tid = _make_tour(c)                      # public
    peer = _client_for(make_user())
    assert peer.delete(f"/tours/{tid}").status_code == 403
    assert c.get(f"/tours/{tid}").status_code == 200   # survives


def test_delete_public_tour_cascades_attempts_and_shares(authed_client, make_user, test_db):
    """Deleting a public tour removes every user's attempts and all share links
    (the cascading 'Delete Tour…' case with subscribers + a published share)."""
    c = authed_client
    tid = _make_tour(c)                      # public
    aid = c.post(f"/tours/{tid}/attempts",
                 data={"start_date": "2026-07-01", "end_date": "2026-07-10"}).json()["id"]
    token = c.post(f"/tours/{tid}/publish", json={"attempt_id": aid}).json()["token"]

    peer = _client_for(make_user())
    peer.post(f"/tours/{tid}/attempts", data={"start_date": "2026-07-05"})

    assert c.delete(f"/tours/{tid}").status_code == 200

    assert c.get(f"/tours/{tid}").status_code == 404          # gone for creator
    assert peer.get(f"/tours/{tid}").status_code == 404       # gone for peer
    assert c.get(f"/tours/share/{token}/data").status_code == 404   # share link dead

    con = sqlite3.connect(test_db.path)
    left = con.execute("SELECT COUNT(*) FROM tour_attempts WHERE tour_id=?", (tid,)).fetchone()[0]
    con.close()
    assert left == 0                                          # all subscribers' attempts removed


# ── Stage matching with fake activities on unique dates ───────────────────────

def _spread_stage(sid, num, dist=50.0, lat=40.0, lon=-105.0):
    return {"id": sid, "stage_num": num, "name": f"S{num}",
            "distance_mi": dist, "start_lat": lat, "start_lon": lon}


def test_matching_assigns_activities_in_date_order(con):
    """Three stages identical in start-point and distance are distinguishable
    only by date. With three activities on unique days the date-order score must
    pair earliest activity → stage 1, next → stage 2, latest → stage 3."""
    _make_activities_table(con)
    _insert_activity(con, 71, _ts(2026, 7, 2), 50.0, 40.0, -105.0)
    _insert_activity(con, 72, _ts(2026, 7, 4), 50.0, 40.0, -105.0)
    _insert_activity(con, 73, _ts(2026, 7, 6), 50.0, 40.0, -105.0)
    stages = [_spread_stage(201, 1), _spread_stage(202, 2), _spread_stage(203, 3)]

    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10", stages)
    assert result[201]["activity_id"] == 71 and result[201]["date"] == "2026-07-02"
    assert result[202]["activity_id"] == 72 and result[202]["date"] == "2026-07-04"
    assert result[203]["activity_id"] == 73 and result[203]["date"] == "2026-07-06"


def test_matching_one_long_activity_covers_two_stages(con):
    """A single activity whose distance ≈ the sum of two consecutive stages
    matches both (multi-stage grouping)."""
    _make_activities_table(con)
    _insert_activity(con, 80, _ts(2026, 7, 3), 60.0, 40.0, -105.0)
    stages = [_spread_stage(301, 1, dist=30.0), _spread_stage(302, 2, dist=30.0)]

    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10", stages)
    assert result[301] is not None and result[302] is not None
    assert result[301]["activity_id"] == result[302]["activity_id"] == 80


def test_matching_scopes_activities_to_owner(con):
    """Matching only considers the given user's own activities — another user's
    perfectly-matching activity is ignored (multi-tenancy at the matching layer)."""
    _make_activities_table(con)
    _insert_activity(con, 90, _ts(2026, 7, 3), 50.0, 40.0, -105.0, user_id=2)
    result = tours._global_stage_matching(con, 1, "2026-07-01", "2026-07-10",
                                          [_spread_stage(400, 1)])
    assert result[400] is None


# ── Define As Tour: build a tour from existing activities ─────────────────────

def test_create_tour_from_activities(authed_client, add_activity):
    """POST /tours/from-activities creates stages from activities in the given
    order, derives distance/climb/route from each activity's GPS track, and the
    new tour shows uncompleted stages (no completion until an attempt exists)."""
    c = authed_client
    a1 = add_activity(user_id=c.user_id, name="Alpe Climb", distance_mi=8.0,
                      points=[(45.0, 6.0, 100), (45.01, 6.01, 5000), (45.02, 6.02, 9000)])
    a2 = add_activity(user_id=c.user_id, name="Valley Spin", distance_mi=20.0,
                      points=[(46.0, 7.0, 200), (46.05, 7.05, 300)])

    r = c.post("/tours/from-activities", data={
        "title": "My Week", "shared": "1",
        "activity_ids": json.dumps([a2, a1]),   # order preserved (a2 first)
    })
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    tour = c.get(f"/tours/{tid}").json()
    assert tour["title"] == "My Week"
    assert tour["shared"] is True
    stages = tour["stages"]
    assert [s["name"] for s in stages] == ["Valley Spin", "Alpe Climb"]
    assert [s["stage_num"] for s in stages] == [1, 2]
    # Uncompleted — no attempt/tracking window created yet.
    assert all(s["completion"] is None for s in stages)
    # Distance/climb derived from the track (climb only on the ascending one).
    assert stages[0]["distance_mi"] > 0
    assert stages[1]["climb_ft"] > 0


def test_create_tour_from_activities_requires_activities(authed_client):
    c = authed_client
    r = c.post("/tours/from-activities",
               data={"title": "Empty", "activity_ids": json.dumps([])})
    assert r.status_code == 400


def test_create_tour_from_activities_rejects_other_users_activity(authed_client, add_activity, make_user):
    """An activity id belonging to someone else can't be pulled into a tour."""
    c = authed_client
    other = make_user()
    a = add_activity(user_id=other, name="Not Yours", distance_mi=5.0,
                     points=[(45.0, 6.0, 100), (45.01, 6.01, 200)])
    r = c.post("/tours/from-activities",
               data={"title": "Sneaky", "activity_ids": json.dumps([a])})
    assert r.status_code == 404


# ── stage route profile / AI summary context ──────────────────────────────────

def _ramp_route():
    """~3mi: 1mi flat at 5000ft, 1mi climbing to ~6960ft, 1mi back down."""
    pts, lat, step = [], 40.0, 0.00029
    for i in range(50):
        pts.append((lat + i * step, -105.0, 5000.0))
    lat2 = lat + 50 * step
    for i in range(50):
        pts.append((lat2 + i * step, -105.0, 5000.0 + i * 40.0))
    lat3 = lat2 + 50 * step
    for i in range(50):
        pts.append((lat3 + i * step, -105.0, 6960.0 - i * 40.0))
    return pts


def test_route_profile_finds_the_climb_and_its_position():
    p = tours._stage_route_profile(_ramp_route())
    assert len(p["climbs"]) == 1
    c = p["climbs"][0]
    # The climb must start where the flat ends (~mile 1), not at mile 0 —
    # otherwise flat ground dilutes the reported gradient.
    assert 0.8 < c["start_mi"] < 1.2
    assert 1800 < c["gain_ft"] < 2000
    assert c["grade_pct"] > 20
    assert 6900 < p["high_ft"] <= 6960
    assert 1.8 < p["high_at_mi"] < 2.2


def test_route_profile_ignores_noise_and_handles_missing_data():
    assert tours._stage_route_profile([]) == {}
    assert tours._stage_route_profile([(40.0, -105.0, 100.0)]) == {}
    # No elevation anywhere — distance still reported, no invented climbs.
    flat = tours._stage_route_profile([(40.0 + i * 0.01, -105.0, None) for i in range(20)])
    assert flat["climbs"] == []
    assert flat["distance_mi"] > 0
    # Small undulations must not register as climbs.
    wiggle = [(40.0 + i * 0.001, -105.0, 5000.0 + (i % 3) * 20) for i in range(100)]
    assert tours._stage_route_profile(wiggle)["climbs"] == []


def test_format_route_block_states_places_climbs_and_high_point():
    block = tours._format_route_block(
        tours._stage_route_profile(_ramp_route()),
        [{"name": "Boulder", "lat": 40.0, "lon": -105.0},
         {"name": "Nederland", "lat": 40.1, "lon": -105.0}],
    )
    assert "Boulder -> Nederland" in block
    assert "Significant climbs" in block
    assert "% grade" in block
    assert "high" in block


def test_format_route_block_is_empty_without_a_route():
    assert tours._format_route_block({}, []) == ""


# ── share-page stage summary generation ───────────────────────────────────────

@pytest.fixture
def shared_tour(test_db, make_user):
    """A shared tour with one stage that has a real climbing route."""
    uid = make_user()
    test_db.update_user_settings(uid, anthropic_api_key="sk-test")
    con = sqlite3.connect(test_db.path, timeout=10)
    try:
        tours._ensure_tables(con)
        con.execute("INSERT INTO tours (id, created_by, title, start_date, end_date, created_at) "
                    "VALUES (1,?,'Alps Traverse','2026-07-01','2026-07-10',0)", (uid,))
        con.execute("INSERT INTO tour_stages (id, tour_id, stage_num, name, distance_mi, climb_ft, "
                    "start_lat, start_lon) VALUES (10,1,1,'Col du Test',3.0,1960,40.0,-105.0)")
        con.executemany(
            "INSERT INTO tour_stage_points (stage_id, seq, lat, lon, alt_ft) VALUES (10,?,?,?,?)",
            [(i, p[0], p[1], p[2]) for i, p in enumerate(_ramp_route())],
        )
        con.execute("INSERT INTO tour_shares (tour_id, user_id, token) VALUES (1,?,'tok123')", (uid,))
        con.commit()
    finally:
        con.close()
    return uid


@pytest.fixture
def capture_claude(monkeypatch):
    """Stub httpx.AsyncClient.post; record prompts, return a canned summary."""
    seen = []

    class _Resp:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "A summary of the col."}]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            seen.append(kw["json"]["messages"][0]["content"])
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    # Geocoding is a live rate-limited call — stub it with fixed places.
    async def _places(pts):
        return [{"name": "Boulder", "lat": 40.0, "lon": -105.0},
                {"name": "Nederland", "lat": 40.1, "lon": -105.0}]
    import app.routers.weather as wx
    monkeypatch.setattr(wx, "fetch_location_points", _places)
    return seen


def test_share_generates_summary_when_none_is_cached(client, shared_tour, capture_claude):
    """The share page used to 404 for any stage the owner never opened."""
    r = client.get("/tours/share/tok123/stages/10/ai-summary")
    assert r.status_code == 200
    assert r.json()["summary"] == "A summary of the col."
    assert len(capture_claude) == 1


def test_generated_prompt_carries_real_route_detail(client, shared_tour, capture_claude):
    client.get("/tours/share/tok123/stages/10/ai-summary")
    prompt = capture_claude[0]
    assert "Boulder -> Nederland" in prompt          # named places
    assert "Significant climbs" in prompt            # terrain, not just totals
    assert "% grade" in prompt
    assert "NOT been ridden" in prompt               # no invented performance


def test_share_summary_is_cached_after_first_generation(client, shared_tour, capture_claude):
    a = client.get("/tours/share/tok123/stages/10/ai-summary")
    b = client.get("/tours/share/tok123/stages/10/ai-summary")
    assert a.json()["summary"] == b.json()["summary"]
    assert len(capture_claude) == 1                  # generated once, then cached


def _fake_completion(monkeypatch, comp):
    """Force _completions_for_user to report a completion for stage 10."""
    def _c(con, tour_id, user_id, stages, attempt_id):
        return {s["id"]: (comp if s["id"] == 10 else None) for s in stages}, [], None
    monkeypatch.setattr(tours, "_completions_for_user", _c)


COMPLETION = {"activity_id": 7, "date": "2026-07-02", "distance_mi": 3.1,
              "climb_ft": 1975.0, "duration_s": 5400.0,
              "avg_moving_speed_mph": 8.4, "avg_hr": 148.0, "avg_power": 210.0}


def test_completing_a_stage_regenerates_its_summary(client, shared_tour,
                                                    capture_claude, monkeypatch):
    """A summary written before the ride says nothing about how it went."""
    client.get("/tours/share/tok123/stages/10/ai-summary")
    assert "NOT been ridden" in capture_claude[0]

    _fake_completion(monkeypatch, COMPLETION)
    client.get("/tours/share/tok123/stages/10/ai-summary")

    assert len(capture_claude) == 2, "stale pre-completion summary was served"
    prompt = capture_claude[1]
    assert "COMPLETED this stage" in prompt
    assert "2026-07-02" in prompt
    assert "148bpm" in prompt
    assert "210W" in prompt
    assert "8.4mph" in prompt
    # Route detail must survive alongside the performance data.
    assert "Boulder -> Nederland" in prompt


def test_completed_summary_is_cached_and_not_regenerated(client, shared_tour,
                                                         capture_claude, monkeypatch):
    _fake_completion(monkeypatch, COMPLETION)
    client.get("/tours/share/tok123/stages/10/ai-summary")
    client.get("/tours/share/tok123/stages/10/ai-summary")
    assert len(capture_claude) == 1


# ── Opus 5 thinking blocks ────────────────────────────────────────────────────

def test_first_text_skips_thinking_blocks():
    """Opus 5 thinks by default, so content[0] is often not the reply."""
    from app.routers.coach import first_text
    assert first_text({"content": [
        {"type": "thinking", "thinking": "", "signature": "abc"},
        {"type": "text", "text": "  the reply  "},
    ]}) == "the reply"
    # Plain single-text responses still work.
    assert first_text({"content": [{"type": "text", "text": "hi"}]}) == "hi"
    # Nothing usable must not raise.
    assert first_text({"content": [{"type": "thinking", "thinking": ""}]}) == ""
    assert first_text({}) == ""


def test_summary_survives_a_thinking_first_response(client, shared_tour, monkeypatch):
    """Regression: content[0]["text"] raised KeyError and blanked the card."""
    class _Resp:
        status_code = 200
        def json(self):
            return {"content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "A summary of the col."},
            ]}
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw): return _Resp()
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    async def _places(pts): return []
    import app.routers.weather as wx
    monkeypatch.setattr(wx, "fetch_location_points", _places)

    r = client.get("/tours/share/tok123/stages/10/ai-summary")
    assert r.status_code == 200
    assert r.json()["summary"] == "A summary of the col."


def test_token_budgets_leave_room_for_thinking():
    """max_tokens covers thinking + reply; small budgets truncate the prose."""
    import re, pathlib
    for f in ("app/routers/tours.py", "app/routers/api.py"):
        for n in re.findall(r'"max_tokens":\s*(\d+)', pathlib.Path(f).read_text()):
            assert int(n) >= 1000, f"{f}: max_tokens {n} is too small for thinking"
