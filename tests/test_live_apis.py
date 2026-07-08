"""Live external-API checks — verify each third-party service the app depends on
is up and returning the expected shape. These make REAL network calls and use
real credentials, so they are OPT-IN only:

    venv_arm64/bin/python -m pytest --run-live tests/test_live_apis.py -v

Credentials are discovered automatically (env / .env / your real Ascent DB via
ASCENT_DB_PATH) by tests/live_creds.py. A check whose credential is missing is
SKIPPED, not failed. The normal suite never runs these (see conftest --run-live).
"""

import re
import time

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


# ── Wikipedia place resolver (Location Summary) ───────────────────────────────
# The "Location Summary" modal resolves a reverse-geocoded label (e.g.
# "Dimos Epidaurus, Greece") to a Wikipedia article. The resolution LOGIC lives
# in app/static/location_summary.js and is unit-tested deterministically in
# tests/js/test_location_summary.js. The mirror below re-implements that same
# strategy in Python so we can assert, against the REAL Wikipedia API, that each
# label historically reported as broken now lands on the right place — catching
# both our-strategy bugs and upstream data/ranking changes. Keep it in lockstep
# with the JS (the JS unit tests are the source of truth for the logic).

_PLACE_DESC = re.compile(
    r"\b(municipalit|cit(y|ies)|town|village|commune|count(y|ies)|region|province|"
    r"prefectur|district|settlement|communit|administrativ|capital|localit|borough|"
    r"hamlet|neighbourhood|neighborhood|suburb|metropolis|metropolitan|urban|"
    r"human settlement|island|isle|mountain|peak|summit|hill|lake|river|valley|"
    r"glacier|national park|range|plateau|massif|resort|seat of|place in|place located)", re.I)
_NON_PLACE_DESC = re.compile(
    r"\b(scandal|elections?|referendum|actor|actress|player|footballer|coach|singer|"
    r"musician|writer|author|poet|painter|politician|film|movie|album|song|single|"
    r"tv series|series|team|club|f\.?c\.?|tournament|championship|\bcup\b|league|"
    r"festival|season|company|corporation|band|novel|video game|battle|war|treaty|"
    r"dynasty|manuscript|species|genus|deity|mytholog|saint|monarch|king|queen|"
    r"emperor|president|minister|general)\b", re.I)
_YEAR_TITLE = re.compile(r"\b(1[6-9]\d\d|20\d\d)\b")
_ADMIN_PREFIX = re.compile(
    r"^(dimos|bashkia e|bashkia|komuna e|komuna|qarku i|qarku|rrethi i|rrethi|"
    r"municipality of|municipality|city of|town of|commune de|commune of|comune di|"
    r"città di|gemeinde|ciudad de|concello de|freguesia de|distrito de|kommune|kommun|"
    r"obshtina|op[sš]tina|gmina|powiat|comuna|municipio de|municipio|prefecture of)\s+", re.I)

_WIKI_PROPS = {"prop": "coordinates|description|pageprops", "ppprop": "disambiguation",
               "colimit": "max", "format": "json"}


def _wiki(client, params):
    """GET the Wikipedia query API, backing off on rate limits (429)."""
    for attempt in range(6):
        r = client.get("https://en.wikipedia.org/w/api.php",
                        params={**_WIKI_PROPS, "action": "query", **params},
                        headers=UA, timeout=15)
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            wait = float(retry_after) if (retry_after or "").isdigit() else 2.0 * (2 ** attempt)
            time.sleep(min(wait, 20))
            continue
        r.raise_for_status()
        return r.json().get("query", {}).get("pages", {})
    r.raise_for_status()


def _parse_label(name):
    parts = [s.strip() for s in name.split(",") if s.strip()]
    bare = parts[0] if parts else ""
    place = _ADMIN_PREFIX.sub("", bare).strip() or bare
    last = place.split()[-1] if place.split() else ""
    country = parts[-1] if len(parts) > 1 else ""
    return place, last, country, bare


def _pick_place(pages, prefer, near):
    cands = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        co = (p.get("coordinates") or [None])[0]
        cands.append({"title": p.get("title", ""), "desc": p.get("description", "") or "",
                      "dis": p.get("pageprops", {}).get("disambiguation") is not None,
                      "lat": co["lat"] if co else None, "lon": co["lon"] if co else None})
    is_bad = lambda c: c["dis"] or bool(_NON_PLACE_DESC.search(c["desc"]) or _YEAR_TITLE.search(c["title"]))
    is_place = lambda c: bool(_PLACE_DESC.search(c["desc"]))
    if prefer:
        pl = prefer.strip().lower()
        for c in cands:
            if c["title"].lower() == pl and not is_bad(c) and (is_place(c) or c["lat"] is not None):
                return c["title"]
    places = [c for c in cands if is_place(c) and not is_bad(c)]
    if not places:
        return None
    if near:
        with_coord = [c for c in places if c["lat"] is not None]
        if with_coord:
            with_coord.sort(key=lambda c: (c["lat"] - near[0]) ** 2 + (c["lon"] - near[1]) ** 2)
            return with_coord[0]["title"]
    return places[0]["title"]


def _resolve_title(client, name, near=None):
    place, last, country, bare = _parse_label(name)

    def prefix(q, prefer):
        return _pick_place(_wiki(client, {"generator": "prefixsearch", "gpslimit": 6,
                                          "gpssearch": q}), prefer, near)

    def search(q, prefer):
        return _pick_place(_wiki(client, {"generator": "search", "gsrnamespace": 0,
                                          "gsrlimit": 8, "gsrsearch": q}), prefer, near)

    attempts = [(prefix, place, place)]
    if last and last != place:
        attempts.append((prefix, last, last))
    attempts.append((search, f"{place} {country}".strip() if country else place, place))
    if place != bare:
        attempts.append((search, place, place))
    if last and last != place:
        attempts.append((search, f"{last} {country}".strip() if country else last, last))
    for fn, q, prefer in attempts:
        if q and q.strip():
            t = fn(q.strip(), prefer)
            if t:
                return t
    return None


# (label, near-coord or None, {acceptable article titles}) — every case here is a
# label that once resolved to the WRONG article (an event, a person, a
# disambiguation page, a distant city) or to nothing.
_RESOLVER_CASES = [
    # Admin-prefix stripping + non-place rejection (Greek "Dimos", Albanian "Bashkia").
    ("Dimos Epidaurus, Greece",   None, {"Epidaurus"}),                  # was → Athens
    ("Dimos Fyli, Greece",        None, {"Fyli"}),                       # was → 2004–05 Greek Football Cup
    ("Bashkia Roskovec, Albania", None, {"Roskovec"}),                   # was → 2015 Albanian local elections
    ("Bashkia e Fierit, Albania", None, {"Fier", "Fier County"}),        # was → Albanian incinerators scandal
    ("Nafplio, Greece",           None, {"Nafplio"}),
    ("Athens, Greece",            None, {"Athens"}),
    ("Chamonix, France",          None, {"Chamonix"}),
    # US trajectory names: disambiguation page + redirect handling.
    ("Santa Cruz County",         None, {"Santa Cruz County, California"}),  # was → disambiguation page
    ("Scotts Valley",             None, {"Scotts Valley, California"}),
    ("Capitola",                  None, {"Capitola, California"}),
    # Coordinate disambiguation of identical names.
    ("San Jose",   (36.97, -122.03), {"San Jose, California"}),             # was → no summary (redirect)
    ("San Jose",   (9.93,  -84.08),  {"San José, Costa Rica"}),
    ("Santa Cruz", (36.97, -122.03), {"Santa Cruz, California"}),
    ("Santa Cruz", (-17.8,  -63.18), {"Santa Cruz de la Sierra", "Santa Cruz Department"}),
]


@pytest.mark.api("Wikipedia place resolver")
def test_location_summary_resolver():
    """Every historically-broken label resolves to the correct place via the real
    Wikipedia API (mirrors app/static/location_summary.js)."""
    try:
        with httpx.Client() as client:
            failures = []
            for name, near, acceptable in _RESOLVER_CASES:
                got = _resolve_title(client, name, near)
                near_s = f" near {near}" if near else ""
                if got not in acceptable:
                    failures.append(f"{name!r}{near_s} → {got!r} (expected one of {sorted(acceptable)})")
                time.sleep(0.6)  # be polite to the API
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        pytest.skip(f"Wikipedia API unavailable: {e}")
    assert not failures, "resolver mismatches:\n  " + "\n  ".join(failures)
