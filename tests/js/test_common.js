'use strict';
/* Tests for app/static/common.js: fmtHMS, esc, _haversineMi, MAP_TILES. */

// ── fmtHMS ────────────────────────────────────────────────────────────────────
test('fmtHMS falsy -> em dash', function () {
  eq(fmtHMS(0), '—');
  eq(fmtHMS(null), '—');
  eq(fmtHMS(undefined), '—');
});
test('fmtHMS under an hour -> M:SS', function () {
  eq(fmtHMS(59), '0:59');
  eq(fmtHMS(60), '1:00');
  eq(fmtHMS(65), '1:05');
  eq(fmtHMS(90), '1:30');
});
test('fmtHMS an hour or more -> H:MM:SS', function () {
  eq(fmtHMS(3600), '1:00:00');
  eq(fmtHMS(3661), '1:01:01');
  eq(fmtHMS(7325), '2:02:05');
});
test('fmtHMS rounds to nearest second', function () {
  eq(fmtHMS(3661.6), '1:01:02');
  eq(fmtHMS(89.4), '1:29');
});

// ── esc ───────────────────────────────────────────────────────────────────────
test('esc escapes & < > but not quotes', function () {
  eq(esc('<b>&"'), '&lt;b&gt;&amp;"');
});
test('esc passes plain text through', function () {
  eq(esc('plain text'), 'plain text');
});
test('esc nullish -> empty string', function () {
  eq(esc(null), '');
  eq(esc(undefined), '');
});

// ── _haversineMi ──────────────────────────────────────────────────────────────
test('_haversineMi same point is zero', function () {
  eq(_haversineMi(40, -105, 40, -105), 0);
});
test('_haversineMi one degree of latitude ~= 69.09 mi', function () {
  approx(_haversineMi(0, 0, 1, 0), 69.09, 0.1);
});
test('_haversineMi is symmetric', function () {
  var d1 = _haversineMi(40.0, -105.0, 40.5, -105.5);
  var d2 = _haversineMi(40.5, -105.5, 40.0, -105.0);
  approx(d1, d2, 1e-9);
});

// ── MAP_TILES ─────────────────────────────────────────────────────────────────
test('MAP_TILES has the five canonical styles', function () {
  eq(Object.keys(MAP_TILES).sort(),
     ['carto-dark', 'carto-light', 'esri-sat', 'osm', 'topo']);
});
test('MAP_TILES each entry has url + attr strings', function () {
  Object.keys(MAP_TILES).forEach(function (k) {
    ok(typeof MAP_TILES[k].url === 'string' && MAP_TILES[k].url.length, k + ' url');
    ok(typeof MAP_TILES[k].attr === 'string' && MAP_TILES[k].attr.length, k + ' attr');
  });
});
test('MAP_TILES osm has no subdomain token, topo requires one', function () {
  ok(MAP_TILES['osm'].url.indexOf('{s}') === -1, 'osm should not use {s}');
  ok(MAP_TILES['topo'].url.indexOf('{s}') !== -1, 'topo should use {s}');
});
