'use strict';
/* test_stage_table.js — the shared tour stage table (app/static/stage_table.js).
   Covers the parts that are pure logic: the column set it builds from the shared
   list, the one-time migration of preferences stored under the table's old
   column ids, the stage cell renderers, the sort accessors, and the grid-template
   / --stage-min-w math that makes the column dividers behave. */

// An earlier test file (test_elev_panel.js) swaps the global U for a bare
// {metric} stub, so restore a real one rather than depending on load order.
U = makeUnits(false);

// A stage with a completed ride, and one still to do.
var _done = {
  id: 7, stage_num: 2, name: 'Col du Galibier',
  distance_mi: 10, climb_ft: 3000,
  completion: {
    activity_id: 91, date: '2026-07-12',
    duration_s: 7200, moving_s: 3600,
    avg_moving_speed_mph: 12.5, avg_hr: 142.4, max_hr: 171.6,
    avg_cadence: 78.2, avg_power: 211.7, suffer_score: 88.5, calories: 1430.2,
  },
};
var _todo = { id: 8, stage_num: 3, name: 'Col de la Croix', distance_mi: 5, climb_ft: 1000 };

function _col(id) {
  return StageTable.COLS.filter(function (c) { return c.id === id; })[0];
}
function _render(id, stage) { return _col(id).render(stage); }

test('stage_table: offers exactly the shared column list, in the same order', function () {
  eq(StageTable.COLS.map(function (c) { return c.id; }), LIST_COL_IDS);
});

test('stage_table: labels match Activities, except the title column reads "Stage"', function () {
  StageTable.COLS.forEach(function (c, i) {
    var want = LIST_COLS[i].id === 'name' ? 'Stage' : LIST_COLS[i].label;
    eq(c.label, want, 'label for ' + c.id);
  });
});

test('stage_table: every column has a width sized for the sidebar', function () {
  StageTable.COLS.forEach(function (c) {
    ok(/^(\d+px|minmax\(\d+px,1fr\))$/.test(c.w), c.id + ' width is ' + c.w);
  });
  eq(_col('name').w, 'minmax(60px,1fr)', 'the title column is the flexible track');
});

test('stage_table: User and Pace have no stage data, so they render an em-dash', function () {
  eq(_render('user', _done), '—');
  eq(_render('pace', _done), '—');
});

// ── Cell renderers ───────────────────────────────────────────────────────────
test('stage_table: distance and ascent come from the route, in the active units', function () {
  U.metric = false;
  eq(_render('dist',  _done), '10.0');
  eq(_render('climb', _done), 3000);
  U.metric = true;
  eq(_render('dist',  _done), '16.1');
  eq(_render('climb', _done), 914);
  U.metric = false;
});

test('stage_table: MovTime and Duration are distinct columns', function () {
  eq(_render('active',   _done), fmtHMS(3600));
  eq(_render('duration', _done), fmtHMS(7200));
  ok(_render('active', _done) !== _render('duration', _done), 'they must not be the same value');
});

test('stage_table: MvSpd is the ride average; AvgSpd divides by elapsed time', function () {
  U.metric = false;
  eq(_render('mvspd',  _done), '12.5');
  eq(_render('avgspd', _done), '5.0');   // 10 mi over 7200 s elapsed
  U.metric = true;
  eq(_render('mvspd',  _done), '20.1');
  eq(_render('avgspd', _done), '8.0');
  U.metric = false;
});

test('stage_table: ride metrics round, and carry their unit suffix where Activities does', function () {
  eq(_render('hr',       _done), 142);
  eq(_render('maxhr',    _done), 172);
  eq(_render('calories', _done), 1430);
  eq(_render('suffer',   _done), 89);
  eq(_render('power',    _done), '212 W');
  eq(_render('cadence',  _done), '78 rpm');
});

test('stage_table: an unridden stage shows an em-dash for every ride metric', function () {
  ['date','active','duration','mvspd','avgspd','hr','maxhr','power','cadence','calories','suffer']
    .forEach(function (id) { eq(_render(id, _todo), '—', id + ' on an unridden stage'); });
});

test('stage_table: the name cell marks completion, and keeps the route distance', function () {
  ok(_render('name', _done).indexOf('✓') >= 0, 'a ridden stage gets a check');
  ok(_render('name', _todo).indexOf('stage-dot') >= 0, 'an unridden stage gets a dot');
  ok(_render('name', _done).indexOf('Col du Galibier') >= 0);
});

test('stage_table: a stage name is escaped, not injected', function () {
  var html = _render('name', { id: 9, name: '<img src=x onerror=1>' });
  ok(html.indexOf('<img') < 0, 'raw tag must not survive: ' + html);
  ok(html.indexOf('&lt;img') >= 0);
});

// ── Sorting ──────────────────────────────────────────────────────────────────
test('stage_table: a sort key exists for every sortable column', function () {
  StageTable.COLS.forEach(function (c) {
    if (c.sort) ok(typeof StageTable.SORT_KEY[c.sort] === 'function',
                   'missing SORT_KEY for ' + c.id);
  });
});

test('stage_table: sort accessors read the completion, defaulting to 0', function () {
  var K = StageTable.SORT_KEY;
  eq(K.stage_num(_done), 2);
  eq(K.name(_done), 'col du galibier');
  eq(K.date(_done), '2026-07-12');
  eq(K.dist(_done), 10);
  eq(K.active(_done), 3600);
  eq(K.duration(_done), 7200);
  eq(K.mvspd(_done), 12.5);
  eq(K.avgspd(_done), 5);
  eq(K.hr(_done), 142.4);
  eq(K.power(_done), 211.7);
  // An unridden stage sorts as zero / empty rather than throwing.
  eq(K.active(_todo), 0);
  eq(K.date(_todo), '');
  eq(K.avgspd(_todo), 0);
});

// ── Grid template + the min-width that makes dividers draggable ──────────────
test('stage_table: the default column set is name-first and matches the old default', function () {
  eq(StageTable.DEFAULT_COL_IDS, ['name','dist','climb','date','active']);
});

test('stage_table: colsTemplate lists one track per active column', function () {
  var tpl = StageTable.colsTemplate().split(' ');
  eq(tpl.length, StageTable.colIds().length);
});

test('stage_table: --stage-min-w sums the tracks, counting a flexible one at its floor', function () {
  var hScroll = document.createElement('div');
  document._byId['stage-h-scroll'] = hScroll;

  StageTable.applyMinW();
  var active = StageTable.activeCols();
  var want = active.reduce(function (t, c) {
    var w = StageTable.widthOverrides()[c.id] || c.w;
    var m = w.match(/minmax\((\d+)/);
    return t + (m ? parseInt(m[1]) : parseInt(w));
  }, 0);
  eq(hScroll.style.getPropertyValue('--stage-min-w'), want + 'px');

  // Widening one column must raise the floor by exactly that much — this is what
  // lets the grid overflow into the scroll area instead of the flexible track
  // silently re-absorbing the drag.
  var before = parseInt(hScroll.style.getPropertyValue('--stage-min-w'));
  StageTable.widthOverrides()['dist'] = '104px';
  StageTable.applyMinW();
  var after = parseInt(hScroll.style.getPropertyValue('--stage-min-w'));
  eq(after - before, 104 - parseInt(_col('dist').w));
  delete StageTable.widthOverrides()['dist'];

  delete document._byId['stage-h-scroll'];
});

// ── One-time migration of preferences stored under the old column ids ────────
// stage_table_fixture.js seeded LEGACY_STAGE_COLS / LEGACY_STAGE_WIDTHS before
// the module loaded, so these observe the real migration, not a re-run of it.
test('stage_table: legacy column ids are renamed to the shared ones', function () {
  eq(JSON.parse(localStorage.getItem('ascent-stage-cols')),
     ['name','dist','climb','date','active','mvspd'],
     "'duration'->'active', 'speed'->'mvspd', 'num' dropped");
});

test('stage_table: the live column order is the migrated one', function () {
  eq(StageTable.colIds(), ['name','dist','climb','date','active','mvspd']);
});

test('stage_table: legacy width keys are renamed and flexible widths collapsed', function () {
  eq(JSON.parse(localStorage.getItem('ascent-stage-col-widths')),
     { name: '384px', active: '64px', mvspd: '70px' });
  eq(StageTable.widthOverrides().name, '384px',
     'a stored minmax() would leave the title column unshrinkable');
});

test('stage_table: migration is stamped so it cannot run twice', function () {
  eq(localStorage.getItem('ascent-stage-cols-v'), '2');
});

// ── render(): the desktop column table ───────────────────────────────────────
function _renderInto(stages, selId) {
  var list  = document.createElement('div');
  var empty = document.createElement('div');
  document._byId['stage-list']   = list;
  document._byId['sidebar-empty'] = empty;
  StageTable.init({
    stages:      function () { return stages; },
    pointsCache: function () { return {}; },
    selectedId:  function () { return selId == null ? null : selId; },
    onSelect:    function () {},
  });
  StageTable.render();
  delete document._byId['stage-list'];
  delete document._byId['sidebar-empty'];
  return { list: list, empty: empty };
}

test('stage_table render: one row per stage, each a grid of the active columns', function () {
  var r = _renderInto([_done, _todo], null);
  eq(r.list.children.length, 2);
  r.list.children.forEach(function (row) {
    eq(countOccurrences(row.innerHTML, 'class="st-cell'),
       StageTable.colIds().length - 1, 'non-title cells');
    eq(countOccurrences(row.innerHTML, 'class="st-name-cell"'), 1, 'exactly one title cell');
    eq(row.style.getPropertyValue('--stage-cols'), StageTable.colsTemplate());
  });
});

test('stage_table render: the selected stage row is marked active', function () {
  var r = _renderInto([_done, _todo], 8);
  eq(r.list.children.map(function (x) { return x.className; }),
     ['stage-row', 'stage-row active']);
});

test('stage_table render: rows carry their stage id for the click handler', function () {
  var r = _renderInto([_done, _todo], null);
  eq(r.list.children.map(function (x) { return x.dataset.id; }), [7, 8]);
});

test('stage_table render: right-aligned columns get the right-aligned cell class', function () {
  var r = _renderInto([_done], null);
  var rights = StageTable.activeCols().filter(function (c) {
    return c.align === 'right' && !c.titleCell;
  }).length;
  eq(countOccurrences(r.list.children[0].innerHTML, 'st-cell st-cell-right'), rights);
});

test('stage_table render: the empty-state shows only when there are no stages', function () {
  eq(_renderInto([], null).empty.style.display, '');
  eq(_renderInto([_done], null).empty.style.display, 'none');
});

test('stage_table render: rows come out in the table sort order', function () {
  var r = _renderInto([_todo, _done], null);   // passed in reverse stage order
  eq(r.list.children.map(function (x) { return x.dataset.id; }), [7, 8],
     'default sort is by stage number, so the stage-2 row leads');
});
