// Shared stage-detail helpers for the tour pages (tour.html + tour_share.html).
// These render into #stage-detail / the mobile Analysis-tab panels identically on
// both pages; per-page differences (data endpoints, lightbox fn) come in via ctx.
//
// Requires these globals (present on both pages): U (units), esc (HTML escape).

// Activity ids we've already auto-synced from Strava this session — so a re-render
// after a sync (or a no-op sync) never re-triggers the auto-sync into a loop.
const _autoSyncedActs = new Set();

// ── Overview photo-split state (non-phone whole-tour view) ────────────────────
// The divider under the AI Summary drags the top (info+AI) pane's height; the
// photo grid below takes the rest. The chosen height persists per browser.
let _tourOvTopH = (() => {
  try { const v = parseFloat(localStorage.getItem('ascent-tour-ov-split')); return isFinite(v) ? v : null; }
  catch (e) { return null; }
})();
let _ovDrag = null;          // { el, top, divider, startY, startH } while dragging
let _ovGlobalWired = false;  // document-level drag listeners attached once
function _ovWireGlobal() {
  if (_ovGlobalWired) return;
  _ovGlobalWired = true;
  const move = y => {
    if (!_ovDrag) return;
    const { el, top, startY, startH } = _ovDrag;
    const h = Math.max(60, Math.min(el.clientHeight - 80, startH + (y - startY)));
    top.style.flex = `0 0 ${h}px`;
    _tourOvTopH = h;
  };
  const end = () => {
    if (!_ovDrag) return;
    _ovDrag.divider.classList.remove('dragging');
    document.body.style.cursor = '';
    _ovDrag = null;
    try { localStorage.setItem('ascent-tour-ov-split', String(_tourOvTopH)); } catch (e) {}
  };
  document.addEventListener('mousemove', e => move(e.clientY));
  document.addEventListener('mouseup', end);
  document.addEventListener('touchmove', e => { if (_ovDrag) { move(e.touches[0].clientY); e.preventDefault(); } }, { passive: false });
  document.addEventListener('touchend', end);
}

const TourStageDetail = {

  // ── AI stage summary ────────────────────────────────────────────────────────
  // Generation takes many seconds, so the endpoint answers 202 {pending} and
  // generates in the background; we poll until it lands. Holding one long
  // request open instead used to die whenever the machine scaled to zero
  // mid-flight, leaving the card spinning forever.
  //
  // ctx: { url(force), refreshable, onRefresh }
  stageSummary(el, ctx) {
    if (!el) return;
    const btn = ctx.refreshable
      ? '<button id="stage-ai-summary-refresh" title="Regenerate summary" style="background:none;border:none;cursor:pointer;color:var(--muted);font-size:13px;padding:0;line-height:1;flex-shrink:0">↺</button>'
      : '';
    el.insertAdjacentHTML('beforeend',
      '<div class="ai-card" id="stage-ai-summary-card">' +
      '<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px">' +
        '<div class="ai-card-label" style="margin-bottom:0">AI Stage Summary</div>' + btn +
      '</div>' +
      '<div class="ai-card-body ai-card-loading" id="stage-ai-summary-text">Generating summary…</div>' +
      '</div>');
    const refresh = document.getElementById('stage-ai-summary-refresh');
    if (refresh) refresh.onclick = () => TourStageDetail.stageSummaryLoad(ctx, true);
    TourStageDetail.stageSummaryLoad(ctx, false);
  },

  stageSummaryLoad(ctx, force) {
    const card = () => document.getElementById('stage-ai-summary-card');
    const txt  = () => document.getElementById('stage-ai-summary-text');
    const btn  = () => document.getElementById('stage-ai-summary-refresh');
    const t0 = Date.now();
    const LIMIT = 150000;   // give up after ~2.5min of polling

    const start = txt();
    if (start) { start.textContent = 'Generating summary…'; start.classList.add('ai-card-loading'); }
    const b0 = btn(); if (b0) b0.style.opacity = '0.4';

    const settle = (text, drop) => {
      const c = card(); if (!c) return;
      if (drop) { c.remove(); return; }
      const t = txt(); if (t) { t.textContent = text; t.classList.remove('ai-card-loading'); }
      const b = btn(); if (b) b.style.opacity = '';
    };
    const again = () => {
      if (Date.now() - t0 < LIMIT) { setTimeout(poll, 3000); return true; }
      return false;
    };

    // force applies to the first request only — re-sending it on every poll
    // would restart generation the moment it finished.
    let f = force;
    const poll = () => {
      fetch(ctx.url(f))
        .then(r => { f = false; return r; })
        .then(r => {
          if (r.status === 202) return { pending: true };          // still generating
          if (r.status === 404) return { none: true };             // nothing to show
          if (!r.ok) return { failed: true };
          return r.json();
        })
        .then(d => {
          if (d.pending) { if (!again()) settle('Summary is taking longer than expected.'); return; }
          if (d.none)    { settle(null, true); return; }
          if (d.failed)  { if (!again()) settle('Summary unavailable.'); return; }
          settle(d.summary || 'Summary unavailable.');
        })
        // A dropped connection (cold start, sleeping machine) is worth retrying.
        .catch(() => { if (!again()) settle('Summary unavailable.'); });
    };
    poll();
  },

  // Mobile Analysis tab: move the activity stat chips into a collapsed "Performance"
  // section under the AI summary. Idempotent — safe to call after each render.
  enhanceAnalysis() {
    const el = document.getElementById('stage-detail');
    if (!el || el.querySelector('.tour-stats-section')) return;
    const grid = el.querySelector('.act-stats-row .stats-grid') || el.querySelector('.stats-grid');
    if (!grid) return;
    // Anchor may BE the grid itself (tour_share has no .act-stats-row wrapper). Insert
    // the section first, then move the grid into it — moving first would make the grid
    // its own ancestor's target and throw HierarchyRequestError.
    const anchor = grid.closest('.act-stats-row') || grid;
    const section = document.createElement('div');
    section.className = 'tour-stats-section';
    section.innerHTML = '<button type="button" class="tour-stats-bar"><span>Performance</span>' +
      '<span class="tour-stats-arrow">▾</span></button><div class="tour-stats-pane"></div>';
    anchor.insertAdjacentElement('afterend', section);
    section.querySelector('.tour-stats-pane').appendChild(grid);
    section.querySelector('.tour-stats-bar').addEventListener('click', function () {
      section.classList.toggle('open');   // CSS rotates the triangle
    });
  },

  // Tour overview (INFO tab / no stage selected): 7 stat chips + start/end/last-completed
  // locations. Identical on both pages. ctx: { stages, pointsCache, cacheId, renderAi(el) }.
  // The AI summary differs (owner can regenerate; share shows the cached one) so it comes
  // in via ctx.renderAi.
  overview(el, ctx) {
    const stages = ctx.stages || [];
    if (!stages.length) { el.classList.remove('tour-ov-split'); el.innerHTML = ''; return; }
    const statStages = _dedupeStatStages(stages, ctx.pointsCache);
    const done       = statStages.filter(s => s.completion);
    const totalDist  = statStages.reduce((s, x) => s + (x.distance_mi || 0), 0);
    const totalClimb = statStages.reduce((s, x) => s + (x.climb_ft || 0), 0);
    const doneDist   = done.reduce((s, x) => s + (x.completion?.distance_mi ?? x.distance_mi ?? 0), 0);
    const doneClimb  = done.reduce((s, x) => s + (x.completion?.climb_ft ?? x.climb_ft ?? 0), 0);
    const pctD = totalDist  > 0 ? Math.round(doneDist / totalDist * 100) : 0;
    const pctC = totalClimb > 0 ? Math.round(doneClimb / totalClimb * 100) : 0;
    const n = statStages.length || 1, avgDist = totalDist / n, avgClimb = totalClimb / n;
    const maxDistIdx  = stages.reduce((mi, x, i) => (x.distance_mi || 0) > (stages[mi].distance_mi || 0) ? i : mi, 0);
    const maxClimbIdx = stages.reduce((mi, x, i) => (x.climb_ft || 0) > (stages[mi].climb_ft || 0) ? i : mi, 0);
    const maxDist = stages[maxDistIdx]?.distance_mi || 0, maxClimb = stages[maxClimbIdx]?.climb_ft || 0;
    const alt = (val, mi) => U.metric ? val.toFixed(1) + ' mi' : (val * 1.60934).toFixed(1) + ' km';
    const altC = ft => U.metric ? Math.round(ft) + ' ft' : Math.round(ft * 0.3048) + ' m';
    const chips =
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Stages</div><div class="sc-val">${done.length} / ${statStages.length}</div><div class="sc-sub">completed</div></div>` +
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Total Dist</div><div class="sc-val">${U.distS(totalDist)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${alt(totalDist)}</div><div class="sc-sub">${U.distS(doneDist)} done (${pctD}%)</div></div>` +
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Total Ascent</div><div class="sc-val">${U.climbS(totalClimb)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${altC(totalClimb)}</div><div class="sc-sub">${U.climbS(doneClimb)} done (${pctC}%)</div></div>` +
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Avg Dist</div><div class="sc-val">${U.distS(avgDist)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${alt(avgDist)}</div><div class="sc-sub">per stage</div></div>` +
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Avg Ascent</div><div class="sc-val">${U.climbS(avgClimb)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${altC(avgClimb)}</div><div class="sc-sub">per stage</div></div>` +
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Max Stage Dist</div><div class="sc-val">${U.distS(maxDist)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${alt(maxDist)}</div><div class="sc-sub">Stage ${stageDisplayNum(stages[maxDistIdx], stages)}</div></div>` +
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Max Ascent</div><div class="sc-val">${U.climbS(maxClimb)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${altC(maxClimb)}</div><div class="sc-sub">Stage ${stageDisplayNum(stages[maxClimbIdx], stages)}</div></div>`;
    let html = `<div class="stats-grid" style="grid-template-columns:repeat(7,1fr);margin-bottom:14px">${chips}</div>`;
    const first = stages[0], last = stages[stages.length - 1];
    const lastDone = done.length ? done[done.length - 1] : null;
    const lastStagePts = last ? (ctx.pointsCache[String(last.id)] || []) : [];
    const lastPt = lastStagePts.length ? lastStagePts[lastStagePts.length - 1] : null;
    html += `<div style="display:flex;flex-direction:column;gap:6px">`;
    if (first?.start_lat != null) html += `<div style="font-size:12px"><span style="color:var(--muted);margin-right:6px">Start:</span><span id="ov-start-loc">Loading…</span></div>`;
    if (lastPt) html += `<div style="font-size:12px"><span style="color:var(--muted);margin-right:6px">End:</span><span id="ov-end-loc">Loading…</span></div>`;
    if (lastDone && lastDone.start_lat != null) html += `<div style="font-size:12px"><span style="color:var(--muted);margin-right:6px">Last completed:</span><span id="ov-last-loc">Loading…</span></div>`;
    html += `</div>`;
    // Photo placement differs by layout:
    //  • Split-pane share layout (#photos-body present) → the separate PHOTOS pane.
    //  • tour.html owner layout → the in-panel tour-ov-split (info top / photos bottom).
    const splitPane = !!document.getElementById('photos-body');
    const usePhotos = !splitPane && !!ctx.photos && stages.some(s => s.completion && s.completion.activity_id);
    let topEl;
    if (usePhotos) {
      el.classList.add('tour-ov-split');
      el.innerHTML =
        `<div class="tour-ov-top">${html}</div>` +
        `<div class="tour-ov-divider" title="Drag to resize photos"></div>` +
        `<div class="tour-ov-photos"><div class="tour-ov-photos-label">Photos</div><div class="tour-ov-photos-grid"></div></div>`;
      topEl = el.querySelector('.tour-ov-top');
    } else {
      el.classList.remove('tour-ov-split');
      el.innerHTML = html;
      topEl = el;
    }
    const cid = ctx.cacheId;
    const geocacheFill = (lat, lon, elId, cacheKey) => {
      const ls = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
      const fill = (e, loc) => { e.innerHTML = loc ? LocationSummary.linkHTML(loc, { lat, lon }) : '—'; };
      const cached = ls(cacheKey);
      if (cached) { const e = document.getElementById(elId); if (e) fill(e, cached); return; }
      reverseGeocode(lat, lon).then(loc => {
        if (loc) { try { localStorage.setItem(cacheKey, loc); } catch (e) {} }
        const e = document.getElementById(elId); if (e) fill(e, loc);
      });
    };
    if (first?.start_lat != null) geocacheFill(first.start_lat, first.start_lon, 'ov-start-loc', `tour-ov-start-${cid}`);
    if (lastPt) geocacheFill(lastPt[0], lastPt[1], 'ov-end-loc', `tour-ov-endpt-${cid}`);
    if (lastDone?.start_lat != null) geocacheFill(lastDone.start_lat, lastDone.start_lon, 'ov-last-loc', `tour-ov-last-${lastDone.id}`);
    if (ctx.renderAi) ctx.renderAi(topEl);
    if (usePhotos) { this._ovApplySplit(el); this._ovInitDivider(el); this._ovLoadPhotos(el, ctx); }
    // Split-pane share layout: photos go to the separate PHOTOS pane (all-stage media);
    // no elevation for the whole-tour overview, so hide the ANALYSIS pane.
    if (splitPane) {
      if (window.ShareLayout) ShareLayout.showAnalysis(false);
      if (ctx.photos) this._collectStageMedia(stages).then(m => this.photosPane(m, ctx.photos));
      else this.photosPane(null);
    }
  },

  // ── Overview photo split: apply saved/default top-pane height ────────────────
  _ovApplySplit(el) {
    const top = el.querySelector('.tour-ov-top');
    if (!top) return;
    const ch = el.clientHeight || 500;
    let h = _tourOvTopH != null ? _tourOvTopH : Math.round(ch * 0.52);
    h = Math.max(60, Math.min(ch - 80, h));
    top.style.flex = `0 0 ${h}px`;
  },

  // Wire the divider drag (mouse + touch). Document-level move/up handlers are
  // attached once globally; the divider only records the drag start.
  _ovInitDivider(el) {
    _ovWireGlobal();
    const divider = el.querySelector('.tour-ov-divider');
    const top = el.querySelector('.tour-ov-top');
    if (!divider || !top) return;
    const start = y => {
      _ovDrag = { el, top, divider, startY: y, startH: top.getBoundingClientRect().height };
      divider.classList.add('dragging');
      document.body.style.cursor = 'row-resize';
    };
    divider.addEventListener('mousedown', e => { start(e.clientY); e.preventDefault(); });
    divider.addEventListener('touchstart', e => { start(e.touches[0].clientY); e.preventDefault(); }, { passive: false });
  },

  // Collect every completed stage's activity media (de-duped, in stage order) into
  // one combined list. Returns a Promise<media[]>. Shared by the desktop split and
  // the phone all-stages Photos tab.
  _collectStageMedia(stages) {
    const seen = new Set(), actIds = [];
    (stages || []).forEach(s => {
      const id = s.completion && s.completion.activity_id;
      if (id && !seen.has(id)) { seen.add(id); actIds.push(id); }
    });
    return Promise.all(actIds.map(id => Lightbox.fetchMedia(id)))
      .then(lists => { const media = []; lists.forEach(l => l.forEach(m => media.push(m))); return media; });
  },

  // Open the shared Lightbox over `media`; after a caption save, re-render the grid
  // in place (same sizing) so its caption text updates. p: { captionEdit, onCaptionSave? }.
  _openPhotoGrid(media, idx, p, gridEl, gridOpts) {
    Lightbox.open(media, idx, {
      download: true,
      captionEdit: !!p.captionEdit,
      onCaptionSave: p.onCaptionSave
        ? async (item, caption) => {
            await p.onCaptionSave(item, caption);
            item.caption = caption || undefined;
            Lightbox.renderGrid(gridEl, media, i => this._openPhotoGrid(media, i, p, gridEl, gridOpts), gridOpts);
          }
        : undefined,
    });
  },

  // Desktop whole-tour split: fill the photo grid below the divider. ctx.photos:
  // { captionEdit, onCaptionSave?, setMedia? }.
  _ovLoadPhotos(el, ctx) {
    const gridEl = el.querySelector('.tour-ov-photos-grid');
    const labelEl = el.querySelector('.tour-ov-photos-label');
    if (!gridEl) return;
    gridEl.innerHTML = '<div class="tour-ov-photos-empty">Loading photos…</div>';
    this._collectStageMedia(ctx.stages).then(media => {
      if (ctx.photos.setMedia) ctx.photos.setMedia(media);
      if (!media.length) { gridEl.innerHTML = '<div class="tour-ov-photos-empty">No photos yet.</div>'; return; }
      if (labelEl) labelEl.textContent = `Photos · ${media.length}`;
      const opts = { colW: '110px', thumbH: 110 };
      Lightbox.renderGrid(gridEl, media, i => this._openPhotoGrid(media, i, ctx.photos, gridEl, opts), opts);
    }).catch(() => { gridEl.innerHTML = '<div class="tour-ov-photos-empty">Failed to load photos.</div>'; });
  },

  // Phone all-stages Photos tab (level-1 overview). Renders every stage's photos/videos
  // into `el` as a thumbnail grid — identical grid + lightbox to the per-stage Photos
  // tab (default 3-column sizing). ctx: { stages, captionEdit, onCaptionSave?, setMedia? }.
  allPhotos(el, ctx) {
    el.innerHTML = '<div class="tour-tab-empty">Loading photos…</div>';
    this._collectStageMedia(ctx.stages).then(media => {
      if (ctx.setMedia) ctx.setMedia(media);
      if (!media.length) { el.innerHTML = '<div class="tour-tab-empty">No photos yet.</div>'; return; }
      el.innerHTML = '';
      Lightbox.renderGrid(el, media, i => this._openPhotoGrid(media, i, ctx, el, {}));
    }).catch(() => { el.innerHTML = '<div class="tour-tab-empty">Failed to load photos.</div>'; });
  },

  // Prev/next stage nav buttons (the small ←/→ pair). Shared by both pages.
  // Uses the page-global selectStage(); `stages` is the ordered stage list.
  stageNav(stage, stages) {
    const i = stages.findIndex(s => String(s.id) === String(stage.id));
    const prev = i > 0 ? stages[i - 1] : null;
    const next = (i >= 0 && i < stages.length - 1) ? stages[i + 1] : null;
    const btn = (s, arrow) => s
      ? `<button onclick="selectStage('${s.id}')" title="Stage ${stageDisplayNum(s, stages)}" style="background:none;border:1px solid var(--border2);border-radius:4px;width:26px;height:24px;font-size:13px;color:var(--text);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${arrow}</button>`
      : `<button disabled style="background:none;border:1px solid var(--border2);border-radius:4px;width:26px;height:24px;font-size:13px;color:var(--muted);display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;opacity:.3">${arrow}</button>`;
    return `<div style="display:flex;gap:4px;margin-right:4px;flex-shrink:0">${btn(prev, '←')}${btn(next, '→')}</div>`;
  },

  // Full stage header: "Stage N: name" + the prev/next nav. Shared by both pages
  // (Tour_share uses it for completed stages too; Tours builds a custom avatar
  // header for completed stages and calls stageNav() directly).
  stageHeader(stage, stages) {
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px"><div style="font-size:15px;font-weight:600;line-height:1.3;flex:1">Stage ${stageDisplayNum(stage, stages)}: ${esc(stage.name)}</div>${this.stageNav(stage, stages)}</div>`;
  },

  // Uncompleted-stage detail: header + estimated-route chips + trajectory/GPX/forecast.
  // Identical on both pages; per-page bits come via ctx:
  //   { stage, stages, pts, gpxHref, forecastEndpoint,
  //     renderTrajectory(pts),      // each page keeps its own geocode strategy;
  //                                 // fills #stage-loc-text
  //     afterForecastHtml }         // optional HTML appended inside the block
  // After this returns, the caller appends page-specific AI cards / buttons to `el`.
  estimatedRoute(el, ctx) {
    const { stage, stages, pts } = ctx;
    const distP = U.metric ? (+(stage.distance_mi * 1.60934).toFixed(1)) + ' km' : (+stage.distance_mi).toFixed(1) + ' mi';
    const distS = U.metric ? (+stage.distance_mi.toFixed(1)) + ' mi' : (+(stage.distance_mi * 1.60934).toFixed(1)) + ' km';
    const climP = U.metric ? Math.round(stage.climb_ft * 0.3048) + ' m' : Math.round(stage.climb_ft) + ' ft';
    const climS = U.metric ? Math.round(stage.climb_ft) + ' ft' : Math.round(stage.climb_ft * 0.3048) + ' m';
    const { gainFt: bigFt, startKm: bigKm } = biggestClimbInfo(pts);
    const bigP = U.metric ? Math.round(bigFt * 0.3048) + ' m' : Math.round(bigFt) + ' ft';
    const bigS = U.metric ? Math.round(bigFt) + ' ft' : Math.round(bigFt * 0.3048) + ' m';
    const bigAt = U.metric ? `at ${bigKm.toFixed(1)} km` : `at ${(bigKm * 0.621371).toFixed(1)} mi`;
    const chip4 = (label, val, altSub, bot) =>
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">${label}</div><div class="sc-val">${val}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${altSub}</div><div class="sc-sub">${bot}</div></div>`;

    let html = this.stageHeader(stage, stages);
    html += `<div class="section-label" style="margin-bottom:6px">Estimated Route</div>`;
    html += `<div class="stats-grid est-stats" style="grid-template-columns:repeat(${bigFt > 0 ? 3 : 2},1fr);margin-bottom:10px">`;
    html += chip4('Distance', distP, distS, '&nbsp;');
    html += chip4('Total Ascent', climP, climS, '&nbsp;');
    if (bigFt > 0) html += chip4('Biggest Ascent', bigP, bigS, bigAt);
    html += `</div>`;
    html += `<div style="display:flex;align-items:baseline;gap:10px;min-height:1.4em">
      <div style="flex:1;font-size:11px;color:var(--muted)"><span style="font-size:10px;text-transform:uppercase;letter-spacing:.06em">Trajectory:</span> <span id="stage-loc-text">Loading…</span></div>
      <a href="${ctx.gpxHref}" download style="flex-shrink:0;font-size:11px;font-weight:600;color:#f97316;border:1px solid #f97316;border-radius:4px;padding:2px 8px;text-decoration:none;line-height:1.6">↓ GPX</a>
    </div>`;
    html += `<div style="margin-top:6px;font-size:11px;color:var(--muted)"><span style="font-size:10px;text-transform:uppercase;letter-spacing:.06em">Forecast:</span> <span id="stage-forecast-text">Loading…</span></div>`;
    html += ctx.afterForecastHtml || '';
    el.innerHTML = html;

    ctx.renderTrajectory(pts);

    fetch(ctx.forecastEndpoint)
      .then(r => r.json())
      .then(d => {
        const txt = document.getElementById('stage-forecast-text');
        if (!txt) return;
        if (d.out_of_range) { txt.textContent = 'unavailable'; return; }
        const fc = d.forecast;
        if (!fc) { txt.textContent = ''; return; }
        const parts = [];
        if (fc.description) parts.push(fc.description);
        if (fc.temp_max_f != null && fc.temp_min_f != null) parts.push(U.tempS(fc.temp_max_f) + ' / ' + U.tempS(fc.temp_min_f));
        if (fc.wind_kph != null) parts.push('Wind ' + U.windS(fc.wind_kph));
        if (fc.precip_mm > 0) parts.push(U.precipS(fc.precip_mm));
        txt.textContent = parts.join(' · ');
      })
      .catch(() => { const t = document.getElementById('stage-forecast-text'); if (t) t.textContent = ''; });
  },

  // Draw the tour routes onto the map. Shared drawing core; each page provides its own
  // data (Tours fetches live points; Tour_share uses pre-loaded ones) and handles the
  // bounds-fit afterwards. ctx: { map, routeGroup, stages, pointsCache, activeId,
  // selColor, startIcon, endIcon }. Returns { allPts, groupIds }.
  drawRoutes(ctx) {
    const { routeGroup, stages, pointsCache, activeId } = ctx;
    routeGroup.clearLayers();
    if (typeof MapUtils !== 'undefined') MapUtils.clearPhotoMarkers(ctx.map);
    const allPts = [];
    const altIds = _altStageIds(stages, pointsCache);
    const groups = _stageSegmentGroups(stages, pointsCache);
    const activeGroup = activeId
      ? (groups.find(g => g.some(s => String(s.id) === String(activeId))) || [])
      : [];
    const groupIds = new Set(activeGroup.map(s => String(s.id)));
    // Among alternate routes for the same segment, a ridden stage wins: its
    // unridden siblings aren't drawn at all, so the shared road reads as done
    // instead of being overdrawn in blue. The selected stage's own group is
    // exempt — that view exists to compare its alternates.
    const hidden = new Set();
    for (const g of groups) {
      if (!g.some(s => s.completion)) continue;
      for (const s of g)
        if (!s.completion && !groupIds.has(String(s.id))) hidden.add(String(s.id));
    }
    let activeLine = null;
    // Completed routes are lifted above uncompleted ones: where an unridden
    // alternate retraces a stage that IS done, the shared section must read as
    // done (red), leaving only the alternate's divergence blue.
    const doneLines = [];
    stages.forEach(s => {
      const pts = pointsCache[String(s.id)];
      if (!pts?.length) return;
      const sid = String(s.id);
      if (hidden.has(sid)) return;
      const lpts = pts.map(p => [p[0], p[1]]);
      const isActive  = activeId && sid === String(activeId);
      const isSibling = activeId && !isActive && groupIds.has(sid);
      let opts;
      if (isActive)       opts = { color: ctx.selColor, weight: 5, opacity: 1.0 };
      else if (isSibling) opts = { color: ctx.selColor, weight: 3, opacity: 0.9, dashArray: '6,8' };
      else { opts = { color: stageColor(s), weight: 3, opacity: activeId ? 0.18 : 0.85 }; if (altIds.has(sid) && !s.completion) opts.dashArray = '6,8'; }
      const line = L.polyline(lpts, opts).addTo(routeGroup);
      if (isActive) activeLine = line;
      else if (s.completion) doneLines.push(line);
      allPts.push(...lpts);
    });
    doneLines.forEach(l => l.bringToFront());
    if (activeLine) activeLine.bringToFront();
    if (!activeId) {
      const first = stages[0];
      if (first?.start_lat != null) L.marker([first.start_lat, first.start_lon], { icon: ctx.startIcon, zIndexOffset: 50, interactive: false }).addTo(routeGroup);
      // One numbered circle per segment group (alternates excluded from numbering).
      _dedupeStatStages(stages, pointsCache).forEach((s, i) => {
        if (s.start_lat == null || s.start_lon == null) return;
        const icon = L.divIcon({ className: '', html: `<div style="width:16px;height:16px;border-radius:50%;background:#fff;border:1.5px solid #000;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;color:#000;font-family:-apple-system,sans-serif;line-height:1;box-sizing:border-box">${stageDisplayNum(s, stages, i + 1)}</div>`, iconSize: [16, 16], iconAnchor: [8, 8] });
        L.marker([s.start_lat, s.start_lon], { icon, zIndexOffset: 150, interactive: false }).addTo(routeGroup);
      });
      const last = stages[stages.length - 1], lastPts = last ? pointsCache[String(last.id)] : null;
      if (lastPts?.length) L.marker(lastPts[lastPts.length - 1], { icon: ctx.endIcon, zIndexOffset: 200, interactive: false }).addTo(routeGroup);
    } else {
      const pts = pointsCache[String(activeId)];
      if (pts?.length) {
        L.marker(pts[0],            { icon: ctx.startIcon, zIndexOffset: 200, interactive: false }).addTo(routeGroup);
        L.marker(pts[pts.length - 1], { icon: ctx.endIcon,   zIndexOffset: 200, interactive: false }).addTo(routeGroup);
      }
    }
    return { allPts, groupIds };
  },

  // Simple one-line-per-stage list. Shared by both pages (on Tours: phone only; on
  // Tour_share: always). ctx: { stages, pointsCache }. Uses page globals selectedId,
  // esc, U, stageColor, _altStageIds — all present on both pages.
  stageList(el, ctx) {
    const stages = ctx.stages || [];
    const altIds = _altStageIds(stages, ctx.pointsCache);
    el.innerHTML = stages.map(s => {
      const statusHtml = s.completion
        ? `<div class="stage-status"><div class="stage-status-done">✓</div><div class="stage-status-date">${s.completion.date}</div></div>`
        : `<div class="stage-status"><div class="stage-status-none">—</div></div>`;
      const nameHtml = altIds.has(String(s.id)) ? `<em>(${esc(s.name)})</em>` : esc(s.name);
      return `<div class="stage-item${String(s.id) === String(selectedId) ? ' active' : ''}" onclick="selectStage('${s.id}')">` +
        `<span class="stage-dot" style="background:${stageColor(s)}"></span>` +
        `<div class="stage-item-body"><div class="stage-item-name">${nameHtml}</div>` +
        `<div class="stage-item-sub">${U.distS(s.distance_mi)} · ${U.climbS(s.climb_ft)} climb</div></div>` +
        statusHtml + `</div>`;
    }).join('');
  },

  // Lazy Photos tab. ctx: { activityId, lightboxName, setMedia(media) }.
  photosTab(el, ctx) {
    const actId = ctx.activityId;
    if (!actId) return;   // uncompleted stage → controller shows the placeholder
    Lightbox.fetchMedia(actId).then(media => {
      if (!media.length) { el.innerHTML = '<div class="tour-tab-empty">No photos for this stage.</div>'; return; }
      ctx.setMedia(media);
      Lightbox.renderGrid(el, media, i => window[ctx.lightboxName](i));
    });
  },

  // Lazy Forecast tab. ctx: { stage, forecastEndpoint }. Opens the shared
  // weather-forecast modal (openWeatherForecast) so the user can pick a date/time
  // and see conditions sampled along the stage's route — same modal the route
  // builder and activity detail use. forecastEndpoint points at the stage's
  // route-forecast URL (authed or public share).
  forecastTab(el, ctx) {
    const stage = ctx.stage;
    if (!stage) return;
    const name = stage.name || 'Stage';
    const open = () => {
      if (typeof window.openWeatherForecast === 'function') {
        window.openWeatherForecast('tour_stage', stage.id, name, ctx.forecastEndpoint);
      }
    };
    el.innerHTML =
      '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:36px 20px;text-align:center">' +
        '<button type="button" class="tour-fc-btn" style="background:#f97316;color:#fff;border:none;border-radius:10px;padding:11px 18px;font-size:14px;font-weight:600;cursor:pointer">Get Route Forecast</button>' +
        '<div style="font-size:12px;color:var(--muted)">Pick a date &amp; time to see weather along this stage.</div>' +
      '</div>';
    el.querySelector('.tour-fc-btn').addEventListener('click', open);
    open();   // entering the tab brings up the date/time picker straight away
  },

  // Fill the always-on map badge (bottom-left) with the focused route's distance +
  // climb. A selected stage shows that stage; the whole-tour view shows the tour
  // totals (deduped like the overview stats, so alternates aren't double-counted).
  // ctx: { stages, pointsCache, activeId }.
  updateMapBadge(ctx) {
    const badge = document.getElementById('mapBadge');
    if (!badge) return;
    const { stages, pointsCache, activeId } = ctx;
    let dist, climb;
    if (activeId) {
      const s = stages.find(x => String(x.id) === String(activeId));
      dist = s?.distance_mi || 0; climb = s?.climb_ft || 0;
    } else {
      const stat = _dedupeStatStages(stages, pointsCache);
      dist  = stat.reduce((t, x) => t + (x.distance_mi || 0), 0);
      climb = stat.reduce((t, x) => t + (x.climb_ft || 0), 0);
    }
    badge.textContent = `${U.distS(dist)} · ${U.climbS(climb)} climb`;
    badge.style.display = 'block';
  },

  // Render a completed activity's detail body — name (+ Strava / Sync buttons),
  // date · weather · location, RPE, notes, AI Summary, stat chips and the photo
  // strip — into `bodyEl`, then wire the async weather/location fill, photos and
  // (optional) Strava re-sync. Shared verbatim by tour_share (a completed stage)
  // and activity_share (a single activity); per-page bits arrive via ctx:
  //   { weatherLocEndpoint,     // GET → {weather, locations, locations_points, region}
  //     syncEndpoint,           // (optional) POST → re-sync from Strava; shows a Sync button
  //     onSynced,               // (optional) async () => re-render after a successful sync
  //     getMap,                 // () => Leaflet map (for photo markers)
  //     setMedia,               // (media) => store the lightbox media list
  //     analysis }              // (optional) run enhanceAnalysis() after (phone)
  completedActivityBody(bodyEl, act, ctx) {
    if (!bodyEl) return;
    const _rpeLabels = ['', 'Easy', 'Easy+', 'Moderate', 'Moderate+', 'Medium', 'Hard', 'Hard+', 'Very Hard', 'Max-', 'Max'];
    let rpeHtml = '';
    if (act.perceived_exertion != null) {
      const v = act.perceived_exertion;
      rpeHtml = `<div style="font-size:11px;color:var(--muted);margin-bottom:6px">Effort: <span style="color:var(--text);font-weight:500">${_rpeLabels[v] || v}</span> <span style="color:var(--muted2)">(${v}/10)</span></div>`;
    }
    const stravaLink = act.strava_activity_id
      ? `<a href="https://www.strava.com/activities/${act.strava_activity_id}" target="_blank" style="font-size:11px;font-weight:600;color:#fc4c02;border:1px solid rgba(252,76,2,.4);border-radius:4px;padding:3px 8px;text-decoration:none;line-height:1.4;flex-shrink:0">View on Strava ↗</a>`
      : '';
    const syncBtn = (ctx.syncEndpoint && act.strava_activity_id)
      ? `<button id="share-sync-btn" style="font-size:11px;font-weight:600;color:var(--muted);border:1px solid var(--border2);border-radius:4px;padding:3px 8px;background:none;cursor:pointer;line-height:1.4;flex-shrink:0">↻ Sync</button>`
      : '';

    let aHtml = '';
    aHtml += `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px">
      <div style="font-size:14px;font-weight:600;line-height:1.3">${esc(act.name || '(unnamed)')}</div>
      <div style="display:flex;gap:6px;flex-shrink:0">${syncBtn}${stravaLink}</div>
    </div>`;
    const ts = act.start_time ? fmtDate(act.start_time) : '';
    aHtml += `<div style="display:grid;grid-template-columns:auto 1fr;column-gap:20px;row-gap:3px;margin-bottom:7px;align-items:baseline">
      <span style="font-size:11.5px;color:var(--muted)">${ts}</span>
      <span id="share-wx" style="font-size:11px;color:var(--muted)"></span>
      <span></span>
      <span id="share-loc" style="font-size:11px;color:var(--muted)"></span>
    </div>`;
    aHtml += rpeHtml;
    if (act.notes) aHtml += `<div class="act-notes">${esc(act.notes)}</div>`;
    if (act.ai_summary) aHtml += `<div class="ai-card" style="margin-top:8px;margin-bottom:8px"><div class="ai-card-label">AI Summary</div><div class="ai-card-body">${esc(act.ai_summary)}</div></div>`;
    aHtml += `<div class="stats-grid">${buildActivityStatChips(act, U, esc, fmtHMS)}</div>`;
    bodyEl.innerHTML = aHtml;   // photos render into the separate PHOTOS pane (see photosPane)
    if (ctx.analysis) this.enhanceAnalysis();

    // Sync button → re-sync from Strava, then let the caller re-render.
    const sBtn = bodyEl.querySelector('#share-sync-btn');
    const runSync = async () => {
      const btn = bodyEl.querySelector('#share-sync-btn');
      if (btn) { btn.disabled = true; btn.textContent = '↻ Syncing…'; }
      const restore = () => { const b = document.getElementById('share-sync-btn'); if (b) { b.disabled = false; b.textContent = '↻ Sync'; } };
      try {
        const r = await fetch(ctx.syncEndpoint, { method: 'POST' });
        // A 200 that reports {ok:false} (no Strava / not connected) is a no-op — don't
        // re-render, or the auto-sync below would fire again. Tour sync returns the
        // activity JSON (no `ok` field) on success, so `ok !== false` passes through.
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.ok === false) { restore(); return; }
        if (ctx.onSynced) await ctx.onSynced();
      } catch (e) { restore(); }
    };
    if (sBtn) sBtn.addEventListener('click', runSync);

    // Async: weather + location.
    fetch(ctx.weatherLocEndpoint).then(r => r.json()).then(d => {
      const wx = document.getElementById('share-wx'), loc = document.getElementById('share-loc');
      if (wx && d.weather?.description) { const w = d.weather, parts = [w.description]; if (w.avg_temp_f != null) parts.push(U.tempS(w.avg_temp_f)); if (w.avg_wind_kph != null) parts.push('Wind ' + U.windS(w.avg_wind_kph)); if (w.precip_mm > 0) parts.push(U.precipS(w.precip_mm)); wx.textContent = parts.join(' · '); }
      if (loc && d.locations) loc.innerHTML = (d.locations_points && d.locations_points.length) ? LocationSummary.linkifyList(d.locations_points.map(p => ({ label: p.name, name: p.name, lat: p.lat, lon: p.lon }))) : LocationSummary.linkifyNames(d.locations, d.region);
    }).catch(() => {});

    // Auto-sync once per activity if the Strava description hasn't been pulled in yet.
    if (ctx.syncEndpoint && act.strava_activity_id && !act.notes && !_autoSyncedActs.has(act.id)) {
      _autoSyncedActs.add(act.id);
      runSync();
    }
  },

  // Photo markers on the map for one stage / activity's media, without the PHOTOS
  // pane. Tapping a marker opens that photo; on phone it selects the Photos tab
  // first, so closing the lightbox lands on the grid rather than back on the map.
  // Used where the pane isn't available (phone tabs) or is filled separately
  // (tour.html's own photo strip). `source` is an activity id (fetch its media), a
  // ready media[] array, or null to just clear.
  // ctx: { getMap, setMedia, lightboxName }.
  photoMarkers(source, ctx = {}) {
    const map = ctx.getMap && ctx.getMap();
    if (!map || typeof MapUtils === 'undefined') return;
    const place = media => {
      if (ctx.setMedia) ctx.setMedia(media);
      MapUtils.clearPhotoMarkers(map);
      if (!media || !media.length) return;
      MapUtils.placePhotoMarkers(map, media, idx => {
        // TourNav is a top-level const, not a window property — reference it bare.
        if (typeof TourNav !== 'undefined' && TourNav.isPhone()) TourNav.navL2('photos');
        window[ctx.lightboxName](idx);
      });
    };
    if (Array.isArray(source)) place(source);
    else if (source != null) Lightbox.fetchMedia(source).then(place).catch(() => {});
    else place([]);
  },

  // Render the PHOTOS pane (#photos-body) as a thumbnail grid and drop matching photo
  // markers on the map, toggling the whole pane's visibility to match. Shared by the
  // share pages' completed / overview views. `source` is an activity id (fetch its
  // media), a ready media[] array (e.g. all-stage photos), or null/undefined to hide.
  // ctx: { getMap, setMedia, captionEdit, onCaptionSave }.
  photosPane(source, ctx = {}) {
    const body = document.getElementById('photos-body');
    const show = has => { if (window.ShareLayout) ShareLayout.showPhotos(has); };
    const render = media => {
      if (ctx.setMedia) ctx.setMedia(media);
      if (!media || !media.length) { if (body) body.innerHTML = ''; show(false); return; }
      show(true);
      if (!body) return;
      const map = ctx.getMap && ctx.getMap();
      if (map && typeof MapUtils !== 'undefined') MapUtils.clearPhotoMarkers(map);
      const open = i => Lightbox.open(media, i, { download: true, captionEdit: !!ctx.captionEdit, onCaptionSave: ctx.onCaptionSave });
      Lightbox.renderGrid(body, media, open, { colW: '110px', thumbH: 110 });
      if (map && typeof MapUtils !== 'undefined') MapUtils.placePhotoMarkers(map, media, open);
    };
    if (Array.isArray(source)) render(source);
    else if (source != null) Lightbox.fetchMedia(source).then(render).catch(() => show(false));
    else show(false);
  },
};
