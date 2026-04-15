// ── DEFINE SEGMENT ────────────────────────────────────────────────────────────

const seg = {
  open: false,
  actId: null,
  data: null,       // ref to elevChartData {dist_m, alt_ft}
  geo:  null,       // ref to splitsState.geo (GeoJSON)
  editingId: null,  // segment ID being edited, or null for new
  startIdx: 0,
  endIdx: 0,
  viewStart: 0,
  viewEnd: 0,
  initViewSpan: 0,   // view span at zoom=50% (slider center)
  map: null,
  segPoly: null,
  prePoly: null,
  postPoly: null,
  startDot: null,
  endDot: null,
  chart: null,
  dragging: null,       // null | 'start' | 'end'
  panOriginX: null,
  panOriginView: null,
};

// ── Public entry point ─────────────────────────────────────────────────────────

function openDefineSegment() {
  if (!elevChartData || !elevChartData.dist_m || !elevChartData.dist_m.length) {
    alert('No GPS elevation data for this activity.');
    return;
  }

  seg.actId = currentAct ? currentAct.id : null;
  seg.data  = elevChartData;
  seg.geo   = splitsState ? splitsState.geo : null;

  // Determine edit vs. create mode
  const editId = window._selectedSegmentId || null;
  seg.editingId = editId;

  // Smart defaults
  const defaults = segSmartDefaults();
  seg.startIdx = defaults.startIdx;
  seg.endIdx   = defaults.endIdx;

  // In edit mode, initialize handles at the segment's matched indices
  if (editId) {
    const sel = document.getElementById('seg-selector');
    const opt = sel && sel.selectedIndex > 0 ? sel.options[sel.selectedIndex] : null;
    if (opt && opt.dataset.startIdx != null && opt.dataset.endIdx != null) {
      const s0 = parseInt(opt.dataset.startIdx, 10);
      const s1 = parseInt(opt.dataset.endIdx, 10);
      if (!isNaN(s0) && !isNaN(s1)) { seg.startIdx = s0; seg.endIdx = s1; }
    }
  }

  segInitViewWindow();
  seg.initViewSpan = seg.viewEnd - seg.viewStart;   // anchor for 50% slider

  // Show overlay
  const overlay = document.getElementById('seg-overlay');
  overlay.classList.add('open');
  seg.open = true;

  // Reset zoom slider to 50% (= current view)
  const slider = document.getElementById('seg-zoom-slider');
  if (slider) slider.value = 50;

  // Set modal header title
  const headerSpan = document.querySelector('#seg-header span');
  if (headerSpan) headerSpan.textContent = editId ? 'Edit Segment' : 'Define Segment';

  // Init name field
  const nameInput = document.getElementById('seg-name-input');
  if (nameInput) {
    if (editId) {
      const sel = document.getElementById('seg-selector');
      const opt = sel && sel.selectedIndex > 0 ? sel.options[sel.selectedIndex] : null;
      nameInput.value = opt ? opt.textContent : '';
    } else {
      nameInput.value = '';
    }
  }

  // Show/hide Update/Delete buttons; set Save label
  const saveBtn   = document.getElementById('seg-save-btn');
  const updateBtn = document.getElementById('seg-update-btn');
  const deleteBtn = document.getElementById('seg-delete-btn');
  if (saveBtn)   saveBtn.textContent = editId ? 'Save as new…' : 'Save';
  if (updateBtn) updateBtn.style.display = editId ? '' : 'none';
  if (deleteBtn) deleteBtn.style.display = editId ? '' : 'none';

  // Init Leaflet map
  segInitMap();

  // Init Chart.js chart
  segBuildChart();

  // Position handles after first render; invalidateSize after layout settles
  requestAnimationFrame(() => {
    if (seg.map) seg.map.invalidateSize();
    segPositionHandles();
    segUpdateMap();
    segFitMap();
    segUpdateStats();
  });
}

function closeDefineSegment() {
  if (seg.chart) { seg.chart.destroy(); seg.chart = null; }
  if (seg.map)   { seg.map.remove(); seg.map = null; }
  seg.segPoly = null; seg.prePoly = null; seg.postPoly = null;
  seg.startDot = null; seg.endDot = null;
  seg.open = false; seg.dragging = null;

  const overlay = document.getElementById('seg-overlay');
  if (overlay) overlay.classList.remove('open');
}

// ── Smart defaults ─────────────────────────────────────────────────────────────

function segSmartDefaults() {
  const n = seg.data.dist_m.length;
  let startIdx = 0, endIdx = n - 1;

  if (cmp && cmp.selStartIdx != null && cmp.selEndIdx != null) {
    startIdx = Math.max(0, Math.min(cmp.selStartIdx, n - 1));
    endIdx   = Math.max(0, Math.min(cmp.selEndIdx, n - 1));
    if (endIdx <= startIdx) endIdx = Math.min(startIdx + 1, n - 1);
  } else {
    endIdx = segFindHillEnd(0);
  }

  return { startIdx, endIdx };
}

function segFindHillEnd(fromIdx) {
  const { dist_m, alt_ft } = seg.data;
  const n = dist_m.length;
  if (n < 4) return n - 1;

  const w = Math.max(10, Math.floor(n * 0.02));

  // Smooth alt_ft
  const smooth = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let sum = 0, cnt = 0;
    for (let j = Math.max(0, i - w); j <= Math.min(n - 1, i + w); j++) {
      sum += alt_ft[j]; cnt++;
    }
    smooth[i] = sum / cnt;
  }

  // Find base: local min in first 20% of remaining after fromIdx
  const searchEnd = fromIdx + Math.floor((n - fromIdx) * 0.20);
  let baseIdx = fromIdx;
  for (let i = fromIdx + 1; i <= Math.min(searchEnd, n - w - 1); i++) {
    if (smooth[i] < smooth[baseIdx]) baseIdx = i;
  }

  // Scan from base+w onward for local max
  const minDistM = 800; // 0.5 mi
  for (let i = baseIdx + w; i < n - w; i++) {
    if ((dist_m[i] - dist_m[baseIdx]) < minDistM) continue;
    // Local max within ±w?
    let isMax = true;
    for (let j = Math.max(0, i - w); j <= Math.min(n - 1, i + w); j++) {
      if (smooth[j] > smooth[i]) { isMax = false; break; }
    }
    if (isMax) return i;
  }

  // Fallback: ~10 miles from start
  const targetDist = dist_m[fromIdx] + 10 * 1609.344;
  let lo = fromIdx, hi = n - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (dist_m[mid] < targetDist) lo = mid; else hi = mid;
  }
  return Math.min(hi, n - 1);
}

function segInitViewWindow() {
  const n = seg.data.dist_m.length;
  const segLen = seg.endIdx - seg.startIdx;
  const pad = Math.max(Math.floor(segLen * 0.15), 5);
  seg.viewStart = Math.max(0, seg.startIdx - pad);
  seg.viewEnd   = Math.min(n - 1, seg.endIdx + pad);
}

// ── Leaflet map ────────────────────────────────────────────────────────────────

function segInitMap() {
  const container = document.getElementById('seg-map');
  if (!container) return;

  // Destroy existing
  if (seg.map) { seg.map.remove(); seg.map = null; }

  seg.map = L.map(container, { zoomControl: true, attributionControl: false });

  const styleKey = _uiPrefsGet('ascent-map-style') || 'osm';
  const style = MAP_STYLES[styleKey] || MAP_STYLES['osm'];
  seg.tileLayer = L.tileLayer(style.url, { maxZoom: 19, attribution: style.attr }).addTo(seg.map);

  // Mark active style button
  document.querySelectorAll('#seg-map-style-bar .map-style-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.style === styleKey));
}

function setSegMapStyle(styleKey) {
  const style = MAP_STYLES[styleKey] || MAP_STYLES['osm'];
  if (seg.tileLayer && seg.map) { seg.map.removeLayer(seg.tileLayer); }
  seg.tileLayer = L.tileLayer(style.url, { maxZoom: 19, attribution: style.attr }).addTo(seg.map);
  _uiPrefsSet('ascent-map-style', styleKey);
  document.querySelectorAll('#seg-map-style-bar .map-style-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.style === styleKey));
}

function segUpdateMap() {
  if (!seg.map || !seg.geo) return;

  const coords = (seg.geo.geometry ? seg.geo.geometry.coordinates : seg.geo.coordinates) || [];
  if (!coords.length) return;

  const n = seg.data.dist_m.length;
  const ci1 = Math.floor(seg.startIdx / (n - 1) * (coords.length - 1));
  const ci2 = Math.ceil(seg.endIdx   / (n - 1) * (coords.length - 1));

  // Convert GeoJSON [lon,lat,alt] → Leaflet [lat,lon]
  const toLL = c => [c[1], c[0]];

  const preCoords  = coords.slice(0, ci1 + 1).map(toLL);
  const segCoords  = coords.slice(ci1, ci2 + 1).map(toLL);
  const postCoords = coords.slice(ci2).map(toLL);

  if (seg.prePoly)  { seg.map.removeLayer(seg.prePoly);  seg.prePoly  = null; }
  if (seg.postPoly) { seg.map.removeLayer(seg.postPoly); seg.postPoly = null; }
  if (seg.segPoly)  { seg.map.removeLayer(seg.segPoly);  seg.segPoly  = null; }
  if (seg.startDot) { seg.map.removeLayer(seg.startDot); seg.startDot = null; }
  if (seg.endDot)   { seg.map.removeLayer(seg.endDot);   seg.endDot   = null; }

  if (preCoords.length  > 1) seg.prePoly  = L.polyline(preCoords,  { color: '#64748b', weight: 3, opacity: 0.6 }).addTo(seg.map);
  if (postCoords.length > 1) seg.postPoly = L.polyline(postCoords, { color: '#64748b', weight: 3, opacity: 0.6 }).addTo(seg.map);
  if (segCoords.length  > 1) {
    seg.segPoly = L.polyline(segCoords, { color: '#ef4444', weight: 5, opacity: 0.9 }).addTo(seg.map);

    // Red dot markers
    const dotIcon = L.divIcon({ className: '', html: '<div style="width:10px;height:10px;background:#ef4444;border:2px solid #fff;border-radius:50%;box-shadow:0 1px 4px rgba(0,0,0,.5)"></div>', iconSize: [10, 10], iconAnchor: [5, 5] });
    seg.startDot = L.marker(segCoords[0], { icon: dotIcon }).addTo(seg.map);
    seg.endDot   = L.marker(segCoords[segCoords.length - 1], { icon: dotIcon }).addTo(seg.map);
  }
}

function segFitMap() {
  if (!seg.map) return;
  if (seg.segPoly) {
    seg.map.fitBounds(seg.segPoly.getBounds(), { padding: [30, 30] });
  } else if (seg.geo) {
    const coords = (seg.geo.geometry ? seg.geo.geometry.coordinates : seg.geo.coordinates) || [];
    const toLL = c => [c[1], c[0]];
    const allLL = coords.map(toLL);
    if (allLL.length > 1) seg.map.fitBounds(L.polyline(allLL).getBounds(), { padding: [20, 20] });
  }
}

// ── Chart.js chart ─────────────────────────────────────────────────────────────

function segBuildChart() {
  const canvas = document.getElementById('seg-chart');
  if (!canvas) return;
  if (seg.chart) { seg.chart.destroy(); seg.chart = null; }

  const { viewStart, viewEnd } = seg;
  const allPts  = segBuildDataset(viewStart, viewEnd, viewStart, viewEnd);    // gray: all view-window
  const segPts  = segBuildDataset(seg.startIdx, seg.endIdx, viewStart, viewEnd); // red: segment only

  const handlePosPlugin = {
    id: 'segHandlePos',
    afterDraw() { segPositionHandles(); },
  };

  seg.chart = new Chart(canvas, {
    type: 'line',
    plugins: [handlePosPlugin],
    data: {
      datasets: [
        {
          data: allPts,
          borderColor: '#64748b',
          backgroundColor: 'rgba(100,116,139,.15)',
          borderWidth: 1.5,
          fill: true,
          pointRadius: 0,
          tension: 0.3,
        },
        {
          data: segPts,
          borderColor: '#ef4444',
          backgroundColor: 'rgba(239,68,68,.15)',
          borderWidth: 3,
          fill: false,
          pointRadius: 0,
          tension: 0.3,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: {
          type: 'linear',
          min: segIdxToDist(viewStart),
          max: segIdxToDist(viewEnd),
          ticks: { color: '#94a3b8', font: { size: 10 }, maxTicksLimit: 8,
                   callback: v => v.toFixed(1) },
          grid: { color: 'rgba(148,163,184,.12)' },
        },
        y: {
          ticks: { color: '#94a3b8', font: { size: 10 }, maxTicksLimit: 5,
                   callback: v => Math.round(v) },
          grid: { color: 'rgba(148,163,184,.12)' },
        },
      },
    },
  });
}

// Build {x, y} dataset for indices from [fromIdx..toIdx], but only include points
// within the view window [viewStart..viewEnd].
function segBuildDataset(fromIdx, toIdx, viewStart, viewEnd) {
  const { dist_m, alt_ft } = seg.data;
  const pts = [];
  const lo = Math.max(fromIdx, viewStart);
  const hi = Math.min(toIdx, viewEnd);
  for (let i = lo; i <= hi; i++) {
    pts.push({ x: segIdxToDist(i), y: U.alt(alt_ft[i]) });
  }
  return pts;
}

function segIdxToDist(idx) {
  const { dist_m } = seg.data;
  return dist_m[idx] / (U.metric ? 1000 : 1609.344);
}

function segUpdateChart() {
  if (!seg.chart) return;

  const { viewStart, viewEnd } = seg;
  seg.chart.data.datasets[0].data = segBuildDataset(viewStart, viewEnd, viewStart, viewEnd);
  seg.chart.data.datasets[1].data = segBuildDataset(seg.startIdx, seg.endIdx, viewStart, viewEnd);
  seg.chart.options.scales.x.min = segIdxToDist(viewStart);
  seg.chart.options.scales.x.max = segIdxToDist(viewEnd);
  seg.chart.update('none');
  segPositionHandles();
}

// ── Handle positioning ─────────────────────────────────────────────────────────

function segPositionHandles() {
  if (!seg.chart || !seg.chart.scales || !seg.chart.chartArea) return;
  const xScale = seg.chart.scales.x;
  const yScale = seg.chart.scales.y;
  if (!xScale || !yScale) return;

  const { alt_ft } = seg.data;
  const startPx = xScale.getPixelForValue(segIdxToDist(seg.startIdx));
  const endPx   = xScale.getPixelForValue(segIdxToDist(seg.endIdx));
  const startY  = yScale.getPixelForValue(U.alt(alt_ft[seg.startIdx]));
  const endY    = yScale.getPixelForValue(U.alt(alt_ft[seg.endIdx]));

  const hStart = document.getElementById('seg-handle-start');
  const hEnd   = document.getElementById('seg-handle-end');
  if (hStart) { hStart.style.left = startPx + 'px'; hStart.style.top = startY + 'px'; }
  if (hEnd)   { hEnd.style.left   = endPx   + 'px'; hEnd.style.top   = endY   + 'px'; }
}

// ── Drag handles ───────────────────────────────────────────────────────────────

function segPxToIdx(px) {
  if (!seg.chart || !seg.chart.scales) return seg.startIdx;
  const xScale = seg.chart.scales.x;
  const distVal = xScale.getValueForPixel(px);
  const { dist_m } = seg.data;
  const factor = U.metric ? 1000 : 1609.344;
  const targetM = distVal * factor;

  // Binary search in [viewStart..viewEnd]
  let lo = seg.viewStart, hi = seg.viewEnd;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (dist_m[mid] <= targetM) lo = mid; else hi = mid;
  }
  // Return closer index
  const dLo = Math.abs(dist_m[lo] - targetM);
  const dHi = Math.abs(dist_m[hi] - targetM);
  return dLo <= dHi ? lo : hi;
}

(function _segDragWire() {
  let _canvas = null;

  function getCanvas() {
    if (!_canvas) _canvas = document.getElementById('seg-chart');
    return _canvas;
  }

  function onMouseMove(e) {
    if (!seg.dragging && seg.panOriginX === null) return;
    const canvas = getCanvas();
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();

    if (seg.dragging) {
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const px = clientX - rect.left;
      const newIdx = segPxToIdx(px);
      const n = seg.data.dist_m.length;

      if (seg.dragging === 'start') {
        seg.startIdx = Math.max(seg.viewStart, Math.min(newIdx, seg.endIdx - 10));
      } else {
        seg.endIdx = Math.min(seg.viewEnd, Math.max(newIdx, seg.startIdx + 10));
      }
      segUpdateChart();
      segUpdateMap();
      segUpdateStats();
    } else if (seg.panOriginX !== null) {
      // Panning
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const dx = clientX - seg.panOriginX;
      if (!seg.chart || !seg.chart.scales) return;
      const xScale = seg.chart.scales.x;
      const chartAreaWidth = xScale.right - xScale.left;
      const viewSpan = seg.panOriginView.viewEnd - seg.panOriginView.viewStart;
      const { dist_m } = seg.data;
      const factor = U.metric ? 1000 : 1609.344;
      const distSpan = (dist_m[seg.panOriginView.viewEnd] - dist_m[seg.panOriginView.viewStart]) / factor;
      const distPerPx = distSpan / chartAreaWidth;
      const shiftDist = -dx * distPerPx;

      // Convert shiftDist back to index shift
      const originMid = dist_m[seg.panOriginView.viewStart] / factor;
      const newStart = originMid + shiftDist;
      const n = dist_m.length;

      // Find new viewStart index
      const targetM = newStart * factor;
      let lo = 0, hi = n - 1;
      while (lo < hi - 1) {
        const mid = (lo + hi) >> 1;
        if (dist_m[mid] <= targetM) lo = mid; else hi = mid;
      }
      let newViewStart = lo;
      newViewStart = Math.max(0, Math.min(newViewStart, n - 1 - viewSpan));
      seg.viewStart = Math.round(newViewStart);
      seg.viewEnd   = Math.min(n - 1, seg.viewStart + viewSpan);

      segUpdateChart();
    }
  }

  function onMouseUp() {
    seg.dragging = null;
    seg.panOriginX = null;
    seg.panOriginView = null;
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    document.removeEventListener('touchmove', onMouseMove);
    document.removeEventListener('touchend', onMouseUp);
  }

  function startHandleDrag(which, e) {
    if (!seg.open) return;
    e.stopPropagation();
    e.preventDefault();
    seg.dragging = which;
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchmove', onMouseMove, { passive: false });
    document.addEventListener('touchend', onMouseUp);
  }

  function startPan(e) {
    if (!seg.open || seg.dragging) return;
    const target = e.target || e.srcElement;
    if (target && target.classList.contains('seg-handle')) return;
    seg.panOriginX = e.touches ? e.touches[0].clientX : e.clientX;
    seg.panOriginView = { viewStart: seg.viewStart, viewEnd: seg.viewEnd };
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchmove', onMouseMove, { passive: false });
    document.addEventListener('touchend', onMouseUp);
  }

  // Wire up after DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    const hStart = document.getElementById('seg-handle-start');
    const hEnd   = document.getElementById('seg-handle-end');
    const area   = document.getElementById('seg-chart-area');

    if (hStart) {
      hStart.addEventListener('mousedown',  e => startHandleDrag('start', e));
      hStart.addEventListener('touchstart', e => startHandleDrag('start', e), { passive: false });
    }
    if (hEnd) {
      hEnd.addEventListener('mousedown',  e => startHandleDrag('end', e));
      hEnd.addEventListener('touchstart', e => startHandleDrag('end', e), { passive: false });
    }
    if (area) {
      area.addEventListener('mousedown',  startPan);
      area.addEventListener('touchstart', startPan, { passive: true });
    }
  });
})();

// ── Zoom ───────────────────────────────────────────────────────────────────────

function segOnZoom(val) {
  const pct = parseFloat(val);   // 0–100; 50 = initial view
  const n = seg.data.dist_m.length;
  const segLen = Math.max(1, seg.endIdx - seg.startIdx);
  const minSpan = Math.max(Math.ceil(segLen * 1.1), 10);   // tightest zoom (right)
  const maxSpan = n - 1;                                    // widest zoom (left)
  const initSpan = seg.initViewSpan || Math.floor((n - 1) / 2);

  let viewSpan;
  if (pct <= 50) {
    // Left half: 0→maxSpan, 50→initSpan
    const t = pct / 50;
    viewSpan = Math.round(maxSpan + t * (initSpan - maxSpan));
  } else {
    // Right half: 50→initSpan, 100→minSpan
    const t = (pct - 50) / 50;
    viewSpan = Math.round(initSpan + t * (minSpan - initSpan));
  }
  viewSpan = Math.max(minSpan, Math.min(viewSpan, maxSpan));

  const segMid = (seg.startIdx + seg.endIdx) / 2;
  seg.viewStart = Math.max(0, Math.round(segMid - viewSpan / 2));
  seg.viewEnd   = Math.min(n - 1, seg.viewStart + viewSpan);
  if (seg.viewEnd === n - 1) seg.viewStart = Math.max(0, seg.viewEnd - viewSpan);

  segUpdateChart();
}

// ── Stats ─────────────────────────────────────────────────────────────────────

function segUpdateStats() {
  const { dist_m, alt_ft } = seg.data;
  const { startIdx, endIdx } = seg;

  const lengthM = dist_m[endIdx] - dist_m[startIdx];
  const displayLen = U.metric
    ? (lengthM / 1000).toFixed(2) + ' km'
    : (lengthM / 1609.344).toFixed(2) + ' mi';

  let climbFt = 0;
  for (let i = startIdx; i < endIdx; i++) {
    const d = alt_ft[i + 1] - alt_ft[i];
    if (d > 0) climbFt += d;
  }
  const displayClimb = U.metric
    ? Math.round(climbFt * 0.3048) + ' m'
    : Math.round(climbFt) + ' ft';

  const lenEl   = document.getElementById('seg-stat-len');
  const climbEl = document.getElementById('seg-stat-climb');
  if (lenEl)   lenEl.textContent   = displayLen;
  if (climbEl) climbEl.textContent = displayClimb;
}

// ── Save as New mini-dialog ────────────────────────────────────────────────────

let _segSaveAsResolve = null;

function segSaveAsOpen(defaultName) {
  return new Promise(resolve => {
    _segSaveAsResolve = resolve;
    const input = document.getElementById('seg-saveas-input');
    if (input) { input.value = defaultName || ''; }
    const overlay = document.getElementById('seg-saveas-overlay');
    if (overlay) { overlay.style.display = 'flex'; }
    requestAnimationFrame(() => { if (input) input.focus(); });
  });
}

function segSaveAsClose() {
  const overlay = document.getElementById('seg-saveas-overlay');
  if (overlay) overlay.style.display = 'none';
  if (_segSaveAsResolve) { _segSaveAsResolve(null); _segSaveAsResolve = null; }
}

function segSaveAsConfirm() {
  const input = document.getElementById('seg-saveas-input');
  const name = input ? input.value.trim() : '';
  if (!name) { if (input) input.focus(); return; }
  const overlay = document.getElementById('seg-saveas-overlay');
  if (overlay) overlay.style.display = 'none';
  if (_segSaveAsResolve) { _segSaveAsResolve(name); _segSaveAsResolve = null; }
}

// ── Delete confirmation mini-dialog ───────────────────────────────────────────

let _segDelResolve = null;

function segDelOpen(segName) {
  return new Promise(resolve => {
    _segDelResolve = resolve;
    const msg = document.getElementById('seg-del-msg');
    if (msg) msg.textContent = `"${segName}" will be permanently deleted. This cannot be undone.`;
    const overlay = document.getElementById('seg-del-overlay');
    if (overlay) overlay.style.display = 'flex';
  });
}

function segDelClose() {
  const overlay = document.getElementById('seg-del-overlay');
  if (overlay) overlay.style.display = 'none';
  if (_segDelResolve) { _segDelResolve(false); _segDelResolve = null; }
}

function segDelConfirm() {
  const overlay = document.getElementById('seg-del-overlay');
  if (overlay) overlay.style.display = 'none';
  if (_segDelResolve) { _segDelResolve(true); _segDelResolve = null; }
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function segSave() {
  if (!seg.actId) return;

  let name;
  if (seg.editingId) {
    // "Save as new" — ask via custom dialog
    const currentName = (document.getElementById('seg-name-input') || {}).value || '';
    name = await segSaveAsOpen(currentName);
    if (!name) return; // cancelled
  } else {
    const nameInput = document.getElementById('seg-name-input');
    name = nameInput ? nameInput.value.trim() : '';
    if (!name) { nameInput && nameInput.focus(); return; }
  }

  const saveBtn = document.getElementById('seg-save-btn');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }

  const actId = seg.actId;
  try {
    const resp = await fetch('/api/segments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        activity_id: actId,
        start_idx: seg.startIdx,
        end_idx: seg.endIdx,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Save failed');
    }

    const saved = await resp.json();
    const newId = saved.id;
    if (saveBtn) { saveBtn.textContent = 'Saved!'; }
    setTimeout(async () => {
      closeDefineSegment();
      await loadSegmentSelector(actId);
      if (newId) {
        const sel = document.getElementById('seg-selector');
        if (sel) {
          sel.value = String(newId);
          onSegSelectorChange(String(newId));
        }
      }
    }, 900);
  } catch (err) {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = seg.editingId ? 'Save as new…' : 'Save';
    }
    alert('Error saving segment: ' + err.message);
  }
}

async function segUpdate() {
  if (!seg.actId || !seg.editingId) return;

  const nameInput = document.getElementById('seg-name-input');
  const name = nameInput ? nameInput.value.trim() : '';
  if (!name) { nameInput && nameInput.focus(); return; }

  const updateBtn = document.getElementById('seg-update-btn');
  if (updateBtn) { updateBtn.disabled = true; updateBtn.textContent = 'Updating…'; }

  const actId = seg.actId;
  const editingId = seg.editingId;
  try {
    const resp = await fetch(`/api/segments/${editingId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        activity_id: actId,
        start_idx: seg.startIdx,
        end_idx: seg.endIdx,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Update failed');
    }

    if (updateBtn) { updateBtn.textContent = 'Updated!'; }
    setTimeout(async () => {
      closeDefineSegment();
      await loadSegmentSelector(actId);
      // Re-select the updated segment
      const sel = document.getElementById('seg-selector');
      if (sel) {
        sel.value = String(editingId);
        onSegSelectorChange(String(editingId));
      }
    }, 900);
  } catch (err) {
    if (updateBtn) { updateBtn.disabled = false; updateBtn.textContent = 'Update'; }
    alert('Error updating segment: ' + err.message);
  }
}

async function segDelete() {
  if (!seg.editingId) return;
  const name = (document.getElementById('seg-name-input') || {}).value || 'this segment';
  const confirmed = await segDelOpen(name);
  if (!confirmed) return;
  const editingId = seg.editingId;
  const actId = seg.actId;
  try {
    const resp = await fetch(`/api/segments/${editingId}`, { method: 'DELETE' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Delete failed');
    }
    closeDefineSegment();
    await loadSegmentSelector(actId);
  } catch (err) {
    alert('Error deleting segment: ' + err.message);
  }
}

// ── Panel resize ──────────────────────────────────────────────────────────────

(function _segResizeWire() {
  function invalidate() {
    if (seg.map) seg.map.invalidateSize();
  }

  // ResizeObserver keeps map in sync when panel size changes any way
  function initResizeObserver() {
    const panel = document.getElementById('seg-panel');
    if (!panel || typeof ResizeObserver === 'undefined') return;
    new ResizeObserver(invalidate).observe(panel);
  }

  // Corner-drag: mouse
  function initCornerDrag() {
    const handle = document.getElementById('seg-resize-handle');
    const panel  = document.getElementById('seg-panel');
    if (!handle || !panel) return;

    let startX, startY, startW, startH;

    function onMove(e) {
      const newW = Math.max(420, Math.min(startW + (e.clientX - startX), window.innerWidth  * 0.97));
      const newH = Math.max(420, Math.min(startH + (e.clientY - startY), window.innerHeight * 0.94));
      panel.style.width  = newW + 'px';
      panel.style.height = newH + 'px';
      invalidate();
    }
    function onUp() {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup',   onUp);
      invalidate();
    }

    handle.addEventListener('mousedown', e => {
      e.preventDefault();
      e.stopPropagation();
      startX = e.clientX; startY = e.clientY;
      startW = panel.offsetWidth; startH = panel.offsetHeight;
      document.body.style.cursor = 'nwse-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup',   onUp);
    });

    // Touch
    handle.addEventListener('touchstart', e => {
      e.preventDefault();
      e.stopPropagation();
      const t = e.touches[0];
      startX = t.clientX; startY = t.clientY;
      startW = panel.offsetWidth; startH = panel.offsetHeight;

      function onTouchMove(e) {
        const t = e.touches[0];
        const newW = Math.max(420, Math.min(startW + (t.clientX - startX), window.innerWidth  * 0.97));
        const newH = Math.max(420, Math.min(startH + (t.clientY - startY), window.innerHeight * 0.94));
        panel.style.width  = newW + 'px';
        panel.style.height = newH + 'px';
        invalidate();
      }
      function onTouchEnd() {
        document.removeEventListener('touchmove', onTouchMove);
        document.removeEventListener('touchend',  onTouchEnd);
        invalidate();
      }
      document.addEventListener('touchmove', onTouchMove, { passive: false });
      document.addEventListener('touchend',  onTouchEnd);
    }, { passive: false });
  }

  document.addEventListener('DOMContentLoaded', () => {
    initResizeObserver();
    initCornerDrag();
  });
  setTimeout(() => { initResizeObserver(); initCornerDrag(); }, 500);
})();
