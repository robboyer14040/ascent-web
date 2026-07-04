"""Parsing of data returned from Strava queries — the pure parsers plus an
insert_activity_summary round-trip into a real temp DB. Uses the captured-shape
payloads in tests/samples.py (no network)."""

import json
from datetime import datetime, timezone

from app.strava_importer import (
    iso_to_unix, parse_tz_offset, parse_tz_name, event_type_from_workout,
    build_attributes_json, build_points_rows, _decode_polyline_bbox,
    StravaImporter, M_TO_MI, M_TO_FT,
)
from tests.samples import STRAVA_SUMMARY_RIDE, STRAVA_STREAMS


def _attrs_dict(attrs_json: str) -> dict:
    flat = json.loads(attrs_json)
    return dict(zip(flat[::2], flat[1::2]))


# ── timestamp / timezone parsing ──────────────────────────────────────────────

def test_iso_to_unix():
    expected = int(datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc).timestamp())
    assert iso_to_unix("2026-06-15T13:30:00Z") == expected
    assert iso_to_unix(None) is None
    assert iso_to_unix("not a date") is None


def test_parse_tz_offset_and_name():
    assert parse_tz_offset(STRAVA_SUMMARY_RIDE) == -8 * 3600
    assert parse_tz_name(STRAVA_SUMMARY_RIDE) == "America/Los_Angeles"
    # No timezone string → falls back to utc_offset int.
    assert parse_tz_offset({"utc_offset": 3600}) == 3600
    assert parse_tz_name({}) is None


def test_event_type_from_workout():
    assert event_type_from_workout(None, "Ride") is None
    assert event_type_from_workout(1, "Run") == "race"
    assert event_type_from_workout(2, "Run") == "long_run"
    assert event_type_from_workout(3, "Run") == "workout"
    assert event_type_from_workout(0, "Run") == "training"
    assert event_type_from_workout(1, "Ride") == "training"   # only runs get race/long


# ── attribute NSArray building ────────────────────────────────────────────────

def test_build_attributes_json_shape_and_values():
    raw = build_attributes_json(STRAVA_SUMMARY_RIDE, gear_name="Tarmac SL7")
    flat = json.loads(raw)
    # flat NSArray-style: even length, keys at even indices are strings.
    assert len(flat) % 2 == 0
    assert all(isinstance(k, str) for k in flat[::2])

    a = _attrs_dict(raw)
    assert a["name"] == "Morning Ride"
    assert a["activity"] == "Ride"
    assert a["equipment"] == "Tarmac SL7"
    assert a["location"] == "San Francisco"
    assert a["durationAsFloat"] == 5700
    assert a["movingDurationAsFloat"] == 5400
    assert a["distance"] == round(40234.5 * M_TO_MI, 4)
    assert a["totalClimb"] == round(320.0 * M_TO_FT, 1)
    assert a["avgHeartRate"] == 148.0
    assert a["maxHeartRate"] == 176.0
    assert a["avgPower"] == 210.0          # weighted_average_watts preferred
    assert a["maxPower"] == 620.0
    assert a["avgTemperature"] == 64.4     # 18°C → °F
    assert a["calories"] == 980
    assert a["sufferScore"] == 72
    # workout_type is None on a ride → no eventType key emitted
    assert "eventType" not in a


# ── streams → point rows ──────────────────────────────────────────────────────

def test_build_points_rows_conversions():
    rows = build_points_rows(STRAVA_STREAMS, activity_id_db=42)
    assert len(rows) == 4
    # tuple layout: (act_id, t, active_dt, lat, lon, alt_ft, hr, cad, temp, v, pwr, dist_mi, flags)
    assert rows[1][0] == 42
    assert rows[1][1] == 5                    # time
    assert rows[1][2] == 5                    # active_dt (moving)
    assert rows[0][2] == 0                    # first point not moving → 0
    assert rows[1][3] == 37.771               # lat
    assert rows[1][6] == 130.0                # heartrate
    assert abs(rows[1][5] - 15.0 * M_TO_FT) < 1e-6      # altitude m → ft
    assert abs(rows[1][11] - 50.0 * M_TO_MI) < 1e-9     # distance m → mi


def test_build_points_rows_empty_streams():
    assert build_points_rows({}, 1) == []


# ── polyline bbox ─────────────────────────────────────────────────────────────

def test_decode_polyline_bbox():
    bbox = _decode_polyline_bbox(STRAVA_SUMMARY_RIDE["map"]["summary_polyline"])
    assert bbox is not None
    min_lat, max_lat, min_lon, max_lon = bbox
    assert min_lat < max_lat and min_lon < max_lon
    assert 38.0 < min_lat < 39.0            # canonical sample's south-west corner
    assert _decode_polyline_bbox("") is None


# ── round-trip: summary insert into a real DB ─────────────────────────────────

def test_insert_activity_summary_round_trip(test_db, make_user):
    uid = make_user()
    importer = StravaImporter(test_db.path)
    aid = importer.insert_activity_summary(STRAVA_SUMMARY_RIDE, gear_name="Tarmac SL7",
                                           user_id=uid)
    assert aid is not None
    assert test_db.count_activities() == 1

    import sqlite3
    con = sqlite3.connect(test_db.path)
    row = con.execute(
        "SELECT strava_activity_id, user_id, distance_mi, start_lat, name "
        "FROM activities WHERE id=?", (aid,)).fetchone()
    con.close()
    assert row[0] == STRAVA_SUMMARY_RIDE["id"]
    assert row[1] == uid
    assert abs(row[2] - 40234.5 * M_TO_MI) < 1e-4
    assert abs(row[3] - 37.77) < 1e-6
    assert row[4] == "Morning Ride"
