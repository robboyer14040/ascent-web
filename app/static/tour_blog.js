// tour_blog.js — Coffee-table-book presentation of a shared tour.
// Renders a linear page: cover → intro → table of contents → per-stage stories with
// stats, elevation, map, Strava notes, author prose, and orientation-aware photos.
// Owner (window.BLOG.isOwner) gets inline editing of the intro, per-stage prose, photo
// captions (via the lightbox), cover-photo selection, and a per-stage photo manager
// (reorder + hide/show, blog-only).

(function () {
  const B = window.BLOG;
  const U = makeUnits(B.useMetric);
  const TOKEN = B.token;

  const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  let CONTENT = null;
  let COMMENTS = [];              // flat list; threaded client-side via parent_id
  const POINTS = {};              // stageId(str) -> [[lat,lon,alt_ft],...]
  const stageMedia = {};          // stageId -> media[]

  const fmtWhen = ts => {
    try { return new Date(ts * 1000).toLocaleDateString('en-US',
      { month: 'short', day: 'numeric', year: 'numeric' }); } catch (e) { return ''; }
  };

  // ── Caption persistence (mirrors upload_media.js _saveCaption) ──────────────
  async function saveCaption(item, caption) {
    const parts = item.url.split('/').filter(Boolean);
    const actId = parts[parts.length - 2], filename = parts[parts.length - 1];
    const r = await fetch(`/activities/${actId}/set-media-caption`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, caption, user_upload: !!item.user_upload }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Save failed');
    item.caption = caption;
  }

  // ── Boot ────────────────────────────────────────────────────────────────────
  async function boot() {
    const root = document.getElementById('blog-root');
    try {
      const [content, planned, actual, comments] = await Promise.all([
        fetch(`/tours/blog/${TOKEN}/content`).then(r => { if (!r.ok) throw 0; return r.json(); }),
        fetch(`/tours/share/${TOKEN}/points`).then(r => r.json()).catch(() => ({})),
        fetch(`/tours/share/${TOKEN}/activity-points`).then(r => r.json()).catch(() => ({})),
        fetch(`/tours/blog/${TOKEN}/comments`).then(r => r.json()).catch(() => ({ comments: [] })),
      ]);
      CONTENT = content;
      COMMENTS = comments.comments || [];
      Object.assign(POINTS, planned);
      Object.entries(actual).forEach(([sid, pts]) => { if (pts && pts.length) POINTS[sid] = pts; });
    } catch (e) {
      root.innerHTML = '<div style="padding:60px 0;text-align:center;color:#ef4444">Failed to load this tour.</div>';
      return;
    }
    render();
  }

  function render() {
    const root = document.getElementById('blog-root');
    root.innerHTML = '';
    root.appendChild(buildCover());
    root.appendChild(buildTopbar());
    root.appendChild(buildIntroSection());
    root.appendChild(buildTOC());
    CONTENT.stages.forEach(s => root.appendChild(buildStage(s)));
    root.appendChild(buildOverallComments());

    // Maps + elevation + photos after the DOM exists.
    initOverviewMap();
    CONTENT.stages.forEach(s => { initStageMap(s); initStageElev(s); loadStagePhotos(s); hydrateStageMeta(s); renderStageProse(s); });
    renderAllComments();
    pollPendingSummaries();
  }

  // Summaries missing on load are queued server-side and built one at a time,
  // so re-fetch periodically and drop each in as it lands.
  let _aiPollTimer = null;
  function pollPendingSummaries() {
    clearTimeout(_aiPollTimer);
    if (!CONTENT.stages.some(s => s.ai_pending)) return;
    _aiPollTimer = setTimeout(async () => {
      let fresh;
      try {
        fresh = await fetch(`/tours/blog/${TOKEN}/content`).then(r => r.ok ? r.json() : null);
      } catch (e) { fresh = null; }
      if (!fresh) { pollPendingSummaries(); return; }
      const byId = new Map(fresh.stages.map(s => [s.id, s]));
      CONTENT.stages.forEach(s => {
        const f = byId.get(s.id);
        if (!f || !s.ai_pending || f.ai_pending) return;
        s.ai_html = f.ai_html;
        s.ai_pending = false;
        renderStageProse(s);
      });
      pollPendingSummaries();
    }, 8000);
  }

  // ── Cover ─────────────────────────────────────────────────────────────────
  function buildCover() {
    const el = document.createElement('div');
    el.className = 'blog-cover' + (CONTENT.cover_media ? '' : ' no-photo');
    el.id = 'blog-cover';
    const img = CONTENT.cover_media
      ? `<img class="cover-photo" src="${esc(CONTENT.cover_media)}" alt="">` : '';
    el.innerHTML = `${img}
      <div class="cover-scrim">
        <h1 class="blog-title">${esc(CONTENT.title || B.title)}</h1>
        <div class="blog-byline">
          <img src="/api/avatar/${B.ownerId}?thumb=1" onerror="this.style.display='none'">
          <span>A tour by ${esc(B.author)}</span>
        </div>
      </div>`;
    return el;
  }

  function buildTopbar() {
    const el = document.createElement('div');
    el.className = 'blog-topbar';
    el.innerHTML = `<a href="/tours/share/${TOKEN}">View interactive map →</a><span class="spacer"></span>` +
      (B.isOwner
        ? `<span style="font-size:12px;color:var(--muted)">You're the author — edits below save instantly.</span>
           <button class="edit-btn" id="share-link-btn"
             title="Anyone with this link can read the blog and leave comments, but not edit it.">Copy public link</button>`
        : '');
    const share = el.querySelector('#share-link-btn');
    if (share) share.onclick = () => copyPublicLink(share);
    return el;
  }

  // The current blog URL is already the read-only public link: viewing needs no
  // login and editing is gated to the signed-in author, so anyone (even non-Ascent
  // users) can open it to read + comment. This just hands the owner that link.
  async function copyPublicLink(btn) {
    const url = window.location.href;
    try {
      await navigator.clipboard.writeText(url);
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = url; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (_) {}
      ta.remove();
    }
    const orig = btn.textContent;
    btn.textContent = 'Link copied ✓';
    setTimeout(() => { btn.textContent = orig; }, 1800);
  }

  // ── AI summary (read-only for everyone, owner included) ─────────────────────
  // Deliberately separate from the author's own prose — this is the machine's
  // account of the route, never something a person edits.
  function aiBlock(label, html, pending) {
    if (html && html.trim()) {
      return `<div class="ai-sum"><div class="ai-sum-k">${label}</div>` +
             `<div class="blog-prose ai-sum-body">${html}</div></div>`;
    }
    if (!pending) return '';
    return `<div class="ai-sum"><div class="ai-sum-k">${label}</div>` +
           `<div class="blog-prose ai-sum-body ai-sum-wait">Generating summary…</div></div>`;
  }

  // ── Intro ───────────────────────────────────────────────────────────────────
  function buildIntroSection() {
    const sec = document.createElement('div');
    sec.className = 'blog-section';
    sec.id = 'intro-section';
    renderIntro(sec);
    return sec;
  }

  function renderIntro(sec) {
    const hasIntro = !!(CONTENT.intro_html && CONTENT.intro_html.trim());
    const editBtn = B.isOwner ? `<button class="edit-btn" id="edit-intro-btn">Edit intro</button>` : '';
    sec.innerHTML = `<div class="edit-row"><div class="blog-h" style="margin:0">Introduction</div>${editBtn}</div>` +
      (hasIntro
        ? `<div class="blog-prose">${CONTENT.intro_html}</div>`
        : `<div class="blog-prose empty-hint">${B.isOwner ? 'No introduction yet — click “Edit intro” to add one.' : ''}</div>`) +
      aiBlock('AI Tour Summary', CONTENT.intro_ai_html, false);
    const btn = sec.querySelector('#edit-intro-btn');
    if (btn) btn.onclick = () => editIntro(sec);
  }

  function editIntro(sec) {
    sec.innerHTML = `<div class="blog-h">Edit introduction</div>
      <div class="blog-editor">
        <textarea id="intro-ta">${esc(CONTENT.intro_raw || '')}</textarea>
        <div class="md-hint">Markdown-lite: **bold**, *italic*, [text](https://link). Blank line = new paragraph.</div>
        <div class="actions">
          <button class="btn-primary" id="intro-save">Save</button>
          <button class="btn-ghost" id="intro-cancel">Cancel</button>
        </div>
      </div>`;
    const ta = sec.querySelector('#intro-ta');
    sec.querySelector('#intro-cancel').onclick = () => renderIntro(sec);
    sec.querySelector('#intro-save').onclick = async () => {
      const body = { intro: ta.value, cover_media: CONTENT.cover_media };
      const r = await fetch(`/tours/blog/${TOKEN}/intro`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!r.ok) { alert('Failed to save.'); return; }
      const d = await r.json();
      CONTENT.intro_raw = ta.value; CONTENT.intro_html = d.intro_html;
      renderIntro(sec);
    };
  }

  // ── Table of contents ────────────────────────────────────────────────────────
  function statStages() { return CONTENT.stages.filter(s => s.alt_override !== 1); }

  function buildTOC() {
    const sec = document.createElement('div');
    sec.className = 'blog-section';
    const ss = statStages();
    const done = ss.filter(s => s.completion).length;
    const totDist = ss.reduce((a, s) => a + (s.completion?.distance_mi || s.distance_mi || 0), 0);
    const totClimb = ss.reduce((a, s) => a + (s.completion?.climb_ft || s.climb_ft || 0), 0);

    const chips = [
      chip('Stages', `${done}/${ss.length}`),
      chip('Distance', U.distS(totDist)),
      chip('Ascent', U.climbS(totClimb)),
    ].join('');

    const items = CONTENT.stages.map(s => {
      const meta = `${U.distS(s.completion?.distance_mi || s.distance_mi || 0)} · ${U.climbS(s.completion?.climb_ft || s.climb_ft || 0)}`;
      return `<a class="toc-item ${s.completion ? 'done' : ''}" href="#stage-${s.id}">
        <span class="toc-num">${s.stage_num}</span>
        <span class="toc-name">${esc(s.name)}</span>
        <span class="toc-meta">${meta}</span>
      </a>`;
    }).join('');

    sec.innerHTML = `<div class="blog-h">The Tour</div>
      <div id="toc-map"></div>
      <div class="toc-stats">${chips}</div>
      <div class="blog-h">Stages</div>
      <div class="toc-list">${items}</div>`;
    return sec;
  }

  function chip(l, v) { return `<div class="bchip"><div class="l">${esc(l)}</div><div class="v">${esc(v)}</div></div>`; }

  // ── Stage block ──────────────────────────────────────────────────────────────
  function buildStage(s) {
    const sec = document.createElement('div');
    sec.className = 'stage-block';
    sec.id = `stage-${s.id}`;

    const c = s.completion;
    const status = c
      ? `<div class="stage-date">✓ Completed ${esc(c.date)}</div>`
      : `<div class="stage-todo">Planned stage</div>`;

    const chips = [];
    chips.push(chip('Distance', U.distS(c?.distance_mi || s.distance_mi || 0)));
    chips.push(chip('Ascent', U.climbS(c?.climb_ft || s.climb_ft || 0)));
    if (c?.moving_s) chips.push(chip('Moving', fmtHMS(c.moving_s)));
    if (c?.avg_moving_speed_mph) chips.push(chip('Avg speed', U.speedS(c.avg_moving_speed_mph)));
    if (c?.avg_hr) chips.push(chip('Avg HR', Math.round(c.avg_hr) + ' bpm'));

    const hasPts = (POINTS[String(s.id)] || []).length > 1;

    sec.innerHTML = `
      <div class="stage-eyebrow">Stage ${s.stage_num}</div>
      <h2 class="stage-title">${esc(s.name)}</h2>
      ${status}
      <div class="blog-stats">${chips.join('')}</div>
      ${hasPts ? `<div class="stage-map" id="smap-${s.id}"></div>` : ''}
      ${hasPts ? `<div class="stage-elev" id="selev-${s.id}"><canvas></canvas></div>` : ''}
      <div class="stage-meta" id="meta-${s.id}"></div>
      <div id="prose-${s.id}"></div>
      <div class="blog-figs" id="figs-${s.id}"></div>
      <div class="blog-comments" id="cmts-${s.stage_num}"></div>`;

    // renderStageProse(s) is called from render() after this element is appended —
    // getElementById needs it in the document first.
    return sec;
  }

  function renderStageProse(s) {
    const host = document.getElementById(`prose-${s.id}`);
    if (!host) return;
    const has = !!(s.body_html && s.body_html.trim());
    const ai = aiBlock(`AI Stage Summary`, s.ai_html, s.ai_pending);
    // Non-owners with no description still get the AI summary, if there is one.
    if (!has && !B.isOwner) { host.innerHTML = ai; return; }
    const editBtn = B.isOwner
      ? `<button class="edit-btn" id="ep-${s.id}">${has ? 'Edit description' : 'Add description'}</button>` : '';
    host.innerHTML = ai +
      `<div class="edit-row" style="margin-top:14px"><div class="blog-h" style="margin:0">Description</div>${editBtn}</div>` +
      (has ? `<div class="blog-prose">${s.body_html}</div>`
           : `<div class="blog-prose empty-hint">No description yet — click “Add description”.</div>`);
    const btn = host.querySelector(`#ep-${s.id}`);
    if (btn) btn.onclick = () => editStageProse(s);
  }

  function editStageProse(s) {
    const host = document.getElementById(`prose-${s.id}`);
    host.innerHTML = `<div class="edit-row" style="margin-top:14px"><div class="blog-h" style="margin:0">Edit description</div></div>
      <div class="blog-editor">
        <textarea id="ta-${s.id}">${esc(s.body_raw || '')}</textarea>
        <div class="md-hint">Markdown-lite: **bold**, *italic*, [text](https://link). Blank line = new paragraph.</div>
        <div class="actions">
          <button class="btn-primary" id="sv-${s.id}">Save</button>
          <button class="btn-ghost" id="cn-${s.id}">Cancel</button>
        </div>
      </div>`;
    const ta = host.querySelector(`#ta-${s.id}`);
    host.querySelector(`#cn-${s.id}`).onclick = () => renderStageProse(s);
    host.querySelector(`#sv-${s.id}`).onclick = async () => {
      const r = await fetch(`/tours/blog/${TOKEN}/stage/${s.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: ta.value }),
      });
      if (!r.ok) { alert('Failed to save.'); return; }
      const d = await r.json();
      s.body_raw = ta.value; s.body_html = d.body_html;
      renderStageProse(s);
    };
  }

  // ── Weather + location (before the description) ───────────────────────────────
  // Async-hydrated from the shared activity endpoint (cached, self-warming). Location
  // names are clickable — identical to the tour / tour-share pages (LocationSummary).
  function hydrateStageMeta(s) {
    const host = document.getElementById(`meta-${s.id}`); if (!host) return;
    const actId = s.completion?.activity_id; if (!actId) return;
    fetch(`/api/activities/${actId}/weather-location`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        const rows = [];
        if (d.locations) {
          const links = (d.locations_points && d.locations_points.length)
            ? LocationSummary.linkifyList(d.locations_points.map(
                p => ({ label: p.name, name: p.name, lat: p.lat, lon: p.lon })))
            : LocationSummary.linkifyNames(d.locations, d.region);
          rows.push(`<div class="sm-loc"><span class="sm-ic">📍</span>${links}</div>`);
        }
        if (d.weather?.description) {
          const w = d.weather, parts = [w.description];
          if (w.avg_temp_f   != null) parts.push(U.tempS(w.avg_temp_f));
          if (w.avg_wind_kph != null) parts.push('Wind ' + U.windS(w.avg_wind_kph));
          if (w.precip_mm > 0)        parts.push(U.precipS(w.precip_mm));
          rows.push(`<div class="sm-wx"><span class="sm-ic">🌤</span>${esc(parts.join(' · '))}</div>`);
        }
        if (rows.length) host.innerHTML = rows.join('');
      })
      .catch(() => {});
  }

  // ── Maps ─────────────────────────────────────────────────────────────────────
  function tileLayer() {
    return L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      { maxZoom: 18, attribution: '© OpenStreetMap' });
  }

  function initOverviewMap() {
    const el = document.getElementById('toc-map'); if (!el) return;
    const map = L.map(el, { zoomControl: true, attributionControl: true }).setView([20, 0], 2);
    tileLayer().addTo(map);
    MapUtils.addScale(map, U.metric);
    const all = [];
    CONTENT.stages.forEach(s => {
      const pts = POINTS[String(s.id)]; if (!pts || pts.length < 2) return;
      const latlngs = pts.map(p => [p[0], p[1]]);
      L.polyline(latlngs, { color: s.completion ? '#16a34a' : '#1e40af', weight: 3, opacity: .85 }).addTo(map);
      all.push(...latlngs);
    });
    if (all.length) map.fitBounds(L.latLngBounds(all), { padding: [24, 24] });
    setTimeout(() => map.invalidateSize(), 60);
  }

  function initStageMap(s) {
    const el = document.getElementById(`smap-${s.id}`); if (!el) return;
    const pts = POINTS[String(s.id)]; if (!pts || pts.length < 2) return;
    const latlngs = pts.map(p => [p[0], p[1]]);
    const map = L.map(el, { zoomControl: true, attributionControl: false }).setView(latlngs[0], 12);
    tileLayer().addTo(map);
    MapUtils.addScale(map, U.metric);
    L.polyline(latlngs, { color: s.completion ? '#16a34a' : '#1e40af', weight: 4 }).addTo(map);
    map.fitBounds(L.latLngBounds(latlngs), { padding: [20, 20] });
    setTimeout(() => map.invalidateSize(), 60);
  }

  // ── Elevation profile (static; reuses buildStageElevChart) ───────────────────
  function chartPtsFor(pts) {
    const R = 3958.8; let cum = 0; const out = [];
    for (let i = 0; i < pts.length; i++) {
      if (i > 0) {
        const dLat = (pts[i][0] - pts[i - 1][0]) * Math.PI / 180;
        const dLon = (pts[i][1] - pts[i - 1][1]) * Math.PI / 180;
        const a = Math.sin(dLat / 2) ** 2 +
          Math.cos(pts[i - 1][0] * Math.PI / 180) * Math.cos(pts[i][0] * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
        cum += R * 2 * Math.asin(Math.sqrt(a));
      }
      const x = U.metric ? cum * 1.60934 : cum;         // mi → km
      const altFt = pts[i][2] || 0;
      const y = U.metric ? altFt * 0.3048 : altFt;       // ft → m
      out.push({ x, y });
    }
    return out;
  }

  function initStageElev(s) {
    const strip = document.getElementById(`selev-${s.id}`); if (!strip) return;
    const pts = POINTS[String(s.id)]; if (!pts || pts.length < 2) return;
    const canvas = strip.querySelector('canvas');
    const chartPts = chartPtsFor(pts);
    // Slope-gradient fill (green→red), computed from the full-resolution route points.
    const distConv = mi => U.metric ? mi * 1.60934 : mi;
    let gradPts = null;
    try { gradPts = computeStageGradFromRawPts(pts, 1, distConv); } catch (e) { gradPts = null; }
    try { buildStageElevChart(canvas, chartPts, { gradeOn: !!(gradPts && gradPts.length), gradPts }); }
    catch (e) { strip.style.display = 'none'; }
  }

  // ── Photos / videos ──────────────────────────────────────────────────────────
  function loadStagePhotos(s) {
    const actId = s.completion?.activity_id; if (!actId) return;
    fetch(`/activities/${actId}/photos`).then(r => r.ok ? r.json() : null).then(d => {
      const media = (d && d.media) || [];
      if (!media.length) return;
      stageMedia[s.id] = media;
      renderFigures(s);
    }).catch(() => {});
  }

  // Blog-only media layout (order + hidden), keyed by media URL. Mirrors the server's
  // _apply_media_layout so page and PDF agree.
  function stageLayout(s) {
    return { order: s.media_order || [], hidden: s.media_hidden || [] };
  }

  // Raw activity media, stable-sorted by the saved order (URLs not in the order keep
  // their natural position after known ones). Hidden items are kept in place — the
  // manager needs them; the reading view filters them via visibleMedia().
  function orderedAll(s) {
    const media = stageMedia[s.id] || [];
    const { order } = stageLayout(s);
    const rank = new Map(order.map((u, i) => [u, i]));
    return media
      .map((m, i) => [m, i])
      .sort((a, b) => (rank.has(a[0].url) ? rank.get(a[0].url) : order.length + a[1])
                    - (rank.has(b[0].url) ? rank.get(b[0].url) : order.length + b[1]))
      .map(pair => pair[0]);
  }

  function visibleMedia(s) {
    const hide = new Set(stageLayout(s).hidden);
    return orderedAll(s).filter(m => !hide.has(m.url));
  }

  function renderFigures(s) {
    const host = document.getElementById(`figs-${s.id}`); if (!host) return;
    const raw = stageMedia[s.id] || [];
    const media = visibleMedia(s);
    host.innerHTML = '';
    if (B.isOwner && raw.length) {
      const bar = document.createElement('div');
      bar.className = 'figs-toolbar';
      bar.innerHTML = `<button class="edit-btn">Manage photos</button>`;
      bar.querySelector('button').onclick = () => openMediaManager(s);
      host.appendChild(bar);
    }
    media.forEach((m, i) => host.appendChild(buildFigure(s, m, i)));
  }

  function buildFigure(s, m, idx) {
    const fig = document.createElement('figure');
    fig.className = 'blog-fig';
    const isVideo = m.type === 'video';
    const capHtml = m.caption
      ? esc(m.caption)
      : (B.isOwner ? '<span class="cap-empty">Click the photo to add a description…</span>' : '');
    const media = isVideo
      ? `<div class="vthumb"><img src="${esc(m.url)}" alt=""><span class="play">▶</span></div>`
      : `<img src="${esc(m.url)}" alt="" loading="lazy">`;
    const coverBtn = (B.isOwner && !isVideo)
      ? `<button class="edit-btn set-cover" style="margin:6px 0 0">Set as cover</button>` : '';
    fig.innerHTML = `${media}<figcaption>${capHtml}${coverBtn ? '<br>' + coverBtn : ''}</figcaption>`;

    const clickable = fig.querySelector('img, .vthumb');
    clickable.onclick = () => openLightbox(s, idx);

    const cb = fig.querySelector('.set-cover');
    if (cb) cb.onclick = (e) => { e.stopPropagation(); setCover(m.url); };

    // Orientation: portrait → caption beside (alternating); landscape → below (default).
    if (!isVideo) {
      const img = fig.querySelector('img');
      const applyOrient = () => {
        if (img.naturalHeight > img.naturalWidth * 1.1) {
          fig.classList.add('portrait', idx % 2 === 0 ? 'left' : 'right');
        }
      };
      if (img.complete && img.naturalWidth) applyOrient();
      else img.addEventListener('load', applyOrient, { once: true });
    }
    return fig;
  }

  function openLightbox(s, idx) {
    const media = visibleMedia(s);
    let lastEdited = idx;                       // where to land back in the blog
    Lightbox.open(media, idx, {
      download: true,
      captionEdit: B.isOwner,
      // Arrow keys while editing cycle to the next/prev photo (see lightbox.js),
      // saving through here — so lastEdited follows the caption you just wrote.
      onCaptionSave: async (item, cap) => {
        await saveCaption(item, cap);
        const i = media.indexOf(item);
        if (i >= 0) lastEdited = i;
      },
      onClose: () => {
        renderFigures(s);                        // reflect any caption edits inline
        const host = document.getElementById(`figs-${s.id}`);
        const fig = host && host.querySelectorAll('.blog-fig')[lastEdited];
        if (fig) fig.scrollIntoView({ behavior: 'smooth', block: 'center' });
      },
    });
  }

  async function setCover(url) {
    CONTENT.cover_media = url;
    const r = await fetch(`/tours/blog/${TOKEN}/intro`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intro: CONTENT.intro_raw || '', cover_media: url }),
    });
    if (!r.ok) { alert('Failed to set cover.'); return; }
    // Rebuild just the cover in place.
    const old = document.getElementById('blog-cover');
    if (old) old.replaceWith(buildCover());
  }

  // ── Photo/video manager (owner) ───────────────────────────────────────────────
  // A per-stage grid: drag the handle to reorder, tap the eye to hide/show. Hidden
  // items stay in the grid (greyed) so they can be restored. Blog-only — the activity
  // media is untouched.
  function openMediaManager(s) {
    const host = document.getElementById(`figs-${s.id}`); if (!host) return;
    const hide = new Set(stageLayout(s).hidden);
    const items = orderedAll(s);                 // hidden included, in current order

    const thumb = (m) => {
      const isVideo = m.type === 'video';
      const hidden = hide.has(m.url);
      return `<div class="mm-thumb ${hidden ? 'hidden' : ''}" data-url="${esc(m.url)}">
        <img src="${esc(m.url)}" alt="" loading="lazy">
        ${isVideo ? '<span class="mm-badge">▶</span>' : ''}
        <span class="mm-off">🚫</span>
        <button class="mm-eye" title="Hide/show in blog">${hidden ? '🚫' : '👁'}</button>
        <button class="mm-drag" title="Drag to reorder">⠿</button>
      </div>`;
    };

    host.innerHTML = `<div class="mediamgr">
      <div class="mm-head">
        <div class="blog-h" style="margin:0">Manage photos</div>
        <span class="spacer"></span>
        <button class="btn-primary" id="mm-done-${s.id}">Done</button>
        <button class="btn-ghost" id="mm-cancel-${s.id}">Cancel</button>
      </div>
      <div class="mm-hint">Drag ⠿ to reorder · tap 👁 to hide/show. Changes affect only the blog.</div>
      <div class="mm-grid" id="mm-grid-${s.id}">${items.map(thumb).join('')}</div>
    </div>`;

    const grid = host.querySelector(`#mm-grid-${s.id}`);
    grid.querySelectorAll('.mm-eye').forEach(btn => {
      btn.onclick = () => {
        const cell = btn.closest('.mm-thumb');
        const on = cell.classList.toggle('hidden');
        btn.textContent = on ? '🚫' : '👁';
      };
    });
    makeSortable(grid);

    host.querySelector(`#mm-cancel-${s.id}`).onclick = () => renderFigures(s);
    host.querySelector(`#mm-done-${s.id}`).onclick = async () => {
      const cells = [...grid.querySelectorAll('.mm-thumb')];
      const order = cells.map(c => c.dataset.url);
      const hidden = cells.filter(c => c.classList.contains('hidden')).map(c => c.dataset.url);
      const r = await fetch(`/tours/blog/${TOKEN}/stage/${s.id}/media`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order, hidden }),
      });
      if (!r.ok) { alert('Failed to save.'); return; }
      s.media_order = order; s.media_hidden = hidden;
      renderFigures(s);
    };
  }

  // Minimal pointer-based sortable (mouse + touch): drag by the .mm-drag handle.
  function makeSortable(grid) {
    let dragEl = null;
    grid.querySelectorAll('.mm-drag').forEach(handle => {
      handle.style.touchAction = 'none';
      handle.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        dragEl = handle.closest('.mm-thumb');
        dragEl.classList.add('mm-dragging');
        handle.setPointerCapture(e.pointerId);
      });
      handle.addEventListener('pointermove', (e) => {
        if (!dragEl) return;
        const el = document.elementFromPoint(e.clientX, e.clientY);
        const over = el && el.closest('.mm-thumb');
        if (!over || over === dragEl || over.parentElement !== grid) return;
        // If the drag target sits after the hovered cell, insert before it; else after.
        const after = over.compareDocumentPosition(dragEl) & Node.DOCUMENT_POSITION_FOLLOWING;
        grid.insertBefore(dragEl, after ? over : over.nextSibling);
      });
      const end = (e) => {
        if (dragEl) { dragEl.classList.remove('mm-dragging'); dragEl = null; }
        try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
      };
      handle.addEventListener('pointerup', end);
      handle.addEventListener('pointercancel', end);
    });
  }

  // ── Comments ─────────────────────────────────────────────────────────────────
  function buildOverallComments() {
    const sec = document.createElement('div');
    sec.className = 'blog-section';
    sec.innerHTML = `<div class="blog-h">Guestbook</div><div class="blog-comments" id="cmts-overall" style="border-top:none;padding-top:0"></div>`;
    return sec;
  }

  // container key: a stage_num, or the string 'overall' for tour-wide comments.
  function containerFor(stageNum) {
    return document.getElementById(stageNum == null ? 'cmts-overall' : `cmts-${stageNum}`);
  }

  function renderAllComments() {
    // Every stage container + the overall one.
    CONTENT.stages.forEach(s => renderCommentBlock(s.stage_num));
    renderCommentBlock(null);
  }

  function renderCommentBlock(stageNum) {
    const host = containerFor(stageNum);
    if (!host) return;
    const mine = COMMENTS.filter(c => (c.stage_num == null ? null : c.stage_num) === (stageNum == null ? null : stageNum));
    const tops = mine.filter(c => !c.parent_id);
    const repliesOf = pid => mine.filter(c => c.parent_id === pid);

    const label = stageNum == null ? '' : `<div class="ch">Comments</div>`;
    const list = tops.map(c => cmtHtml(c, repliesOf(c.id))).join('') ||
      `<div style="font-size:13px;color:var(--muted);margin-bottom:6px">${stageNum == null ? 'Sign the guestbook — be the first to comment.' : 'No comments yet.'}</div>`;

    host.innerHTML = label + list + formHtml(stageNum, null);
    wireForm(host, stageNum, null);
    // Owner reply + delete actions.
    host.querySelectorAll('[data-reply]').forEach(btn => {
      btn.onclick = () => openReply(host, stageNum, parseInt(btn.dataset.reply));
    });
    host.querySelectorAll('[data-del]').forEach(btn => {
      btn.onclick = () => deleteComment(parseInt(btn.dataset.del), stageNum);
    });
  }

  function cmtHtml(c, replies) {
    const head = who => `<div class="cmt-head">
        <span class="cmt-author ${who.is_owner ? 'owner' : ''}">${esc(who.author_name)}</span>
        ${who.is_owner ? '<span class="cmt-badge">Author</span>' : ''}
        <span class="cmt-when">${fmtWhen(who.created_at)}</span>
      </div>`;
    const actions = c => {
      const parts = [];
      if (B.isOwner && !c.parent_id) parts.push(`<button data-reply="${c.id}">Reply</button>`);
      if (B.isOwner) parts.push(`<button data-del="${c.id}">Delete</button>`);
      return parts.length ? `<div class="cmt-actions">${parts.join('')}</div>` : '';
    };
    const rep = (replies || []).map(r =>
      `<div class="cmt reply">${head(r)}<div class="cmt-body">${esc(r.body)}</div>${actions(r)}</div>`).join('');
    return `<div class="cmt">${head(c)}<div class="cmt-body">${esc(c.body)}</div>${actions(c)}${rep}</div>`;
  }

  function formHtml(stageNum, parentId) {
    const nameField = B.isOwner ? '' :
      `<input name="name" placeholder="Your name" maxlength="80" autocomplete="off">`;
    return `<form class="cmt-form" data-stage="${stageNum == null ? '' : stageNum}" ${parentId ? `data-parent="${parentId}"` : ''}>
        ${nameField}
        <input name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
        <textarea name="body" placeholder="${parentId ? 'Write a reply…' : 'Add a comment…'}" maxlength="4000"></textarea>
        <div class="row"><button type="submit" class="btn-primary">${parentId ? 'Reply' : 'Post comment'}</button>
        ${parentId ? '<button type="button" class="btn-ghost" data-cancel>Cancel</button>' : ''}</div>
      </form>`;
  }

  function wireForm(scope, stageNum, parentId) {
    const form = parentId
      ? scope.querySelector(`form[data-parent="${parentId}"]`)
      : scope.querySelector('form.cmt-form:not([data-parent])');
    if (!form) return;
    form.onsubmit = async e => {
      e.preventDefault();
      const body = form.querySelector('[name="body"]').value.trim();
      if (!body) return;
      const payload = {
        stage_num: stageNum == null ? null : stageNum,
        author_name: (form.querySelector('[name="name"]') || {}).value || '',
        website: form.querySelector('[name="website"]').value,
        body,
        parent_id: parentId || null,
      };
      const r = await fetch(`/tours/blog/${TOKEN}/comments`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!r.ok) { alert('Could not post comment.'); return; }
      const d = await r.json();
      COMMENTS.push(d.comment);
      renderCommentBlock(stageNum);
    };
    const cancel = form.querySelector('[data-cancel]');
    if (cancel) cancel.onclick = () => renderCommentBlock(stageNum);
  }

  function openReply(host, stageNum, parentId) {
    // Render an inline reply form right under the target comment.
    const anchor = host.querySelector(`[data-reply="${parentId}"]`);
    if (!anchor || anchor.parentElement.querySelector('form')) return;
    anchor.closest('.cmt').insertAdjacentHTML('beforeend', formHtml(stageNum, parentId));
    wireForm(anchor.closest('.cmt'), stageNum, parentId);
  }

  async function deleteComment(id, stageNum) {
    if (!confirm('Delete this comment (and its replies)?')) return;
    const r = await fetch(`/tours/blog/${TOKEN}/comments/${id}`, { method: 'DELETE' });
    if (!r.ok) { alert('Could not delete.'); return; }
    COMMENTS = COMMENTS.filter(c => c.id !== id && c.parent_id !== id);
    renderCommentBlock(stageNum);
  }

  boot();
})();
