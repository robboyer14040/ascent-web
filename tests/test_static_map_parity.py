"""static_map.py's grades must stay equal to the interactive chart's.

`_point_grades` is documented to mirror stage_elev_chart.js
`computeStageGradFromRawPts` so a stage looks the same on the page and in the
exported PDF. Both now call one shared haversine (app.geo.haversine_mi /
_haversineMi in common.js), and these are the same numbers asserted by
tests/js/test_stage_elev_chart.js — so a change on either side fails here.
"""

import pytest

from app.geo import haversine_mi
from app.static_map import _point_grades


# Same input as tests/js/test_stage_elev_chart.js
CLIMB = [
    [40.0000, -105.0000, 5000],
    [40.0010, -105.0000, 5020],
    [40.0020, -105.0000, 5055],
    [40.0030, -105.0000, 5070],
    [40.0040, -105.0000, 5070],
    [40.0050, -105.0000, 5040],
]

# Shared with the JS test's PY_GRADES.
EXPECTED = [5.482203, 5.482203, 9.593856, 4.111653, 0.0, -8.223305]


def test_point_grades_are_pinned():
    grades = _point_grades(CLIMB)
    assert len(grades) == len(EXPECTED)
    for got, want in zip(grades, EXPECTED):
        assert got == pytest.approx(want, abs=1e-6)


def test_chart_precision_matches_the_js_rounding():
    """The chart rounds to 1dp; those are the values the JS test asserts."""
    assert [round(g, 1) for g in _point_grades(CLIMB)] == \
           [5.5, 5.5, 9.6, 4.1, 0.0, -8.2]


def test_total_distance_is_pinned():
    assert haversine_mi(40.0, -105.0, 40.0050, -105.0) == pytest.approx(0.34547, abs=1e-5)


def test_degenerate_input_does_not_raise():
    assert _point_grades([]) == []
    assert _point_grades([[40.0, -105.0, 5000]]) == [0.0]


def test_repeated_points_stay_finite():
    """A GPS pause gives zero distance between points — must not divide by zero."""
    import math
    grades = _point_grades([[40.0, -105.0, 5000],
                            [40.0, -105.0, 5000],
                            [40.001, -105.0, 5010]])
    assert all(math.isfinite(g) for g in grades)
