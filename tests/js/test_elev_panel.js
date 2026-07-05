/* test_elev_panel.js — pure region-info formatters + row/header assembly from
   app/static/elev_panel.js. These are the byte-for-byte-duplicated helpers that both
   the Activity Analysis page and the Tour stage page now share; locking their output
   here guards the double-maintenance surface. Only `U.metric` is used by the helpers. */

// ── formatters (statute) ──────────────────────────────────────────────────────
U = { metric: false };
test('elevFmt statute units', function () {
  eq(elevFmt.alt(131.4), '131 ft');
  eq(elevFmt.alt(null), '—');
  eq(elevFmt.clmb(200.6), '201 ft');
  eq(elevFmt.clmb(0), '—');            // 0 climb shows dash, not "0 ft"
  eq(elevFmt.spd(20.16), '20.2 mph');
  eq(elevFmt.hr(150.7), '151 bpm');
  eq(elevFmt.pwr(240.4), '240 W');
  eq(elevFmt.grad(8.25), '8.3%');
  eq(elevFmt.grad(-4), '-4.0%');
  eq(elevFmt.cad(90.6), '91 rpm');
  eq(elevFmt.distSel(1609.344), '1.00 mi');
  eq(elevFmt.hr(null), '—');
});

// ── formatters (metric) ───────────────────────────────────────────────────────
test('elevFmt metric units', function () {
  U = { metric: true };
  eq(elevFmt.alt(131), '131 m');
  eq(elevFmt.clmb(200), '200 m');
  eq(elevFmt.spd(32.2), '32.2 km/h');
  eq(elevFmt.distSel(1000), '1.00 km');
  U = { metric: false };
});

// ── elapsed time ──────────────────────────────────────────────────────────────
test('elevFmtElapsed', function () {
  eq(elevFmtElapsed(0), '00:00');
  eq(elevFmtElapsed(65), '01:05');
  eq(elevFmtElapsed(3661), '1:01:01');
  eq(elevFmtElapsed(59.6), '01:00');   // rounds
  eq(elevFmtElapsed(-5), '00:00');     // clamps to 0
});

// ── stats rows ────────────────────────────────────────────────────────────────
test('elevStatsRows omits absent metrics (uncompleted stage)', function () {
  var rows = elevStatsRows({
    alt: { min: 10, max: 131, maxIdx: 5 }, climb: 200, descent: 50,
    grad: { max: 8.2, maxIdx: 7, avg: 1.1 },
    hr: null, power: null, speed: null, cadence: null,
  });
  eq(rows.length, 3);                                  // only altitude/climb/gradient
  eq(rows[0][1].label, 'Max Altitude');
  eq(rows[0][1].clickIdx, 5);
  eq(rows[2][0].clickIdx, 7);                          // Max Gradient seek index
});

test('elevStatsRows includes present metrics (completed activity)', function () {
  var rows = elevStatsRows({
    alt: { min: 10, max: 131, maxIdx: 5 }, climb: 200, descent: 50,
    grad: { max: 8.2, maxIdx: 7, avg: 1.1 },
    hr:      { max: 175, avg: 140, maxIdx: 11 },
    power:   { max: 400, avg: 220, maxIdx: 12 },
    speed:   { max: 30, mov: 18, maxIdx: 13 },
    cadence: { max: 95, avg: 80, maxIdx: 14 },
  });
  eq(rows.length, 7);
  eq(rows[3][0].label, 'Max HR');    eq(rows[3][0].val, '175 bpm');   eq(rows[3][0].clickIdx, 11);
  eq(rows[4][0].label, 'Max 3s Power');
  eq(rows[5][0].label, 'Max Speed'); eq(rows[5][1].label, 'Mov Speed');
  eq(rows[6][0].label, 'Max Cadence');
});

// ── header ────────────────────────────────────────────────────────────────────
test('elevHeaderHtml variants', function () {
  eq(elevHeaderHtml(null), '');
  var distOnly = elevHeaderHtml({ distStr: '5.00 mi' });
  ok(distOnly.indexOf('es-header') >= 0 && distOnly.indexOf('5.00 mi') >= 0, 'dist-only header');
  ok(distOnly.indexOf('Start') < 0, 'dist-only header has no Start');
  var full = elevHeaderHtml({ startSec: 65, durSec: 3661, distStr: '5.00 mi' });
  ok(full.indexOf('Start') >= 0 && full.indexOf('01:05') >= 0, 'full header Start');
  ok(full.indexOf('Duration') >= 0 && full.indexOf('1:01:01') >= 0, 'full header Duration');
  ok(full.indexOf('es-maxat') >= 0, 'full header has max@ slot');
});

// ── computeRegionStats ────────────────────────────────────────────────────────
// Build a ctx over plain arrays. `metrics` maps field → {values, toSeek} so a test can
// give metric samples their own (finer) index space, as the Tour page does.
function _ctx(alt, grad, distM, metrics, header) {
  return {
    altAt:   si => alt[si],
    gradAt:  si => grad ? (grad[si] == null ? null : grad[si]) : null,
    distMAt: si => distM[si],
    metricSamples: (f, l, h) => {
      var m = metrics && metrics[f];
      if (!m) return null;
      if (Array.isArray(m)) return { values: m.slice(l, h + 1), toSeekIdx: function (li) { return l + li; } };
      return m;                                                  // explicit {values, toSeekIdx}
    },
    rangeHeader: () => header === undefined ? { distM: distM[distM.length - 1] - distM[0] } : header,
  };
}

test('computeRegionStats altitude/climb/gradient/metrics (statute)', function () {
  U = { metric: false };
  var r = computeRegionStats(_ctx(
    [100, 120, 110, 140, 130],
    [0, 5, -3, 8, -2],
    [0, 100, 200, 300, 400],
    { hr: [0, 150, 160, 0, 155], power: [0, 200, 0, 0, 0],
      speed: [0, 10, 20, 0.3, 15], cadence: [0, 80, 90, 0, 85] }
  ), 0, 4);
  eq(r.rows.length, 7);
  eq(r.rows[0][0].val, '100 ft');                 // Min Altitude
  eq(r.rows[0][1].val, '140 ft'); eq(r.rows[0][1].clickIdx, 3);   // Max Altitude + seek
  eq(r.rows[1][0].val, '50 ft');  eq(r.rows[1][1].val, '20 ft');  // Climb / Descent
  eq(r.rows[2][0].val, '8.0%');   eq(r.rows[2][0].clickIdx, 3);   // Max Gradient (prefers +8 over -3)
  eq(r.rows[2][1].val, '2.3%');                                   // Avg Gradient (30ft / 1312ft)
  eq(r.rows[3][0].val, '160 bpm'); eq(r.rows[3][0].clickIdx, 2); eq(r.rows[3][1].val, '155 bpm');
  eq(r.rows[4][0].val, '200 W');   eq(r.rows[4][0].clickIdx, 1);
  eq(r.rows[5][0].val, '20.0 mph'); eq(r.rows[5][1].val, '15.0 mph');  // Max / Mov speed (mov = ≥0.5)
  eq(r.rows[6][0].val, '90 rpm');  eq(r.rows[6][0].clickIdx, 2);
});

test('computeRegionStats gradient falls back to steepest descent when no positive', function () {
  U = { metric: false };
  var r = computeRegionStats(_ctx([100, 90, 70], [-2, -6, -3], [0, 100, 200], {}), 0, 2);
  eq(r.rows[2][0].val, '-6.0%'); eq(r.rows[2][0].clickIdx, 1);
});

test('computeRegionStats preserves fine-resolution metric seek index', function () {
  U = { metric: false };
  // hr spikes at fine index 1, which maps to seek index 20 (coarser si space)
  var r = computeRegionStats(_ctx([100, 110, 120], null, [0, 100, 200], {
    hr: { values: [100, 190, 120], toSeekIdx: function (li) { return [10, 20, 30][li]; } },
  }), 0, 2);
  eq(r.rows[3][0].val, '190 bpm');
  eq(r.rows[3][0].clickIdx, 20);   // mapped seek index, not the local index 1
});

test('computeRegionStats header variants', function () {
  U = { metric: false };
  var mk = h => computeRegionStats(_ctx([10, 20], null, [0, 1609.344], {}, h), 0, 1).headerHtml;
  eq(mk(null), '');
  ok(mk({ distM: 1609.344 }).indexOf('1.00 mi') >= 0, 'dist-only');
  ok(mk({ startSec: 0, durSec: 65, distM: 1609.344 }).indexOf('Duration') >= 0, 'full header');
});

// ── elevHudRows ───────────────────────────────────────────────────────────────
test('elevHudRows omit (undefined) vs dash (null) vs value, with colours', function () {
  U = { metric: false };
  var color = (field, v) => field + ':' + (v == null ? 'none' : 'val');
  // Tour-style: no time (omit), altitude present, HR absent (dash)
  var rows = elevHudRows({ time: undefined, dist: 1609.344, alt: 131, grad: 3.2,
                           hr: null, power: null, cadence: null, speed: 20 }, color);
  eq(rows.map(r => r.label), ['Distance', 'Altitude', 'Gradient', 'Heart Rate', 'Power', 'Cadence', 'Speed']);
  eq(rows[0].val, '1.00 mi');
  eq(rows[1].val, '131 ft');
  eq(rows[3].val, '—');                 // HR absent → dash
  eq(rows[3].color, 'hr:none');         // colour resolver still runs for dashes
  eq(rows[6].val, '20.0 mph');
  eq(rows[6].color, 'speed:val');
});

test('elevHudRows full set with Time', function () {
  U = { metric: false };
  var rows = elevHudRows({ time: 3661, dist: 0, alt: 0, grad: 0, hr: 150, power: 200, cadence: 90, speed: 25 },
                         () => '#fff');
  eq(rows.length, 8);
  eq(rows[0].val, '1:01:01');
  eq(rows[4].val, '150 bpm');
});
