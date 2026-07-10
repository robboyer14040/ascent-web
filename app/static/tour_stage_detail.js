// Shared stage-detail helpers for the tour pages (tour.html + tour_share.html).
// These render into #stage-detail / the mobile Analysis-tab panels identically on
// both pages; per-page differences (data endpoints, lightbox fn) come in via ctx.
//
// Requires these globals (present on both pages): U (units), esc (HTML escape).
const TourStageDetail = {

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
    if (!stages.length) { el.innerHTML = ''; return; }
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
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Max Stage Dist</div><div class="sc-val">${U.distS(maxDist)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${alt(maxDist)}</div><div class="sc-sub">Stage ${maxDistIdx + 1}</div></div>` +
      `<div class="stat-chip" style="white-space:nowrap"><div class="sc-label">Max Ascent</div><div class="sc-val">${U.climbS(maxClimb)}</div><div class="sc-sub" style="font-size:10px;opacity:.65">${altC(maxClimb)}</div><div class="sc-sub">Stage ${maxClimbIdx + 1}</div></div>`;
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
    el.innerHTML = html;
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
    if (ctx.renderAi) ctx.renderAi(el);
  },

  // Prev/next stage nav buttons (the small ←/→ pair). Shared by both pages.
  // Uses the page-global selectStage(); `stages` is the ordered stage list.
  stageNav(stage, stages) {
    const i = stages.findIndex(s => String(s.id) === String(stage.id));
    const prev = i > 0 ? stages[i - 1] : null;
    const next = (i >= 0 && i < stages.length - 1) ? stages[i + 1] : null;
    const btn = (s, arrow) => s
      ? `<button onclick="selectStage('${s.id}')" title="Stage ${s.stage_num}" style="background:none;border:1px solid var(--border2);border-radius:4px;width:26px;height:24px;font-size:13px;color:var(--text);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${arrow}</button>`
      : `<button disabled style="background:none;border:1px solid var(--border2);border-radius:4px;width:26px;height:24px;font-size:13px;color:var(--muted);display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;opacity:.3">${arrow}</button>`;
    return `<div style="display:flex;gap:4px;margin-right:4px;flex-shrink:0">${btn(prev, '←')}${btn(next, '→')}</div>`;
  },

  // Full stage header: "Stage N: name" + the prev/next nav. Shared by both pages
  // (Tour_share uses it for completed stages too; Tours builds a custom avatar
  // header for completed stages and calls stageNav() directly).
  stageHeader(stage, stages) {
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px"><div style="font-size:15px;font-weight:600;line-height:1.3;flex:1">Stage ${stage.stage_num}: ${esc(stage.name)}</div>${this.stageNav(stage, stages)}</div>`;
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
    const activeGroup = activeId
      ? (_stageSegmentGroups(stages, pointsCache).find(g => g.some(s => String(s.id) === String(activeId))) || [])
      : [];
    const groupIds = new Set(activeGroup.map(s => String(s.id)));
    let activeLine = null;
    stages.forEach(s => {
      const pts = pointsCache[String(s.id)];
      if (!pts?.length) return;
      const lpts = pts.map(p => [p[0], p[1]]);
      const sid = String(s.id);
      const isActive  = activeId && sid === String(activeId);
      const isSibling = activeId && !isActive && groupIds.has(sid);
      let opts;
      if (isActive)       opts = { color: ctx.selColor, weight: 5, opacity: 1.0 };
      else if (isSibling) opts = { color: ctx.selColor, weight: 3, opacity: 0.9, dashArray: '6,8' };
      else { opts = { color: stageColor(s), weight: 3, opacity: activeId ? 0.18 : 0.85 }; if (altIds.has(sid)) opts.dashArray = '6,8'; }
      const line = L.polyline(lpts, opts).addTo(routeGroup);
      if (isActive) activeLine = line;
      allPts.push(...lpts);
    });
    if (activeLine) activeLine.bringToFront();
    if (!activeId) {
      const first = stages[0];
      if (first?.start_lat != null) L.marker([first.start_lat, first.start_lon], { icon: ctx.startIcon, zIndexOffset: 50, interactive: false }).addTo(routeGroup);
      // One numbered circle per segment group (alternates excluded from numbering).
      _dedupeStatStages(stages, pointsCache).forEach((s, i) => {
        if (s.start_lat == null || s.start_lon == null) return;
        const icon = L.divIcon({ className: '', html: `<div style="width:16px;height:16px;border-radius:50%;background:#fff;border:1.5px solid #000;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;color:#000;font-family:-apple-system,sans-serif;line-height:1;box-sizing:border-box">${i + 1}</div>`, iconSize: [16, 16], iconAnchor: [8, 8] });
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
};
