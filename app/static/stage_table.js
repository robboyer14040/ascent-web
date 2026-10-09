'use strict';
/* stage_table.js — the tour stage column table, shared by the Tours page
   (tour.html) and the public shared-tour page (tour_share.html).

   Owns the column header, the Visible Columns picker, header sorting, the
   drag-to-reorder and drag-to-resize separators, the horizontal scrollbar, and
   the sidebar | main divider. The column list itself comes from list_cols.js,
   so the picker offers the same choices as the Activities page.

   A page wires itself in with StageTable.init({...}) — see init() for the
   context it must supply. Expects these globals (all already present on both
   tour pages): U (tour_units.js), esc + fmtHMS (common.js), stageColor and
   _altStageIds (tour_stage.js / page), TourNav, TourStageDetail, LIST_COLS +
   buildListCols (list_cols.js).

   Markup each page must provide, inside #sidebar:
     #stage-list-wrap > #stage-h-scroll > #stage-col-head
                                       > #stage-list-v-scroll > #stage-list
                                                              > #sidebar-empty
                      > #stage-hscroll-track > #stage-hscroll-thumb
                      > #stage-col-gear-btn   (onclick=StageTable.togglePicker)
                      > #stage-col-picker
   and a #sidebar-resize sibling of #sidebar. */

const StageTable = (function () {

  // ── Columns ────────────────────────────────────────────────────────────────
  // The canonical list from list_cols.js, plus this table's own widths (a tour
  // sidebar is far narrower than the Activities pane) and stage renderers.
  // 'user' and 'pace' have no stage equivalent, so they fall through to the
  // shared em-dash renderer rather than being dropped from the picker.
  const _comp = s => s.completion || null;
  const _n    = v => (v ? Math.round(v) : '—');

  let _altIds = new Set();
  const _stageName = s => {
    const nm = esc(s.name);
    return _altIds.has(String(s.id)) ? `<em>(${nm})</em>` : nm;
  };

  const COLS = buildListCols({
    label: { name: 'Stage' },
    w: {
      user:'46px', date:'68px', name:'minmax(60px,1fr)', dist:'54px', active:'64px',
      duration:'64px', climb:'58px', mvspd:'64px', avgspd:'64px', hr:'44px',
      maxhr:'52px', power:'56px', cadence:'60px', calories:'48px', suffer:'52px',
      pace:'58px',
    },
    // Stage sorting is client-side, so a column's sort key is just its own id.
    sort: {
      name:'name', date:'date', dist:'dist', active:'active', duration:'duration',
      climb:'climb', mvspd:'mvspd', avgspd:'avgspd', hr:'hr', maxhr:'maxhr',
      power:'power', cadence:'cadence', calories:'calories', suffer:'suffer',
    },
    render: {
      name:     s => (s.completion
        ? `<span style="color:#22c55e;font-size:11px;line-height:1;flex-shrink:0;font-weight:700">✓</span><span class="st-name-text">${_stageName(s)}</span>`
        : `<span class="stage-dot" style="background:${stageColor(s)}"></span><span class="st-name-text">${_stageName(s)}</span>`),
      date:     s => (_comp(s) ? _comp(s).date : '—'),
      dist:     s => (s.distance_mi ? (U.metric ? (s.distance_mi*1.60934).toFixed(1) : s.distance_mi.toFixed(1)) : '—'),
      climb:    s => (s.climb_ft ? (U.metric ? Math.round(s.climb_ft*0.3048) : Math.round(s.climb_ft)) : '—'),
      active:   s => (_comp(s)?.moving_s   ? fmtHMS(_comp(s).moving_s)   : '—'),
      duration: s => (_comp(s)?.duration_s ? fmtHMS(_comp(s).duration_s) : '—'),
      mvspd:    s => { const v = _comp(s)?.avg_moving_speed_mph; return v ? (U.metric?(v*1.60934).toFixed(1):v.toFixed(1)) : '—'; },
      avgspd:   s => { const d = _comp(s)?.duration_s, mi = s.distance_mi;
                       if (!d || !mi) return '—';
                       const v = mi / (d/3600); return U.metric ? (v*1.60934).toFixed(1) : v.toFixed(1); },
      hr:       s => _n(_comp(s)?.avg_hr),
      maxhr:    s => _n(_comp(s)?.max_hr),
      power:    s => (_comp(s)?.avg_power   ? Math.round(_comp(s).avg_power)+' W'     : '—'),
      cadence:  s => (_comp(s)?.avg_cadence ? Math.round(_comp(s).avg_cadence)+' rpm' : '—'),
      calories: s => _n(_comp(s)?.calories),
      suffer:   s => _n(_comp(s)?.suffer_score),
    },
  });

  const DEFAULT_COL_IDS = ['name','dist','climb','date','active'];

  // Client-side sort accessors, keyed by column id (plus the default order).
  const SORT_KEY = {
    stage_num: s => s.stage_num || 0,
    name:      s => (s.name||'').toLowerCase(),
    date:      s => (s.completion ? s.completion.date || '' : ''),
    dist:      s => s.distance_mi || 0,
    climb:     s => s.climb_ft || 0,
    active:    s => s.completion?.moving_s || 0,
    duration:  s => s.completion?.duration_s || 0,
    mvspd:     s => s.completion?.avg_moving_speed_mph || 0,
    avgspd:    s => { const d=s.completion?.duration_s; return (d && s.distance_mi) ? s.distance_mi/(d/3600) : 0; },
    hr:        s => s.completion?.avg_hr || 0,
    maxhr:     s => s.completion?.max_hr || 0,
    power:     s => s.completion?.avg_power || 0,
    cadence:   s => s.completion?.avg_cadence || 0,
    calories:  s => s.completion?.calories || 0,
    suffer:    s => s.completion?.suffer_score || 0,
  };

  // ── Stored preferences ─────────────────────────────────────────────────────
  const K_COLS = 'ascent-stage-cols', K_W = 'ascent-stage-col-widths', K_VER = 'ascent-stage-cols-v';

  // Before the columns were shared, this table used its own ids: 'duration'
  // meant moving time and 'speed' meant average moving speed. The canonical
  // list calls those 'active' and 'mvspd' (and has its own 'duration' for
  // elapsed time), so rename them once. 'num' is a long-retired column.
  const ID_RENAMES = { duration: 'active', speed: 'mvspd' };

  function _get(k) { try { return localStorage.getItem(k); } catch(e) { return null; } }
  function _set(k, v) { try { localStorage.setItem(k, v); } catch(e) {} }

  function _migrate() {
    if (_get(K_VER) === '2') return;
    try {
      const ids = JSON.parse(_get(K_COLS) || 'null');
      if (Array.isArray(ids)) {
        _set(K_COLS, JSON.stringify(ids.filter(id => id !== 'num')
                                       .map(id => ID_RENAMES[id] || id)));
      }
      const ws = JSON.parse(_get(K_W) || 'null');
      if (ws && typeof ws === 'object') {
        const out = {};
        Object.keys(ws).forEach(k => {
          // An older build stored 'name' as minmax(Npx,1fr), which keeps the
          // column flexible and so unshrinkable. Collapse it to its px floor.
          const m = String(ws[k]).match(/^minmax\((\d+)px/);
          out[ID_RENAMES[k] || k] = m ? m[1] + 'px' : ws[k];
        });
        _set(K_W, JSON.stringify(out));
      }
    } catch(e) {}
    _set(K_VER, '2');
  }
  _migrate();

  function loadColIds() {
    try {
      const saved = JSON.parse(_get(K_COLS) || 'null');
      if (saved && Array.isArray(saved) && saved.length >= 2) return saved;
    } catch(e) {}
    return DEFAULT_COL_IDS.slice();
  }
  function saveColIds() { _set(K_COLS, JSON.stringify(colIds)); }

  let colIds = loadColIds();
  let widthOverrides = {};
  try { widthOverrides = JSON.parse(_get(K_W) || '{}'); } catch(e) {}
  function saveWidths() { _set(K_W, JSON.stringify(widthOverrides)); }

  let sortBy = 'stage_num', sortDir = 'asc';
  let suppressClick = false;
  let touchDragPending = null;   // { colId, div, startX, startY }
  let touchDrag = null;          // { colId, ghost, startX, origLeft, toId }
  let hbarDrag = false, hbarStartX = 0, hbarStartScroll = 0;

  function activeCols() { return colIds.map(id => COLS.find(c => c.id === id)).filter(Boolean); }
  function colsTemplate() { return activeCols().map(c => widthOverrides[c.id] || c.w).join(' '); }

  // Sum of the columns' minimum track sizes, pushed onto #stage-h-scroll as
  // --stage-min-w. The header and every row take their min-width from it, so
  // they overflow (and stay aligned) together once the columns outgrow the
  // sidebar — and a flexible track can no longer re-absorb a drag's delta.
  function applyMinW() {
    const hScroll = document.getElementById('stage-h-scroll');
    if (!hScroll) return;
    const minW = activeCols().reduce((t, c) => {
      const w = widthOverrides[c.id] || c.w;
      if (w.startsWith('minmax')) { const m = w.match(/minmax\((\d+)/); return t + (m ? parseInt(m[1]) : 60); }
      return t + (parseInt(w) || 60);
    }, 0);
    hScroll.style.setProperty('--stage-min-w', minW + 'px');
  }

  // ── Page context ───────────────────────────────────────────────────────────
  let ctx = {
    stages:      () => [],
    pointsCache: () => ({}),
    selectedId:  () => null,
    onSelect:    () => {},
    onResize:    () => {},
  };

  // ── Column header ──────────────────────────────────────────────────────────
  function buildHead() {
    const head = document.getElementById('stage-col-head');
    if (!head) return;
    const cols = activeCols();
    head.style.setProperty('--stage-cols', colsTemplate());
    head.innerHTML = '';
    cols.forEach((col, i) => {
      const isSorted = sortBy === col.sort && col.sort;
      const div = document.createElement('div');
      div.className = 'stch' + (isSorted ? ' sorted' : '') + (!col.sort ? ' nosort' : '');
      div.style.alignItems = col.align === 'right' ? 'flex-end' : 'flex-start';
      if (col.titleCell) div.style.paddingLeft = '21px';
      div.dataset.colId = col.id;
      const sortArrow = isSorted ? ` <span class="sort-arr">${sortDir==='desc'?'↓':'↑'}</span>` : '';
      const unitLine = col.unitLabel ? `<span style="font-size:8px;font-weight:400;opacity:.5;text-transform:none;letter-spacing:0;line-height:1">(${col.unitLabel()})</span>` : '';
      div.innerHTML = `<span style="overflow:hidden;min-width:0;display:flex;flex-direction:column;gap:2px"><span style="line-height:1">${col.label}${sortArrow}</span>${unitLine}</span>`;

      if (col.sort) {
        div.addEventListener('click', () => {
          if (suppressClick) { suppressClick = false; return; }
          if (sortBy === col.sort) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
          else { sortBy = col.sort; sortDir = 'asc'; }
          buildHead();
          render();
        });
      }

      // Drag-to-reorder
      div.draggable = true;
      div.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/plain', col.id);
        setTimeout(() => div.classList.add('dragging-col'), 0);
      });
      div.addEventListener('dragend', () => div.classList.remove('dragging-col'));
      div.addEventListener('dragover', e => { e.preventDefault(); div.classList.add('drag-over'); });
      div.addEventListener('dragleave', () => div.classList.remove('drag-over'));
      div.addEventListener('drop', e => {
        e.preventDefault();
        div.classList.remove('drag-over');
        reorder(e.dataTransfer.getData('text/plain'), col.id);
      });
      // Touch drag-to-reorder (iOS/iPad — HTML5 DnD not supported there)
      div.addEventListener('touchstart', e => {
        if (e.touches.length !== 1) return;
        if (e.target.classList.contains('stch-sep')) return;
        const t = e.touches[0];
        touchDragPending = { colId: col.id, div, startX: t.clientX, startY: t.clientY };
      }, {passive: true});

      // Resize separator between columns (mouse and touch)
      if (i < cols.length - 1) {
        const sep = document.createElement('div');
        sep.className = 'stch-sep';
        sep.addEventListener('pointerdown', e => {
          e.preventDefault();
          e.stopPropagation();
          div.draggable = false;
          sep.setPointerCapture(e.pointerId);
          sep.classList.add('resizing');
          const startX  = e.clientX;
          const startPx = div.offsetWidth || (parseInt(col.w) || 60);
          // The title column starts as the one flexible (1fr) track, so all of
          // the sidebar's slack pools there: it swallows another column's delta,
          // and refuses to shrink because it re-absorbs whatever it gives up. As
          // on the Activities page a dragged column becomes a fixed width, so
          // freeze the flexible one before dragging anything else.
          if (!col.titleCell) {
            const flexEl = head.querySelector('.stch[data-col-id="name"]');
            if (flexEl) widthOverrides['name'] = flexEl.offsetWidth + 'px';
          }
          function onMove(me) {
            widthOverrides[col.id] = Math.max(28, startPx + (me.clientX - startX)) + 'px';
            const tpl = colsTemplate();
            head.style.setProperty('--stage-cols', tpl);
            document.querySelectorAll('.stage-row').forEach(r => r.style.setProperty('--stage-cols', tpl));
            applyMinW();   // live, so the grid widens into the scroll area
            updateHScrollbar();
          }
          function onUp() {
            div.draggable = true;
            sep.classList.remove('resizing');
            sep.removeEventListener('pointermove',   onMove);
            sep.removeEventListener('pointerup',     onUp);
            sep.removeEventListener('pointercancel', onUp);
            saveWidths();
            buildHead();
            render();
          }
          sep.addEventListener('pointermove',   onMove);
          sep.addEventListener('pointerup',     onUp);
          sep.addEventListener('pointercancel', onUp);
        });
        div.appendChild(sep);
      }
      head.appendChild(div);
    });
    applyMinW();
    requestAnimationFrame(() => updateHScrollbar());
  }

  function reorder(fromId, toId) {
    if (!fromId || fromId === toId) return;
    const fromIdx = colIds.indexOf(fromId), toIdx = colIds.indexOf(toId);
    if (fromIdx < 0 || toIdx < 0) return;
    colIds.splice(fromIdx, 1);
    colIds.splice(toIdx, 0, fromId);
    saveColIds();
    suppressClick = true;
    buildHead();
    render();
  }

  // ── Visible Columns picker ─────────────────────────────────────────────────
  function buildPicker() {
    const picker = document.getElementById('stage-col-picker');
    if (!picker) return;
    picker.innerHTML = '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:2px 8px 6px">Visible Columns</div>';
    COLS.forEach(col => {
      const item = document.createElement('label');
      item.className = 'cp-item';
      item.innerHTML = `<input type="checkbox" ${colIds.includes(col.id)?'checked':''} data-col-id="${col.id}"> ${col.label}`;
      item.querySelector('input').addEventListener('change', e => {
        if (e.target.checked) {
          if (!colIds.includes(col.id)) {
            // Insert at the position it holds in the default set rather than
            // appending, so re-adding a column puts it back where it belongs.
            const defaultPos = DEFAULT_COL_IDS.indexOf(col.id);
            if (defaultPos <= 0) { colIds.push(col.id); }
            else {
              let insertAfter = -1;
              for (let p = defaultPos - 1; p >= 0; p--) {
                const idx = colIds.indexOf(DEFAULT_COL_IDS[p]);
                if (idx >= 0) { insertAfter = idx; break; }
              }
              colIds.splice(insertAfter + 1, 0, col.id);
            }
          }
        } else {
          if (colIds.length <= 2) { e.target.checked = true; return; }  // keep at least 2
          colIds = colIds.filter(id => id !== col.id);
        }
        saveColIds();
        buildHead();
        render();
      });
      picker.appendChild(item);
    });
  }

  function togglePicker(e) {
    e.stopPropagation();
    const picker = document.getElementById('stage-col-picker');
    if (!picker) return;
    if (!picker.classList.contains('open')) {
      buildPicker();
      const rect = e.currentTarget.getBoundingClientRect();
      picker.style.top   = (rect.bottom + 2) + 'px';
      picker.style.right = (window.innerWidth - rect.right) + 'px';
      picker.style.left  = 'auto';
    }
    picker.classList.toggle('open');
  }

  // ── Rows ───────────────────────────────────────────────────────────────────
  function sorted() {
    const list = ctx.stages().slice();
    const keyFn = SORT_KEY[sortBy] || SORT_KEY.stage_num;
    const dir = sortDir === 'desc' ? -1 : 1;
    list.sort((a, b) => { const ka=keyFn(a), kb=keyFn(b); return ka<kb?-dir:ka>kb?dir:0; });
    return list;
  }

  function render() {
    const list = document.getElementById('stage-list');
    if (!list) return;
    const stages = ctx.stages();
    const empty = document.getElementById('sidebar-empty');
    if (empty) empty.style.display = stages.length ? 'none' : '';
    _altIds = _altStageIds(stages, ctx.pointsCache());

    // Phone: the shared simple card list. Desktop: the column table.
    if (TourNav.isPhone()) {
      TourStageDetail.stageList(list, { stages, pointsCache: ctx.pointsCache() });
      return;
    }

    const selId = ctx.selectedId();
    const cols  = activeCols();
    const tpl   = colsTemplate();
    list.innerHTML = '';
    sorted().forEach(s => {
      const row = document.createElement('div');
      row.className = 'stage-row' + (String(s.id)===String(selId) ? ' active' : '');
      row.dataset.id = s.id;
      row.style.setProperty('--stage-cols', tpl);
      row.innerHTML = cols.map(col => {
        if (col.titleCell) return `<div class="st-name-cell">${col.render(s)}</div>`;
        return `<div class="st-cell${col.align==='right'?' st-cell-right':''}">${col.render(s)}</div>`;
      }).join('');
      row.addEventListener('click', () => ctx.onSelect(String(s.id)));
      list.appendChild(row);
    });
  }

  // ── Horizontal scrollbar ───────────────────────────────────────────────────
  function updateHScrollbar() {
    const hScroll = document.getElementById('stage-h-scroll');
    const track   = document.getElementById('stage-hscroll-track');
    const thumb   = document.getElementById('stage-hscroll-thumb');
    if (!hScroll || !track || !thumb) return;
    const sw = hScroll.scrollWidth, cw = hScroll.clientWidth;
    if (sw <= cw + 1) { track.style.display = 'none'; return; }
    track.style.display = 'block';
    thumb.style.width = Math.max(20, Math.round((cw / sw) * cw)) + 'px';
    thumb.style.left  = Math.round((hScroll.scrollLeft / sw) * cw) + 'px';
  }

  function initHScrollbar() {
    const hScroll = document.getElementById('stage-h-scroll');
    const thumb   = document.getElementById('stage-hscroll-thumb');
    if (!hScroll || !thumb) return;
    hScroll.addEventListener('scroll', updateHScrollbar);
    window.addEventListener('resize', updateHScrollbar);

    const start = x => { hbarDrag = true; hbarStartX = x; hbarStartScroll = hScroll.scrollLeft; thumb.classList.add('dragging'); };
    const move  = x => {
      if (!hbarDrag) return;
      const sw = hScroll.scrollWidth, cw = hScroll.clientWidth;
      hScroll.scrollLeft = hbarStartScroll + (x - hbarStartX) * (sw / cw);
      updateHScrollbar();
    };
    const end = () => { hbarDrag = false; thumb.classList.remove('dragging'); };

    thumb.addEventListener('mousedown',  e => { start(e.clientX); e.preventDefault(); });
    document.addEventListener('mousemove', e => { if (hbarDrag) move(e.clientX); });
    document.addEventListener('mouseup',   end);
    thumb.addEventListener('touchstart',  e => { start(e.touches[0].clientX); }, {passive: true});
    document.addEventListener('touchmove', e => { if (hbarDrag) { move(e.touches[0].clientX); e.preventDefault(); } }, {passive: false});
    document.addEventListener('touchend',  end);
  }

  // ── Touch column reorder (iOS/iPad) ────────────────────────────────────────
  function initTouchReorder() {
    document.addEventListener('touchmove', e => {
      if (hbarDrag) return;   // scrollbar thumb drag owns this gesture

      if (touchDragPending) {
        const t = e.touches[0];
        if (Math.abs(t.clientX - touchDragPending.startX) > 8 ||
            Math.abs(t.clientY - touchDragPending.startY) > 4) {
          const div  = touchDragPending.div;
          const rect = div.getBoundingClientRect();
          const ghost = document.createElement('div');
          ghost.textContent = div.textContent.replace(/[↑↓]/g,'').trim();
          ghost.style.cssText = `position:fixed;top:${rect.top}px;left:${rect.left}px;width:${rect.width}px;height:${rect.height}px;display:flex;align-items:center;padding:0 4px;pointer-events:none;z-index:9999;opacity:.85;background:#3a3a3c;border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,.5);font-size:10px;font-weight:600;color:#f5f5f7;text-transform:uppercase;letter-spacing:.04em`;
          document.body.appendChild(ghost);
          div.classList.add('dragging-col');
          touchDrag = { colId: touchDragPending.colId, ghost, startX: t.clientX, origLeft: rect.left, toId: null };
          touchDragPending = null;
        }
      }
      if (!touchDrag) return;
      e.preventDefault();
      const t = e.touches[0];
      touchDrag.ghost.style.left = (touchDrag.origLeft + (t.clientX - touchDrag.startX)) + 'px';
      touchDrag.ghost.style.display = 'none';
      const el = document.elementFromPoint(t.clientX, t.clientY);
      touchDrag.ghost.style.display = '';
      const target = el && el.closest('.stch[data-col-id]');
      document.querySelectorAll('.stch').forEach(h => h.classList.remove('drag-over'));
      if (target && target.dataset.colId !== touchDrag.colId) {
        target.classList.add('drag-over');
        touchDrag.toId = target.dataset.colId;
      } else {
        touchDrag.toId = null;
      }
    }, {passive: false});

    document.addEventListener('touchend', () => {
      touchDragPending = null;
      if (!touchDrag) return;
      document.querySelectorAll('.stch').forEach(h => { h.classList.remove('drag-over'); h.classList.remove('dragging-col'); });
      touchDrag.ghost.remove();
      const { colId, toId } = touchDrag;
      touchDrag = null;
      reorder(colId, toId);
    });

    document.addEventListener('touchcancel', () => {
      touchDragPending = null;
      if (touchDrag) { touchDrag.ghost.remove(); touchDrag = null; }
      document.querySelectorAll('.stch').forEach(h => { h.classList.remove('drag-over'); h.classList.remove('dragging-col'); });
    });
  }

  // ── Sidebar | main divider ─────────────────────────────────────────────────
  // Max width matches the Activities page: 65% of the window, not a fixed cap.
  const MIN_W = 180;
  const maxW = () => Math.max(MIN_W, window.innerWidth * 0.65);

  function initSidebarResize() {
    const handle  = document.getElementById('sidebar-resize');
    const sidebar = document.getElementById('sidebar');
    if (!handle || !sidebar) return;
    handle.addEventListener('pointerdown', e => {
      if (e.button !== undefined && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      try { handle.setPointerCapture(e.pointerId); } catch(_) {}
      const startX = e.clientX, startW = sidebar.offsetWidth;
      handle.classList.add('dragging');
      document.body.style.cssText = 'cursor:col-resize;user-select:none';
      function pm(ev) {
        sidebar.style.width = Math.max(MIN_W, Math.min(maxW(), startW + (ev.clientX - startX))) + 'px';
        updateHScrollbar();
        ctx.onResize();
      }
      function pu(ev) {
        try { handle.releasePointerCapture(ev.pointerId); } catch(_) {}
        handle.classList.remove('dragging');
        document.body.style.cssText = '';
        _set('ascent-tour-sidebar-w', sidebar.offsetWidth);
        handle.removeEventListener('pointermove',   pm);
        handle.removeEventListener('pointerup',     pu);
        handle.removeEventListener('pointercancel', pu);
      }
      handle.addEventListener('pointermove',   pm);
      handle.addEventListener('pointerup',     pu);
      handle.addEventListener('pointercancel', pu);
    });
    // Restore saved width (desktop only — phones have CSS-controlled widths)
    if (!TourNav.isPhone()) {
      const w = parseInt(_get('ascent-tour-sidebar-w'));
      if (w >= MIN_W) sidebar.style.width = Math.min(w, maxW()) + 'px';
    }
  }

  /* init(context)
       stages()      → the current stage array
       pointsCache() → stage id → points, for spotting alternate routes
       selectedId()  → the selected stage id, or null
       onSelect(id)  → called when a row is clicked
       onResize()    → called while the sidebar divider is dragged (map refit) */
  function init(context) {
    Object.assign(ctx, context || {});
    initHScrollbar();
    initTouchReorder();
    initSidebarResize();
    document.addEventListener('click', e => {
      if (!e.target.closest('#stage-col-gear-btn'))
        document.getElementById('stage-col-picker')?.classList.remove('open');
    });
    buildHead();
  }

  return {
    init, buildHead, buildPicker, togglePicker, render, sorted,
    updateHScrollbar, applyMinW, colsTemplate, activeCols,
    COLS, DEFAULT_COL_IDS, SORT_KEY,
    altIds:         () => _altIds,
    colIds:         () => colIds,
    widthOverrides: () => widthOverrides,
    sortState:      () => ({ by: sortBy, dir: sortDir }),
  };
})();
window.StageTable = StageTable;
