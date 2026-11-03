'use strict';
/* Tests for app/static/stage_elev_chart.js `computeStageGradFromRawPts`.

   The per-point gradient is documented to mirror static_map.py `_point_grades`
   so the interactive page and the exported PDF agree. That parity used to rest
   on two hand-maintained copies of the same haversine; both now call one shared
   helper (_haversineMi here, app.geo.haversine_mi in Python).

   The JS adds bin-max downsampling and rounds y to 1 decimal on top of the raw
   per-point grade, so with step=1 each y equals Python's grade rounded to 1dp.
   PY_GRADES below is captured from static_map._point_grades on the same input;
   tests/test_static_map_parity.py pins it from the Python side. */

// A climb, a rollover, then a descent: [lat, lon, alt_ft].
var CLIMB = [
  [40.0000, -105.0000, 5000],
  [40.0010, -105.0000, 5020],
  [40.0020, -105.0000, 5055],
  [40.0030, -105.0000, 5070],
  [40.0040, -105.0000, 5070],
  [40.0050, -105.0000, 5040]
];

// static_map._point_grades(CLIMB), raw per-point slope %.
var PY_GRADES = [5.482203, 5.482203, 9.593856, 4.111653, 0.0, -8.223305];

function identity(x) { return x; }
function round1(v) { return Math.round(v * 10) / 10; }

test('computeStageGradFromRawPts is defined', function () {
  eq(typeof computeStageGradFromRawPts, 'function');
});

test('per-point grades match static_map._point_grades at chart precision', function () {
  var g = computeStageGradFromRawPts(CLIMB, 1, identity);
  eq(g.length, PY_GRADES.length, 'one gradient per input point');
  for (var i = 0; i < PY_GRADES.length; i++) {
    eq(g[i].y, round1(PY_GRADES[i]), 'grade[' + i + '] matches Python');
  }
});

test('x axis is cumulative distance through distConv', function () {
  var g = computeStageGradFromRawPts(CLIMB, 1, identity);
  eq(g[0].x, 0, 'starts at zero');
  ok(g[5].x > g[0].x, 'distance increases');
  approx(g[5].x, 0.34547, 1e-5, 'total matches the shared haversine');
});

test('grade signs follow the terrain', function () {
  var g = computeStageGradFromRawPts(CLIMB, 1, identity);
  ok(g[1].y > 0, 'climb is positive');
  eq(g[4].y, 0, 'flat is zero');
  ok(g[5].y < 0, 'descent is negative');
});

test('degenerate input does not throw', function () {
  eq(computeStageGradFromRawPts([], 1, identity).length, 0);
  eq(computeStageGradFromRawPts([[40, -105, 5000]], 1, identity).length, 0);
});

test('repeated points (a GPS pause) stay finite', function () {
  var g = computeStageGradFromRawPts(
    [[40, -105, 5000], [40, -105, 5000], [40.001, -105, 5010]], 1, identity);
  for (var i = 0; i < g.length; i++) {
    ok(isFinite(g[i].y), 'grade[' + i + '] is finite, got ' + g[i].y);
  }
});

test('the shared haversine agrees with the Python one', function () {
  approx(_haversineMi(40.0, -105.0, 40.0050, -105.0), 0.34547, 1e-5);
});
