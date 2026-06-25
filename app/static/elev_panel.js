// ── shared elevation-panel helpers ───────────────────────────────────────────
// Used by altitude_chart.js (Activities/Analysis) and stage_elev_chart.js
// (Tour / Tour_share). Neither file should contain its own copy of these.

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
