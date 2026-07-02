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
  if (!chartDot.style.display) chartDot.style.display = 'none';
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
  function _fmtElap(s) {
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    return h ? `${h}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`
             : `${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
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
      }
    }

    const rows = [
      tSec != null ? { label:'Time',       val:_fmtElap(tSec),                                                              color:_HC.distance } : null,
      { label:'Distance',  val: U.metric ? (distM/1000).toFixed(2)+' km' : (distM/1609.344).toFixed(2)+' mi',               color:_HC.distance },
      altD != null ? { label:'Altitude',   val: Math.round(altD)+' '+(U.metric?'m':'ft'),                                   color:_HC.altitude } : null,
      grad != null ? { label:'Gradient',   val: grad.toFixed(1)+'%',                                                        color:_HC.gradient } : null,
      { label:'Heart Rate', val:(hr&&hr>0)  ? Math.round(hr)+' bpm'  : '—',                                                 color:_HC.hr },
      { label:'Power',      val:(pwr&&pwr>0)? Math.round(pwr)+' W'   : '—',                                                 color:_HC.power },
      { label:'Cadence',    val:(cad&&cad>0)? Math.round(cad)+' rpm' : '—',                                                 color:_HC.cadence },
      { label:'Speed',      val:(spd&&spd>0)? (U.metric?(spd*1.60934).toFixed(1)+' km/h':spd.toFixed(1)+' mph'):'—',        color:_HC.speed },
    ].filter(Boolean);

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
    const distM0 = U.metric ? cpts[lo].x*1000 : cpts[lo].x*1609.344;
    const distM1 = U.metric ? cpts[hi].x*1000 : cpts[hi].x*1609.344;

    // climb from chartPts (always available)
    let totalClimbFt = 0;
    for (let i = lo + 1; i <= hi; i++) {
      const dAlt = cpts[i].y - cpts[i-1].y;
      if (dAlt > 0) totalClimbFt += U.metric ? dAlt / 0.3048 : dAlt;
    }

    // steepest gradient
    let maxGradVal = null, maxGradCptsIdx = null;
    if (gpts?.length) {
      const hiClamped = Math.min(hi, gpts.length - 1);
      const hasPos = Array.from({length: hiClamped - lo + 1}, (_, k) => gpts[lo + k].y).some(g => g > 0);
      let best = -Infinity;
      for (let i = lo; i <= hiClamped; i++) {
        const g = gpts[i].y;
        if (hasPos && g <= 0) continue;
        if (Math.abs(g) > best) { best = Math.abs(g); maxGradVal = g; maxGradCptsIdx = i; }
      }
    }

    // per-point actData fields
    let maxSpd=null, avgSpd=null, movSpd=null, maxHR=null, avgHR=null, maxPwr=null, avgPwr=null;
    let maxCad=null, avgCad=null;
    let startSec=null, durSec=null;
    let maxHRCptsIdx=null, maxPwrCptsIdx=null, maxSpdCptsIdx=null, maxCadCptsIdx=null;
    if (ad?.dist_m?.length) {
      const ai0 = Math.max(0, Math.floor(_bsearch(ad.dist_m, distM0)));
      const ai1 = Math.min(ad.dist_m.length - 1, Math.ceil(_bsearch(ad.dist_m, distM1)));
      const cxArr = cpts.map(p => p.x);
      const _aiToCi = ai => Math.max(0, Math.min(cpts.length-1,
        Math.round(_bsearch(cxArr, U.metric ? ad.dist_m[ai]/1000 : ad.dist_m[ai]/1609.344))));
      const _argmaxAi = (arr, from, to, pred=()=>true) => {
        let bi=-1, bv=-Infinity;
        for (let k=from; k<=to; k++) if (arr[k]!=null && pred(arr[k]) && arr[k]>bv) { bv=arr[k]; bi=k; }
        return bi;
      };
      if (ad.speed?.length) {
        const sl = ad.speed.slice(ai0, ai1+1);
        const v = sl.filter(x=>x>0), mv = sl.filter(x=>x>=0.5);
        maxSpd = v.length  ? Math.max(...v)                    : null;
        avgSpd = v.length  ? v.reduce((a,b)=>a+b,0)/v.length  : null;
        movSpd = mv.length ? mv.reduce((a,b)=>a+b,0)/mv.length : null;
        const bi = _argmaxAi(ad.speed, ai0, ai1, v=>v>0);
        if (bi >= 0) maxSpdCptsIdx = _aiToCi(bi);
      }
      if (ad.hr?.length) {
        const hl = ad.hr.slice(ai0, ai1+1).filter(x=>x>0);
        maxHR = hl.length ? Math.max(...hl)                    : null;
        avgHR = hl.length ? hl.reduce((a,b)=>a+b,0)/hl.length : null;
        const bi = _argmaxAi(ad.hr, ai0, ai1, v=>v>0);
        if (bi >= 0) maxHRCptsIdx = _aiToCi(bi);
      }
      if (ad.power?.length) {
        const pl = ad.power.slice(ai0, ai1+1).filter(x=>x>0);
        maxPwr = pl.length ? Math.max(...pl)                    : null;
        avgPwr = pl.length ? pl.reduce((a,b)=>a+b,0)/pl.length : null;
        const bi = _argmaxAi(ad.power, ai0, ai1, v=>v>0);
        if (bi >= 0) maxPwrCptsIdx = _aiToCi(bi);
      }
      if (ad.cadence?.length) {
        const cl = ad.cadence.slice(ai0, ai1+1).filter(x=>x>0);
        maxCad = cl.length ? Math.max(...cl)                    : null;
        avgCad = cl.length ? cl.reduce((a,b)=>a+b,0)/cl.length : null;
        const bi = _argmaxAi(ad.cadence, ai0, ai1, v=>v>0);
        if (bi >= 0) maxCadCptsIdx = _aiToCi(bi);
      }
      if (ad.time?.length) { startSec = ad.time[ai0]; durSec = ad.time[ai1] - startSec; }
    }

    // min/max altitude from chartPts (always available)
    let minAltDisplay = null, maxAltDisplay = null;
    let maxAltCptsIdx = null;
    if (hi > lo) {
      minAltDisplay = cpts[lo].y;
      maxAltDisplay = cpts[lo].y;
      maxAltCptsIdx = lo;
      for (let i = lo + 1; i <= hi; i++) {
        if (cpts[i].y < minAltDisplay) minAltDisplay = cpts[i].y;
        if (cpts[i].y > maxAltDisplay) { maxAltDisplay = cpts[i].y; maxAltCptsIdx = i; }
      }
    }

    // total descent from chartPts
    let totalDescentFt = 0;
    for (let i = lo + 1; i <= hi; i++) {
      const dAlt = cpts[i].y - cpts[i-1].y;
      if (dAlt < 0) totalDescentFt += U.metric ? Math.abs(dAlt) / 0.3048 : Math.abs(dAlt);
    }

    // average gradient: net elevation / total distance
    let avgGrad = null;
    if (hi > lo) {
      const dxDisplay = cpts[hi].x - cpts[lo].x;
      const dyDisplay = cpts[hi].y - cpts[lo].y;
      const factor = U.metric ? 1000 : 5280;
      if (dxDisplay > 0) avgGrad = dyDisplay / (dxDisplay * factor) * 100;
    }

    const fS  = v => v!=null ? (U.metric?(v*1.60934).toFixed(1)+' km/h':v.toFixed(1)+' mph') : '—';
    const fH  = v => v!=null ? Math.round(v)+' bpm' : '—';
    const fP  = v => v!=null ? Math.round(v)+' W'   : '—';
    const fG  = v => v!=null ? v.toFixed(1)+'%'     : '—';
    const fC  = ft => ft>0 ? Math.round(U.metric?ft*0.3048:ft)+(U.metric?' m':' ft') : '—';
    const fA  = v => v!=null ? Math.round(v)+(U.metric?' m':' ft') : '—';
    const fCd = v => v!=null ? Math.round(v)+' rpm' : '—';

    const hasT = startSec != null && durSec != null;
    const selDistM = distM1 - distM0;
    const selDistStr = U.metric ? (selDistM/1000).toFixed(2)+' km' : (selDistM/1609.344).toFixed(2)+' mi';
    let hdr = '';
    if (hasT) {
      hdr = `<div class="es-header"><span><span class="es-lbl">Start </span><span class="es-val">${_fmtElap(Math.round(startSec))}</span></span>` +
            `<span class="es-hdr-dur"><span class="es-lbl">Duration </span><span class="es-val">${_fmtElap(Math.round(durSec))}</span></span>` +
            `<span class="es-maxat" style="display:none"><span class="es-lbl">max@ </span><span class="es-val es-maxat-val"></span></span>` +
            `<span class="es-hdr-dist"><span class="es-val">${selDistStr}</span></span></div>`;
    } else {
      hdr = `<div class="es-header"><span><span class="es-val">${selDistStr}</span></span></div>`;
    }
    const rows = [
      [{ label:'Min Altitude',  val:fA(minAltDisplay) },
       { label:'Max Altitude',  val:fA(maxAltDisplay), clickIdx: maxAltCptsIdx }],
      [{ label:'Total Climb',   val:fC(totalClimbFt) },
       { label:'Total Descent', val:fC(totalDescentFt) }],
      [{ label:'Max Gradient',  val:fG(maxGradVal), clickIdx: maxGradCptsIdx },
       { label:'Avg Gradient',  val:fG(avgGrad) }],
      ...(maxHR!=null  ? [[{ label:'Max HR',       val:fH(maxHR),  clickIdx: maxHRCptsIdx  },
                            { label:'Avg HR',       val:fH(avgHR)  }]] : []),
      ...(maxPwr!=null ? [[{ label:'Max 3s Power',  val:fP(maxPwr), clickIdx: maxPwrCptsIdx },
                            { label:'Avg 3s Power', val:fP(avgPwr) }]] : []),
      ...(maxSpd!=null ? [[{ label:'Max Speed',     val:fS(maxSpd), clickIdx: maxSpdCptsIdx },
                            { label:'Mov Speed',    val:fS(movSpd) }]] : []),
      ...(maxCad!=null ? [[{ label:'Max Cadence',   val:fCd(maxCad), clickIdx: maxCadCptsIdx },
                            { label:'Avg Cadence',  val:fCd(avgCad) }]] : []),
    ];
    const xLo = chart.scales.x.getPixelForValue(cpts[lo].x);
    const xHi = chart.scales.x.getPixelForValue(cpts[hi].x);
    elevSelRender(selBox, xLo, xHi, ca, hdr, rows,
      document.getElementById('stage-elev-hud-btn'), strip,
      (ci, b) => {
        // Move the map dot to the position at cpts index ci
        const rpts = opts.getRawPts?.(), theMap = opts.getMap?.();
        if (rpts?.length && theMap && ci >= 0 && ci < rpts.length) {
          const rp = rpts[ci];
          if (rp) {
            const ll = [rp[0], rp[1]];
            const dh = `<div style="width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 35%,#ffe066,#f59e0b 55%,#b45309);border:1.5px solid #fff;box-shadow:0 0 3px rgba(0,0,0,.5)"></div>`;
            let dot = opts.getMapDot?.();
            if (!dot) {
              dot = L.marker(ll, { icon: L.divIcon({ html: dh, className: '', iconSize: [10,10], iconAnchor: [5,5] }),
                zIndexOffset: 1000, interactive: false }).addTo(theMap);
              opts.setMapDot?.(dot);
            } else { dot.setLatLng(ll); }
          }
        }
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
            if (maxAtVal) maxAtVal.textContent = _fmtElap(t2);
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

  // ── event wiring (replaces old listeners) ─────────────────────────────────
  const prev = strip._seiH;
  if (prev) {
    strip.removeEventListener('mousemove',  prev.mm);
    strip.removeEventListener('mouseleave', prev.ml);
    strip.removeEventListener('mousedown',  prev.md);
    strip.removeEventListener('touchstart', prev.ts);
    if (prev.wh) strip.removeEventListener('wheel', prev.wh);
  }

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
      }
    }
  }

  function _onMM(e) {
    if (strip._seiDrag) return;
    if (hudOn) _showHud(e.clientX);
    else _trackPosOnly(e.clientX);
  }
  function _onML() {
    if (!strip._seiDrag) {
      _hideHud();
      chartDot.style.display = 'none';
    }
  }

  function _onMD(e) {
    const chart = opts.getChart?.(); if (!chart) return;
    const ca = chart.chartArea, rect = strip.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (!ca || px < ca.left || px > ca.right) return;
    e.preventDefault(); _hideHud();
    strip._seiDrag = true;
    const startPx = px, startIdx = Math.round(_clientXToFi(e.clientX));
    dragRect.style.cssText = `display:block;position:absolute;top:0;bottom:0;background:rgba(160,160,160,.18);border-left:1px solid rgba(130,130,130,.5);border-right:1px solid rgba(130,130,130,.5);pointer-events:none;z-index:4;left:${px}px;width:0`;

    function _docMM(ev) {
      const r2 = strip.getBoundingClientRect(), cur = ev.clientX - r2.left;
      const lo2 = Math.max(ca.left, Math.min(startPx, cur)), hi2 = Math.min(ca.right, Math.max(startPx, cur));
      dragRect.style.left = lo2+'px'; dragRect.style.width = (hi2-lo2)+'px';
      const curIdx = Math.round(_clientXToFi(ev.clientX));
      _applySel(Math.min(startIdx, curIdx), Math.max(startIdx, curIdx));
    }
    function _docMU(ev) {
      document.removeEventListener('mousemove', _docMM);
      document.removeEventListener('mouseup',   _docMU);
      strip._seiDrag = false;
      const curPx = ev.clientX - strip.getBoundingClientRect().left;
      if (Math.abs(curPx - startPx) > 4) {
        const curIdx = Math.round(_clientXToFi(ev.clientX));
        _applySel(Math.min(startIdx, curIdx), Math.max(startIdx, curIdx));
      } else { dragRect.style.display = 'none'; _clearSel(); }
    }
    document.addEventListener('mousemove', _docMM);
    document.addEventListener('mouseup',   _docMU);
  }

  function _onTS(e) {
    const t = e.touches[0]; if (!t) return;
    const chart = opts.getChart?.(); if (!chart) return;
    if (hudOn) {
      _showHud(t.clientX);
      function _tM(ev) { const t2=ev.touches[0]; if (t2) _showHud(t2.clientX); }
      function _tE()   { document.removeEventListener('touchmove',_tM); document.removeEventListener('touchend',_tE); }
      document.addEventListener('touchmove', _tM, { passive:true });
      document.addEventListener('touchend',  _tE, { passive:true });
      return;
    }
    const ca = chart.chartArea, rect = strip.getBoundingClientRect();
    const px = t.clientX - rect.left;
    if (!ca || px < ca.left || px > ca.right) return;
    const startPx = px, startIdx = Math.round(_clientXToFi(t.clientX));
    dragRect.style.cssText = `display:block;position:absolute;top:0;bottom:0;background:rgba(160,160,160,.18);border-left:1px solid rgba(130,130,130,.5);border-right:1px solid rgba(130,130,130,.5);pointer-events:none;z-index:4;left:${px}px;width:0`;

    function _tM(ev) {
      const t2=ev.touches[0]; if (!t2) return;
      const r2=strip.getBoundingClientRect(), cur=t2.clientX-r2.left;
      const lo2=Math.max(ca.left,Math.min(startPx,cur)), hi2=Math.min(ca.right,Math.max(startPx,cur));
      dragRect.style.left=lo2+'px'; dragRect.style.width=(hi2-lo2)+'px';
      const curIdx=Math.round(_clientXToFi(t2.clientX));
      _applySel(Math.min(startIdx,curIdx), Math.max(startIdx,curIdx));
    }
    function _tE(ev) {
      document.removeEventListener('touchmove', _tM);
      document.removeEventListener('touchend',  _tE);
      const t2 = ev.changedTouches[0];
      if (t2) {
        const curPx = t2.clientX - strip.getBoundingClientRect().left;
        if (Math.abs(curPx - startPx) > 4) {
          const curIdx = Math.round(_clientXToFi(t2.clientX));
          _applySel(Math.min(startIdx,curIdx), Math.max(startIdx,curIdx));
        } else { dragRect.style.display = 'none'; _clearSel(); }
      }
    }
    document.addEventListener('touchmove', _tM, { passive:true });
    document.addEventListener('touchend',  _tE, { passive:true });
  }

  strip.addEventListener('mousemove',  _onMM);
  strip.addEventListener('mouseleave', _onML);
  strip.addEventListener('mousedown',  _onMD);
  strip.addEventListener('touchstart', _onTS, { passive:true });
  strip.addEventListener('wheel',      _onWheel, { passive: false });
  strip._seiH = { mm:_onMM, ml:_onML, md:_onMD, ts:_onTS, wh:_onWheel };
}
