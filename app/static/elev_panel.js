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
  for (const cand of candidates) {
    if (peaks.every(p => Math.abs(p.x - cand.x) >= minSepX)) {
      peaks.push(cand);
      if (peaks.length >= n) break;
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
      peakList.forEach(pk => {
        const px = xSc.getPixelForValue(xVal(pk));
        const py = ySc.getPixelForValue(pk.y);
        const label = labelFn(pk);
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
        cx.font = 'bold 9px -apple-system,sans-serif';
        const tw = cx.measureText(label).width;
        const bw = tw + 10;
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
      });
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

// Render and position the selection-stats box.
//   box        — element to populate (absolute-positioned inside containerEl)
//   pxLo/pxHi — pixel x of selection edges within containerEl coordinate space
//   ca         — Chart.js chartArea {left, right}
//   headerHtml — pre-built header HTML string (or '')
//   rows       — [[{label,val}, {label,val}], ...] — one pair per grid row
//   hudBtn     — HUD toggle button element (anchors vertical position)
//   containerEl — the CSS offset parent of box
function elevSelRender(box, pxLo, pxHi, ca, headerHtml, rows, hudBtn, containerEl, onMaxClick, onDismiss) {
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
        onMaxClick(parseInt(el.dataset.maxIdx, 10), box);
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
