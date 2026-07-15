// ── STAGE ELEVATION CHART ──────────────────────────────────────────────────────
// Shared elevation strip chart builder for tour and tour_share pages.
// Depends on: Chart.js, global U (units object).

function _elevIsLight() {
  const t = document.documentElement.dataset.theme;
  if (t === 'light') return true;
  if (t === 'dark')  return false;
  return window.matchMedia('(prefers-color-scheme: light)').matches;
}


// Compute smoothed gradient % at each chartPt using a distance-based window (~200m each side).
// chartPts: [{x: cumDist (in mi or km), y: alt (in ft or m)}, ...]
// Used as fallback when full-resolution raw pts are not available.
function computeStageGradPts(chartPts) {
  const n = chartPts.length;
  if (n < 2) return chartPts.map(p => ({ x: p.x, y: 0 }));
  const metric = typeof U !== 'undefined' && U.metric;
  const factor = metric ? 1000 : 5280; // km→m or mi→ft
  const half   = metric ? 0.025 : 0.016; // ~25m each side in display units
  const gradPts = [];
  for (let i = 0; i < n; i++) {
    const cx = chartPts[i].x;
    let lo = i, hi = i;
    while (lo > 0 && cx - chartPts[lo - 1].x <= half) lo--;
    while (hi < n - 1 && chartPts[hi + 1].x - cx <= half) hi++;
    // If the target distance didn't reach any neighbor, use the nearest point each side
    if (lo === hi) { if (lo > 0) lo--; else if (hi < n - 1) hi++; }
    const dx = chartPts[hi].x - chartPts[lo].x;
    const dy = chartPts[hi].y - chartPts[lo].y;
    gradPts.push({ x: cx, y: dx > 0 ? Math.round(dy / (dx * factor) * 1000) / 10 : 0 });
  }
  return gradPts;
}

// Compute gradient from full-resolution raw pts [[lat,lon,alt_ft],...], downsampled
// to align with chartPts (same step). Uses bin-max so short steep sections aren't missed.
// distConv: cumulative miles → display x-units (same fn used when building chartPts).
function computeStageGradFromRawPts(pts, step, distConv) {
  const n = pts.length;
  if (n < 2) return [];

  // Cumulative miles for all raw pts
  const R = 3958.8;
  const cumMi = new Array(n);
  cumMi[0] = 0;
  for (let i = 1; i < n; i++) {
    const dLat = (pts[i][0] - pts[i-1][0]) * Math.PI / 180;
    const dLon = (pts[i][1] - pts[i-1][1]) * Math.PI / 180;
    const a = Math.sin(dLat/2)**2 + Math.cos(pts[i-1][0]*Math.PI/180)*Math.cos(pts[i][0]*Math.PI/180)*Math.sin(dLon/2)**2;
    cumMi[i] = cumMi[i-1] + R * 2 * Math.asin(Math.sqrt(a));
  }

  // Per-point gradient with ~25m half-window (0.016 mi ≈ 25.7 m)
  const half = 0.016;
  const gradRaw = new Array(n).fill(0);
  for (let i = 0; i < n; i++) {
    let lo = i, hi = i;
    while (lo > 0 && cumMi[i] - cumMi[lo - 1] <= half) lo--;
    while (hi < n - 1 && cumMi[hi + 1] - cumMi[i] <= half) hi++;
    if (lo === hi) { if (lo > 0) lo--; else if (hi < n - 1) hi++; }
    const dAlt  = (pts[hi][2] || 0) - (pts[lo][2] || 0); // feet
    const dDist = (cumMi[hi] - cumMi[lo]) * 5280;         // feet
    gradRaw[i]  = dDist > 1 ? (dAlt / dDist) * 100 : 0;
  }

  // Downsample with bin-max, mirroring the chartPts construction loop exactly
  const gradPts = [];
  for (let i = 0; i < n; i++) {
    if (i % step === 0 || i === n - 1) {
      const end = Math.min(i + step, n);
      let maxAbsG = 0, maxG = gradRaw[i];
      for (let j = i; j < end; j++) {
        if (Math.abs(gradRaw[j]) > maxAbsG) { maxAbsG = Math.abs(gradRaw[j]); maxG = gradRaw[j]; }
      }
      gradPts.push({ x: distConv(cumMi[i]), y: Math.round(maxG * 10) / 10 });
    }
  }
  return gradPts;
}


// Build (or rebuild) the Chart.js elevation strip.
// opts: { gradeOn: bool, showPeaks: bool, gradPts: [{x,y}] }
// Returns the new Chart instance.
function buildStageElevChart(canvas, chartPts, opts) {
  const gradeOn   = !!(opts && opts.gradeOn);
  const showPeaks = !!(opts && opts.showPeaks);
  const light     = _elevIsLight();
  const metric    = typeof U !== 'undefined' && U.metric;
  const xUnit     = metric ? 'km' : 'mi';
  const yUnit     = metric ? 'm'  : 'ft';
  const gridX     = light ? 'rgba(0,0,0,.08)'  : 'rgba(255,255,255,.04)';
  const gridY     = light ? 'rgba(0,0,0,.10)'  : 'rgba(255,255,255,.06)';

  const totalX = chartPts[chartPts.length - 1].x;
  const minY   = Math.min(...chartPts.map(p => p.y));
  const yFloor = Math.floor(minY / 100) * 100;

  const peakList = showPeaks ? findPeaks(chartPts, 3) : [];

  const peakPlugin = makePeakPlugin(peakList, {
    labelFn: pk => pk.y + ' ' + yUnit,
    isLightFn: _elevIsLight,
  });

  const gradPts = gradeOn
    ? ((opts && opts.gradPts && opts.gradPts.length) ? opts.gradPts : computeStageGradPts(chartPts))
    : null;

  const gradFillPlugin = (gradeOn && gradPts)
    ? makeElevFillPlugin(chartPts, gradPts.map(p => p.y), 'y', light)
    : null;

  const datasets = [{
    data: chartPts,
    fill: !gradeOn,
    borderColor: gradeOn ? 'rgba(255,255,255,0.35)' : '#3b82f6',
    backgroundColor: gradeOn ? 'transparent' : 'rgba(30,80,180,.55)',
    borderWidth: 1.5, pointRadius: 0, tension: 0.3,
  }];

  const scales = {
    x: {
      type: 'linear', min: 0, max: totalX,
      ticks: { color: '#64748b', font: { size: 9 }, maxTicksLimit: 8,
               callback: v => `${(+v).toFixed(1)} ${xUnit}` },
      grid: { color: gridX },
    },
    y: {
      position: 'left', min: yFloor,
      afterBuildTicks(axis) { if (axis.ticks.length > 1) axis.ticks.pop(); },
      ticks: { color: '#64748b', font: { size: 9 }, maxTicksLimit: 4,
               callback: v => `${v} ${yUnit}` },
      grid: { color: gridY },
    },
  };

  return new Chart(canvas, {
    type: 'line',
    data: { datasets },
    plugins: [...(gradFillPlugin ? [gradFillPlugin] : []), peakPlugin],
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      layout: { padding: { top: peakList.length ? 27 : 4, right: 10, bottom: 0, left: 0 } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales,
    },
  });
}

// ── STAGE ELEVATION INTERACTION ───────────────────────────────────────────────
// Attaches Activities-page-style hover HUD and drag region selection to the
// elevation strip after buildStageElevChart has built the chart. Call this from
// drawStageElevation (and again after _rebuildStageElevChart via _stageElevReapplySel).
//
// opts:
//   stripId      string         container id (default 'stage-elev-strip')
//   getChart     ()=>Chart|null
//   getChartPts  ()=>[{x,y}]   x=display-dist (km or mi), y=display-alt (m or ft)
//   getGradPts   ()=>[{x,y}]   gradient % at each chartPt
//   getRawPts    ()=>[[lat,lon,...]]   for the map dot
//   getActData   ()=>{dist_m,hr,speed,power,cadence,time}|null  per-point arrays
//   getMap       ()=>Leaflet map|null
//   getMapDot    ()=>Leaflet marker|null
//   setMapDot    (marker)=>void
//   onSelection  (lo,hi)=>void  integer chartPts indices, called when sel changes
//   onClear      ()=>void
//
// Exposes on window:
//   _stageElevHudToggle()    toggle HUD mode (called by the HUD button)
//   _stageElevGetSel()       returns {lo,hi} or null
//   _stageElevClearSel()     clears the selection
//   _stageElevReapplySel()   redraws the green overlay after chart rebuild
function stageElevInteraction(opts) {
  const strip = document.getElementById(opts.stripId || 'stage-elev-strip');
  if (!strip) return;

  // ── overlay elements (created once, reused on re-init) ────────────────────
  function _getOrMake(cls) {
    let el = strip.querySelector('.' + cls);
    if (!el) { el = document.createElement('div'); el.className = cls; strip.appendChild(el); }
    return el;
  }
  const hudPanel = _getOrMake('se-hover-panel');
  const selBox   = _getOrMake('se-sel-stats');
  const dragRect = _getOrMake('se-sel-rect');
  const chartDot = _getOrMake('se-elev-dot');
  chartDot.style.position     = 'absolute';
  chartDot.style.pointerEvents = 'none';
  chartDot.style.width        = '10px';
  chartDot.style.height       = '10px';
  chartDot.style.borderRadius = '50%';
  chartDot.style.background   = 'radial-gradient(circle at 35% 35%,#ffe066,#f59e0b 55%,#b45309)';
  chartDot.style.boxShadow    = '0 1px 3px rgba(0,0,0,.6)';
  chartDot.style.transform    = 'translate(-50%,-50%)';
  chartDot.style.zIndex       = '6';
  // Hidden until first hover; reset on each stage draw so a puck left showing (it now
  // persists after mouseleave — see hideHover) doesn't linger onto the next stage.
  chartDot.style.display = 'none';
  // Hide legacy elements if still in DOM
  const _oldHud = document.getElementById('stage-elev-hud');
  const _oldSel = document.getElementById('stage-elev-sel');
  if (_oldHud) _oldHud.style.display = 'none';
  if (_oldSel) _oldSel.style.display = 'none';

  // ── state — read HUD state from button so it survives stage changes ────────
  let hudOn = !!(document.getElementById('stage-elev-hud-btn')?.classList.contains('active'));
  let selLo = -1, selHi = -1;
  const _zoom = { level: 1, offset: 0, maxLevel: 16 };

  // ── helpers ───────────────────────────────────────────────────────────────
  function _bsearch(arr, v) {
    if (!arr.length) return 0;
    let lo = 0, hi = arr.length - 1;
    while (lo < hi - 1) { const m = (lo + hi) >> 1; if (arr[m] < v) lo = m; else hi = m; }
    const span = arr[hi] - arr[lo];
    return lo + (span > 0 ? Math.max(0, Math.min(1, (v - arr[lo]) / span)) : 0);
  }
  function _lerp(arr, fi) {
    if (!arr?.length) return null;
    const lo = Math.min(arr.length - 2, Math.max(0, Math.floor(fi)));
    const hi = lo + 1;
    return arr[lo] + (arr[hi] - arr[lo]) * (fi - lo);
  }
  const _HC = { hr:'#ef4444', power:'#eab308', cadence:'#a855f7', speed:'#22c55e',
                altitude:'#60a5fa', distance:'rgba(255,255,255,.75)', gradient:'#fb923c' };

  function _clientXToFi(clientX) {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.();
    if (!chart || !cpts?.length) return 0;
    const ca = chart.chartArea, rect = strip.getBoundingClientRect();
    const px = Math.max(ca.left, Math.min(ca.right, clientX - rect.left));
    return _bsearch(cpts.map(p => p.x), chart.scales.x.getValueForPixel(px));
  }
  function _clientXToDistM(clientX) {
    const chart = opts.getChart?.(); if (!chart) return 0;
    const ca = chart.chartArea, rect = strip.getBoundingClientRect();
    const px = Math.max(ca.left, Math.min(ca.right, clientX - rect.left));
    const xv = chart.scales.x.getValueForPixel(px);
    return U.metric ? xv * 1000 : xv * 1609.344;
  }

  // ── hover HUD ─────────────────────────────────────────────────────────────
  function _showHud(clientX) {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.(), gpts = opts.getGradPts?.();
    if (!chart || !cpts?.length) { hudPanel.style.display = 'none'; return; }
    const ca = chart.chartArea, rect = strip.getBoundingClientRect();
    const px = clientX - rect.left;
    if (px < ca.left || px > ca.right) { hudPanel.style.display = 'none'; return; }

    // crosshair
    const xh = document.getElementById('stage-elev-xhair');
    if (xh) xh.style.cssText = `display:block;position:absolute;top:${ca.top}px;left:${px}px;` +
      `height:${ca.bottom-ca.top}px;width:1px;background:rgba(255,255,255,.45);pointer-events:none;z-index:5`;

    const fi    = _clientXToFi(clientX);
    const distM = _clientXToDistM(clientX);
    const ad    = opts.getActData?.();
    const afi   = (ad?.dist_m?.length) ? _bsearch(ad.dist_m, distM) : -1;

    const altD = _lerp(cpts.map(p => p.y), fi);
    const grad = gpts?.length ? _lerp(gpts.map(p => p.y), fi) : null;
    const tSec = afi >= 0 && ad?.time?.length    ? _lerp(ad.time,    afi) : null;
    const hr   = afi >= 0 && ad?.hr?.length      ? _lerp(ad.hr,      afi) : null;
    const pwr  = afi >= 0 && ad?.power?.length   ? _lerp(ad.power,   afi) : null;
    const cad  = afi >= 0 && ad?.cadence?.length ? _lerp(ad.cadence, afi) : null;
    const spd  = afi >= 0 && ad?.speed?.length   ? _lerp(ad.speed,   afi) : null;

    // chart dot
    const ys = chart.scales.y;
    if (altD != null && ys) {
      const dotY = ys.getPixelForValue(altD);
      chartDot.style.display = 'block';
      chartDot.style.left    = px + 'px';
      chartDot.style.top     = dotY + 'px';
    }

    // map dot
    const rpts = opts.getRawPts?.(), theMap = opts.getMap?.();
    if (rpts?.length && theMap) {
      const ri = Math.max(0, Math.min(rpts.length - 1, Math.round(fi)));
      const rp = rpts[ri];
      if (rp) {
        const ll = [rp[0], rp[1]];
        const dh = `<div style="width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 35%,#ffe066,#f59e0b 55%,#b45309);border:1.5px solid #fff;box-shadow:0 0 3px rgba(0,0,0,.5)"></div>`;
        let dot = opts.getMapDot?.();
        if (!dot) {
          dot = L.marker(ll, { icon: L.divIcon({ html: dh, className: '', iconSize: [10,10], iconAnchor: [5,5] }),
            zIndexOffset: 1000, interactive: false }).addTo(theMap);
          opts.setMapDot?.(dot);
        } else { dot.setLatLng(ll); }
        MapUtils.keepPointVisible(theMap, ll);
      }
    }

    const _cmap = { time:'distance', dist:'distance', alt:'altitude', grad:'gradient',
                    hr:'hr', power:'power', cadence:'cadence', speed:'speed' };
    const rows = elevHudRows({
      time: tSec != null ? tSec : undefined,     // omit Time row when there's no time data
      dist: distM,
      alt:  altD != null ? altD : undefined,     // omit when absent (route with no altitude)
      grad: grad != null ? grad : undefined,     // omit when there's no gradient series
      hr:      (hr  && hr  > 0) ? hr  : null,
      power:   (pwr && pwr > 0) ? pwr : null,
      cadence: (cad && cad > 0) ? cad : null,
      speed:   (spd && spd > 0) ? (U.metric ? spd * 1.60934 : spd) : null,
    }, field => _HC[_cmap[field]]);

    elevHudRender(hudPanel, rows, px, strip.offsetWidth);
  }

  function _hideHud() {
    hudPanel.style.display = 'none';
    const xh = document.getElementById('stage-elev-xhair');
    if (xh) xh.style.display = 'none';
  }

  // ── selection ─────────────────────────────────────────────────────────────
  function _applyDataset(lo, hi) {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.();
    if (!chart || !cpts) return;
    chart.data.datasets = chart.data.datasets.filter(d => !d._stageSel);
    if (hi > lo) {
      chart.data.datasets.push({
        _stageSel: true,
        data: cpts.slice(lo, hi + 1),
        fill: true, borderColor: 'rgba(160,160,160,0)', backgroundColor: 'rgba(160,160,160,0.25)',
        pointRadius: 0, tension: 0, order: 0,
      });
    }
    chart.update('none');
  }

  function _showSelStats(lo, hi) {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.(), gpts = opts.getGradPts?.();
    const ad = opts.getActData?.();
    if (!chart || !cpts || hi - lo < 5) { selBox.style.display = 'none'; return; }
    const ca = chart.chartArea;
    // Data access for the shared region-stats computation. Seek indices are chartPts
    // indices; altitude/gradient come from cpts/gpts, per-point metrics from the finer
    // actData (mapped back to a chartPts index by distance so a short spike keeps its spot).
    const _distM = si => U.metric ? cpts[si].x * 1000 : cpts[si].x * 1609.344;
    const _aiRange = (l, h) => [
      Math.max(0, Math.floor(_bsearch(ad.dist_m, _distM(l)))),
      Math.min(ad.dist_m.length - 1, Math.ceil(_bsearch(ad.dist_m, _distM(h)))),
    ];
    const hasT = !!(ad?.time?.length && ad?.dist_m?.length);
    const ctx = {
      altAt:   si => cpts[si].y,
      gradAt:  si => (gpts && si < gpts.length) ? gpts[si].y : null,
      distMAt: _distM,
      metricSamples: (field, l, h) => {
        if (!ad?.[field]?.length || !ad?.dist_m?.length) return null;
        const [ai0, ai1] = _aiRange(l, h);
        const cxArr = cpts.map(p => p.x);
        return {
          values: ad[field].slice(ai0, ai1 + 1),
          toSeekIdx: li => { const ai = ai0 + li; return Math.max(0, Math.min(cpts.length - 1,
            Math.round(_bsearch(cxArr, U.metric ? ad.dist_m[ai] / 1000 : ad.dist_m[ai] / 1609.344)))); },
        };
      },
      rangeHeader: (l, h) => {
        if (hasT) { const [ai0, ai1] = _aiRange(l, h);
          return { startSec: ad.time[ai0], durSec: ad.time[ai1] - ad.time[ai0], distM: _distM(h) - _distM(l) }; }
        return { distM: _distM(h) - _distM(l) };
      },
    };
    const { rows, headerHtml: hdr } = computeRegionStats(ctx, lo, hi);
    const xLo = chart.scales.x.getPixelForValue(cpts[lo].x);
    const xHi = chart.scales.x.getPixelForValue(cpts[hi].x);
    elevSelRender(selBox, xLo, xHi, ca, hdr, rows,
      document.getElementById('stage-elev-hud-btn'), strip,
      (ci, b, e) => {
        // Move the strip puck + map dot to the clicked point (cpts index ci), then freeze
        // them there so the next mousemove doesn't yank the puck back to the cursor.
        let puckClientX = null;
        if (chart?.scales?.x && cpts[ci]) {
          puckClientX = strip.getBoundingClientRect().left + chart.scales.x.getPixelForValue(cpts[ci].x);
          _trackPosOnly(puckClientX);
        }
        if (e && puckClientX != null) puckStickArm(puckClientX, e.clientX);
        // Hide hover HUD so the stats pane isn't visually replaced
        hudPanel.style.display = 'none';
        const xh = document.getElementById('stage-elev-xhair');
        if (xh) xh.style.display = 'none';
        // Update max@: time display
        if (hasT) {
          const ad2 = opts.getActData?.();
          if (ad2?.dist_m?.length && ad2?.time?.length) {
            const distM2 = U.metric ? cpts[ci].x * 1000 : cpts[ci].x * 1609.344;
            const afi2 = _bsearch(ad2.dist_m, distM2);
            const t2 = _lerp(ad2.time, afi2) || 0;
            const maxAtEl = b.querySelector('.es-maxat');
            const maxAtVal = b.querySelector('.es-maxat-val');
            if (maxAtVal) maxAtVal.textContent = elevFmtElapsed(t2);
            if (maxAtEl) maxAtEl.style.display = '';
          }
        }
      });
  }

  function _applySel(lo, hi) {
    selLo = lo; selHi = hi;
    _applyDataset(lo, hi);
    opts.onSelection?.(lo, hi);
    _showSelStats(lo, hi);
  }
  function _clearSel() {
    selLo = -1; selHi = -1;
    const chart = opts.getChart?.();
    if (chart) { chart.data.datasets = chart.data.datasets.filter(d => !d._stageSel); chart.update('none'); }
    selBox.style.display = 'none';
    dragRect.style.display = 'none';
    opts.onClear?.();
  }

  // ── window-level API ──────────────────────────────────────────────────────
  window._stageElevHudToggle = function() {
    hudOn = !hudOn;
    const btn = document.getElementById('stage-elev-hud-btn');
    if (btn) btn.classList.toggle('active', hudOn);
    if (!hudOn) {
      _hideHud();
    } else {
      const chart = opts.getChart?.();
      if (chart?.chartArea) {
        const midPx = (chart.chartArea.left + chart.chartArea.right) / 2;
        _showHud(strip.getBoundingClientRect().left + midPx);
      }
    }
    _clearSel();
  };
  window._stageElevGetSel    = () => (selLo >= 0 && selHi > selLo) ? { lo: selLo, hi: selHi } : null;
  window._stageElevClearSel  = _clearSel;
  window._stageElevReapplySel = () => { if (selLo >= 0 && selHi > selLo) _applyDataset(selLo, selHi); };

  // ── wheel zoom ───────────────────────────────────────────────────────────
  function _zoomApply() {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.();
    if (!chart || !cpts?.length) return;
    const total = cpts[cpts.length - 1].x;
    _zoom.level = Math.max(1, Math.min(_zoom.maxLevel, _zoom.level));
    if (_zoom.level <= 1) {
      chart.options.scales.x.min = 0;
      chart.options.scales.x.max = total;
    } else {
      const winSize = total / _zoom.level;
      const maxOff  = total - winSize;
      const start   = Math.max(0, Math.min(_zoom.offset * maxOff, maxOff));
      chart.options.scales.x.min = start;
      chart.options.scales.x.max = start + winSize;
    }
    chart.update('none');
  }

  function _zoomAtPixel(px, factor) {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.();
    if (!chart || !cpts?.length) return;
    const total    = cpts[cpts.length - 1].x;
    const ca       = chart.chartArea;
    if (!ca) return;
    const curX     = chart.scales.x.getValueForPixel(Math.max(ca.left, Math.min(ca.right, px)));
    const curXFrac = curX / total;
    _zoom.level    = Math.max(1, Math.min(_zoom.maxLevel, _zoom.level * factor));
    if (_zoom.level <= 1) {
      _zoom.offset = 0;
    } else {
      const winFrac = 1 / _zoom.level;
      const maxOff  = 1 - winFrac;
      _zoom.offset  = maxOff > 0
        ? Math.max(0, Math.min(maxOff, curXFrac - winFrac / 2)) / maxOff
        : 0;
    }
    _zoomApply();
  }

  function _onWheel(e) {
    if (!opts.getChart?.()) return;
    e.preventDefault();
    const rect   = strip.getBoundingClientRect();
    const px     = e.clientX - rect.left;
    const factor = e.deltaY < 0 ? 1.4 : 1 / 1.4;
    _zoomAtPixel(px, factor);
  }

  // ── dot tracking + event wiring ───────────────────────────────────────────
  function _trackPosOnly(clientX) {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.();
    if (!chart || !cpts?.length) return;
    const ca = chart.chartArea, rect = strip.getBoundingClientRect();
    const px = clientX - rect.left;
    if (!ca || px < ca.left || px > ca.right) { chartDot.style.display = 'none'; return; }

    const fi   = _clientXToFi(clientX);
    const altD = _lerp(cpts.map(p => p.y), fi);
    const ys   = chart.scales.y;
    if (altD != null && ys) {
      const dotY = ys.getPixelForValue(altD);
      chartDot.style.display = 'block';
      chartDot.style.left    = px + 'px';
      chartDot.style.top     = dotY + 'px';
    }

    const rpts = opts.getRawPts?.(), theMap = opts.getMap?.();
    if (rpts?.length && theMap) {
      const ri = Math.max(0, Math.min(rpts.length - 1, Math.round(fi)));
      const rp = rpts[ri];
      if (rp) {
        const ll  = [rp[0], rp[1]];
        const dh  = `<div style="width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 35%,#ffe066,#f59e0b 55%,#b45309);border:1.5px solid #fff;box-shadow:0 0 3px rgba(0,0,0,.5)"></div>`;
        let dot = opts.getMapDot?.();
        if (!dot) {
          dot = L.marker(ll, { icon: L.divIcon({ html: dh, className: '', iconSize: [10,10], iconAnchor: [5,5] }),
            zIndexOffset: 1000, interactive: false }).addTo(theMap);
          opts.setMapDot?.(dot);
        } else { dot.setLatLng(ll); }
        MapUtils.keepPointVisible(theMap, ll);
      }
    }
  }

  // Is a tap (clientX) within the current selection's pixel range on the strip?
  function _tapInSelection(clientX) {
    const chart = opts.getChart?.(), cpts = opts.getChartPts?.();
    if (!chart || !cpts || selLo < 0 || selHi <= selLo) return false;
    const rect = strip.getBoundingClientRect();
    const px = clientX - rect.left;
    const xLo = chart.scales.x.getPixelForValue(cpts[selLo].x);
    const xHi = chart.scales.x.getPixelForValue(cpts[selHi].x);
    return px >= Math.min(xLo, xHi) - 6 && px <= Math.max(xLo, xHi) + 6;
  }

  // Stable per-strip API the once-registered document handler reads at event time,
  // so it always sees the CURRENT stage's selection/functions (not a stale closure).
  strip._tourSelApi = {
    getSel:    () => ({ lo: selLo, hi: selHi }),
    selBox:    selBox,
    tapInSel:  clientX => _tapInSelection(clientX),
    showStats: () => _showSelStats(selLo, selHi),
  };

  // Single document-level tap handler owns modal show/hide (touch). One decision per
  // tap, modal state read once — no oscillation. A drag (>10px) is left to runDrag,
  // which starts a fresh selection. onTapNoDrag is a no-op so a tap never clears.
  if (!strip._tourModalTap) {
    strip._tourModalTap = true;
    let _tsx = 0, _tsy = 0, _tmoved = false;
    document.addEventListener('touchstart', e => { const t = e.touches[0]; if (t) { _tsx = t.clientX; _tsy = t.clientY; _tmoved = false; } }, { passive: true });
    document.addEventListener('touchmove',  e => { const t = e.touches[0]; if (t && (Math.abs(t.clientX - _tsx) > 10 || Math.abs(t.clientY - _tsy) > 10)) _tmoved = true; }, { passive: true });
    document.addEventListener('touchend',   e => {
      if (_tmoved) return;                                 // a drag/scroll, not a tap
      const api = strip._tourSelApi; if (!api) return;
      const { lo, hi } = api.getSel(); if (lo < 0 || hi <= lo) return;   // no selection
      const t = e.changedTouches[0]; if (!t) return;
      const target = document.elementFromPoint(t.clientX, t.clientY);
      if (api.selBox.contains(target)) return;             // tap on the modal → let it handle itself
      if (api.selBox.style.display !== 'none') {
        api.selBox.style.display = 'none';                 // modal open → dismiss, keep selection
      } else if (strip.contains(target) && api.tapInSel(t.clientX)) {
        api.showStats();                                   // modal hidden + tap on selection → re-open
      }
    }, { passive: true });
  }

  elevRegionInteraction({
    container:   strip,
    chartArea:   () => opts.getChart?.()?.chartArea || null,
    clientXToIndex: clientX => Math.round(_clientXToFi(clientX)),
    hudActive:   () => hudOn,
    hover:       clientX => _trackPosOnly(clientX),
    showHud:     clientX => _showHud(clientX),
    hideHover:   () => { _hideHud(); },   // leave the puck (chart + map dot) at its last location
    onDragStart: () => _hideHud(),
    applySelection: (lo, hi) => _applySel(lo, hi),
    clearSelection: () => _clearSel(),
    onTapNoDrag: () => {},                        // taps are owned by the document handler above
    tapOnModal:  target => selBox.contains(target),
    dragRectStart:  px => { dragRect.style.cssText = `display:block;position:absolute;top:0;bottom:0;background:rgba(160,160,160,.18);border-left:1px solid rgba(130,130,130,.5);border-right:1px solid rgba(130,130,130,.5);pointer-events:none;z-index:4;left:${px}px;width:0`; },
    dragRectUpdate: (lo, hi) => { dragRect.style.left = lo+'px'; dragRect.style.width = (hi-lo)+'px'; },
    dragRectHide:   () => { dragRect.style.display = 'none'; },
    suppressHoverWhileDragging: true,
    onWheel: _onWheel,
  });
}
