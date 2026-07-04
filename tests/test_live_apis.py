"""Live external-API checks — verify each third-party service the app depends on
is up and returning the expected shape. These make REAL network calls and use
real credentials, so they are OPT-IN only:

    venv_arm64/bin/python -m pytest --run-live tests/test_live_apis.py -v

Credentials are discovered automatically (env / .env / your real Ascent DB via
ASCENT_DB_PATH) by tests/live_creds.py. A check whose credential is missing is
SKIPPED, not failed. The normal suite never runs these (see conftest --run-live).
"""

import httpx
import pytest

from tests import live_creds

pytestmark = pytest.mark.live

UA = {"User-Agent": "Ascent-Web/1.0 (live API self-test)"}

# A land tile that every XYZ provider serves (z/x/y = 5/5/12, over North America).
_Z, _X, _Y = 5, 5, 12


# ── Anthropic AI ──────────────────────────────────────────────────────────────

@pytest.mark.api("Anthropic API")
def test_anthropic_messages_api():
    key = live_creds.anthropic_key()
    if not key:
        pytest.skip("no Anthropic key (set ANTHROPIC_API_KEY, .env, or ASCENT_DB_PATH)")
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 16,
              "messages": [{"role": "user", "content": "Reply with the single word: pong"}]},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    text = " ".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    assert text.strip(), "Anthropic returned no text content"


@pytest.mark.api("Anthropic activity-title generation")
def test_activity_title_generation(authed_client, test_db, add_activity):
    """End-to-end: the real /suggest-title endpoint calls Claude and returns a title."""
    key = live_creds.anthropic_key()
    if not key:
        pytest.skip("no Anthropic key")
    uid = authed_client.user_id
    test_db.update_user_settings(uid, anthropic_api_key=key)
    aid = add_activity(
        user_id=uid, name="Evening Ride", distance_mi=24.6,
        attrs=["activity", "Ride", "totalClimb", 1350, "durationAsFloat", 5400,
               "avgHeartRate", 148, "avgPower", 205],
    )
    r = authed_client.post(f"/api/activities/{aid}/suggest-title")
    assert r.status_code == 200, r.text
    assert r.json().get("title", "").strip(), "empty title"


# ── Strava ────────────────────────────────────────────────────────────────────

@pytest.mark.api("Strava API")
def test_strava_athlete():
    token = live_creds.strava_token()
    if not token:
        pytest.skip("no Strava token (.env: STRAVA_REFRESH_TOKEN + CLIENT_ID/SECRET, "
                    "or ASCENT_DB_PATH with a connected user)")
    r = httpx.get("https://www.strava.com/api/v3/athlete",
                  headers={"Authorization": f"Bearer {token}"}, timeout=20)
    assert r.status_code == 200, r.text
    assert "id" in r.json()


@pytest.mark.api("Strava reachability")
def test_strava_api_reachable_and_enforces_auth():
    # No creds needed: proves the API is up and rejects unauthenticated calls.
    r = httpx.get("https://www.strava.com/api/v3/athlete", timeout=20)
    assert r.status_code == 401


# ── Weather (forecast + historical) + elevation ───────────────────────────────

@pytest.mark.api("Open-Meteo forecast")
def test_open_meteo_forecast():
    r = httpx.get("https://api.open-meteo.com/v1/forecast",
                  params={"latitude": 37.77, "longitude": -122.42,
                          "hourly": "temperature_2m", "forecast_days": 1}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json().get("hourly", {}).get("temperature_2m")


@pytest.mark.api("Open-Meteo ERA5 archive")
def test_open_meteo_era5_archive():
    r = httpx.get("https://archive-api.open-meteo.com/v1/era5",
                  params={"latitude": 37.77, "longitude": -122.42,
                          "start_date": "2024-06-01", "end_date": "2024-06-01",
                          "hourly": "temperature_2m"}, timeout=25)
    assert r.status_code == 200, r.text
    assert r.json().get("hourly", {}).get("temperature_2m")


@pytest.mark.api("Open-Meteo elevation")
def test_open_meteo_elevation():
    r = httpx.get("https://api.open-meteo.com/v1/elevation",
                  params={"latitude": 37.77, "longitude": -122.42}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json().get("elevation")


# ── Geocoding / geolocation ───────────────────────────────────────────────────

@pytest.mark.api("Nominatim geocoding")
def test_nominatim_reverse_geocode():
    r = httpx.get("https://nominatim.openstreetmap.org/reverse",
                  params={"lat": 37.77, "lon": -122.42, "format": "json"},
                  headers=UA, timeout=15)
    assert r.status_code == 200, r.text
    assert "address" in r.json()


@pytest.mark.api("BigDataCloud geocoding")
def test_bigdatacloud_reverse_geocode():
    r = httpx.get("https://api.bigdatacloud.net/data/reverse-geocode-client",
                  params={"latitude": 37.77, "longitude": -122.42, "localityLanguage": "en"},
                  timeout=15)
    assert r.status_code == 200, r.text
    assert r.json().get("countryName")


# ── Routing (Valhalla — Stadia if keyed, else OSM.de) ─────────────────────────

def _valhalla_urls():
    """(route_url, height_url, keyed). Prefers Stadia (the app's primary) when a
    key is present; otherwise the free OSM.de instance the app falls back to."""
    key = live_creds.stadia_key()
    if key:
        return (f"https://valhalla.stadiamaps.com/route?api_key={key}",
                f"https://valhalla.stadiamaps.com/height?api_key={key}", True)
    return ("https://valhalla1.openstreetmap.de/route",
            "https://valhalla1.openstreetmap.de/height", False)


def _post_valhalla(url, body, keyed):
    r = httpx.post(url, json=body, timeout=20)
    # The free OSM.de fallback is often overloaded (502/503/504). Treat that as a
    # skip with guidance rather than a hard failure; still fail hard for Stadia.
    if not keyed and r.status_code in (502, 503, 504):
        pytest.skip(f"free OSM.de Valhalla fallback unavailable (HTTP {r.status_code}); "
                    "set STADIA_API_KEY to test the primary routing service")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.api("Valhalla routing")
def test_valhalla_route():
    route_url, _, keyed = _valhalla_urls()
    body = {"locations": [{"lat": 37.77, "lon": -122.42}, {"lat": 37.80, "lon": -122.42}],
            "costing": "pedestrian", "directions_options": {"units": "kilometers"}}
    assert _post_valhalla(route_url, body, keyed)["trip"]["legs"]


@pytest.mark.api("Valhalla elevation")
def test_valhalla_height():
    _, height_url, keyed = _valhalla_urls()
    body = {"shape": [{"lat": 37.77, "lon": -122.42}, {"lat": 37.80, "lon": -122.42}],
            "height_precision": 1}
    assert _post_valhalla(height_url, body, keyed).get("height")


# ── Map tile providers ────────────────────────────────────────────────────────

TILE_PROVIDERS = [
    ("osm",         f"https://tile.openstreetmap.org/{_Z}/{_X}/{_Y}.png"),
    ("opentopomap", f"https://a.tile.opentopomap.org/{_Z}/{_X}/{_Y}.png"),
    ("carto-dark",  f"https://a.basemaps.cartocdn.com/dark_all/{_Z}/{_X}/{_Y}.png"),
    ("carto-light", f"https://a.basemaps.cartocdn.com/light_all/{_Z}/{_X}/{_Y}.png"),
    ("esri-sat",    f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{_Z}/{_Y}/{_X}"),
    ("waymarkedtrails", f"https://tile.waymarkedtrails.org/cycling/{_Z}/{_X}/{_Y}.png"),
]


@pytest.mark.api("Map tile")
@pytest.mark.parametrize("name,url", TILE_PROVIDERS, ids=[p[0] for p in TILE_PROVIDERS])
def test_map_tile_serves_image(name, url):
    r = httpx.get(url, headers=UA, timeout=15, follow_redirects=True)
    assert r.status_code == 200, f"{name}: HTTP {r.status_code}"
    ctype = r.headers.get("content-type", "")
    assert ctype.startswith("image/"), f"{name}: content-type {ctype!r}"


@pytest.mark.api("Stadia tiles")
def test_stadia_tile():
    key = live_creds.stadia_key()
    if not key:
        pytest.skip("no STADIA_API_KEY")
    url = f"https://tiles.stadiamaps.com/tiles/osm_bright/{_Z}/{_X}/{_Y}.png?api_key={key}"
    r = httpx.get(url, headers=UA, timeout=15)
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("image/")


# ── JS/CSS asset CDN ──────────────────────────────────────────────────────────

@pytest.mark.api("jsDelivr CDN")
def test_jsdelivr_cdn():
    r = httpx.get("https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js", timeout=15)
    assert r.status_code == 200
    assert len(r.content) > 1000
