// ── MOBILE LAYOUT ─────────────────────────────────────────────────────────────
function _isMobile() { return window.innerWidth <= 767; }
// True for both portrait phones AND landscape phones (short screens)
function _isPhoneLayout() { return window.innerWidth <= 767 || window.innerHeight <= 500; }

function _mobMoveElements() {
  if (!_isPhoneLayout()) return;
  const tabMap      = document.getElementById('mob-tab-map');
  const tabInfo     = document.getElementById('mob-tab-info');
  const tabAnalysis = document.getElementById('mob-tab-analysis');
  const tabPhotos   = document.getElementById('mob-tab-photos');
  if (!tabMap) return;
  // Map + transport bar → map tab
  const detail    = document.getElementById('detail');
  const transport = document.getElementById('transport-bar');
  if (detail)    tabMap.insertBefore(detail, tabMap.firstChild);
  if (transport) tabMap.appendChild(transport);
  // Photo panel → photos tab (extracted from info-panel)
  const photoDragH = document.getElementById('photo-drag-handle');
  const infoRight  = document.getElementById('info-right');
  if (photoDragH) photoDragH.remove();
  if (infoRight)  tabPhotos.appendChild(infoRight);
  // Chart wrap → analysis tab
  const chartWrap = document.getElementById('chart-wrap');
  if (chartWrap) tabAnalysis.appendChild(chartWrap);
  // Set initial chart height from saved prefs
  if (chartWrap) {
    const ps = loadPaneSizes();
    chartWrap.style.height = (ps.chartH || 220) + 'px';
  }
  // Drag handle between chart and info for resizing
  let mobHandle = document.getElementById('mob-chart-resize-handle');
  if (!mobHandle) {
    mobHandle = document.createElement('div');
    mobHandle.id = 'mob-chart-resize-handle';
    tabAnalysis.appendChild(mobHandle);
    _mobSetupChartResize(mobHandle, chartWrap);
  }
  // Info panel → analysis tab (below chart), collapsible
  const infoPanel = document.getElementById('info-panel');
  if (infoPanel) tabAnalysis.appendChild(infoPanel);
  // Restore phone-specific collapse states
  try {
    const analysisBtn = document.getElementById('analysis-disclose');
    if (_uiPrefsGet('ascent-analysis-open') === '0') {
      if (chartWrap) chartWrap.style.height = ANALYSIS_TITLE_H + 'px';
      if (analysisBtn) analysisBtn.classList.add('collapsed');
      if (mobHandle) mobHandle.style.display = 'none';
    }
    if (_uiPrefsGet('ascent-stats-open') === '0') {
      if (infoPanel) infoPanel.classList.add('stats-collapsed');
      const content = infoPanel && infoPanel.querySelector('.stats-content');
      if (content) content.style.display = 'none';
      const statsBtn = document.getElementById('stats-disclose');
      if (statsBtn) statsBtn.classList.add('collapsed');
    }
  } catch(e) {}
}

function _mobSetupChartResize(handle, chartWrap) {
  if (!handle || !chartWrap) return;
  let _startY = null, _startH = 0;
  const MIN_H = 80;

  handle.addEventListener('touchstart', e => {
    e.preventDefault();
    _startY = e.touches[0].clientY;
    _startH = chartWrap.getBoundingClientRect().height;
  }, {passive: false});

  document.addEventListener('touchmove', e => {
    if (_startY === null) return;
    e.preventDefault();
    const delta = e.touches[0].clientY - _startY;
    const maxH = Math.round(window.innerHeight * 0.75);
    chartWrap.style.height = Math.max(MIN_H, Math.min(maxH, _startH + delta)) + 'px';
    if (typeof elevChart !== 'undefined' && elevChart) elevChart.resize();
  }, {passive: false});

  function _endResize() {
    if (_startY === null) return;
    _startY = null;
    savePaneSizes({chartH: chartWrap.getBoundingClientRect().height});
    if (typeof elevChart !== 'undefined' && elevChart) elevChart.resize();
    if (typeof redrawElevWithOverlays === 'function') redrawElevWithOverlays();
  }

  document.addEventListener('touchend', _endResize);
  document.addEventListener('touchcancel', () => { _startY = null; });

  handle.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    e.preventDefault();
    _startY = e.clientY;
    _startH = chartWrap.getBoundingClientRect().height;
    try { handle.setPointerCapture(e.pointerId); } catch(_) {}
    function pm(ev) {
      const delta = ev.clientY - _startY;
      const maxH = Math.round(window.innerHeight * 0.75);
      chartWrap.style.height = Math.max(MIN_H, Math.min(maxH, _startH + delta)) + 'px';
      if (typeof elevChart !== 'undefined' && elevChart) elevChart.resize();
    }
    function pu(ev) {
      try { handle.releasePointerCapture(ev.pointerId); } catch(_) {}
      savePaneSizes({chartH: chartWrap.getBoundingClientRect().height});
      if (typeof elevChart !== 'undefined' && elevChart) elevChart.resize();
      if (typeof redrawElevWithOverlays === 'function') redrawElevWithOverlays();
      handle.removeEventListener('pointermove', pm);
      handle.removeEventListener('pointerup', pu);
      handle.removeEventListener('pointercancel', pu);
    }
    handle.addEventListener('pointermove', pm);
    handle.addEventListener('pointerup', pu);
    handle.addEventListener('pointercancel', pu);
  });
}

function _mobRefitMap() {
  if (typeof leafMap === 'undefined' || !leafMap) return;
  leafMap.invalidateSize({animate: false});
  if (typeof trackLayer !== 'undefined' && trackLayer) {
    try { leafMap.fitBounds(trackLayer.getBounds(), {padding:[20,20]}); } catch(e) {}
  }
}

function mobilePushDetail(actTitle) {
  if (!_isPhoneLayout()) return;
  // Dismiss keyboard if search or any input was focused — ensures viewport has restored
  // before we slide in the detail screen and refit the map.
  if (document.activeElement && typeof document.activeElement.blur === 'function' &&
      (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA')) {
    document.activeElement.blur();
  }
  // Show forecast tab button only when activity has GPS
  const wxBtn = document.getElementById('mob-wx-btn');
  if (wxBtn) {
    const hasPts = typeof currentAct !== 'undefined' && currentAct &&
                   (currentAct.points_count > 0 || currentAct.points_saved);
    wxBtn.style.display = hasPts ? '' : 'none';
  }
  // Always reset to MAP tab when opening an activity
  mobSwitchTab('map');
  // Clear forecast tab — use resetWeatherForecast so the wx-box is safely returned first
  if (typeof resetWeatherForecast === 'function') resetWeatherForecast();
  else {
    const fPanel = document.getElementById('mob-tab-forecast');
    if (fPanel) { fPanel.innerHTML = ''; delete fPanel.dataset.forecastLoaded; }
  }
  if (_isMobile()) {
    // Portrait: slide detail screen in, then refit after animation finishes
    const ds = document.getElementById('mob-detail-screen');
    if (!ds) return;
    const titleEl = document.getElementById('mob-header-title');
    const backBtn = document.getElementById('mob-back-btn');
    if (titleEl) titleEl.textContent = actTitle || 'Activity';
    if (backBtn) backBtn.style.display = 'flex';
    ds.classList.add('active');
    document.body.classList.add('mob-detail-active');
    if (window.self !== window.top) {
      try { window.parent.postMessage({action:'enterDetail'}, location.origin); } catch(e) {}
    }
    setTimeout(_mobRefitMap, 320); // after 300ms slide animation
    setTimeout(_mobRefitMap, 600); // second pass in case keyboard was still animating away
  } else {
    // Landscape: detail screen always visible — just refit after layout settles
    requestAnimationFrame(() => requestAnimationFrame(_mobRefitMap));
  }
}

function mobOpenForecast() {
  if (typeof currentAct === 'undefined' || !currentAct) return;
  if (typeof openWeatherForecast === 'function') {
    openWeatherForecast('activity', currentAct.id, currentAct.name || currentAct.title || 'Activity');
  }
}

function _mobForecastLoaded() {
  const p = document.getElementById('mob-tab-forecast');
  return !!(p && p.dataset.forecastLoaded);
}

function mobileGoBack() {
  if (!_isMobile()) return;
  const ds = document.getElementById('mob-detail-screen');
  if (!ds) return;
  ds.classList.remove('active');
  document.body.classList.remove('mob-detail-active');
  const titleEl = document.getElementById('mob-header-title');
  const backBtn = document.getElementById('mob-back-btn');
  if (titleEl) titleEl.textContent = 'Ascent';
  if (backBtn) backBtn.style.display = 'none';
  if (window.self !== window.top) {
    try { window.parent.postMessage({action:'exitDetail'}, location.origin); } catch(e) {}
  }
  if (typeof animStop === 'function') animStop();
  if (typeof _panelDetach === 'function') _panelDetach();
}

function mobSwitchTab(tab) {
  // Stop video when leaving the photos tab
  if (tab !== 'photos') _panelDetach();
  document.querySelectorAll('.mob-tab-panel').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById('mob-tab-' + tab);
  if (panel) panel.classList.add('active');
  document.querySelectorAll('.mob-tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll(`.mob-tab-btn[data-tab="${tab}"]`).forEach(b => b.classList.add('active'));
  if (tab === 'map') {
    requestAnimationFrame(_mobRefitMap);
  }
  if (tab === 'analysis') {
    requestAnimationFrame(() => {
      if (typeof elevChart !== 'undefined' && elevChart) elevChart.resize();
      if (typeof redrawElevWithOverlays === 'function') redrawElevWithOverlays();
    });
  }
  if (tab === 'forecast' && !_mobForecastLoaded()) {
    mobOpenForecast();
  }
}

document.addEventListener('DOMContentLoaded', _mobMoveElements);

// On rotation / resize: refit map and resize chart if we're in a phone layout
window.addEventListener('resize', () => {
  if (!_isPhoneLayout()) return;
  // Debounce — layout hasn't settled yet during the resize event
  clearTimeout(window._mobResizeTimer);
  window._mobResizeTimer = setTimeout(() => {
    // Only refit if the map tab is active (or landscape where map is always the default view)
    const mapPanel = document.getElementById('mob-tab-map');
    if (mapPanel && mapPanel.classList.contains('active')) {
      _mobRefitMap();
    }
    // Resize chart if analysis tab is active
    const analysisPanel = document.getElementById('mob-tab-analysis');
    if (analysisPanel && analysisPanel.classList.contains('active')) {
      if (typeof elevChart !== 'undefined' && elevChart) elevChart.resize();
      if (typeof redrawElevWithOverlays === 'function') redrawElevWithOverlays();
    }
  }, 200);
});


// ── LONG-PRESS MULTI-SELECT (touch devices) ───────────────────────────────────
let _lpTimer = null;
let _lpSuppressClick = false;

(function setupLongPress() {
  const actList = document.getElementById('act-list');
  if (!actList) { setTimeout(setupLongPress, 200); return; }

  actList.addEventListener('touchstart', e => {
    const row = e.target.closest('.act-row');
    if (!row) return;
    const id = parseInt(row.dataset.id);
    if (isNaN(id)) return;
    _lpTimer = setTimeout(() => {
      _lpSuppressClick = true;
      if (navigator.vibrate) navigator.vibrate(30);
      if (!state.selectMode) {
        enterSelectMode(id);
      } else {
        // Long press in select mode: toggle and update
        if (state.selectedIds.has(id)) state.selectedIds.delete(id);
        else state.selectedIds.add(id);
        if (state.selectedIds.size === 0) exitSelectMode();
        else { renderVirtualList(); _updateSelectBar(); }
      }
    }, 500);
  }, {passive: true});

  const cancel = () => { clearTimeout(_lpTimer); _lpTimer = null; };
  actList.addEventListener('touchmove',   cancel, {passive: true});
  actList.addEventListener('touchend',    cancel, {passive: true});
  actList.addEventListener('touchcancel', cancel, {passive: true});
})();

function enterSelectMode(id) {
  state.selectMode = true;
  state.selectedIds.clear();
  state.selectedIds.add(id);
  document.getElementById('select-mode-bar').style.display = 'flex';
  renderVirtualList();
  _updateSelectBar();
}

function exitSelectMode() {
  state.selectMode = false;
  document.getElementById('select-mode-bar').style.display = 'none';
  renderVirtualList();
}

function _updateSelectBar() {
  const n = state.selectedIds.size;
  document.getElementById('select-mode-count').textContent =
    n === 1 ? '1 activity selected' : `${n} activities selected`;
}

function _updateMultiActions() {
  const n   = state.selectedIds.size;
  const btn = document.getElementById('bulk-gpx-btn');
  if (!btn) return;
  if (n >= 2) {
    btn.style.display = '';
    document.getElementById('bulk-gpx-count').textContent = n;
  } else {
    btn.style.display = 'none';
  }
}

function _safeFilename(name) {
  return (name || 'activity').replace(/[/\\:*?"<>|]/g, '_').trim() || 'activity';
}

async function downloadSelectedGPX() {
  const ids = [...state.selectedIds];
  if (ids.length < 2) return;

  const btn      = document.getElementById('bulk-gpx-btn');
  const origHTML = btn.innerHTML;

  // File System Access API path — Chrome/Edge only, requires secure context
  const fsaAvailable = typeof window.showDirectoryPicker === 'function'
    && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1');

  if (fsaAvailable) {
    let dirHandle = null;
    try {
      dirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
    } catch(e) {
      if (e.name === 'AbortError') return; // user cancelled — do nothing
      // showDirectoryPicker failed — fall through to ZIP download below
      console.warn('showDirectoryPicker failed:', e.name, e.message);
    }

    if (dirHandle) {
      for (let i = 0; i < ids.length; i++) {
        const id       = ids[i];
        const act      = state.all.find(a => a.id === id);
        const filename = _safeFilename(act?.name) + '.gpx';
        btn.innerHTML  = `↓ GPX ${i + 1}/${ids.length}…`;
        try {
          const resp       = await fetch(`/activities/${id}/export/gpx`);
          const blob       = await resp.blob();
          const fileHandle = await dirHandle.getFileHandle(filename, { create: true });
          const writable   = await fileHandle.createWritable();
          await writable.write(blob);
          await writable.close();
        } catch(e) {
          console.warn(`GPX write failed for activity ${id}:`, e);
        }
      }
      btn.innerHTML = origHTML;
      return;
    }
  }

  // Fallback: server-side ZIP — one download, no per-file dialogs.
  btn.innerHTML = `↓ GPX zipping…`;
  try {
    const resp = await fetch('/export/gpx/batch', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ ids }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const blob = await resp.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = 'activities.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch(e) {
    console.error('Batch GPX export failed:', e);
  }
  btn.innerHTML = origHTML;
}


// ── COLUMN TOUCH DRAG-TO-REORDER ─────────────────────────────────────────────
let _colDrag = null;      // active drag: { colId, ghost, startX, origLeft, toId }
let _colDragPending = null; // waiting to see if touch moves enough: { colId, div, startX, startY }
let _suppressColClick = false;

document.addEventListener('touchmove', e => {
  if (_colDragPending) {
    const t = e.touches[0];
    const dx = Math.abs(t.clientX - _colDragPending.startX);
    const dy = Math.abs(t.clientY - _colDragPending.startY);
    if (dx > 8 || dy > 4) {
      // Commit to a drag
      const div  = _colDragPending.div;
      const rect = div.getBoundingClientRect();
      const ghost = document.createElement('div');
      ghost.textContent = div.textContent.replace(/[↑↓]/g,'').trim();
      ghost.style.cssText = `position:fixed;top:${rect.top}px;left:${rect.left}px;width:${rect.width}px;height:${rect.height}px;display:flex;align-items:center;padding:0 8px;pointer-events:none;z-index:9999;opacity:.85;background:#3a3a3c;border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,.5);font-size:11px;font-weight:600;color:#f5f5f7;text-transform:uppercase;letter-spacing:.05em`;
      document.body.appendChild(ghost);
      div.classList.add('dragging-col');
      _colDrag = { colId: _colDragPending.colId, ghost, startX: t.clientX, origLeft: rect.left, toId: null };
      _colDragPending = null;
    }
  }
  if (!_colDrag) return;
  e.preventDefault();
  const t = e.touches[0];
  _colDrag.ghost.style.left = (_colDrag.origLeft + (t.clientX - _colDrag.startX)) + 'px';
  // Find column header under finger
  _colDrag.ghost.style.display = 'none';
  const el = document.elementFromPoint(t.clientX, t.clientY);
  _colDrag.ghost.style.display = '';
  const target = el && el.closest('.ch[data-col-id]');
  document.querySelectorAll('.ch').forEach(c => c.classList.remove('drag-over'));
  if (target && target.dataset.colId !== _colDrag.colId) {
    target.classList.add('drag-over');
    _colDrag.toId = target.dataset.colId;
  } else {
    _colDrag.toId = null;
  }
}, {passive: false});

document.addEventListener('touchend', () => {
  _colDragPending = null;
  if (!_colDrag) return;
  document.querySelectorAll('.ch').forEach(c => { c.classList.remove('drag-over'); c.classList.remove('dragging-col'); });
  _colDrag.ghost.remove();
  const { colId, toId } = _colDrag;
  _colDrag = null;
  if (!toId || toId === colId) return;
  _suppressColClick = true;
  const fromIdx = activeColIds.indexOf(colId);
  const toIdx   = activeColIds.indexOf(toId);
  if (fromIdx < 0 || toIdx < 0) return;
  activeColIds.splice(fromIdx, 1);
  activeColIds.splice(toIdx, 0, colId);
  saveColPrefs(activeColIds);
  buildColHead();
  renderVirtualList();
});


