// ── COMPARE ───────────────────────────────────────────────────────────────────
const CMP_COLORS = ['#f97316','#3b82f6','#a855f7','#facc15','#10b981'];

const cmp = {
  open:        false,
  matches:     [],       // [{activity_id,name,start_time,elapsed_s,points:[{t,lat,lon,...}]}]
  maxT:        0,
  currentT:    0,
  playing:     false,
  rafId:       null,
  lastTs:      null,
  map:         null,
  markers:     [],       // Leaflet markers, one per match (overview)
  tracks:      [],       // Leaflet polylines
  zoomMap:     null,     // chase-cam map
  zoomMarkers: [],       // markers on zoom map
  tileLayer:   null,     // overview map tile layer
  zoomTile:    null,     // chase-cam tile layer
  profileChart:  null,   // Chart.js elevation profile
  profileDots:   [],     // {x,y} per match for dot overlay
  _resizing:     false,  // true during/just after resize to suppress overlay click
  _resizeTimer:  null,
  savedSegId:    null,   // currently loaded saved segment id (null = unsaved)
  savedSegName:  null,   // original name of the loaded saved segment (for Rename detection)
  savedSegments: [],     // list of saved segments for current activity
  // Segment selection state (set by elevation drag)
  selStartIdx: null,
  selEndIdx:   null,
};

function fmtCmpTime(s) {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = Math.floor(s%60);
  return `${h}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
}

function fmtElapsed(s) {
  const m = Math.floor(s/60), ss = Math.floor(s%60);
  return `${m}:${String(ss).padStart(2,'0')}`;
}

// Called by elevation selection drag to record the selected indices
function setElevSelection(startIdx, endIdx) {
  cmp.selStartIdx = startIdx;
  cmp.selEndIdx   = endIdx;
  const hasRegion = startIdx !== null && endIdx !== null;
  const compareBtn = document.getElementById('compare-btn');
  const segDefineBtn = document.getElementById('seg-define-btn');
  if (compareBtn)   compareBtn.disabled   = !hasRegion;
  if (segDefineBtn) segDefineBtn.disabled = !hasRegion;
}

async function cmpOpenManualWithSegment() {
  // Multi-select + segment drawn: run compare on selected activities using the drawn segment
  const actIds = [...state.selectedIds].slice(0, 4);
  const refId  = state.selectedId || actIds[0];
  const ordered = [refId, ...actIds.filter(id => id !== refId)].slice(0, 4);

  const overlay = document.getElementById('compare-overlay');
  const loading = document.getElementById('cmp-loading');
  const titleEl = document.getElementById('compare-title');
  const panel   = document.getElementById('compare-panel');
  overlay.classList.add('open');
  const _mBtnMs = document.getElementById('cmp-make-seg-btn');
  if (_mBtnMs) _mBtnMs.style.display = 'none';

  const savedW = _uiPrefsGet('ascent-cmp-w');
  const savedH = _uiPrefsGet('ascent-cmp-h');
  if (panel && !matchMedia('(pointer:coarse)').matches) {
    if (savedW) panel.style.width  = savedW;
    if (savedH) panel.style.height = savedH;
  } else if (panel) {
    panel.style.width = ''; panel.style.height = '';
  }
  const savedSpeed = _uiPrefsGet('ascent-cmp-speed') || '25';
  const speedSel = document.getElementById('cmp-speed');
  if (speedSel) speedSel.value = savedSpeed;
  const friendsCb = document.getElementById('cmp-include-friends');
  if (friendsCb) friendsCb.checked = _uiPrefsGet('ascent-cmp-friends') === '1';

  loading.style.display = 'flex';
  document.getElementById('cmp-loading-text').textContent = 'Searching activities…';

  const _bar = document.getElementById('cmp-progress-bar');
  if (_bar) { _bar.style.transition = 'none'; _bar.style.width = '0%'; }
  void loading.offsetHeight;
  let _pct = 0;
  const _prog = setInterval(() => {
    _pct = _pct + (85 - _pct) * 0.06;
    if (_bar) _bar.style.width = _pct.toFixed(1) + '%';
  }, 80);

  try {
    const r = await fetch('/api/segment/compare', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        activity_id:     currentAct.id,
        start_idx:       cmp.selStartIdx,
        end_idx:         cmp.selEndIdx,
        max_results:     ordered.length,  // total including reference activity
        radius_m:        150,
        include_friends: !!(document.getElementById('cmp-include-friends')?.checked),
        // Pass the specific activity IDs to restrict candidates
        candidate_ids:   ordered.filter(id => id !== currentAct.id),
      }),
    });
    const d = await r.json();
    clearInterval(_prog);
    if (_bar) { _bar.style.transition = 'width .15s ease'; _bar.style.width = '100%'; }
    setTimeout(() => { loading.style.display = 'none'; if (_bar) _bar.style.width = '0%'; }, 150);
    if (!r.ok) {
      cmp.matches = [];
      const _leg = document.getElementById('compare-legend');
      if (_leg) _leg.innerHTML = '';
      titleEl.textContent = d.detail || 'No matches';
      return;
    }

    // Build complete list including placeholder entries for activities missing the segment
    const matchedIds = new Set(d.matches.map(m => m.activity_id));
    const allMatches = [...d.matches];
    for (const id of ordered) {
      if (!matchedIds.has(id)) {
        const act = state.all?.find(a => a.id === id) || state.filtered?.find(a => a.id === id);
        allMatches.push({
          activity_id: id,
          name:        act?.name || `Activity ${id}`,
          start_time:  act?.start_time || null,
          elapsed_s:   null,
          user_id:     act?.user_id || null,
          points:      [],
          missing:     true,
        });
      }
    }
    // Ensure reference activity is first (initCompareProfile uses matches[0] as ref)
    const _refId = currentAct.id;
    const _refM  = allMatches.find(m => m.activity_id === _refId);
    const _restM = allMatches.filter(m => m.activity_id !== _refId);
    cmp.matches      = _refM ? [_refM, ..._restM] : allMatches;
    cmp.currentT     = 0;
    cmp.savedSegId   = null;
    cmp.savedSegName = null;
    cmp._manualIds = ordered;
    cmpUpdateSaveBtn();

    const nFound = d.matches.length;
    const nTotal = ordered.length;
    titleEl.textContent = nFound === nTotal
      ? `${nFound} Activit${nFound===1?'y':'ies'} — ${d.segment_name||'Segment'}`
      : `${nFound} of ${nTotal} Activities — ${d.segment_name||'Segment'}`;

    buildCompareLegend();
    initCompareMap();
    initCompareProfile();
    cmpUpdateUI(0);
    cmpRequestAiAnalysis();
    await cmpLoadSavedSegments();
  } catch(e) {
    clearInterval(_prog);
    if (_bar) _bar.style.width = '0%';
    loading.style.display = 'none';
    titleEl.textContent = 'Error: ' + e.message;
  }
}

async function cmpOpenManual() {
  // Manual compare: use the currently selected activities directly (no matching).
  // First selected = reference; up to 4 total.
  const actIds = [...state.selectedIds].slice(0, 4);
  // Ensure reference (selectedId) is first
  const refId = state.selectedId || actIds[0];
  const ordered = [refId, ...actIds.filter(id => id !== refId)].slice(0, 4);

  const overlay = document.getElementById('compare-overlay');
  const loading = document.getElementById('cmp-loading');
  const _mBtnM = document.getElementById('cmp-make-seg-btn');
  if (_mBtnM) _mBtnM.style.display = 'none';
  const titleEl = document.getElementById('compare-title');
  const panel   = document.getElementById('compare-panel');
  overlay.classList.add('open');

  const savedW = _uiPrefsGet('ascent-cmp-w');
  const savedH = _uiPrefsGet('ascent-cmp-h');
  if (panel && !matchMedia('(pointer:coarse)').matches) {
    if (savedW) panel.style.width  = savedW;
    if (savedH) panel.style.height = savedH;
  } else if (panel) {
    panel.style.width = ''; panel.style.height = '';
  }
  const savedSpeed = _uiPrefsGet('ascent-cmp-speed') || '25';
  const speedSel = document.getElementById('cmp-speed');
  if (speedSel) speedSel.value = savedSpeed;
  const friendsCb = document.getElementById('cmp-include-friends');
  if (friendsCb) friendsCb.checked = _uiPrefsGet('ascent-cmp-friends') === '1';

  titleEl.textContent = `${ordered.length} Activities — choose a segment`;
  loading.style.display = 'none';
  cmp.matches      = [];
  cmp.currentT     = 0;
  cmp.savedSegId   = null;
  cmp.savedSegName = null;
  cmp._manualIds = ordered;  // store for later use when segment chosen
  cmpUpdateSaveBtn();

  // Build placeholder legend with activity names + colors
  const legendEl = document.getElementById('compare-legend');
  if (legendEl) {
    legendEl.innerHTML = '';
    const multiUser = new Set(ordered.map(id => {
      const act = state.all?.find(a => a.id === id) || state.filtered?.find(a => a.id === id);
      return act?.user_id;
    }).filter(id => id != null)).size > 1;
    ordered.forEach((id, i) => {
      const act = state.all?.find(a => a.id === id) || state.filtered?.find(a => a.id === id);
      const rawName = act?.name || `Activity ${id}`;
      const userName = multiUser && act?.user_id && userMap[act.user_id] ? escHtml(userMap[act.user_id].username || '') + ': ' : '';
      const name = userName + escHtml(rawName);
      const color = CMP_COLORS[i % CMP_COLORS.length];
      const li = document.createElement('div');
      li.className = 'cmp-legend-item';
      li.innerHTML = `<span class="cmp-dot" style="background:${color}"></span>` +
        `<span style="font-weight:${i===0?700:400}">${name}</span>`;
      legendEl.appendChild(li);
    });
  }

  // Show full tracks on overview map
  await initCompareMapManual(ordered);

  // Clear profile
  if (cmp.profileChart) { cmp.profileChart.destroy(); cmp.profileChart = null; }

  // Load segments for all selected activities
  await cmpLoadSavedSegments();
}

async function initCompareMapManual(actIds) {
  const mapEl  = document.getElementById('compare-map');
  const zoomEl = document.getElementById('compare-zoom');
  if (cmp.map)          { cmp.map.remove();          cmp.map = null; }
  if (cmp.zoomMap)      { cmp.zoomMap.remove();      cmp.zoomMap = null; }
  if (cmp.profileChart) { cmp.profileChart.destroy(); cmp.profileChart = null; }
  cmp.markers = []; cmp.zoomMarkers = []; cmp.tracks = [];

  const style = MAP_STYLES[_uiPrefsGet('ascent-map-style') || 'osm'] || MAP_STYLES['osm'];

  // Overview map
  cmp.map = L.map(mapEl, {zoomControl:true, attributionControl:false});
  cmp.tileLayer = L.tileLayer(style.url, {maxZoom:19}).addTo(cmp.map);
  MapUtils.addScale(cmp.map, U.metric);

  // Chase-cam map (no interaction)
  if (zoomEl) {
    cmp.zoomMap = L.map(zoomEl, {
      zoomControl:false, attributionControl:false,
      dragging:false, scrollWheelZoom:false, doubleClickZoom:false,
      keyboard:false, touchZoom:false, boxZoom:false,
    });
    cmp.zoomTile = L.tileLayer(style.url, {maxZoom:19}).addTo(cmp.zoomMap);
  }

  const colors = ['#ef4444','#3b82f6','#22c55e','#f97316'];
  const allBounds = [];
  let firstLL = null;

  for (let i = 0; i < actIds.length; i++) {
    try {
      const r = await fetch(`/api/activities/${actIds[i]}/geojson`);
      if (!r.ok) continue;
      const geo = await r.json();
      const coords = geo?.geometry?.coordinates || [];
      if (coords.length < 2) continue;
      const lls = coords.map(c => [c[1], c[0]]);
      if (!firstLL) firstLL = lls[0];
      L.polyline(lls, {color: colors[i % colors.length], weight: i===0?3:2,
                        opacity: i===0?0.9:0.5, smoothFactor:1.5}).addTo(cmp.map);
      if (cmp.zoomMap) {
        L.polyline(lls, {color: colors[i % colors.length], weight: i===0?2:1.5,
                          opacity:0.5, smoothFactor:1.5}).addTo(cmp.zoomMap);
      }
      lls.forEach(ll => allBounds.push(ll));
    } catch(e) { console.warn('initCompareMapManual geo fetch:', e); }
  }

  if (allBounds.length) {
    cmp.map.fitBounds(L.latLngBounds(allBounds), {padding:[16,16]});
    if (cmp.zoomMap && firstLL) cmp.zoomMap.setView(firstLL, 14);
  }

  setTimeout(() => {
    if (cmp.map)     cmp.map.invalidateSize();
    if (cmp.zoomMap) cmp.zoomMap.invalidateSize();
  }, 100);
}

async function openCompare() {
  if (!currentAct) return;

  // Multi-select mode: ≥2 activities selected
  if (state.selectedIds.size >= 2) {
    console.log('[compare] multiselect mode, selectedIds:', [...state.selectedIds], 'selStartIdx:', cmp.selStartIdx, 'selEndIdx:', cmp.selEndIdx);
    // If a segment is already drawn on the elevation chart, use it directly
    // to run a targeted compare on those specific activities
    if (cmp.selStartIdx !== null && cmp.selEndIdx !== null) {
      console.log('[compare] → cmpOpenManualWithSegment');
      await cmpOpenManualWithSegment();
    } else {
      console.log('[compare] → cmpOpenManual (no segment)');
      await cmpOpenManual();
    }
    return;
  }

  const overlay  = document.getElementById('compare-overlay');
  const loading  = document.getElementById('cmp-loading');
  const titleEl  = document.getElementById('compare-title');
  overlay.classList.add('open');

  // Restore persisted settings
  const panel = document.getElementById('compare-panel');
  const savedW = _uiPrefsGet('ascent-cmp-w');
  const savedH = _uiPrefsGet('ascent-cmp-h');
  if (panel && !matchMedia('(pointer:coarse)').matches) {
    if (savedW) panel.style.width  = savedW;
    if (savedH) panel.style.height = savedH;
  } else if (panel) {
    panel.style.width = ''; panel.style.height = '';
  }
  const savedSpeed = _uiPrefsGet('ascent-cmp-speed') || '25';
  const speedSel = document.getElementById('cmp-speed');
  if (speedSel) speedSel.value = savedSpeed;
  const friendsCb = document.getElementById('cmp-include-friends');
  if (friendsCb) friendsCb.checked = _uiPrefsGet('ascent-cmp-friends') === '1';

  // If no segment selected, single activity: load saved segments so
  // the user can pick one from the dropdown to run compare immediately.
  if (cmp.selStartIdx === null || cmp.selEndIdx === null) {
    titleEl.textContent = 'Segment Compare';
    loading.style.display = 'none';
    cmp.matches      = [];
    cmp.currentT     = 0;
    cmp.savedSegId   = null;
    cmp.savedSegName = null;
    cmpUpdateSaveBtn();
    await cmpLoadSavedSegments();
    return;
  }

  loading.style.display = 'flex';
  document.getElementById('cmp-loading-text').textContent = 'Searching activities…';

  // Animate progress bar — force reflow so loading div is visible before fetch
  const _bar = document.getElementById('cmp-progress-bar');
  if (_bar) { _bar.style.transition = 'none'; _bar.style.width = '0%'; }
  void loading.offsetHeight; // force reflow
  let _pct = 0;
  const _prog = setInterval(() => {
    _pct = _pct + (85 - _pct) * 0.06;
    if (_bar) _bar.style.width = _pct.toFixed(1) + '%';
  }, 80);

  try {
    const r = await fetch('/api/segment/compare', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        activity_id:     currentAct.id,
        start_idx:       cmp.selStartIdx,
        end_idx:         cmp.selEndIdx,
        max_results:     4,
        radius_m:        150,
        include_friends: !!(document.getElementById('cmp-include-friends')?.checked),
      }),
    });
    const d = await r.json();
    if (!r.ok) {
      clearInterval(_prog);
      loading.style.display = 'none';
      if (_bar) _bar.style.width = '0%';
      titleEl.textContent = d.detail || 'No matches found';
      return;
    }

    // Reference activity first, then others sorted fastest first
    const refId = currentAct ? currentAct.id : null;
    const ref   = d.matches.find(m => m.activity_id === refId);
    const rest  = d.matches.filter(m => m.activity_id !== refId);
    rest.sort((a, b) => a.elapsed_s - b.elapsed_s);
    cmp.matches  = ref ? [ref, ...rest] : d.matches;
    cmp.maxT     = 1;   // normalized: 0→1
    cmp.currentT = 0;

    clearInterval(_prog);
    if (_bar) { _bar.style.transition = 'width .15s ease'; _bar.style.width = '100%'; }
    setTimeout(() => { loading.style.display = 'none'; if (_bar) _bar.style.width = '0%'; }, 150);
    const nOthers = cmp.matches.length - 1;
    titleEl.textContent = `Selected Activity vs. ${nOthers} Fastest`;

    // Reset saved segment state for a fresh elevation-drag selection
    cmp.savedSegId   = null;
    cmp.savedSegName = null;
    const nameEl = document.getElementById('cmp-seg-name');
    if (nameEl) nameEl.value = '';
    cmpUpdateSaveBtn();

    // Sync map style buttons to current stored style
    const _curStyle = _uiPrefsGet('ascent-map-style') || 'osm';
    document.querySelectorAll('.map-style-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.style === _curStyle);
    });
    buildCompareLegend();
    initCompareMap();
    initCompareProfile();
    cmpUpdateUI(0);
    cmpRequestAiAnalysis();

    // Populate saved segments for this activity
    await cmpLoadSavedSegments();

    // Show "Make Segment…" when the compare was from a drawn region (no named segment selected)
    const _mBtn = document.getElementById('cmp-make-seg-btn');
    if (_mBtn) _mBtn.style.display = (window._selectedSegmentId == null) ? '' : 'none';

  } catch(e) {
    clearInterval(_prog);
    loading.style.display = 'none';
    if (_bar) _bar.style.width = '0%';
    titleEl.textContent = 'Error: ' + e.message;
  }
}

function initCompareOverlayClose() {
  function attachBackdrop() {
    const backdrop = document.getElementById('cmp-backdrop');
    if (!backdrop) return;
    // Desktop only: click backdrop to close.
    // On touch devices the panel fills the screen so there's no backdrop to tap;
    // use the ✕ button instead. Swallowing touchstart on the backdrop prevents
    // any accidental close on iPad.
    const isTouch = window.matchMedia('(pointer:coarse)').matches;
    if (!isTouch) {
      backdrop.addEventListener('click', () => { if (!cmp._resizing) closeCompare(); });
    } else {
      // Absorb all touch events on the backdrop so nothing leaks through
      backdrop.addEventListener('touchstart', e => e.stopPropagation(), {passive: true});
      backdrop.addEventListener('touchend',   e => e.stopPropagation(), {passive: true});
    }
  }
  document.addEventListener('DOMContentLoaded', attachBackdrop);
  setTimeout(attachBackdrop, 500);
}
initCompareOverlayClose();

function closeCompare() {
  document.getElementById('compare-overlay').classList.remove('open');
  // Persist panel size
  const _panel = document.getElementById('compare-panel');
  if (_panel) {
    _uiPrefsSet('ascent-cmp-w', _panel.style.width  || _panel.offsetWidth  + 'px');
    _uiPrefsSet('ascent-cmp-h', _panel.style.height || _panel.offsetHeight + 'px');
  }
  cmpClearAiAnalysis();
  cmp._manualIds = null;
  cmpStop();
  if (cmp.map)          { cmp.map.remove();          cmp.map = null; }
  if (cmp.zoomMap)      { cmp.zoomMap.remove();      cmp.zoomMap = null; }
  if (cmp.profileChart) { cmp.profileChart.destroy(); cmp.profileChart = null; }
  cmp.tileLayer = null; cmp.zoomTile = null;
  cmp.markers      = [];
  cmp.zoomMarkers  = [];
  cmp.tracks       = [];
  cmp.savedSegments = [];
}

function buildCompareLegend() {
  const leg = document.getElementById('compare-legend');
  leg.innerHTML = '';
  const multiUser = new Set(cmp.matches.map(m => m.user_id).filter(id => id != null)).size > 1;

  // Display order: fastest non-missing first, missing last
  const nonMissing = [...cmp.matches].filter(m => !m.missing).sort((a,b) => a.elapsed_s - b.elapsed_s);
  const missing    = cmp.matches.filter(m => m.missing);
  const displayOrder = [...nonMissing, ...missing];
  const fastestId  = nonMissing.length ? nonMissing[0].activity_id : null;

  displayOrder.forEach(m => {
    const origIdx  = cmp.matches.indexOf(m);
    const color    = CMP_COLORS[origIdx % CMP_COLORS.length];
    const isRef    = currentAct && m.activity_id === currentAct.id;
    const isFastest = m.activity_id === fastestId;
    const date     = m.start_time ? new Date(m.start_time*1000).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) : '';
    const userName = multiUser && m.user_id && userMap[m.user_id] ? escHtml(userMap[m.user_id].username || '') + ': ' : '';
    const dispName = userName + escHtml(m.name || '');
    const item     = document.createElement('div');
    item.className = 'cmp-legend-item';
    if (m.missing) {
      item.innerHTML =
        `<div class="cmp-sphere" style="background:radial-gradient(circle at 35% 35%,#9ca3af66,#9ca3af33 55%,#9ca3af11);flex-shrink:0;opacity:0.6"></div>` +
        `<span style="font-style:italic;color:var(--muted)">${dispName}</span>` +
        `<span style="color:var(--muted);font-size:11px;white-space:nowrap">${date}</span>` +
        `<span style="margin-left:auto;display:flex;gap:6px;align-items:center;flex-shrink:0">` +
        `<span style="font-size:11px;color:var(--muted);white-space:nowrap;font-family:ui-monospace,'SF Mono','Fira Code',monospace;font-variant-numeric:tabular-nums;min-width:40px;text-align:right">—</span>` +
        `<span id="cmp-delta-${m.activity_id}" style="font-size:12px;min-width:50px;text-align:right;color:var(--muted);font-family:ui-monospace,'SF Mono','Fira Code',monospace;font-variant-numeric:tabular-nums">—</span>` +
        `</span>`;
    } else {
      const nameStyle    = isRef ? 'font-weight:700' : '';
      const elapsedStyle = isFastest
        ? 'font-weight:700;color:#fff'
        : 'color:var(--muted)';
      item.style.cursor = 'pointer';
      item.title = 'Go to activity';
      item.innerHTML =
        `<div class="cmp-sphere" style="background:radial-gradient(circle at 35% 35%,${color}ee,${color}88 55%,${color}44);flex-shrink:0"></div>` +
        `<span style="${nameStyle}">${dispName}</span>` +
        `<span style="color:var(--muted);font-size:11px;white-space:nowrap">${date}</span>` +
        `<span style="margin-left:auto;display:flex;gap:6px;align-items:center;flex-shrink:0">` +
        `<span style="font-size:11px;font-family:ui-monospace,'SF Mono','Fira Code',monospace;font-variant-numeric:tabular-nums;white-space:nowrap;min-width:40px;text-align:right;${elapsedStyle}">${fmtElapsed(m.elapsed_s)}</span>` +
        `<span id="cmp-delta-${m.activity_id}" style="font-family:ui-monospace,'SF Mono','Fira Code',monospace;` +
        `font-size:12px;font-weight:600;min-width:50px;text-align:right;font-variant-numeric:tabular-nums;` +
        `color:var(--muted2)">0:00</span>` +
        `</span>`;
      const actId  = m.activity_id;
      const mSi    = m.start_idx ?? null;
      const mEi    = m.end_idx   ?? null;
      item.addEventListener('click', async () => {
        closeCompare();
        await selectActivity(actId);
        scrollToSelected();
        if (window._applyElevSelection && mSi !== null && mEi !== null && mEi > mSi)
          window._applyElevSelection(mSi, mEi);
      });
    }
    leg.appendChild(item);
  });
}

function initCompareMapBrowse() {
  // Show full activity on the overview map in browse mode (no segment selected)
  const mapEl = document.getElementById('compare-map');
  if (cmp.map) { cmp.map.remove(); cmp.map = null; }
  if (cmp.zoomMap) { cmp.zoomMap.remove(); cmp.zoomMap = null; }
  cmp.markers = []; cmp.zoomMarkers = []; cmp.tracks = [];

  cmp.map = L.map(mapEl, {zoomControl:true, attributionControl:false});
  const _cmpStyle = MAP_STYLES[_uiPrefsGet('ascent-map-style') || 'osm'] || MAP_STYLES['osm'];
  cmp.tileLayer = L.tileLayer(_cmpStyle.url, {maxZoom:19});
  cmp.tileLayer.addTo(cmp.map);
  MapUtils.addScale(cmp.map, U.metric);

  // Draw the full activity track
  if (splitsState.geo) {
    const coords = splitsState.geo.geometry?.coordinates || [];
    if (coords.length > 1) {
      const lls = coords.map(c => [c[1], c[0]]);
      L.polyline(lls, {color:'#ef4444', weight:3, opacity:0.85, smoothFactor:1.5}).addTo(cmp.map);
      cmp.map.fitBounds(L.latLngBounds(lls), {padding:[16,16]});
    }
  }

  // Zoom pane: start of activity
  const zoomEl = document.getElementById('compare-zoom');
  if (zoomEl && splitsState.geo) {
    cmp.zoomMap = L.map(zoomEl, {
      zoomControl:false, attributionControl:false,
      dragging:false, scrollWheelZoom:false, doubleClickZoom:false,
      keyboard:false, touchZoom:false, boxZoom:false,
    });
    cmp.zoomTile = L.tileLayer(_cmpStyle.url, {maxZoom:19});
    cmp.zoomTile.addTo(cmp.zoomMap);
    const coords = splitsState.geo.geometry?.coordinates || [];
    if (coords.length > 1) {
      const lls = coords.map(c => [c[1], c[0]]);
      L.polyline(lls, {color:'#ef4444', weight:2, opacity:0.4}).addTo(cmp.zoomMap);
      cmp.zoomMap.setView(lls[0], 14);
    }
  }

  setTimeout(() => {
    if (cmp.map) cmp.map.invalidateSize();
    if (cmp.zoomMap) cmp.zoomMap.invalidateSize();
  }, 100);

  // Show full activity elevation profile
  if (elevChartData) {
    initCompareProfileFromData(elevChartData);
  }
}

function initCompareProfileFromData(data) {
  // Build compare profile from full activity elevation chart data
  const ctx = document.getElementById('cmp-profile-canvas');
  if (!ctx || !data) return;
  if (cmp.profileChart) { cmp.profileChart.destroy(); cmp.profileChart = null; }

  const dist  = data.dist_m;
  const alt   = data.alt_ft;
  if (!dist || !alt) return;

  const step = Math.max(1, Math.floor(dist.length / 600));
  const pts  = [];
  for (let i = 0; i < dist.length; i += step) {
    pts.push({ x: U.metric ? dist[i]/1000 : dist[i]/1609.344, y: U.alt(alt[i]||0) });
  }
  const totalX = pts[pts.length-1]?.x || 1;
  const xUnit  = U.metric ? 'km' : 'mi';
  const yUnit  = U.altUnit();

  cmp.profileChart = new Chart(ctx, {
    type: 'line',
    data: { datasets: [{ data: pts, fill: true,
      borderColor: '#3b82f6', backgroundColor: 'rgba(30,80,180,.5)',
      borderWidth: 1.5, pointRadius: 0, tension: 0.3 }]},
    options: {
      responsive:true, maintainAspectRatio:false, animation:false,
      plugins: { legend:{display:false}, tooltip:{enabled:false} },
      scales: {
        x: { type:'linear', min:0, max:totalX,
             ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8,callback:v=>`${v.toFixed(1)}${xUnit}`},
             grid:{color:'rgba(255,255,255,.04)'} },
        y: { ticks:{color:'#64748b',font:{size:9},maxTicksLimit:4,callback:v=>`${Math.round(v)}${yUnit}`},
             grid:{color:'rgba(255,255,255,.06)'} },
      },
      layout:{padding:{top:10,bottom:2,left:2,right:8}},
    },
  });
}

function initCompareMap() {
  const mapEl = document.getElementById('compare-map');
  if (cmp.map) { cmp.map.remove(); cmp.map = null; }
  cmp.map = L.map(mapEl, {zoomControl:true, attributionControl:false});
  const _cmpStyle = MAP_STYLES[_uiPrefsGet('ascent-map-style') || 'osm'] || MAP_STYLES['osm'];
  cmp.tileLayer = L.tileLayer(_cmpStyle.url, {maxZoom:19});
  cmp.tileLayer.addTo(cmp.map);
  MapUtils.addScale(cmp.map, U.metric);

  cmp.tracks  = [];
  cmp.markers = [];

  const allLL = [];

  cmp.matches.forEach((m, i) => {
    if (m.missing || !m.points || !m.points.length) {
      cmp.tracks.push(null);
      cmp.markers.push(null);
      return;
    }
    const color = CMP_COLORS[i % CMP_COLORS.length];

    // Faint full-segment track
    const lls = m.points.filter(p => p.lat != null && p.lon != null).map(p => [p.lat, p.lon]);
    if (!lls.length) { cmp.tracks.push(null); cmp.markers.push(null); return; }
    allLL.push(...lls);
    const track = L.polyline(lls, {color:'#ef4444', weight:3, opacity:0.8, smoothFactor:1.5}).addTo(cmp.map);
    cmp.tracks.push(track);

    // Sphere marker at start
    const startLL = lls[0];
    const icon = L.divIcon({
      html: `<div style="width:14px;height:14px;border-radius:50%;background:radial-gradient(circle at 35% 35%,${color}ee,${color}88 55%,${color}44);box-shadow:0 1px 4px rgba(0,0,0,.7),inset -1px -1px 3px rgba(0,0,0,.3),inset 1px 1px 2px rgba(255,255,255,.4)"></div>`,
      iconSize:[14,14], iconAnchor:[7,7], className:''
    });
    const marker = L.marker(startLL, {icon, zIndexOffset: 1000 + i}).addTo(cmp.map);
    cmp.markers.push(marker);
  });

  // Fit map to all track points
  if (allLL.length > 0) cmp.map.fitBounds(L.latLngBounds(allLL), {padding:[16,16]});

  // Init zoom (chase-cam) map
  const zoomEl = document.getElementById('compare-zoom');
  if (cmp.zoomMap) { cmp.zoomMap.remove(); cmp.zoomMap = null; }
  if (zoomEl && allLL.length > 0) {
    const center = allLL[Math.floor(allLL.length / 2)];
    cmp.zoomMap = L.map(zoomEl, {
      zoomControl: false, attributionControl: false,
      dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
      keyboard: false, touchZoom: false, boxZoom: false,
    });
    cmp.zoomTile = L.tileLayer(_cmpStyle.url, {maxZoom:19});
    cmp.zoomTile.addTo(cmp.zoomMap);
    cmp.zoomMap.setView(center, 16);

    // Add faint segment tracks to zoom map too
    cmp.matches.forEach((m) => {
      if (m.missing || !m.points || !m.points.length) return;
      const lls = m.points.filter(p => p.lat != null && p.lon != null).map(p => [p.lat, p.lon]);
      if (!lls.length) return;
      L.polyline(lls, {color:'#ef4444', weight:2, opacity:0.4, smoothFactor:1}).addTo(cmp.zoomMap);
    });

    // Add zoom markers (same colors, slightly larger)
    cmp.zoomMarkers = [];
    cmp.matches.forEach((m, i) => {
      if (m.missing || !m.points || !m.points.length) {
        cmp.zoomMarkers.push(null);
        return;
      }
      const color = CMP_COLORS[i % CMP_COLORS.length];
      const zLls = m.points.filter(p => p.lat != null && p.lon != null).map(p => [p.lat, p.lon]);
      if (!zLls.length) { cmp.zoomMarkers.push(null); return; }
      const startLL = zLls[0];
      const icon = L.divIcon({
        html: `<div style="width:18px;height:18px;border-radius:50%;background:radial-gradient(circle at 35% 35%,${color}ee,${color}88 55%,${color}44);box-shadow:0 2px 6px rgba(0,0,0,.8),inset -1px -1px 3px rgba(0,0,0,.3),inset 1px 1px 2px rgba(255,255,255,.4)"></div>`,
        iconSize:[18,18], iconAnchor:[9,9], className:''
      });
      const marker = L.marker(startLL, {icon, zIndexOffset: 2000 + i}).addTo(cmp.zoomMap);
      cmp.zoomMarkers.push(marker);
    });
  }

  setTimeout(() => {
    if (cmp.map) cmp.map.invalidateSize();
    if (cmp.zoomMap) cmp.zoomMap.invalidateSize();
  }, 100);
}

// ── Compare Elevation Profile ────────────────────────────────────────────────
function initCompareProfile() {
  const ctx = document.getElementById('cmp-profile-canvas');
  if (!ctx) return;
  if (cmp.profileChart) { cmp.profileChart.destroy(); cmp.profileChart = null; }
  if (!cmp.matches.length) return;

  // Use reference activity (index 0) points for elevation profile
  const ref = cmp.matches[0];
  if (!ref.points || !ref.points.length) return;

  // Build profile from dist_m already in point data
  const profilePts = ref.points.map(p => ({
    x: U.metric ? (p.dist_m||0)/1000 : (p.dist_m||0)/1609.344,
    y: U.alt(p.alt_ft || 0)
  }));

  const totalDist = profilePts[profilePts.length-1]?.x || 1;
  const xUnit = U.metric ? 'km' : 'mi';
  const yUnit = U.altUnit();

  // Per-rider dot plugin
  const dotPlugin = {
    id: 'cmpDots',
    afterDatasetsDraw(chart) {
      const {ctx: cx, scales} = chart;
      const xSc = scales.x, ySc = scales.y;
      if (!xSc || !ySc) return;
      cmp.profileDots.forEach((d, i) => {
        if (d == null) return;
        const color = CMP_COLORS[i % CMP_COLORS.length];
        const px = xSc.getPixelForValue(d.x);
        const py = ySc.getPixelForValue(d.y);
        cx.save();
        cx.beginPath();
        cx.arc(px, py, 6, 0, Math.PI*2);
        const grad = cx.createRadialGradient(px-1, py-2, 1, px, py, 6);
        grad.addColorStop(0, color + 'ff');
        grad.addColorStop(1, color + '88');
        cx.fillStyle = grad;
        cx.shadowColor = 'rgba(0,0,0,.6)';
        cx.shadowBlur = 4;
        cx.fill();
        cx.restore();
      });
    }
  };

  cmp.profileChart = new Chart(ctx, {
    type: 'line',
    data: { datasets: [{
      data: profilePts, fill: true,
      borderColor: '#3b82f6', backgroundColor: 'rgba(30,80,180,.5)',
      borderWidth: 1.5, pointRadius: 0, tension: 0.3,
    }]},
    plugins: [dotPlugin],
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: {
          type: 'linear', min: 0, max: totalDist,
          ticks: { color: '#64748b', font: { size: 9 }, maxTicksLimit: 8,
                   callback: v => `${v.toFixed(1)}${xUnit}` },
          grid: { color: 'rgba(255,255,255,.04)' },
        },
        y: {
          ticks: { color: '#64748b', font: { size: 9 }, maxTicksLimit: 4,
                   callback: v => `${Math.round(v)}${yUnit}` },
          grid: { color: 'rgba(255,255,255,.06)' },
        },
      },
      layout: { padding: { top: 10, bottom: 2, left: 2, right: 8 } },
    },
  });

  // Store profile for altitude lookup during animation
  cmp.profilePts = profilePts;

  // Initialize dots at start (null for missing entries — not drawn)
  cmp.profileDots = cmp.matches.map((m) => (!m.missing && profilePts[0]) ? {...profilePts[0]} : null);
}

// ── Animation (normalized time: progress 0→1, all riders start+finish together)
// At normalized progress p, rider i is at the point in their track where
// their_elapsed_time / their_total_elapsed = p.
// Faster riders appear ahead spatially at any given p.
// The scrubber/timecode are keyed to the fastest rider's actual elapsed time.
// ─────────────────────────────────────────────────────────────────────────────
function interpAtTime(points, wallT) {
  // Look up interpolated data at wall-clock time wallT (seconds, 0-based).
  if (!points || points.length === 0) return null;
  if (wallT <= 0) return {...points[0]};
  for (let i = 1; i < points.length; i++) {
    if (points[i].t >= wallT) {
      const frac = (wallT - points[i-1].t) / Math.max(0.001, points[i].t - points[i-1].t);
      const lerp = (a, b) => a + frac * (b - a);
      return {
        lat:       lerp(points[i-1].lat,       points[i].lat),
        lon:       lerp(points[i-1].lon,       points[i].lon),
        alt_ft:    lerp(points[i-1].alt_ft,    points[i].alt_ft),
        speed_mph: lerp(points[i-1].speed_mph, points[i].speed_mph),
        dist_m:    lerp(points[i-1].dist_m||0, points[i].dist_m||0),
      };
    }
  }
  return {...points[points.length-1]};
}

function interpOnRefByDist(refPoints, distM) {
  // Project a distance (metres) onto the reference track, returning {lat, lon}.
  // Used to place all riders on the same physical path for map display.
  if (!refPoints || refPoints.length === 0) return null;
  if (distM <= 0) return {lat: refPoints[0].lat, lon: refPoints[0].lon};
  const last = refPoints[refPoints.length-1];
  if (distM >= (last.dist_m||0)) return {lat: last.lat, lon: last.lon};
  for (let i = 1; i < refPoints.length; i++) {
    const d0 = refPoints[i-1].dist_m || 0;
    const d1 = refPoints[i].dist_m   || 0;
    if (d1 >= distM) {
      const frac = (d1 - d0) > 0.001 ? (distM - d0) / (d1 - d0) : 0;
      return {
        lat: refPoints[i-1].lat + frac * (refPoints[i].lat - refPoints[i-1].lat),
        lon: refPoints[i-1].lon + frac * (refPoints[i].lon - refPoints[i-1].lon),
      };
    }
  }
  return {lat: last.lat, lon: last.lon};
}

function cmpUpdateUI(wallT) {
  // wallT = seconds elapsed in animation (0 → fastest elapsed_s)
  const refEl = cmpFastestElapsed();
  cmp.currentT = Math.max(0, Math.min(wallT, refEl));

  document.getElementById('cmp-time').textContent = fmtCmpTime(cmp.currentT);
  document.getElementById('cmp-scrub').value = Math.round((cmp.currentT / refEl) * 1000);

  const refData = interpAtTime(cmp.matches[0]?.points, cmp.currentT);

  let sumLat = 0, sumLon = 0, zoomCount = 0;

  const refPoints = cmp.matches[0]?.points;

  cmp.matches.forEach((m, i) => {
    if (m.missing) return;
    const d = interpAtTime(m.points, cmp.currentT);
    if (!d) return;

    // Map position: use each rider's actual GPS coordinates
    const mapPos = {lat: d.lat, lon: d.lon};

    if (cmp.markers[i])     cmp.markers[i].setLatLng([mapPos.lat, mapPos.lon]);
    if (cmp.zoomMarkers[i]) {
      cmp.zoomMarkers[i].setLatLng([mapPos.lat, mapPos.lon]);
      sumLat += mapPos.lat; sumLon += mapPos.lon; zoomCount++;
    }

    // Profile dot: x = rider's current distance, y = reference activity's altitude at that distance
    if (cmp.profileChart) {
      const distX = U.metric ? (d.dist_m||0)/1000 : (d.dist_m||0)/1609.344;
      const refY = (() => {
        const pts = cmp.profilePts;
        if (!pts || pts.length === 0) return U.alt(d.alt_ft||0);
        if (distX <= pts[0].x) return pts[0].y;
        if (distX >= pts[pts.length-1].x) return pts[pts.length-1].y;
        for (let j = 1; j < pts.length; j++) {
          if (pts[j].x >= distX) {
            const t = (distX - pts[j-1].x) / (pts[j].x - pts[j-1].x);
            return pts[j-1].y + t * (pts[j].y - pts[j-1].y);
          }
        }
        return pts[pts.length-1].y;
      })();
      cmp.profileDots[i] = { x: distX, y: refY };
    }

    // Delta: estimate time ahead/behind reference using distance gap + current speeds
    const deltaEl = document.getElementById('cmp-delta-' + m.activity_id);
    if (deltaEl) {
      if (m.activity_id === cmp.matches[0]?.activity_id) {
        deltaEl.textContent = '0:00';
        deltaEl.style.color = 'var(--muted2)';
      } else if (refData) {
        const refDist  = refData.dist_m  || 0;
        const thisDist = d.dist_m || 0;
        const distDiff = thisDist - refDist; // positive = this rider is ahead
        // Use average speed to convert distance gap to time
        const refSpd  = Math.max(0.5, (refData.speed_mph  || 1) * 0.44704);
        const thisSpd = Math.max(0.5, (d.speed_mph || 1) * 0.44704);
        const avgSpd  = (refSpd + thisSpd) / 2;
        const timeDiff = distDiff / avgSpd; // seconds ahead (+) or behind (-)
        const absDiff  = Math.abs(timeDiff);
        const mm = Math.floor(absDiff / 60);
        const ss = Math.floor(absDiff % 60);
        const sign = timeDiff > 1 ? '−' : timeDiff < -1 ? '+' : '';
        deltaEl.textContent = `${sign}${mm}:${String(ss).padStart(2,'0')}`;
        deltaEl.style.color = timeDiff > 1 ? '#4ade80' : timeDiff < -1 ? '#f87171' : 'var(--muted2)';
      }
    }
  });

  if (cmp.profileChart) cmp.profileChart.update('none');
  if (zoomCount > 0 && cmp.zoomMap) {
    const lls = cmp.zoomMarkers.map(m => m.getLatLng()).filter(ll => ll);
    if (lls.length === 1) {
      cmp.zoomMap.panTo(lls[0], {animate: true, duration: 0.3});
    } else if (lls.length > 1) {
      const bounds = L.latLngBounds(lls);
      // Only refit if bounds changed significantly (>5% of current view)
      // to avoid constant animation jitter at 60fps
      const cur = cmp.zoomMap.getBounds();
      const newSpanLat = bounds.getNorth() - bounds.getSouth();
      const newSpanLon = bounds.getEast()  - bounds.getWest();
      const curSpanLat = cur.getNorth()    - cur.getSouth();
      const curSpanLon = cur.getEast()     - cur.getWest();
      const center     = bounds.getCenter();
      const curCenter  = cur.getCenter();
      const centerShift = Math.abs(center.lat - curCenter.lat) + Math.abs(center.lng - curCenter.lng);
      const spanChange  = Math.abs(newSpanLat - curSpanLat) / (curSpanLat || 0.001)
                        + Math.abs(newSpanLon - curSpanLon) / (curSpanLon || 0.001);
      if (spanChange > 0.08 || centerShift > (curSpanLat + curSpanLon) * 0.05) {
        cmp.zoomMap.fitBounds(bounds, {animate: true, duration: 0.5, padding: [40, 40], maxZoom: 16});
      }
    }
  }
}


// Playback speed: normalized units per second.
// At speed=1 the animation takes as long as the fastest rider's segment.
// Use speed=1 so the animation runs in real-time for the fastest rider.
function cmpGetSpeed() { return parseFloat(document.getElementById("cmp-speed")?.value || 25); }
function cmpSetSpeed(v) {
  _uiPrefsSet('ascent-cmp-speed', v);
}

function cmpFastestElapsed() {
  if (!cmp.matches.length) return 1;
  return Math.min(...cmp.matches.map(m => m.elapsed_s));
}

function cmpFrame(ts) {
  if (!cmp.playing) return;
  if (cmp.lastTs !== null) {
    const refElapsed = cmpFastestElapsed();
    cmp.currentT += ((ts - cmp.lastTs) / 1000) * cmpGetSpeed();
    if (cmp.currentT >= refElapsed) {
      cmpUpdateUI(refElapsed);
      cmpStop();
      return;
    }
    cmpUpdateUI(cmp.currentT);
  }
  cmp.lastTs = ts;
  cmp.rafId  = requestAnimationFrame(cmpFrame);
}

function cmpPlay() {
  if (cmp.playing) { cmpStop(); return; }
  const _refEl = cmpFastestElapsed();
  if (cmp.currentT >= _refEl) cmp.currentT = 0;
  cmp.playing = true;
  cmp.lastTs  = null;
  document.getElementById('cmp-play').textContent = '⏸';
  cmp.rafId = requestAnimationFrame(cmpFrame);
}

function cmpStop() {
  cmp.playing = false;
  if (cmp.rafId) { cancelAnimationFrame(cmp.rafId); cmp.rafId = null; }
  cmp.lastTs = null;
  document.getElementById('cmp-play').textContent = '▶';
}

function cmpJump(frac) {
  cmpStop();
  cmpUpdateUI(frac * cmpFastestElapsed());
}

function cmpScrub(val) {
  cmpStop();
  cmpUpdateUI((val / 1000) * cmpFastestElapsed());
}

// ── COMPARE PANE RESIZE ──────────────────────────────────────────────────────
(function() {
  function initCmpResize() {
    const handle  = document.getElementById('cmp-maps-handle');
    const mapsRow = document.getElementById('compare-maps');
    if (!handle || !mapsRow) return;

    let startY = 0, startH = 0;

    function startResize(clientY) {
      startY = clientY;
      startH = mapsRow.getBoundingClientRect().height;
      handle.classList.add('dragging');
      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
      cmp._resizing = true;
      clearTimeout(cmp._resizeTimer);
    }
    function doResize(clientY) {
      const dy   = clientY - startY;
      const newH = Math.max(120, startH + dy);
      mapsRow.style.flex = 'none';
      mapsRow.style.height = newH + 'px';
      if (cmp.map)     cmp.map.invalidateSize();
      if (cmp.zoomMap) cmp.zoomMap.invalidateSize();
    }
    function endResize() {
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup',   onMouseUp);
      document.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('touchend',  onTouchEnd);
      if (cmp.map)     cmp.map.invalidateSize();
      if (cmp.zoomMap) cmp.zoomMap.invalidateSize();
      if (cmp.profileChart) cmp.profileChart.resize();
      cmp._resizeTimer = setTimeout(() => { cmp._resizing = false; }, 300);
    }

    function onMouseMove(e) { doResize(e.clientY); }
    function onMouseUp()    { endResize(); }
    function onTouchMove(e) {
      e.preventDefault();
      const t = e.touches[0] || e.changedTouches[0];
      if (t) doResize(t.clientY);
    }
    function onTouchEnd(e)  {
      const t = e.changedTouches[0];
      if (t) doResize(t.clientY);
      endResize();
    }

    handle.addEventListener('mousedown', e => {
      e.preventDefault();
      startResize(e.clientY);
      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup',   onMouseUp);
    });
    handle.addEventListener('touchstart', e => {
      e.preventDefault();
      const t = e.touches[0] || e.changedTouches[0];
      if (t) startResize(t.clientY);
      document.addEventListener('touchmove', onTouchMove, {passive: false});
      document.addEventListener('touchend',  onTouchEnd,  {passive: false});
    }, {passive: false});

    // When the panel itself is resized (via CSS resize handle), invalidate maps
    const panel = document.getElementById('compare-panel');
    if (panel && typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => {
        if (cmp.map)          cmp.map.invalidateSize();
        if (cmp.zoomMap)      cmp.zoomMap.invalidateSize();
        if (cmp.profileChart) cmp.profileChart.resize();
        // Mark resizing so overlay click doesn't close panel
        cmp._resizing = true;
        clearTimeout(cmp._resizeTimer);
        cmp._resizeTimer = setTimeout(() => { cmp._resizing = false; }, 300);
      }).observe(panel);
    }
  }

  document.addEventListener('DOMContentLoaded', initCmpResize);
  setTimeout(initCmpResize, 500);
})();

// ── Compare panel corner-drag resize (touch / iPad) ───────────────────────────
(function() {
  function initCmpCornerResize() {
    const handle = document.getElementById('cmp-corner-handle');
    const panel  = document.getElementById('compare-panel');
    if (!handle || !panel) return;

    let startX, startY, startW, startH;

    handle.addEventListener('touchstart', e => {
      e.preventDefault();
      e.stopPropagation();
      const t = e.touches[0];
      startX = t.clientX;
      startY = t.clientY;
      startW = panel.offsetWidth;
      startH = panel.offsetHeight;
      handle.style.opacity = '0.9';
      document.addEventListener('touchmove', onMove,  {passive: false});
      document.addEventListener('touchend',  onEnd,   {passive: false});
    }, {passive: false});

    function onMove(e) {
      e.preventDefault();
      const t = e.touches[0];
      const newW = Math.max(320, Math.min(startW + (t.clientX - startX), window.innerWidth  * 0.98));
      const newH = Math.max(340, Math.min(startH + (t.clientY - startY), window.innerHeight * 0.96));
      panel.style.width  = newW + 'px';
      panel.style.height = newH + 'px';
      if (cmp.map)     cmp.map.invalidateSize();
      if (cmp.zoomMap) cmp.zoomMap.invalidateSize();
      cmp._resizing = true;
      clearTimeout(cmp._resizeTimer);
    }

    function onEnd() {
      handle.style.opacity = '';
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend',  onEnd);
      if (cmp.map)          cmp.map.invalidateSize();
      if (cmp.zoomMap)      cmp.zoomMap.invalidateSize();
      if (cmp.profileChart) cmp.profileChart.resize();
      _uiPrefsSet('ascent-cmp-w', panel.style.width);
      _uiPrefsSet('ascent-cmp-h', panel.style.height);
      cmp._resizeTimer = setTimeout(() => { cmp._resizing = false; }, 300);
    }
  }

  document.addEventListener('DOMContentLoaded', initCmpCornerResize);
  setTimeout(initCmpCornerResize, 500);
})();

// ── SEGMENT SAVE / LOAD ──────────────────────────────────────────────────────

async function cmpLoadSavedSegments() {
  const sel = document.getElementById('cmp-seg-select');
  if (!sel) return;

  // Manual mode: fetch segments for all selected activities
  const ids = (cmp._manualIds && cmp._manualIds.length >= 2)
    ? cmp._manualIds
    : (currentAct ? [currentAct.id] : []);
  if (!ids.length) return;

  try {
    // Collect all segments per activity, then intersect
    const perActivity = [];
    for (const id of ids) {
      const r = await fetch(`/api/segments/for-activity/${id}`);
      if (!r.ok) continue;
      const d = await r.json();
      perActivity.push(Array.isArray(d) ? d : (d.segments || []));
    }
    if (!perActivity.length) { cmp.savedSegments = []; sel.innerHTML = '<option value="">— choose —</option>'; return; }

    // Start with first activity's segments, filter to only those in ALL others
    let allSegs = [...perActivity[0]];
    for (let i = 1; i < perActivity.length; i++) {
      const idsI = new Set(perActivity[i].map(s => s.id));
      allSegs = allSegs.filter(s => idsI.has(s.id));
    }
    allSegs.sort((a, b) => a.name.localeCompare(b.name));
    cmp.savedSegments = allSegs;

    sel.innerHTML = '<option value="">— choose —</option>';
    allSegs.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      const lenStr = s.length_km
        ? U.metric
          ? s.length_km.toFixed(2) + ' km'
          : (s.length_km * 0.621371).toFixed(2) + ' mi'
        : '?';
      opt.textContent = `${s.name} (${lenStr})`;
      if (s.id === cmp.savedSegId) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch(e) { console.warn('cmpLoadSavedSegments:', e); }
}

function cmpUpdateSaveBtn() {
  const nameEl = document.getElementById('cmp-seg-name');
  const btn    = document.getElementById('cmp-seg-save');
  if (!nameEl || !btn) return;
  const currentName = nameEl.value.trim();
  if (cmp.savedSegId) {
    // Rename mode: enable only when name actually changed
    btn.textContent = 'Rename';
    btn.disabled = (currentName === (cmp.savedSegName || '') || currentName === '');
  } else {
    // Save mode: enable when name has text
    btn.textContent = 'Save';
    btn.disabled = (currentName === '');
  }
}

async function cmpSaveSegment() {
  if (!currentAct) return;
  const nameEl = document.getElementById('cmp-seg-name');
  const btn    = document.getElementById('cmp-seg-save');
  const name   = (nameEl?.value || '').trim();
  if (!name) { nameEl?.focus(); return; }

  // If we already have a saved segment loaded, just rename it
  if (cmp.savedSegId) {
    try {
      await fetch(`/api/segments/${cmp.savedSegId}`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({name}),
      });
      cmp.savedSegName = name;
      btn.textContent = 'Renamed!'; btn.classList.add('saved');
      setTimeout(() => { btn.classList.remove('saved'); cmpUpdateSaveBtn(); }, 1500);
      await cmpLoadSavedSegments();
    } catch(e) { console.error(e); }
    return;
  }

  // New save
  const body = {
    name,
    activity_id: currentAct.id,
    start_idx:   cmp.selStartIdx,
    end_idx:     cmp.selEndIdx,
  };
  try {
    const r = await fetch('/api/segments', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) { console.error(await r.text()); return; }
    const d = await r.json();
    cmp.savedSegId   = d.id;
    cmp.savedSegName = name;
    btn.textContent = 'Saved!'; btn.classList.add('saved');
    setTimeout(() => { btn.classList.remove('saved'); cmpUpdateSaveBtn(); }, 1500);
    await cmpLoadSavedSegments();
    // Select the newly saved segment
    const sel = document.getElementById('cmp-seg-select');
    if (sel) sel.value = d.id;
  } catch(e) { console.error(e); }
}

async function cmpRunManualCompare(seg) {
  const overlay = document.getElementById('compare-overlay');
  const loading = document.getElementById('cmp-loading');
  const titleEl = document.getElementById('compare-title');
  overlay.classList.add('open');
  loading.style.display = 'flex';
  document.getElementById('cmp-loading-text').textContent = 'Loading activities…';

  const _bar = document.getElementById('cmp-progress-bar');
  if (_bar) { _bar.style.transition = 'none'; _bar.style.width = '0%'; }
  void loading.offsetHeight;
  let _pct = 0;
  const _prog = setInterval(() => {
    _pct = _pct + (85 - _pct) * 0.06;
    if (_bar) _bar.style.width = _pct.toFixed(1) + '%';
  }, 80);

  try {
    const r = await fetch('/api/segment/compare-manual', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ activity_ids: cmp._manualIds, segment_id: seg.id }),
    });
    const d = await r.json();
    clearInterval(_prog);
    if (_bar) { _bar.style.transition = 'width .15s ease'; _bar.style.width = '100%'; }
    setTimeout(() => { loading.style.display = 'none'; if (_bar) _bar.style.width = '0%'; }, 150);
    if (!r.ok) { titleEl.textContent = d.detail || 'No matches'; return; }

    const refId = cmp._manualIds[0];
    const ref   = d.matches.find(m => m.activity_id === refId);
    const rest  = d.matches.filter(m => m.activity_id !== refId);
    const baseMatches = ref ? [ref, ...rest] : d.matches;

    // Add placeholder entries for activities missing the segment
    const matchedIds = new Set(d.matches.map(m => m.activity_id));
    const allMatches = [...baseMatches];
    for (const id of (cmp._manualIds || [])) {
      if (!matchedIds.has(id)) {
        const act = state.all?.find(a => a.id === id) || state.filtered?.find(a => a.id === id);
        allMatches.push({
          activity_id: id,
          name:        act?.name || `Activity ${id}`,
          start_time:  act?.start_time || null,
          elapsed_s:   null,
          user_id:     act?.user_id || null,
          points:      [],
          missing:     true,
        });
      }
    }
    cmp.matches      = allMatches;
    cmp.currentT     = 0;
    cmp.savedSegId   = seg.id;
    cmp.savedSegName = seg.name;
    const _nameElM = document.getElementById('cmp-seg-name');
    if (_nameElM) _nameElM.value = seg.name;
    cmpUpdateSaveBtn();

    const nFound = d.matches.length;
    const nTotal = (cmp._manualIds || []).length;
    titleEl.textContent = nFound === nTotal
      ? `${escHtml(seg.name)} — ${nFound} Activit${nFound===1?'y':'ies'}`
      : `${escHtml(seg.name)} — ${nFound} of ${nTotal} Activities`;

    buildCompareLegend();
    initCompareMap();
    initCompareProfile();
    cmpUpdateUI(0);
    cmpRequestAiAnalysis();
    await cmpLoadSavedSegments();
  } catch(e) {
    clearInterval(_prog);
    if (_bar) _bar.style.width = '0%';
    loading.style.display = 'none';
    titleEl.textContent = 'Error: ' + e.message;
  }
}

async function cmpLoadSavedSegment(segId) {
  if (!segId) return;
  const seg = cmp.savedSegments.find(s => s.id == segId);
  if (!seg) return;

  // Manual mode: use pre-selected activity IDs
  if (cmp._manualIds && cmp._manualIds.length >= 2) {
    await cmpRunManualCompare(seg);
    return;
  }
  if (!currentAct) return;

  cmp.savedSegId   = seg.id;
  cmp.savedSegName = seg.name;
  const nameEl = document.getElementById('cmp-seg-name');
  if (nameEl) nameEl.value = seg.name;
  cmpUpdateSaveBtn();

  // Update elevation selection to match saved segment
  cmp.selStartIdx = seg.start_idx;
  cmp.selEndIdx   = seg.end_idx;
  setElevSelection(seg.start_idx, seg.end_idx);

  // Re-run compare with the saved segment's start/end on the reference activity
  const overlay = document.getElementById('compare-overlay');
  const loading = document.getElementById('cmp-loading');
  const titleEl = document.getElementById('compare-title');
  overlay.classList.add('open');
  loading.style.display = 'flex';
  document.getElementById('cmp-loading-text').textContent = 'Searching…';

  const _bar = document.getElementById('cmp-progress-bar');
  if (_bar) { _bar.style.transition = 'none'; _bar.style.width = '0%'; }
  void loading.offsetHeight; // force reflow so display:flex takes effect before fetch
  let _pct = 0;
  const _prog = setInterval(() => {
    _pct = _pct + (85 - _pct) * 0.06;
    if (_bar) _bar.style.width = _pct.toFixed(1) + '%';
  }, 80);

  try {
    const r = await fetch('/api/segment/compare', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        activity_id:     currentAct.id,
        start_idx:       seg.start_idx,
        end_idx:         seg.end_idx,
        max_results:     4,
        radius_m:        150,
        include_friends: !!(document.getElementById('cmp-include-friends')?.checked),
      }),
    });
    const d = await r.json();
    clearInterval(_prog);
    if (_bar) { _bar.style.transition = 'width .15s ease'; _bar.style.width = '100%'; }
    setTimeout(() => { loading.style.display = 'none'; if (_bar) _bar.style.width = '0%'; }, 150);
    if (!r.ok) { titleEl.textContent = d.detail || 'No matches'; return; }

    const refId = currentAct.id;
    const ref   = d.matches.find(m => m.activity_id === refId);
    const rest  = d.matches.filter(m => m.activity_id !== refId);
    rest.sort((a, b) => a.elapsed_s - b.elapsed_s);
    cmp.matches  = ref ? [ref, ...rest] : d.matches;
    cmp.currentT = 0;

    const nOthers = cmp.matches.length - 1;
    titleEl.textContent = `${escHtml(seg.name)} — vs. ${nOthers} Fastest`;

    buildCompareLegend();
    initCompareMap();
    initCompareProfile();
    cmpUpdateUI(0);
    cmpRequestAiAnalysis();
  } catch(e) {
    clearInterval(_prog);
    if (_bar) _bar.style.width = '0%';
    loading.style.display = 'none';
    titleEl.textContent = 'Error: ' + e.message;
  }
}

async function cmpDeleteSegment() {
  const sel = document.getElementById('cmp-seg-select');
  const segId = sel?.value;
  if (!segId) return;
  const seg = cmp.savedSegments.find(s => s.id == segId);
  if (!seg) return;
  if (!confirm(`Delete segment "${seg.name}"?`)) return;
  await fetch(`/api/segments/${segId}`, {method: 'DELETE'});
  if (cmp.savedSegId == segId) {
    cmp.savedSegId   = null;
    cmp.savedSegName = null;
    const nameEl = document.getElementById('cmp-seg-name');
    if (nameEl) nameEl.value = '';
    cmpUpdateSaveBtn();
  }
  await cmpLoadSavedSegments();
}

// ── AI ANALYSIS ───────────────────────────────────────────────────────────────

function cmpClearAiAnalysis() {
  const el = document.getElementById('cmp-ai-analysis');
  const textEl = document.getElementById('cmp-ai-analysis-text');
  const spinner = document.getElementById('cmp-ai-spinner');
  if (el) el.style.display = 'none';
  if (textEl) textEl.textContent = '';
  if (spinner) spinner.style.display = 'none';
}

async function cmpRequestAiAnalysis() {
  const matches = cmp.matches.filter(m => !m.missing && m.elapsed_s != null);
  if (matches.length < 2) return;

  const el = document.getElementById('cmp-ai-analysis');
  const textEl = document.getElementById('cmp-ai-analysis-text');
  const spinner = document.getElementById('cmp-ai-spinner');
  if (!el || !textEl) return;

  // Show loading state
  el.style.display = '';
  textEl.textContent = '';
  if (spinner) spinner.style.display = 'flex';

  // Get model from coach model selector (user's preference) or fall back to default
  const modelSel = document.getElementById('coach-model-select') || document.getElementById('coach-setup-model');
  const model = modelSel?.value || 'claude-haiku-4-5-20251001';

  // Build elapsed_times map
  const elapsedTimes = {};
  matches.forEach(m => { elapsedTimes[String(m.activity_id)] = m.elapsed_s; });

  // Segment name from title
  const titleEl = document.getElementById('compare-title');
  const segName = titleEl?.textContent || 'Segment';

  try {
    const r = await fetch('/api/coach/compare-analysis', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        activity_ids: matches.map(m => m.activity_id),
        elapsed_times: elapsedTimes,
        segment_name: segName,
        model,
      }),
    });
    const d = await r.json();
    if (spinner) spinner.style.display = 'none';
    if (!r.ok) {
      textEl.textContent = d.detail || 'Analysis unavailable.';
      textEl.style.color = 'var(--muted)';
      return;
    }
    textEl.style.color = '';
    textEl.textContent = d.analysis || 'No analysis returned.';
  } catch(e) {
    if (spinner) spinner.style.display = 'none';
    textEl.textContent = 'Analysis unavailable.';
    textEl.style.color = 'var(--muted)';
  }
}

// ── MAKE SEGMENT (from Compare) ───────────────────────────────────────────────

function cmpOpenMakeSegment() {
  openDefineSegment();
}
