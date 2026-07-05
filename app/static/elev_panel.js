// ── shared elevation-panel helpers ───────────────────────────────────────────
// Used by altitude_chart.js (Activities/Analysis) and stage_elev_chart.js
// (Tour / Tour_share). Neither file should contain its own copy of these.

// Find the top N altitude peaks in a [{x, y}] points array.
// Minimum separation: 0.25 display-distance-units between peaks.
function findPeaks(points, n) {
  n = n || 3;
  if (points.length < 10) return [];
  const minSepX = 0.25;
  const w = Math.max(5, Math.floor(points.length * 0.02));
  const candidates = [];
  for (let i = 1; i < points.length - 1; i++) {
    const y = points[i].y;
    let isPeak = true;
    const lo = Math.max(0, i - w), hi = Math.min(points.length - 1, i + w);
    for (let j = lo; j <= hi; j++) {
      if (j !== i && points[j].y > y) { isPeak = false; break; }
    }
    if (isPeak) candidates.push({ idx: i, x: points[i].x, y });
  }
  candidates.sort((a, b) => b.y - a.y);
  const peaks = [];
  // Gather a pool larger than `n` (still ≥minSepX apart, highest first) so the
  // label plugin can skip pixel-colliding summits and drop to the next-highest
  // peak, keeping `n` labels on screen.
  const poolMax = n * 4;
  for (const cand of candidates) {
    if (peaks.every(p => Math.abs(p.x - cand.x) >= minSepX)) {
      peaks.push(cand);
      if (peaks.length >= poolMax) break;
    }
  }
  return peaks;
}

// Build a Chart.js plugin that draws peak markers (dot + stem + label bubble).
// opts.xVal(pk)    → x value for getPixelForValue (default: pk.x)
// opts.yScKey      → y scale name to try first (default: 'y')
// opts.labelFn(pk) → bubble text
// opts.isLightFn() → true when chart background is light
function makePeakPlugin(peakList, opts) {
  const xVal      = (opts && opts.xVal)      || (pk => pk.x);
  const yScKey    = (opts && opts.yScKey)    || 'y';
  const labelFn   = (opts && opts.labelFn)   || (pk => String(pk.y));
  const isLightFn = (opts && opts.isLightFn) || (() => false);
  const count     = (opts && opts.count)     || 3;
  return {
    id: 'peakMarkers',
    afterDatasetsDraw(chart) {
      if (!peakList.length) return;
      const { ctx: cx, scales } = chart;
      const xSc = scales.x;
      const ySc = scales[yScKey] || scales.y;
      if (!xSc || !ySc) return;
      const lm = isLightFn();
      const dotR = 3;
      const gap = 4;           // visible px between dot top and oval bottom
      const stemH = dotR + gap; // full stem; dot covers bottom dotR px
      const bh = 16;
      cx.save();
      cx.font = 'bold 9px -apple-system,sans-serif';
      // Track drawn label x-ranges. peakList is sorted highest-first, so the
      // tallest peak wins when two summits sit at nearly the same pixel; a
      // colliding label is skipped and the next-highest peak in the pool takes
      // its place, keeping `count` labels visible (avoids the phantom digit).
      const drawnRanges = [];
      for (const pk of peakList) {
        if (drawnRanges.length >= count) break;
        const px = xSc.getPixelForValue(xVal(pk));
        const py = ySc.getPixelForValue(pk.y);
        const label = labelFn(pk);
        const bw0 = cx.measureText(label).width + 10;
        const lo = px - bw0 / 2, hi = px + bw0 / 2;
        if (drawnRanges.some(r => lo < r.hi && hi > r.lo)) continue;
        drawnRanges.push({ lo, hi });
        // Stem drawn first so dot renders on top, leaving `gap` px visible
        cx.beginPath();
        cx.moveTo(px, py);
        cx.lineTo(px, py - stemH);
        cx.strokeStyle = lm ? 'rgba(0,0,0,.35)' : 'rgba(255,255,255,.5)';
        cx.lineWidth = 1;
        cx.stroke();
        cx.beginPath();
        cx.arc(px, py, dotR, 0, Math.PI * 2);
        cx.fillStyle = lm ? '#555' : '#fff';
        cx.fill();
        const bw = bw0;
        const bx = px - bw / 2;
        const by = py - stemH - bh;
        cx.fillStyle = lm ? 'rgba(0,0,0,.75)' : 'rgba(70,70,70,.95)';
        cx.beginPath();
        cx.roundRect(bx, by, bw, bh, 4);
        cx.fill();
        cx.fillStyle = '#f2f2f7';
        cx.textAlign = 'center';
        cx.textBaseline = 'middle';
        cx.fillText(label, px, by + bh / 2);
      }
      cx.restore();
    },
  };
}

// Render rows into a HUD panel and center it on cursor position `px` within
// the container (clamped so it doesn't overflow the edges).
// rows: [{label, val, color}]
function elevHudRender(panel, rows, px, containerWidth) {
  panel.innerHTML = rows.map(r =>
    `<div class="eh-row"><span class="eh-label">${r.label}</span>` +
    `<span class="eh-val" style="color:${r.color}">${r.val}</span></div>`
  ).join('');
  panel.style.display = 'block';
  const pw = panel.offsetWidth;
  if (px + pw / 2 + 8 > containerWidth) {
    panel.style.left = ''; panel.style.right = (containerWidth - px + 8) + 'px'; panel.style.transform = 'none';
  } else if (px - pw / 2 < 8) {
    panel.style.left = (px + 8) + 'px'; panel.style.right = ''; panel.style.transform = 'none';
  } else {
    panel.style.left = px + 'px'; panel.style.right = ''; panel.style.transform = 'translateX(-50%)';
  }
}

// ── Region-info value formatting + row assembly ───────────────────────────────
// Shared by altitude_chart.js (Activity Analysis) and stage_elev_chart.js (Tour /
// Tour_share). Pure data-in/HTML-out: no DOM, chart, or index-space knowledge.
// Only `U.metric` is available on every page (U.alt/U.speed/U.altUnit are main.js-only),
// so units are inlined here. Every value passed in is already in the user's DISPLAY
// units (m/ft altitude, km/h/mph speed, %, bpm, W, rpm); each page converts before calling.
function elevFmtElapsed(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`
           : `${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
}
const elevFmt = {
  alt:  v => v != null ? `${Math.round(v)} ${U.metric ? 'm' : 'ft'}` : '—',
  clmb: v => v > 0     ? `${Math.round(v)} ${U.metric ? 'm' : 'ft'}` : '—',
  spd:  v => v != null ? `${(+v).toFixed(1)} ${U.metric ? 'km/h' : 'mph'}` : '—',
  hr:   v => v != null ? `${Math.round(v)} bpm` : '—',
  pwr:  v => v != null ? `${Math.round(v)} W`   : '—',
  grad: v => v != null ? `${(+v).toFixed(1)}%`  : '—',
  cad:  v => v != null ? `${Math.round(v)} rpm` : '—',
  distSel: m => U.metric ? (m/1000).toFixed(2)+' km' : (m/1609.344).toFixed(2)+' mi',
};

// Build the [[{label,val,clickIdx?}, {label,val,clickIdx?}], ...] rows for elevSelRender.
//   v.alt     = {min, max, maxIdx}          display altitudes; maxIdx is a page seek index
//   v.climb, v.descent                      display climb/descent (0 → "—")
//   v.grad    = {max, maxIdx, avg}          percent
//   v.hr/power/speed/cadence = {max, avg|mov, maxIdx} | null   (null omits the row;
//                                            this is how uncompleted stages hide metrics)
function elevStatsRows(v) {
  const rows = [
    [{ label:'Min Altitude',  val: elevFmt.alt(v.alt.min) },
     { label:'Max Altitude',  val: elevFmt.alt(v.alt.max),   clickIdx: v.alt.maxIdx }],
    [{ label:'Total Climb',   val: elevFmt.clmb(v.climb) },
     { label:'Total Descent', val: elevFmt.clmb(v.descent) }],
    [{ label:'Max Gradient',  val: elevFmt.grad(v.grad.max), clickIdx: v.grad.maxIdx },
     { label:'Avg Gradient',  val: elevFmt.grad(v.grad.avg) }],
  ];
  if (v.hr)      rows.push([{ label:'Max HR',       val: elevFmt.hr(v.hr.max),       clickIdx: v.hr.maxIdx },      { label:'Avg HR',       val: elevFmt.hr(v.hr.avg) }]);
  if (v.power)   rows.push([{ label:'Max 3s Power', val: elevFmt.pwr(v.power.max),   clickIdx: v.power.maxIdx },   { label:'Avg 3s Power', val: elevFmt.pwr(v.power.avg) }]);
  if (v.speed)   rows.push([{ label:'Max Speed',    val: elevFmt.spd(v.speed.max),   clickIdx: v.speed.maxIdx },   { label:'Mov Speed',    val: elevFmt.spd(v.speed.mov) }]);
  if (v.cadence) rows.push([{ label:'Max Cadence',  val: elevFmt.cad(v.cadence.max), clickIdx: v.cadence.maxIdx }, { label:'Avg Cadence',  val: elevFmt.cad(v.cadence.avg) }]);
  return rows;
}

// Build the stats-popup header HTML.
//   h = null                       → '' (no header)
//   h = {distStr}                  → distance-only header (route-only / no time data)
//   h = {startSec, durSec, distStr}→ Start / Duration / max@ / distance header
function elevHeaderHtml(h) {
  if (!h) return '';
  if (h.startSec == null)
    return `<div class="es-header"><span><span class="es-val">${h.distStr}</span></span></div>`;
  return `<div class="es-header">` +
    `<span><span class="es-lbl">Start </span><span class="es-val">${elevFmtElapsed(h.startSec)}</span></span>` +
    `<span class="es-hdr-dur"><span class="es-lbl">Duration </span><span class="es-val">${elevFmtElapsed(h.durSec)}</span></span>` +
    `<span class="es-maxat" style="display:none"><span class="es-lbl">max@ </span><span class="es-val es-maxat-val"></span></span>` +
    `<span class="es-hdr-dist"><span class="es-val">${h.distStr}</span></span>` +
    `</div>`;
}

// Build the hover-HUD rows for elevHudRender.
//   vals: { time, dist, alt, grad, hr, power, cadence, speed } — display-unit numbers,
//         except `dist` (metres). A field that is `undefined` omits its row; `null` shows
//         "—"; a number is formatted. (Tour omits Time/Altitude/Gradient when absent;
//         Activity shows "—" — the caller picks via undefined vs null.)
//   rowColor(field, value) → css colour for that row (value may be null); lets Activity
//         inject training-zone colours for HR/Power while Tour uses flats.
function elevHudRows(vals, rowColor) {
  const spec = [
    ['time', 'Time', elevFmtElapsed],
    ['dist', 'Distance', elevFmt.distSel],
    ['alt', 'Altitude', elevFmt.alt],
    ['grad', 'Gradient', elevFmt.grad],
    ['hr', 'Heart Rate', elevFmt.hr],
    ['power', 'Power', elevFmt.pwr],
    ['cadence', 'Cadence', elevFmt.cad],
    ['speed', 'Speed', elevFmt.spd],
  ];
  const rows = [];
  for (const [field, label, fmt] of spec) {
    const v = vals[field];
    if (v === undefined) continue;
    rows.push({ label, val: v == null ? '—' : fmt(v), color: rowColor(field, v) });
  }
  return rows;
}

// Compute the full region-info result (rows + header HTML) for a selection [lo, hi].
// The loops (min/max altitude, climb/descent, avg/max gradient, per-metric max/avg/argmax)
// live HERE, once; each page supplies data access + index mapping via `ctx`, whose seek
// indices (`si`) are that page's own space (full-res data index on Activity, downsampled
// chart-point index on Tour). ctx:
//   altAt(si)  → display altitude (m/ft)          gradAt(si) → gradient % (or null)
//   distMAt(si)→ distance in metres
//   metricSamples(field, lo, hi) → { values:[raw], toSeekIdx(localIdx)→si } | null
//        field ∈ {hr,power,speed,cadence}; native resolution (finer than si on Tour), so
//        the fine-resolution argmax is preserved. null ⇒ field absent (row omitted).
//   rangeHeader(lo, hi) → { startSec, durSec, distM } | { distM } | null
// Returns { rows, headerHtml } ready for elevSelRender.
function computeRegionStats(ctx, lo, hi) {
  const mean = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;

  // Altitude min/max + climb/descent (display units, si-space)
  let minAlt = null, maxAlt = null, maxAltIdx = null, climb = 0, descent = 0;
  for (let si = lo; si <= hi; si++) {
    const a = ctx.altAt(si);
    if (a == null) continue;
    if (minAlt == null || a < minAlt) minAlt = a;
    if (maxAlt == null || a > maxAlt) { maxAlt = a; maxAltIdx = si; }
    if (si > lo) {
      const prev = ctx.altAt(si - 1);
      if (prev != null) { const d = a - prev; if (d > 0) climb += d; else descent += -d; }
    }
  }

  // Average gradient: net rise / horizontal run, both in the same length unit → %
  let avgGrad = null;
  if (hi > lo) {
    const rise = ctx.altAt(hi) - ctx.altAt(lo);          // display alt units (m or ft)
    const runM = ctx.distMAt(hi) - ctx.distMAt(lo);       // metres
    const run  = U.metric ? runM : runM / 0.3048;         // → same unit as rise
    if (run > 0) avgGrad = rise / run * 100;
  }

  // Max gradient: argmax |grad|, preferring positive if any positive value is in range
  let maxGrad = null, maxGradIdx = null, best = -Infinity, anyPos = false;
  for (let si = lo; si <= hi; si++) { const g = ctx.gradAt(si); if (g != null && g > 0) { anyPos = true; break; } }
  for (let si = lo; si <= hi; si++) {
    const g = ctx.gradAt(si);
    if (g == null || (anyPos && g <= 0)) continue;
    if (Math.abs(g) > best) { best = Math.abs(g); maxGrad = g; maxGradIdx = si; }
  }

  // Per-point metrics at their native resolution
  const metric = (field, isSpeed) => {
    const s = ctx.metricSamples(field, lo, hi);
    if (!s) return null;
    const { values, toSeekIdx } = s;
    const valid = values.filter(v => v > 0);
    if (!valid.length) return null;
    let bi = -1, bv = -Infinity;
    for (let i = 0; i < values.length; i++) if (values[i] > 0 && values[i] > bv) { bv = values[i]; bi = i; }
    const maxIdx = bi >= 0 ? toSeekIdx(bi) : null;
    if (isSpeed) {
      const mov = values.filter(v => v >= 0.5);
      const disp = mph => U.metric ? mph * 1.60934 : mph;
      return { max: disp(Math.max(...valid)), mov: mov.length ? disp(mean(mov)) : null, maxIdx };
    }
    return { max: Math.max(...valid), avg: mean(valid), maxIdx };
  };

  const rows = elevStatsRows({
    alt: { min: minAlt, max: maxAlt, maxIdx: maxAltIdx },
    climb, descent,
    grad: { max: maxGrad, maxIdx: maxGradIdx, avg: avgGrad },
    hr: metric('hr'), power: metric('power'), speed: metric('speed', true), cadence: metric('cadence'),
  });

  const h = ctx.rangeHeader(lo, hi);
  const headerHtml = elevHeaderHtml(h && (h.startSec != null
    ? { startSec: h.startSec, durSec: h.durSec, distStr: elevFmt.distSel(h.distM) }
    : { distStr: elevFmt.distSel(h.distM) }));

  return { rows, headerHtml };
}

// ── Puck "stick" guard ────────────────────────────────────────────────────────
// After a "max" seek label in the stats popup is clicked, the puck jumps to that
// point and the popup is dismissed (see elevSelRender). Without this guard the very
// next mousemove would immediately yank the puck back to the cursor. We freeze the
// puck until the cursor crosses its x-position: if the cursor sits left of the puck,
// updates resume once it moves right of the puck, and vice-versa.
// Each page's mousemove handler calls puckStickBlocks() before moving the puck; each
// page's max-label handler calls puckStickArm() once the puck is in place.
let _puckStickX = null;   // puck clientX while frozen, or null when inactive
let _puckStickSide = 0;   // sign of (cursorX − puckX) captured at arm time

function puckStickArm(puckClientX, cursorClientX) {
  _puckStickX = puckClientX;
  _puckStickSide = Math.sign(cursorClientX - puckClientX);
}
function puckStickReset() { _puckStickX = null; _puckStickSide = 0; }
function puckStickBlocks(cursorClientX) {
  if (_puckStickX == null) return false;
  const s = Math.sign(cursorClientX - _puckStickX);
  if (_puckStickSide === 0) {              // cursor started on the puck: adopt first real side
    if (s === 0) { puckStickReset(); return false; }
    _puckStickSide = s;
    return true;
  }
  if (s === _puckStickSide) return true;   // still on the same side → stay frozen
  puckStickReset();                        // crossed to the other side → release
  return false;
}

// Render and position the selection-stats box.
//   box        — element to populate (absolute-positioned inside containerEl)
//   pxLo/pxHi — pixel x of selection edges within containerEl coordinate space
//   ca         — Chart.js chartArea {left, right}
//   headerHtml — pre-built header HTML string (or '')
//   rows       — [[{label,val}, {label,val}], ...] — one pair per grid row
//   hudBtn     — HUD toggle button element (anchors vertical position)
//   containerEl — the CSS offset parent of box
function elevSelRender(box, pxLo, pxHi, ca, headerHtml, rows, hudBtn, containerEl, onMaxClick, onDismiss) {
  puckStickReset();  // showing a fresh popup clears any pending puck freeze
  let html = headerHtml + '<div class="es-grid">';
  for (const [L, R] of rows) {
    const lCls  = (onMaxClick && L.clickIdx != null) ? ' es-max-lbl' : '';
    const lData = (onMaxClick && L.clickIdx != null) ? ` data-max-idx="${L.clickIdx}"` : '';
    const rCls  = (onMaxClick && R.clickIdx != null) ? ' es-max-lbl' : '';
    const rData = (onMaxClick && R.clickIdx != null) ? ` data-max-idx="${R.clickIdx}"` : '';
    html += `<div class="es-row"><span class="es-lbl${lCls}"${lData}>${L.label}</span><span class="es-val">${L.val}</span></div>`;
    html += `<div class="es-row"><span class="es-lbl${rCls}"${rData}>${R.label}</span><span class="es-val">${R.val}</span></div>`;
  }
  html += '</div>';
  box.innerHTML = html;
  box.style.display = 'block';

  // Keep onDismiss up to date (may change between renders).
  box._onDismiss = onDismiss || null;

  // Stop mousedown/touchstart from bubbling to the chart wrap on every interaction with the
  // pane — prevents any touch or click anywhere in the pane from starting a drag that would
  // clear the selection. Added once per box element (flag prevents stacking on re-renders).
  if (!box._selPaneStopAdded) {
    box.addEventListener('mousedown',  e => { e.stopPropagation(); e.preventDefault(); });
    box.addEventListener('touchstart', e => e.stopPropagation(), {passive: true});
    // Tapping anywhere in the pane (except underlined "max" seek labels) dismisses it.
    box.addEventListener('click', e => {
      if (!e.target.closest('.es-max-lbl') && box._onDismiss) box._onDismiss();
    });
    box._selPaneStopAdded = true;
  }

  if (onMaxClick) {
    box.querySelectorAll('.es-max-lbl').forEach(el => {
      el.addEventListener('click', e => {
        e.stopPropagation();
        onMaxClick(parseInt(el.dataset.maxIdx, 10), box, e);
        box.style.display = 'none';   // dismiss the popup; the puck now "sticks" (see guard above)
      });
    });
  }

  const bw  = box.offsetWidth;
  const gap = 8;
  const spL = pxLo - ca.left - gap, spR = ca.right - pxHi - gap;
  box.style.left = ((spL >= bw || spL >= spR)
    ? Math.max(ca.left + 2, pxLo - gap - bw)
    : Math.min(ca.right - bw - 2, pxHi + gap)) + 'px';
  box.style.top = ((hudBtn && containerEl)
    ? Math.max(4, Math.round(hudBtn.getBoundingClientRect().top - containerEl.getBoundingClientRect().top))
    : 4) + 'px';
}

// ── Shared drag-select + hover interaction ────────────────────────────────────
// Owns the event machinery both pages share: hover dispatch (with the puck-stick
// guard), the drag-to-select state machine (mouse + touch, 4px click threshold),
// optional wheel zoom, and optional outside-tap dismiss. Everything page-specific —
// element styling, data/index mapping, dot movement, HUD + stats rendering, selection
// side-effects — is delegated through `ctx`. Re-callable: it removes its own previously
// attached listeners first, so chart rebuilds don't stack handlers.
//
// ctx:
//   container                          element the listeners attach to
//   chartArea()                        → {left,right} in container px, or null (no chart/data)
//   clientXToIndex(clientX)            → integer seek index (page owns px→index + axis mode)
//   hudActive()                        → bool
//   hover(clientX)                     non-HUD hover: move puck + map dot (+ hide panel)
//   showHud(clientX)                   HUD hover: full panel + crosshair + dots
//   hideHover()                        pointer left the strip
//   onDragStart()                      a drag began: hide HUD, reset dependent UI
//   applySelection(lo, hi)             ordered seek indices; page shades + shows stats
//   clearSelection()
//   dragRectStart(px)/dragRectUpdate(loPx,hiPx)/dragRectHide()   the drag band overlay
//   suppressHoverWhileDragging         bool (Tour true, Activity false)
//   onWheel(e)                         optional — if present, wheel is wired (Tour zoom)
//   dismissBox()/dismissKeep(target)   optional — if present, tap-outside dismiss (Activity)
function elevRegionInteraction(ctx) {
  const el = ctx.container;
  if (!el) return;

  // Drop listeners from a previous call on this element (chart rebuilds re-invoke us).
  const prev = el._eriH;
  if (prev) {
    el.removeEventListener('mousemove', prev.mm);
    el.removeEventListener('mouseleave', prev.ml);
    el.removeEventListener('mousedown', prev.md);
    el.removeEventListener('touchstart', prev.ts);
    if (prev.wh) el.removeEventListener('wheel', prev.wh);
  }

  let dragging = false;
  const containerPx = clientX => clientX - el.getBoundingClientRect().left;

  function pxInChart(clientX) {
    const ca = ctx.chartArea(); if (!ca) return null;
    const px = containerPx(clientX);
    return (px < ca.left || px > ca.right) ? null : { px, ca };
  }

  function onMouseMove(e) {
    if (dragging && ctx.suppressHoverWhileDragging) return;
    if (puckStickBlocks(e.clientX)) return;   // frozen after a max-label click until cursor crosses puck
    if (ctx.hudActive()) ctx.showHud(e.clientX);
    else ctx.hover(e.clientX);
  }
  function onMouseLeave() { if (!dragging) ctx.hideHover(); }

  // Shared drag driver (mouse + touch). getX(ev) extracts the pointer clientX.
  function runDrag(startClientX, startPx, ca, moveEvt, endEvt, getX, opt) {
    dragging = true;
    ctx.onDragStart();
    const startIdx = ctx.clientXToIndex(startClientX);
    ctx.dragRectStart(startPx);
    function onTrack(ev) {
      const clientX = getX(ev); if (clientX == null) return;
      const cur = containerPx(clientX);
      ctx.dragRectUpdate(Math.max(ca.left, Math.min(startPx, cur)), Math.min(ca.right, Math.max(startPx, cur)));
      const curIdx = ctx.clientXToIndex(clientX);
      ctx.applySelection(Math.min(startIdx, curIdx), Math.max(startIdx, curIdx));
    }
    function onEnd(ev) {
      document.removeEventListener(moveEvt, onTrack);
      document.removeEventListener(endEvt, onEnd);
      dragging = false;
      const clientX = getX(ev);
      const cur = clientX == null ? startPx : containerPx(clientX);
      if (Math.abs(cur - startPx) > 4) {
        const curIdx = ctx.clientXToIndex(clientX);
        ctx.applySelection(Math.min(startIdx, curIdx), Math.max(startIdx, curIdx));
      } else { ctx.dragRectHide(); ctx.clearSelection(); }
    }
    document.addEventListener(moveEvt, onTrack, opt);
    document.addEventListener(endEvt, onEnd, opt);
  }

  function onMouseDown(e) {
    const hit = pxInChart(e.clientX); if (!hit) return;
    e.preventDefault();
    runDrag(e.clientX, hit.px, hit.ca, 'mousemove', 'mouseup', ev => ev.clientX, false);
  }

  function onTouchStart(e) {
    const t = e.touches[0]; if (!t) return;
    if (ctx.hudActive()) {                       // HUD mode: touch drags the HUD, no selection
      ctx.showHud(t.clientX);
      const tm = ev => { const t2 = ev.touches[0]; if (t2) ctx.showHud(t2.clientX); };
      const te = () => { document.removeEventListener('touchmove', tm); document.removeEventListener('touchend', te); };
      document.addEventListener('touchmove', tm, { passive: true });
      document.addEventListener('touchend', te, { passive: true });
      return;
    }
    const hit = pxInChart(t.clientX); if (!hit) return;   // no preventDefault → page can still scroll
    runDrag(t.clientX, hit.px, hit.ca, 'touchmove', 'touchend',
      ev => { const t2 = (ev.touches && ev.touches[0]) || (ev.changedTouches && ev.changedTouches[0]); return t2 ? t2.clientX : null; },
      { passive: true });
  }

  el.addEventListener('mousemove', onMouseMove);
  el.addEventListener('mouseleave', onMouseLeave);
  el.addEventListener('mousedown', onMouseDown);
  el.addEventListener('touchstart', onTouchStart, { passive: true });
  if (ctx.onWheel) el.addEventListener('wheel', ctx.onWheel, { passive: false });
  el._eriH = { mm: onMouseMove, ml: onMouseLeave, md: onMouseDown, ts: onTouchStart, wh: ctx.onWheel };

  // Tap-outside dismiss (Activity): a tap that isn't a drag and lands outside the stats
  // box and any "keep" element (toolbar) clears the selection. Document-level listeners
  // are added once per element (they close over ctx.clearSelection, which is stable).
  if (ctx.dismissBox && !el._eriTap) {
    el._eriTap = true;
    let tapX = 0, tapY = 0;
    document.addEventListener('touchstart', ev => { const t = ev.touches[0]; if (t) { tapX = t.clientX; tapY = t.clientY; } }, { passive: true });
    document.addEventListener('touchend', ev => {
      const box = ctx.dismissBox(); if (!box || box.style.display === 'none') return;
      const t = ev.changedTouches[0]; if (!t) return;
      if (Math.abs(t.clientX - tapX) > 12 || Math.abs(t.clientY - tapY) > 12) return;
      const target = document.elementFromPoint(t.clientX, t.clientY);
      if (!box.contains(target) && !ctx.dismissKeep(target)) ctx.clearSelection();
    }, { passive: true });
  }
}
