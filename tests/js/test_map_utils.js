'use strict';
/* Tests for app/static/map_utils.js: baseLayer dispatch + englishLabels. */

// ── englishLabels ─────────────────────────────────────────────────────────────
var EN_FIELD = ['coalesce', ['get', 'name:en'], ['get', 'name:latin'], ['get', 'name']];

test('englishLabels rewrites a latin/nonlatin text-field', function () {
  var style = { layers: [{ id: 'place', type: 'symbol', layout: { 'text-field':
    ['case', ['has', 'name:nonlatin'],
      ['concat', ['get', 'name:latin'], '\n', ['get', 'name:nonlatin']],
      ['coalesce', ['get', 'name:latin'], ['get', 'name']]] } }] };
  MapUtils.englishLabels(style);
  eq(style.layers[0].layout['text-field'], EN_FIELD);
});

test('englishLabels rewrites the legacy {name:latin} token form', function () {
  var style = { layers: [{ layout: { 'text-field': '{name:latin}' } }] };
  MapUtils.englishLabels(style);
  eq(style.layers[0].layout['text-field'], EN_FIELD);
});

test('englishLabels leaves road-shield ref labels alone', function () {
  var style = { layers: [{ layout: { 'text-field': ['to-string', ['get', 'ref']] } }] };
  MapUtils.englishLabels(style);
  eq(style.layers[0].layout['text-field'], ['to-string', ['get', 'ref']]);
});

test('englishLabels skips layers with no text-field', function () {
  var style = { layers: [{ type: 'fill', paint: {} }, { type: 'symbol', layout: {} }] };
  MapUtils.englishLabels(style);          // must not throw
  ok(!style.layers[1].layout['text-field'], 'no text-field invented');
});

test('englishLabels tolerates a style with no layers', function () {
  eq(Object.keys(MapUtils.englishLabels({})), []);
});

// ── baseLayer ─────────────────────────────────────────────────────────────────
test('baseLayer builds a raster tile layer from style.url', function () {
  var layer = MapUtils.baseLayer({ url: 'https://tiles/{z}/{x}/{y}.png', attr: '© OSM' });
  eq(layer._url, 'https://tiles/{z}/{x}/{y}.png');
  eq(layer._opts.maxZoom, 19);
  eq(layer._opts.attribution, '© OSM');
});

test('baseLayer opts override the defaults', function () {
  var layer = MapUtils.baseLayer({ url: 'u', attr: 'a' }, { maxZoom: 22 });
  eq(layer._opts.maxZoom, 22);
});

test('baseLayer builds a vector layer from style.styleUrl', function () {
  var layer = MapUtils.baseLayer({ styleUrl: 'https://tiles/styles/liberty', attr: '© OFM' });
  ok(layer._url === undefined, 'vector styles must not go through L.tileLayer');
  eq(layer._styleUrl, 'https://tiles/styles/liberty');
  eq(layer.options.attribution, '© OFM');
});

test('baseLayer reuses one vector layer class', function () {
  var a = MapUtils.baseLayer({ styleUrl: 's1' });
  var b = MapUtils.baseLayer({ styleUrl: 's2' });
  ok(a.constructor === b.constructor, 'class rebuilt per call');
});
