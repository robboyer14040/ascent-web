'use strict';
/* test_list_cols.js — the one shared column list (app/static/list_cols.js).
   These lock down the contract that Activities, Tours and Tour_shared all build
   their tables from: which columns exist, in what order, and what buildListCols
   does with a page's per-page widths / sorts / renderers. */

// An earlier test file (test_elev_panel.js) swaps the global U for a bare
// {metric} stub, so restore a real one rather than depending on load order.
U = makeUnits(false);

test('list_cols: the canonical 16 columns, in order', function () {
  eq(LIST_COL_IDS, ['user','date','name','dist','active','duration','climb','mvspd',
                    'avgspd','hr','maxhr','power','cadence','calories','suffer','pace']);
});

test('list_cols: labels match what the picker shows', function () {
  var byId = {};
  LIST_COLS.forEach(function (c) { byId[c.id] = c.label; });
  eq(byId.user, 'User');
  eq(byId.date, 'Date/Time');
  eq(byId.name, 'Title');
  eq(byId.active, 'MovTime');
  eq(byId.duration, 'Duration');
  eq(byId.climb, 'Ascent');
  eq(byId.mvspd, 'MvSpd');
  eq(byId.avgspd, 'AvgSpd');
  eq(byId.calories, 'Cal');
});

test('list_cols: only the measured columns carry a unit', function () {
  var withUnit = LIST_COLS.filter(function (c) { return c.unit; })
                          .map(function (c) { return c.id + ':' + c.unit; });
  eq(withUnit, ['dist:dist','climb:climb','mvspd:speed','avgspd:speed']);
});

test('list_cols: time and count columns are right-aligned, text columns left', function () {
  var left = LIST_COLS.filter(function (c) { return c.align === 'left'; })
                      .map(function (c) { return c.id; });
  eq(left, ['user','date','name']);
});

test('buildListCols: a page supplies widths, sorts and renderers by id', function () {
  var cols = buildListCols({
    w:      { dist: '78px' },
    sort:   { dist: 'distance_m' },
    render: { dist: function (r) { return 'D=' + r.d; } },
    fallbackW: '44px',
  });
  var dist = cols.filter(function (c) { return c.id === 'dist'; })[0];
  eq(dist.w, '78px');
  eq(dist.sort, 'distance_m');
  eq(dist.render({d: 7}), 'D=7');
  eq(dist.align, 'right');
});

test('buildListCols: a column the page cannot fill still exists, rendering an em-dash', function () {
  var cols = buildListCols({ w: {}, render: {} });
  eq(cols.length, 16, 'every column is still offered');
  var pace = cols.filter(function (c) { return c.id === 'pace'; })[0];
  eq(pace.render({anything: 1}), '—');
  eq(pace.sort, '', 'unsortable without a supplied sort key');
});

test('buildListCols: fallbackW covers ids the page omits', function () {
  var cols = buildListCols({ w: { name: '1fr' }, fallbackW: '44px' });
  eq(cols.filter(function (c) { return c.id === 'name'; })[0].w, '1fr');
  eq(cols.filter(function (c) { return c.id === 'suffer'; })[0].w, '44px');
});

test('buildListCols: a page may relabel a column without forking the list', function () {
  var cols = buildListCols({ w: {}, label: { name: 'Stage' } });
  eq(cols.filter(function (c) { return c.id === 'name'; })[0].label, 'Stage');
  eq(cols.filter(function (c) { return c.id === 'dist'; })[0].label, 'Dist',
     'other labels are untouched');
});

test('buildListCols: only the title column is a titleCell', function () {
  var cols = buildListCols({ w: {} });
  var titles = cols.filter(function (c) { return c.titleCell; }).map(function (c) { return c.id; });
  eq(titles, ['name']);
});

test('buildListCols: unitLabel reads the live unit setting', function () {
  var cols = buildListCols({ w: {} });
  var dist = cols.filter(function (c) { return c.id === 'dist'; })[0];
  var climb = cols.filter(function (c) { return c.id === 'climb'; })[0];
  var speed = cols.filter(function (c) { return c.id === 'mvspd'; })[0];
  U.metric = false;
  eq([dist.unitLabel(), climb.unitLabel(), speed.unitLabel()], ['mi','ft','mph']);
  U.metric = true;
  eq([dist.unitLabel(), climb.unitLabel(), speed.unitLabel()], ['km','m','km/h']);
  U.metric = false;
  eq(cols.filter(function (c) { return c.id === 'hr'; })[0].unitLabel, undefined,
     'unitless columns get no unit line');
});
