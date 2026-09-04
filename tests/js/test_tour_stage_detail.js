'use strict';
/* test_tour_stage_detail.js — draw-order rules in TourStageDetail.drawRoutes.
   Leaflet is stubbed: each polyline records the order it was added and every
   bringToFront() call, so "which line ends up on top" is checkable here. */

// stageColor is defined per page (tour.html / tour_share.html); drawRoutes calls it.
var C_DONE = '#ef4444', C_TODO = '#1e40af';
function stageColor(s) { return s.completion ? C_DONE : C_TODO; }

// Stub Leaflet's polyline/marker + a layer group, recording paint order.
function _mapStubs() {
  var order = [];        // ids in final paint order (add order, then bringToFront)
  var group = { clearLayers: function () { order.length = 0; } };
  var nextId = 0;
  L.polyline = function (pts, opts) {
    var id = 'line' + (nextId++);
    return {
      opts: opts,
      addTo: function () { order.push(id); return this; },
      bringToFront: function () {
        order.splice(order.indexOf(id), 1); order.push(id);
        return this;
      },
    };
  };
  L.marker = function () { return { addTo: function () { return this; } }; };
  return { group: group, order: order };
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

test('drawRoutes: completed route paints above an unridden alternate of it', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([{ date: '2025-06-01' }, null]);
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: null, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.order, ['line1', 'line0'], 'the ridden stage must end up on top');
});

test('drawRoutes: an unridden stage stays below its completed alternate', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([null, { date: '2025-06-01' }]);
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: null, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.order, ['line0', 'line1'], 'the ridden alternate must end up on top');
});

test('drawRoutes: the selected stage still paints above a completed one', function () {
  var s = _mapStubs(), d = _sharedSegmentCtx([{ date: '2025-06-01' }, null]);
  TourStageDetail.drawRoutes({
    map: {}, routeGroup: s.group, stages: d.stages, pointsCache: d.pointsCache,
    activeId: 2, selColor: C_DONE, startIcon: {}, endIcon: {},
  });
  eq(s.order, ['line0', 'line1'], 'the active stage stays frontmost');
});
