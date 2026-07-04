"""GPX import/export — a true export→import round-trip through the HTTP layer
against a real temp DB, plus the pure GPX helpers."""

import xml.etree.ElementTree as ET

from app.routers.fitgpx import (
    _gpx_chunks, _normalized_power, _parse_gpx_timestamp, _haversine_m,
)


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_parse_gpx_timestamp():
    assert _parse_gpx_timestamp("2026-06-15T13:30:00Z") is not None
    assert _parse_gpx_timestamp("2026-06-15T13:30:00+00:00") is not None
    assert _parse_gpx_timestamp(None) is None
    assert _parse_gpx_timestamp("garbage") is None


def test_normalized_power():
    # <30 samples → simple mean
    assert _normalized_power([100, 200, 300]) == 200
    assert _normalized_power([]) == 0.0
    # constant power over ≥30 samples → NP equals that power
    assert abs(_normalized_power([200] * 60) - 200) < 1e-6


def test_haversine_m_known_distance():
    # one degree of latitude ≈ 111.19 km
    d = _haversine_m(0.0, 0.0, 1.0, 0.0)
    assert abs(d - 111_195) < 100


def test_gpx_chunks_export_structure():
    act = {"name": "Chunk Test", "activity_type": "Ride", "start_time": 1_700_000_000}
    pts = [
        {"lat": 40.0,  "lon": -105.0, "alt_m": 1600, "t": 0,
         "hr": 0, "cad": 0, "power": 0, "temp_f": 0},
        {"lat": 40.01, "lon": -105.0, "alt_m": 1620, "t": 30,
         "hr": 140, "cad": 85, "power": 210, "temp_f": 64},
    ]
    xml = b"".join(_gpx_chunks(act, pts)).decode("utf-8")
    root = ET.fromstring(xml)                    # parses => well-formed
    text = xml
    assert "<name>Chunk Test</name>" in text
    assert "<type>Ride</type>" in text
    assert text.count("<trkpt ") == 2
    assert text.count("<ele>") == 2
    assert "<gpxtpx:hr>140</gpxtpx:hr>" in text  # extension emitted for the 2nd point


def test_gpx_chunks_skips_invalid_points():
    act = {"name": "X", "activity_type": "", "start_time": 0}
    pts = [
        {"lat": 0.0,   "lon": 0.0,    "alt_m": 0, "t": 0,   # 0,0 dropped
         "hr": 0, "cad": 0, "power": 0, "temp_f": 0},
        {"lat": 999.0, "lon": -105.0, "alt_m": 0, "t": 1,   # 999 sentinel dropped
         "hr": 0, "cad": 0, "power": 0, "temp_f": 0},
        {"lat": 40.0,  "lon": -105.0, "alt_m": 10, "t": 2,  # valid
         "hr": 0, "cad": 0, "power": 0, "temp_f": 0},
    ]
    text = b"".join(_gpx_chunks(act, pts)).decode("utf-8")
    assert text.count("<trkpt ") == 1


# ── round-trip through the HTTP layer ─────────────────────────────────────────

GPX_IMPORT = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="pytest" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk><name>Test Ride</name><type>Ride</type><trkseg>
    <trkpt lat="40.0000" lon="-105.0000"><ele>1600</ele><time>2026-06-15T13:30:00Z</time></trkpt>
    <trkpt lat="40.0050" lon="-105.0000"><ele>1620</ele><time>2026-06-15T13:30:30Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>140</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
    </trkpt>
    <trkpt lat="40.0100" lon="-105.0000"><ele>1650</ele><time>2026-06-15T13:31:00Z</time></trkpt>
    <trkpt lat="40.0150" lon="-105.0000"><ele>1640</ele><time>2026-06-15T13:31:30Z</time></trkpt>
  </trkseg></trk>
</gpx>"""


def test_gpx_import_then_export_round_trip(authed_client):
    # Import
    r = authed_client.post(
        "/import/gpx",
        files={"file": ("ride.gpx", GPX_IMPORT.encode(), "application/gpx+xml")},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    aid = data["id"]
    assert data["distance_mi"] > 0

    # Export the just-imported activity
    r2 = authed_client.get(f"/activities/{aid}/export/gpx")
    assert r2.status_code == 200
    assert "gpx" in r2.headers["content-type"]
    out = r2.text

    # Same four points survive the round-trip, with names/elevations/HR intact.
    assert out.count("<trkpt ") == 4
    assert "<name>Test Ride</name>" in out
    assert 'lat="40.0"' in out and 'lat="40.015"' in out
    assert out.count("<ele>") == 4
    assert "<gpxtpx:hr>140</gpxtpx:hr>" in out


def test_import_gpx_requires_auth(client):
    # No session cookie → the endpoint should refuse.
    r = client.post(
        "/import/gpx",
        files={"file": ("ride.gpx", GPX_IMPORT.encode(), "application/gpx+xml")},
    )
    assert r.status_code == 401
