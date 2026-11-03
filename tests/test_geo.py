"""The shared haversine must reproduce every formulation it replaced.

Distances feed segment matching, stage grades and the elevation charts, so the
consolidation has to be numerically identical — not merely close enough.
"""

import math

import pytest

from app.geo import haversine_m, haversine_km, haversine_mi, central_angle


# Real coordinates: Boulder-area climbs, a long transcontinental pair, the
# poles, the antimeridian, and identical points.
PAIRS = [
    (40.0150, -105.2705, 40.0176, -105.2797),   # a few hundred metres
    (40.0150, -105.2705, 39.7392, -104.9903),   # Boulder → Denver
    (40.0150, -105.2705, 48.8566, 2.3522),      # Boulder → Paris
    (0.0, 0.0, 0.0, 180.0),                     # antipodal on the equator
    (89.9, 0.0, 89.9, 180.0),                   # near the pole
    (0.0, 179.99, 0.0, -179.99),                # across the antimeridian
    (40.0, -105.0, 40.0, -105.0),               # identical points
]


def _asin_km(lat1, lon1, lat2, lon2):
    """db.py / api.py's form."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1); dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.asin(min(1, math.sqrt(a)))


def _radians_free_m(lat1, lon1, lat2, lon2):
    """forecast.py's form (p = pi/180, no math.radians calls)."""
    R = 6_371_000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(max(0.0, a)))


def _atan2_m(lat1, lon1, lat2, lon2):
    """fitgpx.py's form."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1); dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) *
         math.sin(dlam/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _inline_mi(lat1, lon1, lat2, lon2):
    """static_map.py / the JS charts' form."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1); dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.asin(min(1, math.sqrt(a)))


@pytest.mark.parametrize("pair", PAIRS)
def test_matches_the_asin_km_form_exactly(pair):
    assert haversine_km(*pair) == pytest.approx(_asin_km(*pair), abs=0, rel=0)


@pytest.mark.parametrize("pair", PAIRS)
def test_matches_the_inline_miles_form_exactly(pair):
    """static_map.py is documented to mirror stage_elev_chart.js — no drift."""
    assert haversine_mi(*pair) == pytest.approx(_inline_mi(*pair), abs=0, rel=0)


@pytest.mark.parametrize("pair", PAIRS)
def test_matches_the_other_metre_forms(pair):
    """The pi/180 and atan2 variants agree to floating-point tolerance."""
    assert haversine_m(*pair) == pytest.approx(_radians_free_m(*pair), rel=1e-9)
    assert haversine_m(*pair) == pytest.approx(_atan2_m(*pair), rel=1e-9)


@pytest.mark.parametrize("pair", PAIRS)
def test_unit_wrappers_are_consistent(pair):
    km = haversine_km(*pair)
    assert haversine_m(*pair) == pytest.approx(km * 1000.0, rel=1e-12)
    assert haversine_mi(*pair) == pytest.approx(km * (3958.8 / 6371.0), rel=1e-12)


def test_identical_points_are_zero():
    assert haversine_m(40.0, -105.0, 40.0, -105.0) == 0.0


def test_antipodal_does_not_blow_up_on_rounding():
    """min(1.0, ...) guards sqrt(a) drifting above 1 for antipodal points."""
    assert central_angle(0.0, 0.0, 0.0, 180.0) == pytest.approx(math.pi, rel=1e-9)


def test_known_distance():
    """Boulder → Denver is about 38 km."""
    assert haversine_km(40.0150, -105.2705, 39.7392, -104.9903) == pytest.approx(38.0, abs=1.5)
