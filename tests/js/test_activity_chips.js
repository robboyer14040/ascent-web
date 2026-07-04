'use strict';
/* Tests for app/static/activity_chips.js: buildActivityStatChips.
   Uses the real esc/fmtHMS loaded from common.js, plus a small units object U
   that mirrors the per-page one (imperial by default). */

var U = {
  metric: false,
  distS:  function (v) { return (+v.toFixed(2)) + ' mi'; },
  climbS: function (v) { return Math.round(v) + ' ft'; },
  speedS: function (v) { return v + ' mph'; },
};

var A_FULL = {
  distance_mi: 26.2, active_time: 3600, duration: 4000,
  total_climb_ft: 1200, total_descent_ft: 1100,
  avg_speed_mph: 8.0, avg_heartrate: 150, max_heartrate: 175,
  avg_cadence: 88, avg_power: 210, max_power: 480,
  suffer_score: 90, activity_type: 'Run', equipment: 'Shoes',
};

test('full activity renders all 15 chips', function () {
  var html = buildActivityStatChips(A_FULL, U, esc, fmtHMS);
  eq(countOccurrences(html, 'class="stat-chip"'), 15);
});

test('chip labels and a formatted value are present', function () {
  var html = buildActivityStatChips(A_FULL, U, esc, fmtHMS);
  ['Distance', 'Mov Time', 'Ascent', 'Avg HR', 'Avg Pwr', 'Type', 'Equipment']
    .forEach(function (l) { ok(html.indexOf('>' + l + '<') !== -1, 'missing label ' + l); });
  ok(html.indexOf('26.2 mi') !== -1, 'distance value');
});

test('Avg Pwr sub shows max power', function () {
  var html = buildActivityStatChips(A_FULL, U, esc, fmtHMS);
  ok(html.indexOf('max 480 W') !== -1, 'expected max-power sub');
});

test('null-valued chips are filtered out', function () {
  var html = buildActivityStatChips({ distance_mi: 10.0 }, U, esc, fmtHMS);
  eq(countOccurrences(html, 'class="stat-chip"'), 1);   // only Distance qualifies
  ok(html.indexOf('>Cadence<') === -1, 'Cadence chip should be absent');
  ok(html.indexOf('>Distance<') !== -1, 'Distance chip should be present');
});

test('activity_type is HTML-escaped via esc', function () {
  var html = buildActivityStatChips({ activity_type: '<b>Run' }, U, esc, fmtHMS);
  ok(html.indexOf('&lt;b&gt;Run') !== -1, 'type should be escaped');
  ok(html.indexOf('<b>Run') === -1, 'raw tag should not appear');
});

test('long equipment name spans multiple grid columns', function () {
  var longName = 'Specialized Tarmac SL7 Expert';   // 29 chars -> span 3
  var html = buildActivityStatChips({ equipment: longName }, U, esc, fmtHMS);
  ok(html.indexOf('grid-column:span') !== -1, 'expected a span style for long equipment');
});

test('short equipment name does not add a span style', function () {
  var html = buildActivityStatChips({ equipment: 'Shoes' }, U, esc, fmtHMS);
  ok(html.indexOf('grid-column:span') === -1, 'short equipment should not span');
});
