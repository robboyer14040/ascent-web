"""
geo.py — shared great-circle distance helpers.

One haversine with three unit wrappers. This was previously reimplemented in
db.py, routers/forecast.py, routers/fitgpx.py, six nested copies inside
routers/api.py, and inline in static_map.py — in four different but
mathematically equivalent formulations.

The asin formulation is deliberate: static_map.py's grade calculation is
documented to mirror stage_elev_chart.js so the page and the exported PDF agree,
so the arithmetic here stays bit-for-bit what those call sites already used.
"""

import math

EARTH_RADIUS_M  = 6_371_000.0
EARTH_RADIUS_KM = 6371.0
EARTH_RADIUS_MI = 3958.8


def central_angle(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Angular separation in radians between two (lat, lon) degree points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return 2 * math.asin(min(1.0, math.sqrt(a)))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    return EARTH_RADIUS_M * central_angle(lat1, lon1, lat2, lon2)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    return EARTH_RADIUS_KM * central_angle(lat1, lon1, lat2, lon2)


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles."""
    return EARTH_RADIUS_MI * central_angle(lat1, lon1, lat2, lon2)
