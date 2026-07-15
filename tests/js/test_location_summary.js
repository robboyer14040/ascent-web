'use strict';
/* Regression tests for app/static/location_summary.js — the "Location Summary"
   place resolver. Each case below reproduces a real bug found while building the
   feature, so the fix stays fixed:

     • non-place articles (football cup / elections / scandal) must never win
     • disambiguation pages must never win
     • an empty-description redirect ("San Jose") must be skipped for the city
     • "Municipality in …" must register as a place (a trailing \b once broke it)
     • same-named places disambiguate by the clicked coordinate
     • an exact title match beats a higher-ranked but wrong article
     • admin prefixes (Dimos, Bashkia, Bashkia e) are stripped from the query
     • a place is linked only the first time it appears in a trajectory

   The network-dependent end-to-end resolveTitle() path is covered separately by
   the live/opt-in Python suite; here pickPlace() is fed captured API shapes so
   the selection logic is tested deterministically under jsc. */

var LS = window.LocationSummary;
var T  = LS.__test;

// Build a Wikipedia query `pages` object from [title, description, opts] rows.
// opts: {coord:[lat,lon], dis:true}. Index (relevance rank) follows array order.
function _pages(rows) {
  var pages = {};
  rows.forEach(function (r, i) {
    var title = r[0], desc = r[1] || '', opts = r[2] || {};
    var p = { pageid: i + 1, ns: 0, index: i + 1, title: title, description: desc };
    if (opts.coord) p.coordinates = [{ lat: opts.coord[0], lon: opts.coord[1] }];
    if (opts.dis)   p.pageprops = { disambiguation: '' };
    pages[i + 1] = p;
  });
  return pages;
}

// ── parseLabel: administrative-prefix stripping ───────────────────────────────
test('parseLabel strips Greek "Dimos" prefix and pulls country', function () {
  eq(T.parseLabel('Dimos Epidaurus, Greece'),
     { place: 'Epidaurus', last: 'Epidaurus', country: 'Greece', bare: 'Dimos Epidaurus' });
});

test('parseLabel strips Albanian "Bashkia" and "Bashkia e" prefixes', function () {
  eq(T.parseLabel('Bashkia Roskovec, Albania'),
     { place: 'Roskovec', last: 'Roskovec', country: 'Albania', bare: 'Bashkia Roskovec' });
  eq(T.parseLabel('Bashkia e Fierit, Albania'),
     { place: 'Fierit', last: 'Fierit', country: 'Albania', bare: 'Bashkia e Fierit' });
});

test('parseLabel keeps a multi-word name and its last word, no country', function () {
  eq(T.parseLabel('Santa Cruz County'),
     { place: 'Santa Cruz County', last: 'County', country: '', bare: 'Santa Cruz County' });
  eq(T.parseLabel('San Jose'),
     { place: 'San Jose', last: 'Jose', country: '', bare: 'San Jose' });
});

// ── pickPlace: reject non-place articles ──────────────────────────────────────
test('pickPlace rejects an elections article that shares the place name', function () {
  // "Bashkia Roskovec, Albania" once resolved to "2015 Albanian local elections".
  var pages = _pages([['2015 Albanian local elections', '', {}]]);
  eq(T.pickPlace(pages, 'Roskovec', null), null);
});

test('pickPlace rejects a football cup (year in title) and picks the town', function () {
  // "Dimos Fyli, Greece" once resolved to "2004–05 Greek Football Cup".
  var pages = _pages([
    ['2004–05 Greek Football Cup', '', { coord: [38, 23] }],
    ['Fyli', 'Town in Attica, Greece', { coord: [38.06, 23.66] }],
  ]);
  eq(T.pickPlace(pages, 'Fyli', null), 'Fyli');
});

test('pickPlace rejects a "scandal" article by description', function () {
  // "Bashkia e Fierit, Albania" once resolved to "Albanian incinerators scandal".
  var pages = _pages([
    ['Albanian incinerators scandal', 'Construction corruption scandal in Albania in the 2010s', { coord: [40.7, 19.5] }],
    ['Fier', 'Seventh largest city of Albania', { coord: [40.72, 19.55] }],
  ]);
  eq(T.pickPlace(pages, 'Fier', null), 'Fier');
});

// ── pickPlace: reject disambiguation pages ────────────────────────────────────
test('pickPlace skips a disambiguation page for the real place', function () {
  // "Santa Cruz County" once showed the "may refer to…" disambiguation page.
  var pages = _pages([
    ['Santa Cruz County', 'Topics referred to by the same term', { dis: true }],
    ['Santa Cruz County, California', 'County in California, United States', { coord: [37.03, -122.01] }],
    ['Santa Cruz County, Arizona', 'County in Arizona, United States', { coord: [31.53, -110.83] }],
  ]);
  eq(T.pickPlace(pages, 'Santa Cruz County', null), 'Santa Cruz County, California');
});

// ── pickPlace: empty-description redirect skipped ─────────────────────────────
test('pickPlace skips an empty-description redirect ("San Jose") for the city', function () {
  // "San Jose" (a redirect, no description/coords) once produced no summary.
  var pages = _pages([
    ['San Jose', '', {}],
    ['San Jose, California', 'City in California, United States', { coord: [37.34, -121.89] }],
    ['San José, Costa Rica', 'Capital and largest city of Costa Rica', { coord: [9.93, -84.08] }],
  ]);
  eq(T.pickPlace(pages, 'San Jose', null), 'San Jose, California');
});

// ── pickPlace: "Municipality in …" must be a place (the trailing \b bug) ───────
test('pickPlace treats "Municipality in …" / "Administrative unit …" as places', function () {
  // A stem "municipalit" with a trailing \b never matched "Municipality".
  eq(T.pickPlace(_pages([['Roskovec', 'Municipality in Fier, Albania', {}]]), 'Roskovec', null),
     'Roskovec');
  eq(T.pickPlace(_pages([['Strum, Albania', 'Administrative unit in Fier, Albania', { coord: [40.7, 19.6] }]]), 'Strum', null),
     'Strum, Albania');
});

// ── pickPlace: coordinate disambiguation of same-named places ──────────────────
test('pickPlace disambiguates same-named places by the clicked coordinate', function () {
  var santaCruz = _pages([
    ['Santa Cruz', 'Topics referred to by the same term', { dis: true }],
    ['Santa Cruz, California', 'City in California, United States', { coord: [36.97, -122.03] }],
    ['Santa Cruz de la Sierra', 'City in Bolivia', { coord: [-17.8, -63.18] }],
    ['Santa Cruz de Tenerife', 'City in the Canary Islands, Spain', { coord: [28.47, -16.25] }],
  ]);
  eq(T.pickPlace(santaCruz, 'Santa Cruz', { lat: 36.97, lon: -122.03 }), 'Santa Cruz, California');
  eq(T.pickPlace(santaCruz, 'Santa Cruz', { lat: -17.8, lon: -63.18 }),  'Santa Cruz de la Sierra');
  eq(T.pickPlace(santaCruz, 'Santa Cruz', { lat: 28.47, lon: -16.25 }),  'Santa Cruz de Tenerife');

  var sanJose = _pages([
    ['San Jose', '', {}],
    ['San Jose, California', 'City in California, United States', { coord: [37.34, -121.89] }],
    ['San José, Costa Rica', 'Capital and largest city of Costa Rica', { coord: [9.93, -84.08] }],
  ]);
  eq(T.pickPlace(sanJose, 'San Jose', { lat: 9.93, lon: -84.08 }), 'San José, Costa Rica');
});

// ── pickPlace: reject a same-named place 1000s of km from the clicked point ────
test('pickPlace returns null when the nearest place is beyond MAX_NEAR_KM', function () {
  // "Quesada" on a ride in Jaén, Spain once resolved to "Quesada, San Carlos"
  // in Costa Rica (~8000 km away) because it was the only result with coords.
  var pages = _pages([
    ['Quesada, San Carlos', 'District in San Carlos canton, Costa Rica', { coord: [10.32, -84.42] }],
  ]);
  var jaen = { lat: 37.85, lon: -3.4 };
  eq(T.pickPlace(pages, 'Quesada', jaen), null, 'far Costa Rica place rejected');
  // With the real Spanish town in the result set, proximity picks it.
  pages = _pages([
    ['Quesada, San Carlos', 'District in San Carlos canton, Costa Rica', { coord: [10.32, -84.42] }],
    ['Quesada, Spain', 'Municipality in Andalusia, Spain', { coord: [37.85, -3.07] }],
  ]);
  eq(T.pickPlace(pages, 'Quesada', jaen), 'Quesada, Spain');
});

test('pickPlace rejects an exact title match that sits far from the clicked point', function () {
  // Clicking "Paris" on a ride near Paris, Texas must not open Paris, France.
  var pages = _pages([
    ['Paris', 'Capital of France', { coord: [48.85, 2.35] }],
    ['Paris, Texas', 'City in Texas, United States', { coord: [33.66, -95.55] }],
  ]);
  eq(T.pickPlace(pages, 'Paris', { lat: 33.66, lon: -95.55 }), 'Paris, Texas');
});

// ── pickPlace: exact title beats a higher-ranked but wrong article ────────────
test('pickPlace prefers an exact title match over a higher-ranked place', function () {
  // "Dimos Epidaurus, Greece" once resolved to "Athens" (ranked first).
  var pages = _pages([
    ['Athens', 'Capital and largest city of Greece', { coord: [37.98, 23.72] }],
    ['Epidaurus', 'Town in Greece', { coord: [37.63, 23.15] }],
  ]);
  eq(T.pickPlace(pages, 'Epidaurus', null), 'Epidaurus');
});

// ── link helpers: a place is linked only on first appearance ──────────────────
test('linkifyNames links each distinct place once; repeats are plain text', function () {
  var html = LS.linkifyNames('Chamonix → Argentière → Chamonix');
  eq(countOccurrences(html, 'class="loc-link"'), 2, 'first Chamonix + Argentiere linked, repeat plain');
  ok(html.indexOf('Argentière') !== -1);
});

test('linkifyNames attaches a proximity hint to every link when given', function () {
  var html = LS.linkifyNames('Santa Cruz', { lat: 36.97, lon: -122.03 });
  ok(html.indexOf('data-loc-lat="36.97"') !== -1, 'lat hint present');
  ok(html.indexOf('data-loc-lon="-122.03"') !== -1, 'lon hint present');
});

test('linkifyNames flags its shared midpoint hint as coarse; linkifyList does not', function () {
  ok(LS.linkifyNames('Quesada', { lat: 37.85, lon: -3.4 }).indexOf('data-loc-coarse="1"') !== -1,
     'trajectory links carry the coarse flag');
  ok(LS.linkifyList([{ label: 'Quesada', name: 'Quesada', lat: 37.85, lon: -3.4 }])
       .indexOf('data-loc-coarse') === -1,
     'per-point links are precise, no coarse flag');
});

test('linkifyList carries per-place coordinates and de-dupes by name', function () {
  var html = LS.linkifyList([
    { label: 'Nafplio', name: 'Nafplio', lat: 37.56, lon: 22.8 },
    { label: 'Epidaurus', name: 'Epidaurus', lat: 37.63, lon: 23.15 },
    { label: 'Nafplio', name: 'Nafplio', lat: 37.56, lon: 22.8 },
  ]);
  eq(countOccurrences(html, 'class="loc-link"'), 2, 'repeat Nafplio not re-linked');
  ok(html.indexOf('data-loc-lat="37.63"') !== -1, 'Epidaurus coordinate present');
});
