'use strict';
/* test_tour_stage_detail.js — which routes TourStageDetail.drawRoutes paints,
   and in what order. Leaflet is stubbed: each polyline records the order it was
   added and every bringToFront() call. */

// stageColor is defined per page (tour.html / tour_share.html); drawRoutes calls it.
var C_DONE = '#ef4444', C_TODO = '#1e40af';
function stageColor(s) { return s.completion ? C_DONE : C_TODO; }

// Stub Leaflet's polyline/marker + a layer group, recording paint order.
function _mapStubs() {
  var order = [];        // ids in final paint order (add order, then bringToFront)
  var lines = [];        // polylines actually added, in add order
  var group = { clearLayers: function () { order.length = 0; lines.length = 0; } };
  var nextId = 0;
  L.polyline = function (pts, opts) {
    var id = 'line' + (nextId++);
    return {
      opts: opts,
      addTo: function () { order.push(id); lines.push(this); return this; },
      bringToFront: function () {
        order.splice(order.indexOf(id), 1); order.push(id);
        return this;
      },
    };
  };
  L.marker = function () { return { addTo: function () { return this; } }; };
  return { group: group, order: order, lines: lines };
}

// Two stages over the same road: stage 1 ridden, stage 2 its unridden alternate.
function _sharedSegmentCtx(completions) {
  var pts = [[45.0, 6.0, 100], [45.01, 6.01, 200], [45.02, 6.02, 300]];
  var stages = [
    { id: 1, stage_num: 1, name: 'Col A',  start_lat: 45.0, start_lon: 6.0, completion: completions[0] },
    { id: 2, stage_num: 2, name: 'Col A alt', start_lat: 45.0, start_lon: 6.0, completion: completions[1], alt_override: 1 },
  ];
  return { stages: stages, pointsCache: { '1': pts, '2': pts } };
}

test('drawRoutes: an unridden alternate of a ridden stage is not drawn', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([{ date: '2025-06-01' }, null]);
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: null, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.lines.length, 1, 'only the ridden stage is drawn');
  eq(s.lines[0].opts.color, C_DONE, 'and it is drawn as done');
});

test('drawRoutes: a ridden alternate hides the primary it replaces', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([null, { date: '2025-06-01' }]);
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: null, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.lines.length, 1, 'only the ridden alternate is drawn');
  eq(s.lines[0].opts.color, C_DONE, 'and it is drawn as done');
});

test('drawRoutes: with nothing in the group ridden, every alternate is drawn', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([null, null]);
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: null, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.lines.length, 2, 'both routes still show when neither is done');
  eq(s.lines.map(function (l) { return l.opts.color; }), [C_TODO, C_TODO]);
});

test('drawRoutes: selecting a stage still shows its alternates for comparison', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([{ date: '2025-06-01' }, null]);
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: 2, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.lines.length, 2, 'the selected unridden alternate and its ridden sibling');
  eq(s.order, ['line0', 'line1'], 'the active stage stays frontmost');
});

test('drawRoutes: a ridden stage paints above an unridden one it is not grouped with', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([{ date: '2025-06-01' }, null]);
  d.stages[1].alt_override = 0;          // its own segment, not an alternate
  d.stages[1].start_lat = 46.0; d.stages[1].start_lon = 7.0;
  d.pointsCache['2'] = [[46.0, 7.0, 100], [46.01, 7.01, 200]];
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: null, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.lines.length, 2, 'an ungrouped stage is never hidden');
  eq(s.order, ['line1', 'line0'], 'the ridden stage is lifted to the front');
});
