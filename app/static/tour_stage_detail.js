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
    fetch(`/activities/${actId}/photos`).then(r => r.ok ? r.json() : null).then(d => {
      if (!d) return;
      const media = d.media || (d.photos ? d.photos.map(f => ({ url: d.base_url + f, type: 'image' })) : []);
      if (!media.length) { el.innerHTML = '<div class="tour-tab-empty">No photos for this stage.</div>'; return; }
      ctx.setMedia(media);
      let html = '<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start">';
      media.forEach((m, i) => {
        const cap = m.caption ? `<div style="font-size:10px;color:var(--muted);margin-top:3px;line-height:1.3;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;word-break:break-word">${esc(m.caption)}</div>` : '';
        if (m.type === 'image') {
          html += `<div style="flex-shrink:0;width:calc(33.33% - 4px)"><img src="${m.url}" loading="lazy" style="height:96px;width:100%;object-fit:cover;border-radius:5px;cursor:pointer;display:block" onclick="${ctx.lightboxName}(${i})">${cap}</div>`;
        } else {
          html += `<div style="flex-shrink:0;width:calc(33.33% - 4px)"><div style="position:relative;height:96px;border-radius:5px;overflow:hidden;cursor:pointer;background:#000" onclick="${ctx.lightboxName}(${i})"><video src="${m.hls_url || m.url}" muted preload="metadata" playsinline style="width:100%;height:100%;object-fit:cover;pointer-events:none" onloadedmetadata="this.currentTime=0.1"></video><div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none"><div style="width:28px;height:28px;border-radius:50%;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center"><span style="font-size:13px;margin-left:2px">▶</span></div></div></div>${cap}</div>`;
        }
      });
      html += '</div>';
      el.innerHTML = html;
    }).catch(() => {});
  },

  // Lazy Forecast tab. ctx: { stage, forecastUrl } — completed stages use the
  // activity's actual weather; uncompleted use the daily forecast at forecastUrl.
  forecastTab(el, ctx) {
    const stage = ctx.stage;
    if (!stage) return;
    el.innerHTML = '<div class="tour-tab-empty">Loading…</div>';
    const render = parts => { el.innerHTML = parts.length
      ? `<div class="ai-card" style="margin-top:0"><div class="ai-card-label">Forecast</div><div class="ai-card-body">${parts.join(' · ')}</div></div>`
      : '<div class="tour-tab-empty">No forecast available.</div>'; };
    if (stage.completion && stage.completion.activity_id) {
      fetch(`/api/activities/${stage.completion.activity_id}/weather-location`).then(r => r.json()).then(d => {
        const w = d.weather, parts = [];
        if (w && w.description) {
          parts.push(w.description);
          if (w.avg_temp_f != null) parts.push(U.tempS(w.avg_temp_f));
          if (w.avg_wind_kph != null) parts.push('Wind ' + U.windS(w.avg_wind_kph));
          if (w.precip_mm > 0) parts.push(U.precipS(w.precip_mm));
        }
        render(parts);
      }).catch(() => render([]));
    } else {
      fetch(ctx.forecastUrl).then(r => r.json()).then(d => {
        if (d.out_of_range) { el.innerHTML = '<div class="tour-tab-empty">Forecast unavailable (out of range).</div>'; return; }
        const fc = d.forecast, parts = [];
        if (fc) {
          if (fc.description) parts.push(fc.description);
          if (fc.temp_max_f != null && fc.temp_min_f != null) parts.push(U.tempS(fc.temp_max_f) + ' / ' + U.tempS(fc.temp_min_f));
          if (fc.wind_kph != null) parts.push('Wind ' + U.windS(fc.wind_kph));
          if (fc.precip_mm > 0) parts.push(U.precipS(fc.precip_mm));
        }
        render(parts);
      }).catch(() => render([]));
    }
  },
};
