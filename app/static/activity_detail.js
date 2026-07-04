// activity_detail.js — Shared activity detail HTML builder
// Used by both the INFO pane (main.js) and the Tours completed-stage detail (tour.html).
//
// opts = {
//   currentUserId,      // for ownership checks
//   editCallback,       // string fn name: 'startEditActivity' | 'openTourEdit'
//   deleteCallback,     // string fn name: 'deleteSelected' (INFO pane only)
//   resyncCallback,     // string fn name: 'resyncActivity' | 'resyncStageActivity'
//   saveRouteCb,        // string fn name: 'openSaveRouteDialog' | 'openStageSaveRouteDialog'
//   resyncBtnId,        // 'resync-btn' | 'sd-resync-btn'
//   kudosBtnId,         // 'kudos-btn' | 'sd-kudos-btn'
//   kudosToggleCb,      // 'toggleKudosList' | 'toggleSdKudos'
//   commentsBtnId,      // 'comments-btn' | 'sd-comments-btn'
//   commentsToggleCb,   // 'toggleCommentsList' | 'toggleSdComments'
//   weatherElId,        // 'wx-weather-text' | 'sd-weather'
//   locationElId,       // 'wx-location-text' | 'sd-location'
//   userMap,            // {userId: {avatar_url}} for avatar display
//   showAiSummary,      // bool — render AI summary chip
//   aiRefreshCb,        // string fn name for AI summary refresh button
//   escFn,              // HTML escape function
//   fmtDateFn,          // date format function
//   fmtHMSFn,           // seconds → H:MM:SS format function
//   U,                  // units object with distS, climbS, speedS, tempS, windS, precipS
// }

const _actRpeLabels = ['','Easy','Easy+','Moderate','Moderate+','Medium','Hard','Hard+','Very Hard','Max-','Max'];

function _actRpeColor(v) {
  v = Math.max(1, Math.min(10, v));
  const t = (v - 1) / 9;
  let r, g, b;
  if (t <= 0.5) {
    const u = t / 0.5;
    r = Math.round(0x22 + u * (0xea - 0x22));
    g = Math.round(0xc5 + u * (0xb3 - 0xc5));
    b = Math.round(0x5e + u * (0x08 - 0x5e));
  } else {
    const u = (t - 0.5) / 0.5;
    r = Math.round(0xea + u * (0xef - 0xea));
    g = Math.round(0xb3 + u * (0x44 - 0xb3));
    b = Math.round(0x08 + u * (0x44 - 0x08));
  }
  return `rgb(${r},${g},${b})`;
}

function buildActivityDetailHTML(a, opts) {
  const {
    currentUserId,
    editCallback,
    deleteCallback,
    resyncCallback,
    saveRouteCb,
    uploadCb,
    resyncBtnId      = 'resync-btn',
    kudosBtnId       = 'kudos-btn',
    kudosToggleCb    = 'toggleKudosList',
    commentsBtnId    = 'comments-btn',
    commentsToggleCb = 'toggleCommentsList',
    weatherElId      = 'wx-weather-text',
    locationElId     = 'wx-location-text',
    userMap          = {},
    showAiSummary    = false,
    suppressTitle    = false,
    aiRefreshCb      = 'loadAISummary',
    shareCb          = null,
    escFn,
    fmtDateFn,
    fmtHMSFn,
    U,
  } = opts;

  const esc     = escFn;
  const fmtDate = fmtDateFn;
  const fmtHMS  = fmtHMSFn;

  const isDirty = Boolean(a.local_edited_at);
  const isOwner = a.user_id === currentUserId;

  // ── Buttons ──────────────────────────────────────────────────────────────────
  const stravaLink = a.strava_activity_id
    ? `<a href="https://www.strava.com/activities/${a.strava_activity_id}" target="_blank" class="strava">View on Strava ↗</a>`
    : '';
  const kudosBtn = a.strava_activity_id
    ? `<button id="${kudosBtnId}" onclick="${kudosToggleCb}(${a.id},event)" style="display:none;border:1px solid var(--border2);border-radius:4px;padding:3px 7px;font-size:11px;font-weight:600;cursor:pointer;background:transparent;color:var(--text);align-items:center;gap:4px;line-height:1;vertical-align:middle"></button>`
    : '';
  const commentsBtn = a.strava_activity_id
    ? `<button id="${commentsBtnId}" onclick="${commentsToggleCb}(${a.id},event)" style="display:none;border:1px solid var(--border2);border-radius:4px;padding:3px 7px;font-size:11px;font-weight:600;cursor:pointer;background:transparent;color:var(--text);align-items:center;gap:4px;line-height:1;vertical-align:middle"></button>`
    : '';

  const resyncBtn = a.strava_activity_id
    ? (isOwner
        ? `<button class="resync-btn${isDirty ? ' dirty' : ''}" id="${resyncBtnId}" onclick="${resyncCallback}(${a.id})" title="${isDirty ? 'Push local edits then re-sync from Strava' : 'Re-sync metadata from Strava'}"><span class="resync-icon">↻</span> Sync</button>`
        : `<button class="resync-btn" id="${resyncBtnId}" onclick="${resyncCallback}(${a.id})" title="Re-sync from Strava"><span class="resync-icon">↻</span> Sync</button>`)
    : '';
  const editBtn = (isOwner && editCallback)
    ? `<button class="edit-btn" id="edit-act-btn" onclick="${editCallback}(${a.id})" title="Edit title, description, type &amp; equipment">Edit…</button>`
    : '';
  const deleteBtn = (isOwner && deleteCallback)
    ? `<button class="edit-btn delete-act-btn" id="delete-act-btn" onclick="${deleteCallback}()" title="Delete this activity">Delete</button>`
    : '';
  const exportBtn = `<a href="/activities/${a.id}/export/gpx" class="edit-btn" style="text-decoration:none" title="Download as GPX file">↓ GPX</a>`;
  const shareBtn = (isOwner && shareCb)
    ? `<button class="edit-btn" onclick="${shareCb}(${a.id})" title="Share this activity — create a public link for friends"><svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-.15em;margin-right:3px"><circle cx="10" cy="2.5" r="1.5"/><circle cx="2.5" cy="6.5" r="1.5"/><circle cx="10" cy="10.5" r="1.5"/><line x1="4" y1="7.3" x2="8.5" y2="9.7"/><line x1="8.5" y1="3.3" x2="4" y2="5.7"/></svg>Share</button>`
    : '';
  const saveRouteBtn = (saveRouteCb && (a.points_count > 0 || a.points_saved))
    ? `<button class="edit-btn" id="save-route-btn" onclick="${saveRouteCb}(${a.id},'${(a.name||'Route').replace(/\\/g,'\\\\').replace(/'/g,"\\'")}')" title="Save GPS track as a route">+ Route</button>`
    : '';
  const uploadBtn = (isOwner && uploadCb)
    ? `<button class="edit-btn" onclick="${uploadCb}(${a.id})" title="Upload photos or videos">↑ Photos</button>`
    : '';
  const wxBtn = (a.points_count > 0 || a.points_saved)
    ? `<button class="edit-btn" id="wx-act-btn" onclick="openWeatherForecast('activity',${a.id},this.dataset.name)" data-name="${esc(a.name||'Activity')}" title="Forecast weather for this route" style="padding:4px 7px;display:inline-flex;align-items:center"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 17.58A5 5 0 0 0 18 8h-1.26A8 8 0 1 0 4 16.25"/><line x1="8" y1="19" x2="8" y2="21"/><line x1="8" y1="13" x2="8" y2="15"/><line x1="16" y1="19" x2="16" y2="21"/><line x1="16" y1="13" x2="16" y2="15"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="12" y1="15" x2="12" y2="17"/></svg></button>`
    : '';

  // ── Meta (Effort / Keywords) ──────────────────────────────────────────────────
  const meta = [
    ['Effort',    a.effort],
    ['Keyword 1', a.keyword1], ['Keyword 2', a.keyword2], ['Notes tag', a.custom],
  ].filter(([,v])=>v&&v!=='null'&&v!=='0');

  // ── Visibility ────────────────────────────────────────────────────────────────
  const visMap  = {everyone:'Public', followers_only:'Followers Only', only_me:'Private'};
  const visIcon = {everyone:'🌍', followers_only:'👥', only_me:'🔒'};
  const effVis  = a.strava_visibility || a.effective_visibility;
  const stravaEditLink = isOwner && a.strava_activity_id
    ? `<a href="https://www.strava.com/activities/${a.strava_activity_id}/edit" target="_blank" style="opacity:.5;color:inherit;text-decoration:underline;text-underline-offset:2px">(change on Strava)</a>`
    : (a.strava_activity_id ? `<span style="opacity:.5">(change on Strava)</span>` : '');

  // ── Perceived effort + kudos/comments row ─────────────────────────────────────
  let rpeKudosHtml = '';
  if (a.perceived_exertion != null) {
    const v   = a.perceived_exertion;
    const col = _actRpeColor(v);
    const lbl = _actRpeLabels[v] || String(v);
    rpeKudosHtml = `<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">
      <div style="width:14px;height:14px;border-radius:3px;background:${col};flex-shrink:0"></div>
      <span style="font-size:12px;color:var(--text);font-weight:500">${lbl}</span>
      <span style="font-size:11px;color:var(--muted)">(${v}/10)</span>
      ${kudosBtn}${commentsBtn}
    </div>`;
  } else if (kudosBtn || commentsBtn) {
    rpeKudosHtml = `<div style="display:flex;align-items:center;gap:4px;margin-bottom:5px">${kudosBtn}${commentsBtn}</div>`;
  }

  // ── Avatar ────────────────────────────────────────────────────────────────────
  const ownerAvatarUrl = a.user_id && userMap[a.user_id]?.avatar_url
    ? userMap[a.user_id].avatar_url.replace('?thumb=1', '') + '?thumb=1'
    : null;
  const avatarHtml = ownerAvatarUrl
    ? `<img src="${ownerAvatarUrl}" alt="" style="width:32px;height:32px;object-fit:cover;border-radius:4px;flex-shrink:0;margin-right:9px;margin-top:1px">`
    : (a.user_id && userMap[a.user_id]
        ? userInitialAvatar(a.user_id, userMap[a.user_id].username, 32, '4px', 'margin-right:9px;margin-top:1px;')
        : '');

  // ── AI Summary chip ───────────────────────────────────────────────────────────
  const aiChipHtml = showAiSummary
    ? `<div class="stat-chip" id="ai-summary-chip" style="width:252px;flex-shrink:0;align-items:flex-start;justify-content:flex-start;padding:6px 8px;overflow-y:auto;box-sizing:border-box${isOwner?'':';display:none'}"><div class="sc-label" style="margin-bottom:3px;display:flex;align-items:center;justify-content:space-between;width:100%"><span>✦ AI Summary</span>${isOwner?`<button id="ai-summary-refresh" onclick="${aiRefreshCb}(${a.id},true)" title="Regenerate summary" style="background:none;border:none;cursor:pointer;color:var(--muted);font-size:11px;padding:0;line-height:1" tabindex="-1">↺</button>`:''}</div><div id="ai-summary-text" style="font-size:11px;color:var(--muted);line-height:1.45">${isOwner?'Loading…':''}</div></div>`
    : '';

  // ── Assemble ──────────────────────────────────────────────────────────────────
  const titleBarHtml = suppressTitle
    ? (editBtn||deleteBtn||resyncBtn||exportBtn||shareBtn||saveRouteBtn||uploadBtn||wxBtn||stravaLink
        ? `<div class="act-title-bar"><div class="act-title-links" style="margin-left:0">${editBtn}${deleteBtn}${resyncBtn}${exportBtn}${shareBtn}${saveRouteBtn}${uploadBtn}${wxBtn}${stravaLink}</div></div>`
        : '')
    : `<div class="act-title-bar">
      <div style="display:flex;align-items:flex-start;min-width:0;flex:1">${avatarHtml}<div class="act-title">${esc(a.name||'(unnamed)')}${isDirty?'<span style="color:#f97316;font-size:10px;margin-left:5px;font-weight:400;vertical-align:middle">edited</span>':''}</div></div>
      <div class="act-title-links">${editBtn}${deleteBtn}${resyncBtn}${exportBtn}${shareBtn}${saveRouteBtn}${uploadBtn}${wxBtn}${stravaLink}</div>
    </div>`;
  return `
    ${titleBarHtml}
    ${rpeKudosHtml}
    <div style="display:grid;grid-template-columns:auto 1fr;column-gap:20px;row-gap:4px;margin-bottom:6px;align-items:baseline">
      <span style="font-size:11.5px;color:var(--muted)">${fmtDate(a.start_time)}</span>
      <span id="${weatherElId}" style="font-size:11px;color:var(--muted)"></span>
      <span style="font-size:11px;color:var(--muted)">${effVis?`${visIcon[effVis]||''} ${visMap[effVis]||effVis} ${stravaEditLink}`:stravaEditLink}</span>
      <span id="${locationElId}" style="font-size:11px;color:var(--muted)"></span>
    </div>
    ${a.notes?`<div class="act-notes" style="margin-bottom:8px">${esc(a.notes)}</div>`:''}
    <div class="act-stats-row" style="display:flex;gap:10px;align-items:flex-start;margin-bottom:8px;max-width:100%;overflow:hidden">
      <div class="stats-grid">${buildActivityStatChips(a, U, esc, fmtHMS)}</div>
      ${aiChipHtml}
    </div>
    ${meta.length?`<div class="meta-row" style="margin-top:4px">${meta.map(([l,v])=>`<div class="meta-field"><div class="mf-label">${l}</div><div class="mf-val">${esc(String(v))}</div></div>`).join('')}</div>`:''}
  `;
}
