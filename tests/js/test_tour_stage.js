'use strict';
/* Tests for app/static/tour_stage.js: biggestClimbInfo + segment grouping.
   The grouping cases mirror tests/test_tours_backend.py::test_segment_groups_and_collapse
   so the frontend port stays in lockstep with the backend logic. */

// ── biggestClimbInfo ──────────────────────────────────────────────────────────
test('biggestClimbInfo empty / too-short input', function () {
  eq(biggestClimbInfo([]), { gainFt: 0, startKm: 0 });
  eq(biggestClimbInfo([[40, -105]]), { gainFt: 0, startKm: 0 });   // no altitude column
});

test('biggestClimbInfo flat route has no climb', function () {
  var pts = [];
  for (var i = 0; i < 20; i++) pts.push([40 + 0.0005 * i, -105, 1600]);   // constant alt
  eq(biggestClimbInfo(pts), { gainFt: 0, startKm: 0 });
});

test('biggestClimbInfo detects a single sustained climb', function () {
  // 20 points, each ~55.5 m apart, rising 25 ft/step => ~13% grade (well past 3%).
  var pts = [];
  for (var i = 0; i < 20; i++) pts.push([40 + 0.0005 * i, -105, 1600 + 25 * i]);
  var r = biggestClimbInfo(pts);
  ok(r.gainFt > 300, 'expected a large gain, got ' + r.gainFt);   // ~475 ft after smoothing
  approx(r.startKm, 0, 0.2, 'climb should start near the route origin');
});

// ── segment grouping / de-dup / alternate ids ─────────────────────────────────
function _line(baseLat, baseLon) {
  var pts = [];
  for (var i = 0; i < 20; i++) pts.push([baseLat + 0.001 * i, baseLon, 0]);
  return pts;
}
function _stage(id, pts) {
  return { id: id, stage_num: id, distance_mi: 10.0,
           start_lat: pts[0][0], start_lon: pts[0][1] };
}

// Stages 1 and 3 share identical geometry (alternate routes); stage 2 is far away.
var LINE_AC = _line(40.0, -105.0);
var LINE_B  = _line(41.0, -106.0);
var STAGES  = [_stage(1, LINE_AC), _stage(2, LINE_B), _stage(3, LINE_AC)];
var CACHE   = { '1': LINE_AC, '2': LINE_B, '3': LINE_AC };

test('_stageSegmentGroups groups alternate routes together', function () {
  var groups = _stageSegmentGroups(STAGES, CACHE).map(function (g) {
    return g.map(function (s) { return s.id; });
  });
  eq(groups, [[1, 3], [2]]);
});

test('_dedupeStatStages keeps one representative per segment', function () {
  var ids = _dedupeStatStages(STAGES, CACHE).map(function (s) { return s.id; });
  eq(ids, [1, 2]);
});

test('_altStageIds returns the later alternates as a Set of string ids', function () {
  var alt = _altStageIds(STAGES, CACHE);
  ok(alt instanceof Set, 'expected a Set');
  eq(alt.size, 1);
  ok(alt.has('3'), 'stage 3 should be flagged as the alternate');
  ok(!alt.has('1'), 'stage 1 is the original, not an alternate');
});
