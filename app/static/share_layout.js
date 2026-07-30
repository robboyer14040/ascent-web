'use strict';
/* share_layout.js — the split-pane detail layout controller for the share pages
   (tour_share.html + activity_share.html). Wires:
     • the vertical INFO | MAP divider (#info-map-divider) — default 50/50,
     • the ANALYSIS / PHOTOS titlebar drag-resize + disclosure collapse.
   Elements come from _share_detail.html; the elevation strip itself is drawn by
   tour_elev.js. Loaded after that page's map globals exist. Safe if elements are
   absent (guards throughout). */

const ShareLayout = (function () {
  const $ = id => document.getElementById(id);

  // Re-fit the Leaflet map after a pane resize (the page exposes getStageMap()).
  function mapInvalidate() {
    try { const m = window.getStageMap && window.getStageMap(); if (m) m.invalidateSize(); } catch (e) {}
  }
  function elevResize() { try { window.redrawStageElev && window.redrawStageElev(); } catch (e) {} }

  // ── Collapse / expand a titlebar pane (ANALYSIS / PHOTOS) ──────────────────────
  function toggle(which) {
    const wrap = $(which + '-wrap'), btn = $(which + '-disclose');
    if (!wrap) return;
    const collapsed = wrap.classList.toggle('collapsed');
    if (btn) btn.classList.toggle('collapsed', collapsed);
    if (which === 'analysis') elevResize();
    mapInvalidate();
  }

  // Show / hide a whole pane (used per stage-state: overview has no elevation, an
  // uncompleted stage has no photos, etc.).
  function showAnalysis(v) { const w = $('analysis-wrap'); if (w) w.style.display = v ? 'flex' : 'none'; }
  function showPhotos(v)   { const w = $('photos-wrap');   if (w) w.style.display = v ? 'flex' : 'none'; }

  // ── Vertical INFO | MAP divider ────────────────────────────────────────────────
  function wireVDivider() {
    const d = $('info-map-divider'), pane = $('info-pane'), top = $('detail-top');
    if (!d || !pane || !top) return;
    let drag = false, startX = 0, startW = 0;
    const start = x => { drag = true; startX = x; startW = pane.getBoundingClientRect().width; d.classList.add('dragging'); document.body.style.cursor = 'col-resize'; };
    const move  = x => {
      if (!drag) return;
      const total = top.getBoundingClientRect().width;
      const w = Math.max(140, Math.min(total - 140, startW + (x - startX)));
      pane.style.flex = '0 0 ' + w + 'px';
      mapInvalidate();
    };
    const end = () => { if (!drag) return; drag = false; d.classList.remove('dragging'); document.body.style.cursor = ''; mapInvalidate(); };
    d.addEventListener('mousedown', e => { start(e.clientX); e.preventDefault(); });
    document.addEventListener('mousemove', e => move(e.clientX));
    document.addEventListener('mouseup', end);
    d.addEventListener('touchstart', e => { start(e.touches[0].clientX); e.preventDefault(); }, { passive: false });
    d.addEventListener('touchmove',  e => { move(e.touches[0].clientX); e.preventDefault(); }, { passive: false });
    d.addEventListener('touchend', end);
  }

  // ── Titlebar drag-resize (ANALYSIS / PHOTOS) — the bar sits atop its pane, which
  //    grows upward as you drag up (it lives below the flex:1 top region). ─────────
  function wireTitlebar(which) {
    const wrap = $(which + '-wrap'), bar = $(which + '-titlebar');
    if (!wrap || !bar) return;
    let drag = false, startY = 0, startH = 0;
    const start = y => { if (wrap.classList.contains('collapsed')) return; drag = true; startY = y; startH = wrap.getBoundingClientRect().height; bar.classList.add('dragging'); document.body.style.cursor = 'row-resize'; };
    const move  = y => {
      if (!drag) return;
      const h = Math.max(60, Math.min(window.innerHeight * 0.75, startH - (y - startY)));
      wrap.style.height = h + 'px';
      if (which === 'analysis') elevResize();
      mapInvalidate();
    };
    const end = () => { if (!drag) return; drag = false; bar.classList.remove('dragging'); document.body.style.cursor = ''; if (which === 'analysis') elevResize(); };
    bar.addEventListener('mousedown', e => { start(e.clientY); e.preventDefault(); });
    document.addEventListener('mousemove', e => move(e.clientY));
    document.addEventListener('mouseup', end);
    bar.addEventListener('touchstart', e => { start(e.touches[0].clientY); e.preventDefault(); }, { passive: false });
    bar.addEventListener('touchmove',  e => { move(e.touches[0].clientY); e.preventDefault(); }, { passive: false });
    bar.addEventListener('touchend', end);
  }

  function init() { wireVDivider(); wireTitlebar('analysis'); wireTitlebar('photos'); }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { toggle, showAnalysis, showPhotos };
})();
// Exposed on window so inline onclick handlers (in _share_detail.html) and the
// window.ShareLayout checks in tour_stage_detail.js can reach it — a top-level
// `const` binding is not a property of window.
window.ShareLayout = ShareLayout;
