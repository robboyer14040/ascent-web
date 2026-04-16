// ── ELEVATION CHART ───────────────────────────────────────────────────────────
// Draw a simple estimated profile from summary stats (min/max elevation + climb)
async function drawElevationFromSummary(act, version) {
  const ctx = document.getElementById('elevChart');
  if (version !== undefined && version !== elevRenderVersion) return; // stale

  const maxAlt = act.max_altitude_ft || act.src_max_elevation;
  const minAlt = act.min_altitude_ft || act.src_min_elevation;
  const climb  = act.total_climb_ft  || 0;
  const dist   = act.distance_mi     || 1;

  if (!maxAlt && !minAlt && !climb) return; // nothing to show

  // Synthesize a rough 20-point profile: up then down
  const pts = 20;
  const labels = [], vals = [];
  const base = minAlt || Math.max(0, (maxAlt||0) - climb);
  const peak = maxAlt || (base + climb);
  for (let i = 0; i < pts; i++) {
    const t = i / (pts - 1);
    // bell-ish curve peaking around 40% of distance
    const shape = Math.sin(t * Math.PI * 0.9);
    labels.push(U.dist(t * dist).toFixed(1));
    vals.push(U.alt(Math.round(base + (peak - base) * shape)));
  }

  // Custom peak marker plugin
  const peakPlugin = {
    id: 'peakMarkers',
    afterDatasetsDraw(chart) {
      if (!peakList.length) return;
      const {ctx: cx, scales} = chart;
      const xSc = scales.x;
      const ySc = scales.yElev || scales.y;
      if (!xSc || !ySc) return;
      cx.save();
      peakList.forEach(pk => {
        const px = xSc.getPixelForValue(pk.x);
        const py = ySc.getPixelForValue(pk.y);
        const label = U.alt(pk.y) + ' ' + U.altUnit();
        const _lm = _chartIsLight();
        // Stem line
        cx.beginPath();
        cx.moveTo(px, py);
        cx.lineTo(px, py - 20);
        cx.strokeStyle = _lm ? 'rgba(0,0,0,.35)' : 'rgba(255,255,255,.5)';
        cx.lineWidth = 1;
        cx.stroke();
        // Dot at peak
        cx.beginPath();
        cx.arc(px, py, 3, 0, Math.PI*2);
        cx.fillStyle = _lm ? '#555' : '#fff';
        cx.fill();
        // Label bubble
        cx.font = 'bold 9px -apple-system,sans-serif';
        const tw = cx.measureText(label).width;
        const bx = px - tw/2 - 5, by = py - 38, bw = tw + 10, bh = 16;
        cx.fillStyle = 'rgba(0,0,0,.75)';
        cx.beginPath();
        cx.roundRect(bx, by, bw, bh, 4);
        cx.fill();
        cx.fillStyle = '#f2f2f7';
        cx.textAlign = 'center';
        cx.textBaseline = 'middle';
        cx.fillText(label, px, by + bh/2);
      });
      cx.restore();
    }
  };

  const light = _chartIsLight();
  if (elevChart){elevChart.destroy();elevChart=null;}
  elevChart = new Chart(ctx, {
    type:'line',
    data:{labels,datasets:[{data:vals,fill:true,borderColor:'#64748b',backgroundColor:'rgba(100,116,139,.3)',borderWidth:1.5,pointRadius:0,tension:.4,borderDash:[4,3]}]},
    options:{
      responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{
        legend:{display:false},
        tooltip:{callbacks:{label:c=>`~${U.alt(c.raw)} ${U.altUnit()} (estimated)`},titleFont:{size:11},bodyFont:{size:11}},
      },
      scales:{
        x:{
          type:'linear', display:true,
          ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8,callback:v=>`${v.toFixed(1)}${U.distUnit()}`},
          grid:{color:light?'rgba(0,0,0,.08)':'rgba(255,255,255,.04)'},
        },
        y:{ticks:{color:'#64748b',font:{size:10},maxTicksLimit:4,callback:v=>`${U.alt(v)}${U.altUnit()}`},grid:{color:light?'rgba(0,0,0,.10)':'rgba(255,255,255,.06)'}},
      }
    }
  });

  // Show "estimated" label
  const label = document.querySelector('.chart-label');
  if (label) label.textContent = 'Elevation (estimated)';
}

// ── ELEVATION CHART ───────────────────────────────────────────────────────────
// Chart overlay state
let elevChartData = null;  // store last chart data for re-render

let _chartSettingsJustOpened = false;
function toggleChartSettings(e) {
  e.stopPropagation();
  const s = document.getElementById('chart-settings');
  const opening = s.style.display === 'none';
  s.style.display = opening ? 'block' : 'none';
  if (opening) {
    _chartSettingsJustOpened = true;
    setTimeout(() => { _chartSettingsJustOpened = false; }, 300);
  }
}
(function() {
  function chartSettingsDismiss(e) {
    if (_chartSettingsJustOpened) return;
    if (!e.target.closest('#chart-wrap')) {
      const s = document.getElementById('chart-settings');
      if (s) s.style.display = 'none';
    }
  }
  document.addEventListener('click',    chartSettingsDismiss);
  document.addEventListener('touchend', chartSettingsDismiss, {passive: true});
})();

function loadChartPrefs() {
  try {
    const p = JSON.parse(_uiPrefsGet('ascent-chart-overlays') || 'null');
    if (p) {
      const o1 = document.getElementById('overlay1-sel');
      const o1z = document.getElementById('overlay1-zones');
      const pk = document.getElementById('show-peaks');
      const sm = document.getElementById('split-metric-sel');
      const sl = document.getElementById('split-len-sel');
      const sa = document.getElementById('split-agg-sel');
      const sz = document.getElementById('split-zones');
      if (o1 && p.o1 !== undefined) o1.value = p.o1;
      if (o1z && p.o1Zones !== undefined) o1z.checked = p.o1Zones;
      if (pk && p.peaks !== undefined) pk.checked = p.peaks;
      if (sm && p.splitMetric !== undefined) sm.value = p.splitMetric;
      if (sl && p.splitLen !== undefined) sl.value = p.splitLen;
      if (sa && p.splitAgg !== undefined) sa.value = p.splitAgg;
      if (sz && p.splitZones !== undefined) sz.checked = p.splitZones;
      const xt = document.getElementById('xaxis-time');
      if (xt && p.xAxisTime !== undefined) xt.checked = p.xAxisTime;
      if (sm && sm.value) {
        const dc = document.getElementById('split-detail-controls');
        if (dc) dc.style.display = 'block';
      }
    }
    // Migrate from old ascent-splits key if no chart prefs saved yet
    if (!p || p.splitMetric === undefined) {
      try {
        const old = JSON.parse(_uiPrefsGet('ascent-splits') || 'null');
        if (old) {
          const sm = document.getElementById('split-metric-sel');
          const sl = document.getElementById('split-len-sel');
          const sa = document.getElementById('split-agg-sel');
          if (sm && old.metric) { sm.value = old.metric; }
          if (sl && old.len)    { sl.value = old.len; }
          if (sa && old.agg)    { sa.value = old.agg; }
          if (sm && sm.value) {
            const dc = document.getElementById('split-detail-controls');
            if (dc) dc.style.display = 'block';
          }
        }
      } catch(e2) {}
    }
  } catch(e) {}
}
function saveChartPrefs() {
  _uiPrefsSet('ascent-chart-overlays', JSON.stringify({
    o1:          document.getElementById('overlay1-sel')?.value || '',
    o1Zones:     document.getElementById('overlay1-zones')?.checked ?? false,
    peaks:       document.getElementById('show-peaks')?.checked ?? true,
    splitMetric: document.getElementById('split-metric-sel')?.value || '',
    splitLen:    document.getElementById('split-len-sel')?.value || '1',
    splitAgg:    document.getElementById('split-agg-sel')?.value || 'avg',
    splitZones:  document.getElementById('split-zones')?.checked ?? false,
    xAxisTime:   document.getElementById('xaxis-time')?.checked ?? false,
  }));
}

function onSplitMetricChange() {
  const metric = document.getElementById('split-metric-sel')?.value || '';
  const dc = document.getElementById('split-detail-controls');
  if (dc) dc.style.display = metric ? 'block' : 'none';
  redrawElevWithOverlays();
}

function redrawElevWithOverlays() {
  saveChartPrefs();
  if (elevChartData) drawElevation(elevChartData);
}

function findPeaks(points, n=3) {
  if (points.length < 10) return [];
  // Minimum separation: 0.25 miles between peaks
  const minSepX = 0.25;

  // Find all local maxima (smoother window = 2% of points each side, clamped at edges)
  const w = Math.max(5, Math.floor(points.length * 0.02));
  const candidates = [];
  for (let i = 1; i < points.length - 1; i++) {
    const y = points[i].y;
    let isPeak = true;
    const lo = Math.max(0, i - w), hi = Math.min(points.length - 1, i + w);
    for (let j = lo; j <= hi; j++) {
      if (j !== i && points[j].y > y) { isPeak = false; break; }
    }
    if (isPeak) candidates.push({idx: i, x: points[i].x, y});
  }

  // Sort by height descending, greedily pick top N with min separation
  candidates.sort((a, b) => b.y - a.y);
  const peaks = [];
  for (const cand of candidates) {
    if (peaks.every(p => Math.abs(p.x - cand.x) >= minSepX)) {
      peaks.push(cand);
      if (peaks.length >= n) break;
    }
  }
  return peaks;
}

let elevChart=null;

// Register annotation plugin if available
if (typeof window !== 'undefined' && window.Chart && window.ChartAnnotation) {
  Chart.register(window.ChartAnnotation);
}

function _heatColor(frac, light=false) {
  // Blue→green→yellow→red gradient; darker stops for light theme
  const stops = light
    ? [[0,30,58,138],[0.33,20,83,45],[0.67,120,53,15],[1,127,29,29]]   // -900 shades
    : [[0,59,130,246],[0.33,34,197,94],[0.67,234,179,8],[1,239,68,68]]; // -500 shades
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0,r0,g0,b0] = stops[i], [t1,r1,g1,b1] = stops[i+1];
    if (frac <= t1) {
      const t = (frac - t0) / (t1 - t0);
      return `rgb(${Math.round(r0+(r1-r0)*t)},${Math.round(g0+(g1-g0)*t)},${Math.round(b0+(b1-b0)*t)})`;
    }
  }
  return light ? 'rgb(127,29,29)' : 'rgb(239,68,68)';
}

function _fmtSplitVal(value, metric) {
  if (metric === 'speed') return (U.metric ? (value * 1.60934).toFixed(1) : value.toFixed(1));
  if (metric === 'climb') return Math.round(U.metric ? value * 0.3048 : value);
  if (metric === 'pace') {
    const secs = value * 60;
    return `${Math.floor(secs / 60)}:${String(Math.round(secs % 60)).padStart(2, '0')}`;
  }
  if (metric === 'temp') return Math.round(U.metric ? (value - 32) * 5 / 9 : value);
  return Math.round(value);
}

function fmtChartTime(sec) {
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(r).padStart(2,'0')}`;
}

function _chartIsLight() {
  const t = document.documentElement.dataset.theme;
  if (t === 'light') return true;
  if (t === 'dark')  return false;
  return window.matchMedia('(prefers-color-scheme: light)').matches;
}

async function drawElevation(data, version) {
  const label = document.querySelector('.chart-label');
  if (label) label.textContent = 'Elevation';
  const ctx = document.getElementById('elevChart');
  if (version !== undefined && version !== elevRenderVersion) return; // stale

  elevChartData = data;
  const { dist_m, alt_ft, hr, speed, power, cadence, temp_f, time: timeArr } = data;
  if (!alt_ft || alt_ft.every(v => v === 0)) return;
  const light = _chartIsLight();

  const xConv = U.metric ? (m => m/1000)        : (m => m/1609.344);
  const yConv = U.metric ? (ft => Math.round(ft*0.3048)) : (ft => Math.round(ft));
  const xUnit = U.metric ? 'km'  : 'mi';

  const xAxisTime = (document.getElementById('xaxis-time')?.checked ?? false)
                    && timeArr && timeArr.length === dist_m.length;
  // ptX(i) returns the x-axis value for data point index i
  const ptX = xAxisTime ? (i => timeArr[i]) : (i => xConv(dist_m[i]));

  // Full-resolution points always use distance (findPeaks needs consistent spacing)
  const fullPoints = [];
  for (let i = 0; i < dist_m.length; i++) {
    fullPoints.push({ x: xConv(dist_m[i]), y: yConv(alt_ft[i]) });
  }

  // Sample to ~600 points
  const total = alt_ft.length;
  const step  = Math.max(1, Math.floor(total / 600));
  const points = [];
  for (let i = 0; i < total; i += step) {
    points.push({ x: ptX(i), y: yConv(alt_ft[i]) });
  }
  if (!points.length) return;
  const totalX = ptX(total - 1);

  // Overlay settings
  const o1key      = document.getElementById('overlay1-sel')?.value || '';
  const o1Zones    = document.getElementById('overlay1-zones')?.checked ?? false;
  const showPeaks  = document.getElementById('show-peaks')?.checked ?? true;
  const splitMetric  = document.getElementById('split-metric-sel')?.value || '';
  const splitLenRaw  = document.getElementById('split-len-sel')?.value || '1';
  const splitIsTime  = splitLenRaw.startsWith('t:');
  const splitLenMi   = splitIsTime ? 1 : (parseFloat(splitLenRaw) || 1);
  const splitDurSec  = splitIsTime ? (parseInt(splitLenRaw.slice(2)) || 10) : 0;
  const splitAgg     = document.getElementById('split-agg-sel')?.value || 'avg';
  const splitZones  = document.getElementById('split-zones')?.checked ?? false;

  // Single (non-zone) colors for each metric — darker shades in light theme for visibility
  const METRIC_COLORS = light ? {
    hr: '#7f1d1d', power: '#14532d', speed: '#7c2d12',
    cadence: '#4c1d95', climb: '#451a03', pace: '#164e63', temp: '#0c4a6e',
  } : {
    hr: '#ef4444', power: '#22c55e', speed: '#f97316',
    cadence: '#a855f7', climb: '#f59e0b', pace: '#06b6d4', temp: '#38bdf8',
  };

  const temp_disp = temp_f ? temp_f.map(v => v ? +(U.metric ? ((v - 32) * 5 / 9).toFixed(1) : v) : 0) : null;
  const overlayArrs   = { hr, speed, power, cadence, temp: temp_disp };
  const overlayUnits  = { hr: 'bpm', speed: U.speedUnit(), power: 'W', cadence: 'rpm', temp: U.tempUnit() };
  const overlayLabels = { hr: 'Heart Rate', speed: 'Speed', power: 'Power', cadence: 'Cadence', temp: 'Temperature' };

  function makeOverlayPoints(key) {
    const arr = overlayArrs[key];
    if (!arr) return [];
    const pts = [];
    for (let i = 0; i < total; i += step) {
      const v = arr[i];
      if (v != null) pts.push({ x: ptX(i), y: Math.round(v * 10) / 10, _raw: v });
    }
    return pts;
  }

  function makeSegmentColor(key, pts) {
    // Returns a segment.borderColor function for Chart.js that colors by zone
    if (!o1Zones) return null;
    if (key === 'temp') {
      const vals = pts.map(p => p.y).filter(v => v !== 0);
      if (!vals.length) return null;
      const minV = Math.min(...vals), maxV = Math.max(...vals);
      return (ctx) => {
        const pt = pts[ctx.p0DataIndex];
        if (!pt) return METRIC_COLORS.temp;
        const frac = maxV > minV ? (pt.y - minV) / (maxV - minV) : 0.5;
        return _heatColor(frac, light);
      };
    }
    if (key !== 'hr' && key !== 'power') return null;
    const _p = cachedProfile || {};
    const bounds = key === 'hr' ? hrBoundsFor(_p, currentAct) : pwrBoundsFor(_p, currentAct);
    if (!bounds) return null;
    const flat = METRIC_COLORS[key];
    return (ctx) => {
      const pt = pts[ctx.p0DataIndex];
      if (!pt) return flat;
      return zoneColorFor(pt._raw, bounds) || flat;
    };
  }

  const datasets = [
    {
      data: points, fill: true,
      borderColor: '#3b82f6', backgroundColor: 'rgba(30,80,180,.55)',
      borderWidth: 1.5, pointRadius: 0, tension: 0.3, order: 3,
      yAxisID: 'yElev',
    }
  ];

  const elevMax = Math.max(...points.map(p => p.y));

  // Clamp a scale to dataMax: remove the top auto-tick if it would be too crowded
  // (gap < 50% of the tick interval), keeping the axis ceiling at the real data max.
  function clampAxis(dataMax) {
    return {
      max: dataMax,
      afterBuildTicks(axis) {
        const t = axis.ticks;
        if (t.length < 2) return;
        const interval = t[1].value - t[0].value;
        const gap = dataMax - t[t.length - 1].value;
        // If the last auto-tick is already at or very near dataMax, keep it
        if (gap < interval * 0.1) return;
        // If there's enough room (≥50% of interval), add the actual max as top tick
        if (gap >= interval * 0.5) { t.push({ value: dataMax }); return; }
        // Otherwise gap is too tight — drop the last auto-tick so nothing overlaps
        t.pop();
      },
    };
  }

  const scales = {
    x: {
      type: 'linear', display: true, min: 0, max: totalX,
      ticks: {
        color: '#64748b', font: { size: 9 }, maxTicksLimit: 10,
        callback: xAxisTime ? (v => fmtChartTime(v)) : (v => `${v.toFixed(1)}${xUnit}`),
      },
      grid: { color: light ? 'rgba(0,0,0,.08)' : 'rgba(255,255,255,.04)' },
    },
    yElev: {
      position: 'left',
      ...clampAxis(elevMax),
      ticks: { color: '#64748b', font: { size: 9 }, maxTicksLimit: 5, callback: v => `${U.alt(v)}${U.altUnit()}` },
      grid: { color: light ? 'rgba(0,0,0,.10)' : 'rgba(255,255,255,.06)' },
    },
  };

  if (o1key) {
    const o1pts = makeOverlayPoints(o1key);
    if (o1pts.length) {
      const c1 = METRIC_COLORS[o1key] || '#22d3ee';
      const seg1 = makeSegmentColor(o1key, o1pts);
      datasets.push({
        data: o1pts, fill: false,
        borderColor: c1,
        ...(seg1 ? {segment:{borderColor:seg1}} : {}),
        backgroundColor: 'transparent',
        borderWidth: 1.5, pointRadius: 0, tension: 0.3, order: 2,
        yAxisID: 'yLeft',
      });
      const o1max = Math.max(...o1pts.map(p => p.y));
      scales.yLeft = {
        position: 'left', offset: true,
        ...clampAxis(o1max),
        ticks: { color: c1, font: { size: 9 }, maxTicksLimit: 5, callback: v => `${v}` },
        grid: { display: false },
        title: { display: true, text: overlayUnits[o1key], color: c1, font: { size: 9 } },
      };
    }
  }

  // ── Splits overlay plugin ─────────────────────────────────────────────────
  let splitsPlugin = null;
  if (splitMetric && data.dist_m && data.dist_m.length > 1) {
    const _splits = splitIsTime
      ? computeSplitsByTime(data, splitDurSec, splitMetric, splitAgg)
      : computeSplits(data, splitLenMi, splitMetric, splitAgg);
    if (_splits.length) {
      // When overlay and splits share the same metric, reuse the left axis
      const _sharedAxis = splitMetric === o1key && !!o1key && !!scales.yLeft;

      // Convert raw split values to display units (speed mph→km/h, climb ft→m if metric)
      // Skip conversion when sharing yLeft — overlay uses raw values on that axis
      function _toDisplayVal(v) {
        if (_sharedAxis) return v;
        if (splitMetric === 'speed' && U.metric) return v * 1.60934;
        if (splitMetric === 'climb' && U.metric) return v * 0.3048;
        if (splitMetric === 'temp' && U.metric) return (v - 32) * 5 / 9;
        return v;
      }
      const _vals = _splits.map(s => _toDisplayVal(s.value)).filter(v => v > 0);
      const _minV = Math.min(..._vals), _maxV = Math.max(..._vals);
      const _p = cachedProfile || {};
      const _bounds = splitZones && splitMetric === 'hr' ? hrBoundsFor(_p, currentAct)
                    : splitZones && splitMetric === 'power' ? pwrBoundsFor(_p, currentAct) : null;
      const _splitAxisColor = METRIC_COLORS[splitMetric] || '#94a3b8';
      const _splitUnit = { speed: U.speedUnit(), power: 'W', hr: 'bpm', cadence: 'rpm',
                           climb: U.climbUnit(), pace: U.metric ? 'min/km' : 'min/mi',
                           temp: U.tempUnit() }[splitMetric] || '';
      if (!_sharedAxis) {
        scales.yRight = {
          position: 'right',
          suggestedMin: 0,
          ...(_maxV > 0 ? clampAxis(_maxV) : {}),
          ticks: {
            color: _splitAxisColor, font: { size: 9 }, maxTicksLimit: 5,
            callback: v => {
              if (splitMetric === 'pace') {
                const m = Math.floor(v), s = Math.round((v - m) * 60);
                return `${m}:${String(s).padStart(2,'0')}`;
              }
              return Math.round(v * 10) / 10;
            },
          },
          grid: { display: false },
          title: { display: !!_splitUnit, text: _splitUnit, color: _splitAxisColor, font: { size: 9 } },
        };
      }
      splitsPlugin = {
        id: 'splitsOverlay',
        beforeDatasetsDraw(chart) {
          const {ctx: cx, chartArea, scales: sc} = chart;
          const xSc = sc.x;
          const ySc = _sharedAxis ? sc.yLeft : sc.yRight;
          if (!xSc || !ySc || !chartArea) return;
          const {top, bottom} = chartArea;
          cx.save();
          _splits.forEach(sp => {
            const x0 = xSc.getPixelForValue(xAxisTime ? ptX(sp.startIdx) : xConv(sp.startDist));
            const x1 = xSc.getPixelForValue(xAxisTime ? ptX(sp.endIdx)   : xConv(sp.endDist));
            const w = x1 - x0;
            if (w <= 0) return;
            const dv = _toDisplayVal(sp.value);
            let color;
            if (_bounds) {
              color = zoneColorFor(sp.value, _bounds) || METRIC_COLORS[splitMetric];
            } else if (splitZones) {
              const frac = _maxV > _minV ? (dv - _minV) / (_maxV - _minV) : 0.5;
              color = _heatColor(frac, light);
            } else {
              color = METRIC_COLORS[splitMetric] || '#3b82f6';
            }
            const barTop = Math.min(ySc.getPixelForValue(dv), bottom - 1);
            const barH = bottom - barTop;
            cx.globalAlpha = light ? 0.82 : 0.32;
            cx.fillStyle = color;
            cx.fillRect(x0, barTop, w, barH);
            if (w > 32) {
              cx.globalAlpha = 0.9;
              cx.fillStyle = light ? 'rgba(0,0,0,0.7)' : 'rgba(255,255,255,0.9)';
              cx.font = 'bold 9px -apple-system,sans-serif';
              cx.textAlign = 'center';
              cx.textBaseline = 'bottom';
              cx.fillText(_fmtSplitVal(sp.value, splitMetric), x0 + w/2, barTop - 1);
            }
          });
          cx.globalAlpha = 1;
          cx.restore();
        }
      };
    }
  }

  // Peak annotations — drawn via custom plugin
  const peakList = showPeaks ? findPeaks(fullPoints, 3) : [];

  // Custom peak marker plugin
  const peakPlugin = {
    id: 'peakMarkers',
    afterDatasetsDraw(chart) {
      if (!peakList.length) return;
      const {ctx: cx, scales} = chart;
      const xSc = scales.x;
      const ySc = scales.yElev || scales.y;
      if (!xSc || !ySc) return;
      cx.save();
      peakList.forEach(pk => {
        const px = xSc.getPixelForValue(xAxisTime ? ptX(pk.idx) : pk.x);
        const py = ySc.getPixelForValue(pk.y);
        const label = U.alt(pk.y) + ' ' + U.altUnit();
        const _lm = _chartIsLight();
        // Stem line
        cx.beginPath();
        cx.moveTo(px, py);
        cx.lineTo(px, py - 20);
        cx.strokeStyle = _lm ? 'rgba(0,0,0,.35)' : 'rgba(255,255,255,.5)';
        cx.lineWidth = 1;
        cx.stroke();
        // Dot at peak
        cx.beginPath();
        cx.arc(px, py, 3, 0, Math.PI*2);
        cx.fillStyle = _lm ? '#555' : '#fff';
        cx.fill();
        // Label bubble
        cx.font = 'bold 9px -apple-system,sans-serif';
        const tw = cx.measureText(label).width;
        const bx = px - tw/2 - 5, by = py - 38, bw = tw + 10, bh = 16;
        cx.fillStyle = 'rgba(0,0,0,.75)';
        cx.beginPath();
        cx.roundRect(bx, by, bw, bh, 4);
        cx.fill();
        cx.fillStyle = '#f2f2f7';
        cx.textAlign = 'center';
        cx.textBaseline = 'middle';
        cx.fillText(label, px, by + bh/2);
      });
      cx.restore();
    }
  };

  if (elevChart) { elevChart.destroy(); elevChart = null; }
  elevChart = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    plugins: [peakPlugin, ...(splitsPlugin ? [splitsPlugin] : [])],
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      layout: { padding: { top: peakList.length ? 42 : 2, bottom: 2, right: 2 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => xAxisTime ? fmtChartTime(items[0].parsed.x) : `${Number(items[0].parsed.x).toFixed(1)} ${xUnit}`,
            label: c => {
              const ds = c.datasetIndex;
              if (ds === 0) return `${c.parsed.y} ft`;
              return `${c.parsed.y} ${overlayUnits[o1key]||''}`;
            },
          },
          titleFont: { size: 11 }, bodyFont: { size: 11 },
        },
      },
      scales,
    },
  });
}

// ── ELEVATION HOVER PANEL ────────────────────────────────────────────────────
(function() {
  const COLORS = { hr:'#ef4444', power:'#eab308', cadence:'#a855f7', speed:'#22c55e',
                   altitude:'#60a5fa', distance:'#f2f2f7', gradient:'#fb923c' };

  function lerp(arr, idx) {
    // linear interpolation between floor/ceil indices
    if (!arr || !arr.length) return null;
    const lo = Math.floor(idx), hi = Math.ceil(idx);
    if (lo === hi) return arr[lo];
    if (hi >= arr.length) return arr[lo];
    const t = idx - lo;
    return arr[lo] * (1-t) + arr[hi] * t;
  }

  function calcGradient(data, idx) {
    const n = data.alt_ft.length;
    const w = Math.max(1, Math.floor(n / 200)); // window
    const lo = Math.max(0, Math.floor(idx) - w);
    const hi = Math.min(n-1, Math.floor(idx) + w);
    const dAlt = (data.alt_ft[hi] - data.alt_ft[lo]) * 0.3048; // ft→m
    const dDist = (data.dist_m[hi] - data.dist_m[lo]);           // already metres
    if (dDist < 1) return 0;
    return (dAlt / dDist) * 100;
  }

  function setupHover() {
    const wrap = document.getElementById('elev-canvas-area') || document.getElementById('chart-wrap');
    const panel = document.getElementById('elev-hover-panel');
    const cross = document.getElementById('elev-crosshair');
    if (!wrap || !panel || !cross) return;

    wrap.addEventListener('mousemove', e => {
      if (!elevChart || !elevChartData) { panel.style.display='none'; cross.style.display='none'; return; }
      const rect = wrap.getBoundingClientRect();
      const px   = e.clientX - rect.left;
      const ca   = elevChart.chartArea;
      if (!ca || px < ca.left || px > ca.right) { panel.style.display='none'; cross.style.display='none'; return; }

      const xScale = elevChart.scales.x;
      const xVal   = xScale.getValueForPixel(px);

      // Map x-axis value → data index
      const data = elevChartData;
      const n    = data.dist_m.length;
      const useTimeAxis = (document.getElementById('xaxis-time')?.checked ?? false)
                          && data.time && data.time.length === n;
      let lo=0, hi=n-1, idx;
      if (useTimeAxis) {
        while (lo < hi-1) { const mid=Math.floor((lo+hi)/2); if (data.time[mid] < xVal) lo=mid; else hi=mid; }
        idx = lo + (xVal - data.time[lo]) / Math.max(1, data.time[hi] - data.time[lo]);
      } else {
        const xM = U.metric ? xVal * 1000 : xVal * 1609.344;
        while (lo < hi-1) { const mid=Math.floor((lo+hi)/2); if (data.dist_m[mid] < xM) lo=mid; else hi=mid; }
        idx = lo + (xM - data.dist_m[lo]) / Math.max(1, data.dist_m[hi] - data.dist_m[lo]);
      }

      // Crosshair
      cross.style.display = 'block';
      cross.style.left    = px + 'px';
      cross.style.top     = ca.top + 'px';
      cross.style.height  = (ca.bottom - ca.top) + 'px';

      // Move elevation dot to hover position
      const yScale = elevChart.scales.yElev || elevChart.scales.y;
      const altHover = lerp(data.alt_ft, idx);
      const dot = document.getElementById('elev-anim-dot');
      if (dot && yScale && altHover != null) {
        const dotPy = yScale.getPixelForValue(Math.round(altHover));
        dot.style.display = 'block';
        dot.style.left    = px + 'px';
        dot.style.top     = dotPy + 'px';
      }

      // Update scrub slider and timecode to match hover position
      // idx is relative to elevChartData.dist_m — map it to anim.pts by fraction
      const hoverFrac = Math.max(0, Math.min(1, idx / (data.dist_m.length - 1)));
      const scrub = document.getElementById('t-scrub');
      if (scrub) scrub.value = Math.round(hoverFrac * 1000);
      // Timecode from anim.pts if available, else from elevChartData.time
      const timeArr = (anim.pts && anim.pts.time) ? anim.pts.time : data.time;
      let hoverSecs = null;
      if (timeArr && timeArr.length) {
        const tIdx = Math.round(hoverFrac * (timeArr.length - 1));
        hoverSecs = timeArr[Math.max(0, Math.min(timeArr.length-1, tIdx))] || 0;
        const h = Math.floor(hoverSecs/3600), m = Math.floor((hoverSecs%3600)/60), s = Math.floor(hoverSecs%60);
        const ttime = document.getElementById('t-time');
        if (ttime) ttime.textContent = `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
      }

      // Move map dot to hover position
      if (anim.latLon && anim.latLon.length > 0) {
        const mapIdx = Math.max(0, Math.min(anim.latLon.length - 1, Math.round(idx * anim.latLon.length / data.dist_m.length)));
        const ll = anim.latLon[mapIdx];
        if (ll && leafMap) {
          if (!anim.mapDot) {
            anim.mapDot = L.marker(ll, {
              icon: L.divIcon({
                html: `<div style="width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 35%,#ffe066,#f59e0b 55%,#b45309);box-shadow:0 1px 3px rgba(0,0,0,.6)"></div>`,
                iconSize:[10,10], iconAnchor:[5,5], className:''
              }), zIndexOffset: 1000
            }).addTo(leafMap);
          } else {
            anim.mapDot.setLatLng(ll);
          }
        }
      }

      // Values
      const alt   = lerp(data.alt_ft,  idx);
      const hr    = lerp(data.hr,      idx);
      const pwr   = lerp(data.power,   idx);
      const cad   = lerp(data.cadence, idx);
      const spd   = lerp(data.speed,   idx);
      const grad  = calcGradient(data, idx);
      const dist  = lerp(data.dist_m,  idx);

      const _prof = cachedProfile || {};
      const _hrZC = (hr && hr>0) ? (zoneColorFor(hr,  hrBoundsFor(_prof, currentAct))  || COLORS.hr)    : COLORS.hr;
      const _pwZC = (pwr && pwr>0)? (zoneColorFor(pwr, pwrBoundsFor(_prof, currentAct)) || COLORS.power) : COLORS.power;
      const rows = [
        { label:'Time',     val: hoverSecs != null ? (()=>{ const h=Math.floor(hoverSecs/3600),m=Math.floor((hoverSecs%3600)/60),s=Math.floor(hoverSecs%60); return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`; })() : '—', color: 'rgba(255,255,255,.75)' },
        { label:'Distance', val: dist != null ? U.distS(+(dist/1609.344).toFixed(2)) : '—', color: COLORS.distance },
        { label:'Altitude', val: alt  != null ? U.altS(alt)                      : '—', color: COLORS.altitude },
        { label:'Gradient', val: grad != null ? grad.toFixed(1)+'%'              : '—', color: COLORS.gradient },
        { label:'Heart Rate',val:hr && hr>0   ? Math.round(hr)+' bpm'           : '—', color: _hrZC },
        { label:'Power',    val: pwr && pwr>0  ? Math.round(pwr)+' W'           : '—', color: _pwZC },
        { label:'Cadence',  val: cad && cad>0 ? Math.round(cad)+' rpm'          : '—', color: COLORS.cadence },
        { label:'Speed',    val: spd && spd>0 ? U.speedS(spd)                   : '—', color: COLORS.speed },
      ];

      panel.innerHTML = rows.map(r =>
        `<div class="eh-row">
          <span class="eh-label">${r.label}</span>
          <span class="eh-val" style="color:${r.color}">${r.val}</span>
        </div>`
      ).join('');

      // Position panel: left or right of crosshair to avoid overflow
      panel.style.display = 'block';
      const pw = panel.offsetWidth;
      const ww = rect.width;
      if (px + pw/2 + 8 > ww) {
        panel.style.left  = '';
        panel.style.right = (ww - px + 8) + 'px';
        panel.style.transform = 'none';
      } else if (px - pw/2 < 8) {
        panel.style.left  = (px + 8) + 'px';
        panel.style.right = '';
        panel.style.transform = 'none';
      } else {
        panel.style.left      = px + 'px';
        panel.style.right     = '';
        panel.style.transform = 'translateX(-50%)';
      }
    });

    wrap.addEventListener('mouseleave', () => {
      // Just hide the hover panel and crosshair — leave dots, scrub, and timecode where they are
      panel.style.display = 'none';
      cross.style.display = 'none';
    });
  }

  document.addEventListener('DOMContentLoaded', setupHover);
  setTimeout(setupHover, 800);
})();

// ── ELEVATION RANGE SELECTION ─────────────────────────────────────────────────
(function() {
  let dragging   = false;
  let startPx    = 0;
  let startIdx   = 0;

  // Convert pixel x within chart-wrap → data index
  function pxToIdx(px) {
    if (!elevChart || !elevChartData) return -1;
    const ca     = elevChart.chartArea;
    if (!ca) return -1;
    const xScale = elevChart.scales.x;
    const xVal   = xScale.getValueForPixel(Math.max(ca.left, Math.min(ca.right, px)));
    const data   = elevChartData;
    const n      = data.dist_m.length;
    const xM     = U.metric ? xVal * 1000 : xVal * 1609.344;
    let lo = 0, hi = n - 1;
    while (lo < hi - 1) { const mid = Math.floor((lo+hi)/2); if (data.dist_m[mid] < xM) lo = mid; else hi = mid; }
    return Math.max(0, Math.min(n - 1, Math.round(lo + (xM - data.dist_m[lo]) / Math.max(1, data.dist_m[hi] - data.dist_m[lo]))));
  }

  function applyElevSelection(i0, i1) {
    const data = elevChartData;
    if (!data || !elevChart) return;
    const lo = Math.min(i0, i1), hi = Math.max(i0, i1);
    setElevSelection(lo, hi);

    // ── Shade elevation chart ────────────────────────────────────────────────
    elevChart.data.datasets = elevChart.data.datasets.filter(d => !d._elevSel);
    if (hi > lo) {
      const slice = data.dist_m.slice(lo, hi + 1).map((d, j) => ({
        x: U.metric ? d / 1000 : d / 1609.344,
        y: U.alt(data.alt_ft[lo + j] || 0),
      }));
      elevChart.data.datasets.push({
        _elevSel: true, data: slice,
        borderColor: 'rgba(74,222,128,0)', backgroundColor: 'rgba(74,222,128,0.35)',
        fill: true, pointRadius: 0, tension: 0, order: 0, yAxisID: 'yElev',
      });
    }
    elevChart.update('none');

    // ── Auto-run compare if overlay is open and waiting for a selection ───────
    const _cmpOverlay = document.getElementById('compare-overlay');
    if (hi > lo && _cmpOverlay && _cmpOverlay.classList.contains('open') && !cmp.matches.length) {
      openCompare();
    }

    // ── Highlight map segment ─────────────────────────────────────────────────
    if (splitsState._elevSelMapLayer && leafMap) {
      leafMap.removeLayer(splitsState._elevSelMapLayer);
      splitsState._elevSelMapLayer = null;
    }
    const geo = splitsState.geo;
    const pts = splitsState.pts;
    if (hi > lo && geo && pts) {
      const n = pts.dist_m.length;
      const coords = geo.geometry?.coordinates || [];
      const ci1 = Math.floor((lo / (n - 1)) * (coords.length - 1));
      const ci2 = Math.ceil( (hi / (n - 1)) * (coords.length - 1));
      const lls = coords.slice(ci1, ci2 + 1).map(c => [c[1], c[0]]);
      if (lls.length > 1 && leafMap) {
        splitsState._elevSelMapLayer = L.polyline(lls, {
          color: '#4ade80', weight: 5, opacity: 0.85
        }).addTo(leafMap);
      }
    }
  }

  function clearElevSelection() {
    setElevSelection(null, null);
    if (elevChart) {
      elevChart.data.datasets = elevChart.data.datasets.filter(d => !d._elevSel);
      elevChart.update('none');
    }
    if (splitsState._elevSelMapLayer && leafMap) {
      leafMap.removeLayer(splitsState._elevSelMapLayer);
      splitsState._elevSelMapLayer = null;
    }
    const sel = document.getElementById('seg-selector');
    if (sel) sel.value = '';
    const lbl = document.getElementById('seg-selector-label');
    if (lbl) lbl.textContent = 'Entire activity';
    window._selectedSegmentId = null;
    const defineBtn = document.getElementById('seg-define-btn');
    if (defineBtn) defineBtn.textContent = 'Define…';
    const beBtn2 = document.getElementById('seg-best-efforts-btn');
    if (beBtn2) beBtn2.disabled = true;
  }

  function setup() {
    const wrap  = document.getElementById('elev-canvas-area');
    const panel = document.getElementById('elev-hover-panel');
    const cross = document.getElementById('elev-crosshair');
    const rect  = document.getElementById('elev-sel-rect');
    if (!wrap || !rect) return;

    function beginDrag(clientX) {
      if (!elevChart || !elevChartData) return false;
      const ca = elevChart.chartArea;
      if (!ca) return false;
      const wRect = wrap.getBoundingClientRect();
      const px = clientX - wRect.left;
      if (px < ca.left || px > ca.right) return false;
      dragging = true;
      startPx  = px;
      startIdx = pxToIdx(px);
      if (panel) panel.style.display = 'none';
      if (cross) cross.style.display = 'none';
      rect.style.display = 'block';
      rect.style.left  = px + 'px';
      rect.style.width = '0px';
      // Reset segment selector when user draws a new region
      const segSel = document.getElementById('seg-selector');
      if (segSel && segSel.value) {
        segSel.value = '';
        window._selectedSegmentId = null;
        const defineBtn = document.getElementById('seg-define-btn');
        if (defineBtn) defineBtn.textContent = 'Define…';
        const segLbl = document.getElementById('seg-selector-label');
        if (segLbl) segLbl.textContent = 'Entire activity';
        const beBtn2 = document.getElementById('seg-best-efforts-btn');
        if (beBtn2) beBtn2.disabled = true;
      }
      return true;
    }

    function moveDrag(clientX) {
      if (!dragging) return;
      const ca = elevChart.chartArea;
      const wRect = wrap.getBoundingClientRect();
      const curPx  = clientX - wRect.left;
      const lo     = Math.max(ca.left, Math.min(startPx, curPx));
      const hi     = Math.min(ca.right, Math.max(startPx, curPx));
      rect.style.left  = lo + 'px';
      rect.style.width = (hi - lo) + 'px';
      applyElevSelection(startIdx, pxToIdx(curPx));
    }

    function endDrag(clientX) {
      rect.style.display = 'none';
      const wRect = wrap.getBoundingClientRect();
      const curPx  = clientX - wRect.left;
      const moved  = Math.abs(curPx - startPx) > 4;
      if (moved) applyElevSelection(startIdx, pxToIdx(curPx));
      else clearElevSelection();
      dragging = false;
    }

    // Mouse
    wrap.addEventListener('mousedown', e => {
      if (!beginDrag(e.clientX)) return;
      e.preventDefault();
      function onMove(ev) { moveDrag(ev.clientX); }
      function onUp(ev)   { document.removeEventListener('mousemove', onMove);
                            document.removeEventListener('mouseup', onUp);
                            endDrag(ev.clientX); }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });

    // Touch (iPad)
    wrap.addEventListener('touchstart', e => {
      const t = e.touches[0];
      if (!t || !beginDrag(t.clientX)) return;
      // Don't preventDefault — allow scroll unless we actually started dragging
      function onMove(ev) { const t2 = ev.touches[0]; if (t2) moveDrag(t2.clientX); }
      function onEnd(ev)  { const t2 = ev.changedTouches[0];
                            document.removeEventListener('touchmove', onMove);
                            document.removeEventListener('touchend', onEnd);
                            if (t2) endDrag(t2.clientX); }
      document.addEventListener('touchmove', onMove, {passive: true});
      document.addEventListener('touchend',  onEnd,  {passive: true});
    }, {passive: true});
  }

  document.addEventListener('DOMContentLoaded', setup);
  setTimeout(setup, 800);

  // Expose for compare legend navigation and segment selector
  window._applyElevSelection = applyElevSelection;
  window._clearElevSelection = clearElevSelection;
})();

// ── SEGMENT SELECTOR ──────────────────────────────────────────────────────────
async function loadSegmentSelector(actId) {
  const sel  = document.getElementById('seg-selector');
  const wrap = document.getElementById('seg-selector-wrap');
  const lbl  = document.getElementById('seg-selector-label');
  const list = document.getElementById('seg-selector-list');
  if (!sel) return;
  sel.innerHTML = '<option value="">Entire activity</option>';
  sel.value = '';
  if (wrap) wrap.style.display = 'none';
  const prefix = document.getElementById('seg-selector-prefix');
  if (prefix) prefix.style.display = 'none';
  if (lbl)  lbl.textContent = 'Entire activity';
  if (list) list.innerHTML = '';
  // Reset edit state
  window._selectedSegmentId = null;
  const defineBtn = document.getElementById('seg-define-btn');
  if (defineBtn) defineBtn.textContent = 'Define…';
  const beBtn = document.getElementById('seg-best-efforts-btn');
  if (beBtn) { beBtn.disabled = true; }
  if (!actId) return;
  try {
    const r = await fetch(`/api/segments/for-activity/${actId}`);
    if (!r.ok) return;
    const d = await r.json();
    const segs = (d.segments || []).sort((a, b) => a.name.localeCompare(b.name));
    for (const s of segs) {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name;
      const si = s.matched_start_idx ?? s.start_idx;
      const ei = s.matched_end_idx   ?? s.end_idx;
      if (si != null) opt.dataset.startIdx = si;
      if (ei != null) opt.dataset.endIdx   = ei;
      sel.appendChild(opt);
      if (list) {
        const item = document.createElement('div');
        item.textContent = s.name;
        item.dataset.value = s.id;
        item.style.cssText = 'padding:4px 10px;cursor:pointer;font-size:11px;white-space:nowrap;color:var(--text)';
        item.addEventListener('mouseenter', () => item.style.background = 'var(--surface2)');
        item.addEventListener('mouseleave', () => item.style.background = '');
        item.addEventListener('mousedown', e => {
          e.preventDefault();
          sel.value = s.id;
          list.style.display = 'none';
          onSegSelectorChange(s.id);
        });
        list.appendChild(item);
      }
    }
    if (segs.length > 0 && wrap) {
      wrap.style.display = '';
      const prefix = document.getElementById('seg-selector-prefix');
      if (prefix) prefix.style.display = '';
      const pendingId = window._pendingSegmentId;
      if (pendingId) {
        window._pendingSegmentId = null;
        const found = segs.find(s => s.id === pendingId);
        if (found) {
          sel.value = String(pendingId);
          onSegSelectorChange(String(pendingId));
        }
      }
    }
  } catch(e) {}
}

function toggleSegDropdown(e) {
  e.stopPropagation();
  const list = document.getElementById('seg-selector-list');
  if (!list) return;
  if (list.style.display === 'none') {
    list.style.display = '';
    setTimeout(() => document.addEventListener('click', function closeDD(ev) {
      const wrap = document.getElementById('seg-selector-wrap');
      if (!wrap || !wrap.contains(ev.target)) {
        list.style.display = 'none';
        document.removeEventListener('click', closeDD);
      }
    }), 0);
  } else {
    list.style.display = 'none';
  }
}

function onSegSelectorChange(val) {
  const sel = document.getElementById('seg-selector');
  const lbl = document.getElementById('seg-selector-label');
  const defineBtn = document.getElementById('seg-define-btn');
  const beBtn = document.getElementById('seg-best-efforts-btn');
  if (!val) {
    if (window._clearElevSelection) window._clearElevSelection();
    window._selectedSegmentId = null;
    if (defineBtn) defineBtn.textContent = 'Define…';
    if (lbl) lbl.textContent = 'Entire activity';
    if (beBtn) { beBtn.disabled = true; }
    return;
  }
  window._selectedSegmentId = parseInt(val, 10);
  if (defineBtn) defineBtn.textContent = 'Edit…';
  if (beBtn) { beBtn.disabled = false; }
  const opt = sel.options[sel.selectedIndex];
  if (lbl && opt) lbl.textContent = opt.textContent;
  const s0 = parseInt(opt.dataset.startIdx, 10);
  const s1 = parseInt(opt.dataset.endIdx,   10);
  if (!isNaN(s0) && !isNaN(s1) && window._applyElevSelection) {
    window._applyElevSelection(s0, s1);
  }
}

// ── ANIMATION ENGINE ─────────────────────────────────────────────────────────
const anim = {
  pts:       null,   // full point array from /charts endpoint: {time,alt_ft,hr,speed,power,dist_m}
  latLon:    null,   // parallel array of [lat,lon] from geojson
  idx:       0,      // current point index
  speed:     1,      // playback speed multiplier (negative = reverse)
  playing:   false,
  rafId:     null,
  lastTs:    null,
  secPerPt:  1,      // real seconds per point (wall_clock_delta step)
  mapDot:    null,   // Leaflet circle marker
};

function animInit(chartsData, geojson) {
  // Build parallel arrays from chart data and geojson coordinates
  if (!chartsData || !chartsData.time || !chartsData.time.length) return;

  anim.pts = chartsData;

  // Build lat/lon array aligned to chart points by sampling geojson coords
  const coords = geojson?.geometry?.coordinates || [];
  const n = chartsData.time.length;
  anim.latLon = [];
  for (let i = 0; i < n; i++) {
    const ci = Math.round(i / (n-1) * (coords.length-1));
    const c  = coords[ci] || coords[0] || [0,0];
    anim.latLon.push([c[1], c[0]]);  // [lat, lon]
  }

  // Estimate seconds per point
  const times = chartsData.time;
  if (times.length > 1) {
    anim.secPerPt = Math.max(1, (times[times.length-1] - times[0]) / (times.length-1));
  }

  anim.idx = 0;
  anim.playing = false;
  anim.speed = 1;

  const _tbar = document.getElementById('transport-bar');
  document.getElementById('t-scrub').value = 0;
  document.getElementById('t-speed').value = '1';
  // Default state: stopped
  const tStop = document.getElementById('t-stop');
  if (tStop) tStop.classList.add('active');

  // Create animated map dot (keep start/end flags visible during animation)
  if (anim.mapDot) { leafMap.removeLayer(anim.mapDot); anim.mapDot = null; }
  if (anim.latLon[0]) {
    anim.mapDot = L.marker(anim.latLon[0], {
      icon: L.divIcon({
        html: `<div style="
          width:10px;height:10px;border-radius:50%;
          background: radial-gradient(circle at 35% 35%, #ffe066, #f59e0b 55%, #b45309);
          box-shadow: 0 1px 3px rgba(0,0,0,.6), inset 0 -1px 2px rgba(0,0,0,.2);
        "></div>`,
        iconSize: [10,10], iconAnchor: [5,5], className: ''
      }),
      zIndexOffset: 1000
    }).addTo(leafMap);
  }

  animUpdateUI(0);
}

function animPlay() {
  if (!anim.pts) return;
  if (anim.speed < 0) anim.speed = 1;  // reset to forward if was reversing
  anim.playing = true;
  anim.lastTs = null;
  document.getElementById('t-play').classList.add('active');
  document.getElementById('t-stop').classList.remove('active');
  anim.rafId = requestAnimationFrame(animFrame);
}

function animStop() {
  anim.playing = false;
  if (anim.rafId) { cancelAnimationFrame(anim.rafId); anim.rafId = null; }
  const tPlay = document.getElementById('t-play');
  const tRev  = document.getElementById('t-rev');
  const tStop = document.getElementById('t-stop');
  if (tPlay) tPlay.classList.remove('active');
  if (tRev)  tRev.classList.remove('active');
  if (tStop) tStop.classList.add('active');
}

function animSetSpeed(mult) {
  anim.speed = mult;
  anim.playing = true;
  anim.lastTs = null;
  document.getElementById('t-play').classList.toggle('active', mult > 0);
  document.getElementById('t-rev').classList.toggle('active', mult < 0);
  document.getElementById('t-stop').classList.remove('active');
  if (!anim.rafId) anim.rafId = requestAnimationFrame(animFrame);
}

function animSetSpeedVal(v) {
  anim.speed = Math.sign(anim.speed || 1) * parseFloat(v);
}

function animJump(frac) {
  if (!anim.pts) return;
  const n = anim.pts.time.length;
  anim.idx = Math.round(frac * (n - 1));
  animUpdateUI(anim.idx);
}

function animScrub(val) {
  if (!anim.pts) return;
  const n = anim.pts.time.length;
  anim.idx = Math.round((val / 1000) * (n - 1));
  animUpdateUI(anim.idx);
}

function animFrame(ts) {
  anim.rafId = null;
  if (!anim.playing || !anim.pts) return;
  const n = anim.pts.time.length;
  const now = ts || performance.now();

  if (anim.lastTs !== null) {
    const dtReal = (now - anim.lastTs) / 1000;  // real seconds since last frame (~0.016s at 60fps)
    // At 1× speed: complete the activity in ~60 seconds of real time
    // ptsPerSec = n / 60 * speed
    const ptsPerSec = (n / 60.0) * Math.abs(anim.speed);
    const steps = dtReal * ptsPerSec * Math.sign(anim.speed);
    anim.idx = anim.idx + steps;
  }
  anim.lastTs = now;

  const idxInt = Math.floor(anim.idx);
  if (idxInt >= n - 1) { anim.idx = n - 1; animUpdateUI(n - 1); animStop(); return; }
  if (idxInt < 0)      { anim.idx = 0;     animUpdateUI(0);     animStop(); return; }

  animUpdateUI(idxInt);
  anim.rafId = requestAnimationFrame(animFrame);
}

function animUpdateUI(i) {
  if (!anim.pts) return;
  const pts = anim.pts;
  const n   = pts.time.length;
  i = Math.max(0, Math.min(n-1, i));

  // Time display — i is already clamped integer index
  const secs = pts.time[Math.max(0, Math.min(pts.time.length-1, i))] || 0;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  document.getElementById('t-time').textContent =
    `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;

  // Scrubber
  document.getElementById('t-scrub').value = Math.round((i / (n-1)) * 1000);

  // Stats
  const hr  = pts.hr?.[i];
  const spd = pts.speed?.[i];
  const pwr = pts.power?.[i];
  const alt = pts.alt_ft?.[i];
  const dst = pts.dist_m?.[i];

  const _hrEl  = document.getElementById('as-hr');
  const _pwrEl = document.getElementById('as-pwr');
  const _hrLbl = document.getElementById('as-hr-lbl');
  const _pwrLbl= document.getElementById('as-pwr-lbl');
  _hrEl.textContent  = hr  > 0 ? Math.round(hr)  : '—';
  document.getElementById('as-spd').textContent = spd > 0 ? U.speed(spd).toFixed(1)  : '—';
  _pwrEl.textContent = pwr > 0 ? Math.round(pwr) : '—';
  document.getElementById('as-alt').textContent = alt > 0 ? U.alt(alt) : '—';
  document.getElementById('as-dst').textContent = dst > 0 ? U.dist(dst/1609.344).toFixed(1) : '—';
  // Zone coloring
  const _p = cachedProfile || {};
  const _hrC = hr  > 0 ? zoneColorFor(hr,  hrBoundsFor(_p, currentAct))  : null;
  const _pwC = pwr > 0 ? zoneColorFor(pwr, pwrBoundsFor(_p, currentAct)) : null;
  if (_hrEl)  { _hrEl.style.color  = _hrC  || ''; if (_hrLbl)  _hrLbl.style.color  = _hrC  || ''; }
  if (_pwrEl) { _pwrEl.style.color = _pwC  || ''; if (_pwrLbl) _pwrLbl.style.color = _pwC  || ''; }

  // Map dot
  if (anim.mapDot && anim.latLon?.[i]) {
    anim.mapDot.setLatLng(anim.latLon[i]);
  }

  // Elevation chart dot
  const dot = document.getElementById('elev-anim-dot');
  if (dot && elevChart && pts.dist_m?.[i] !== undefined) {
    const chartArea = elevChart.chartArea;
    if (chartArea) {
      const xScale = elevChart.scales.x;
      const yScale = elevChart.scales.yElev || elevChart.scales.y;
      const xMi = U.metric ? pts.dist_m[i] / 1000 : pts.dist_m[i] / 1609.344;
      // Hide dot if it's outside the visible zoom window
      const xMin = elevChart.options.scales.x.min;
      const xMax = elevChart.options.scales.x.max;
      if (xMin !== undefined && xMax !== undefined && (xMi < xMin || xMi > xMax)) {
        dot.style.display = 'none';
      } else {
        const px  = xScale.getPixelForValue(xMi);
        const py  = yScale.getPixelForValue(Math.round(alt || 0));
        dot.style.display = 'block';
        dot.style.left    = px + 'px';
        dot.style.top     = py + 'px';
      }
    }
  }
}

function animReset() {
  anim.playing = false;
  if (anim.rafId) { cancelAnimationFrame(anim.rafId); anim.rafId = null; }
  if (anim.mapDot && leafMap) { leafMap.removeLayer(anim.mapDot); anim.mapDot = null; }
  const dot = document.getElementById('elev-anim-dot');
  if (dot) dot.style.display = 'none';
  // Reset button states
  ['t-play','t-rev'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
  });
  const tStop = document.getElementById('t-stop');
  if (tStop) tStop.classList.remove('active');
  anim.pts = null;
  anim.latLon = null;
  anim.idx = 0;
}

// Keyboard shortcuts: space = play/pause, left/right = step
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
  if (document.getElementById('coach-overlay')?.classList.contains('open')) return;
  if (document.getElementById('lightbox').style.display === 'flex') return;
  if (!anim.pts) return;
  if (e.key === ' ')          { e.preventDefault(); anim.playing ? animStop() : animPlay(); }
  if (e.key === 'ArrowRight') { animJump((anim.idx + 10) / anim.pts.time.length); }
  if (e.key === 'ArrowLeft')  { animJump((anim.idx - 10) / anim.pts.time.length); }
});


// ── SPLITS STATE (used for elevation range selection map highlight) ────────────
const splitsState = {
  pts:              null,
  geo:              null,
  _elevSelMapLayer: null,
};

function computeSplits(pts, lenMi, metric, agg) {
  if (!pts?.dist_m?.length) return [];
  const n    = pts.dist_m.length;
  const lenM = lenMi * 1609.344;
  const splits = [];
  let si = 0, startDist = pts.dist_m[0];

  const getArr = m => ({ speed: pts.speed, power: pts.power, hr: pts.hr,
    cadence: pts.cadence, climb: pts.alt_ft, pace: pts.speed, temp: pts.temp_f })[m] || pts.speed;
  const arr = getArr(metric);

  for (let i = 1; i < n; i++) {
    if ((pts.dist_m[i] - startDist) >= lenM || i === n - 1) {
      const slice = arr.slice(si, i + 1).filter(v => v > 0);
      let value = 0;
      if (slice.length) {
        value = agg === 'max' ? Math.max(...slice) : slice.reduce((a,b)=>a+b,0)/slice.length;
      }
      if (metric === 'climb') {
        const alt = pts.alt_ft.slice(si, i+1);
        value = 0;
        for (let j = 1; j < alt.length; j++) if (alt[j] > alt[j-1]) value += alt[j]-alt[j-1];
      }
      if (metric === 'pace' && value > 0) value = U.metric ? 60 / (value * 1.60934) : 60 / value;

      splits.push({
        startIdx: si, endIdx: i,
        startDist: startDist, endDist: pts.dist_m[i],
        startMi: startDist / 1609.344,
        endMi:   pts.dist_m[i] / 1609.344,
        value: Math.round(value * 10) / 10,
      });
      si = i;
      startDist = pts.dist_m[i];
    }
  }
  return splits;
}

function computeSplitsByTime(pts, durSec, metric, agg) {
  if (!pts?.time?.length || !pts?.dist_m?.length) return [];
  const n = pts.time.length;
  const splits = [];
  let si = 0;

  const getArr = m => ({ speed: pts.speed, power: pts.power, hr: pts.hr,
    cadence: pts.cadence, climb: pts.alt_ft, pace: pts.speed, temp: pts.temp_f })[m] || pts.speed;
  const arr = getArr(metric);

  for (let i = 1; i < n; i++) {
    if ((pts.time[i] - pts.time[si]) >= durSec || i === n - 1) {
      const slice = arr.slice(si, i + 1).filter(v => v > 0);
      let value = 0;
      if (slice.length) {
        value = agg === 'max' ? Math.max(...slice) : slice.reduce((a,b)=>a+b,0)/slice.length;
      }
      if (metric === 'climb') {
        const alt = pts.alt_ft.slice(si, i + 1);
        value = 0;
        for (let j = 1; j < alt.length; j++) if (alt[j] > alt[j-1]) value += alt[j] - alt[j-1];
      }
      if (metric === 'pace' && value > 0) value = U.metric ? 60 / (value * 1.60934) : 60 / value;

      splits.push({
        startIdx: si, endIdx: i,
        startDist: pts.dist_m[si], endDist: pts.dist_m[i],
        startMi: pts.dist_m[si] / 1609.344,
        endMi:   pts.dist_m[i]  / 1609.344,
        value: Math.round(value * 10) / 10,
      });
      si = i;
    }
  }
  return splits;
}

function clearSplitHighlights() {
  if (splitsState._elevSelMapLayer && leafMap) {
    leafMap.removeLayer(splitsState._elevSelMapLayer);
    splitsState._elevSelMapLayer = null;
  }
  if (elevChart) {
    elevChart.data.datasets = elevChart.data.datasets.filter(d => !d._elevSel);
    elevChart.update('none');
  }
}

// ── ELEVATION CHART ZOOM ─────────────────────────────────────────────────────
const elevZoom = {
  level:    1,      // 1 = full view, >1 = zoomed in
  offset:   0,      // scroll position 0–1 (fraction of total range)
  maxLevel: 16,
};

function elevZoomApply() {
  if (!elevChart || !elevChartData) return;
  const dist  = elevChartData.dist_m;
  const total = U.metric ? dist[dist.length-1]/1000 : dist[dist.length-1]/1609.344;
  const bar    = document.getElementById('elev-zoom-bar');
  const label  = document.getElementById('elev-zoom-label');
  const scroll = document.getElementById('elev-zoom-scroll');

  // Always clamp level to [1, maxLevel]
  elevZoom.level = Math.max(1, Math.min(elevZoom.maxLevel, elevZoom.level));

  if (elevZoom.level <= 1) {
    // Full view — set to exact data extents (never go beyond this)
    elevChart.options.scales.x.min = 0;
    elevChart.options.scales.x.max = total;
    if (bar) bar.style.display = 'none';
  } else {
    const window_size = total / elevZoom.level;
    const max_offset  = total - window_size;
    const start = Math.max(0, Math.min(elevZoom.offset * max_offset, max_offset));
    elevChart.options.scales.x.min = start;
    elevChart.options.scales.x.max = start + window_size;
    if (bar) bar.style.display = 'flex';
    if (label) label.textContent = elevZoom.level + '×';
    if (scroll) scroll.value = Math.round(elevZoom.offset * 1000);
  }
  elevChart.update('none');

  // Repaint the anim dot after zoom — must happen after chart update
  requestAnimationFrame(() => {
    if (anim && anim.pts && anim.idx !== undefined) {
      animUpdateUI(Math.floor(anim.idx));
    }
  });
}

function elevZoomStep(dir) {
  // dir: +1 = zoom in, -1 = zoom out
  const steps = [1, 1.5, 2, 3, 4, 6, 8, 12, 16];
  const cur = elevZoom.level;
  let idx = steps.findIndex(s => s >= cur);
  if (idx < 0) idx = steps.length - 1;
  idx = Math.max(0, Math.min(steps.length - 1, idx + dir));
  elevZoom.level = steps[idx];
  elevZoomApply();
}

function elevZoomReset() {
  elevZoom.level  = 1;
  elevZoom.offset = 0;
  elevZoomApply();
}

function elevScrollTo(val) {
  elevZoom.offset = val / 1000;
  elevZoomApply();
}

function elevZoomAtPixel(px, factor) {
  // Zoom in/out centered on pixel position px within chart-wrap
  if (!elevChart || !elevChartData) return;
  const dist  = elevChartData.dist_m;
  const total = U.metric ? dist[dist.length-1]/1000 : dist[dist.length-1]/1609.344;
  const ca    = elevChart.chartArea;
  if (!ca) return;

  // Find x-value at cursor
  const xScale = elevChart.scales.x;
  const curX   = xScale.getValueForPixel(Math.max(ca.left, Math.min(ca.right, px)));
  const curXFrac = curX / total;

  // Apply zoom
  const oldLevel = elevZoom.level;
  elevZoom.level = Math.max(1, Math.min(elevZoom.maxLevel, elevZoom.level * factor));

  if (elevZoom.level <= 1) {
    elevZoom.offset = 0;
  } else {
    // Keep cursor position fixed: curXFrac should stay at same screen position
    const windowFrac = 1 / elevZoom.level;
    const maxOffset  = 1 - windowFrac;
    elevZoom.offset  = Math.max(0, Math.min(maxOffset, curXFrac - windowFrac / 2));
    // Normalise to 0–1 of scrollable range
    if (maxOffset > 0) elevZoom.offset = elevZoom.offset / maxOffset;
    else elevZoom.offset = 0;
  }
  elevZoomApply();
}

// Wire mouse wheel on chart-wrap
document.addEventListener('DOMContentLoaded', () => {
  const wrap = document.getElementById('elev-canvas-area') || document.getElementById('chart-wrap');
  if (!wrap) return;
  wrap.addEventListener('wheel', e => {
    if (!elevChart) return;
    e.preventDefault();
    const rect  = wrap.getBoundingClientRect();
    const px    = e.clientX - rect.left;
    const factor = e.deltaY < 0 ? 1.4 : 1 / 1.4;
    elevZoomAtPixel(px, factor);
  }, {passive: false});
});
