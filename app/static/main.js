// ── UI PREFS (server-seeded, persisted per user) ──────────────────────────────
const _INIT_PREFS = window._UI_PREFS_INITIAL || {};
const _uiPrefs = Object.assign({}, _INIT_PREFS);
let _uiPrefsSaveTimer = null;
function _uiPrefsGet(key, fallback) {
  if (_uiPrefs[key] !== undefined && _uiPrefs[key] !== null) return _uiPrefs[key];
  try { const v = localStorage.getItem(key); if (v !== null) return v; } catch(e) {}
  return (fallback !== undefined) ? fallback : null;
}
function _uiPrefsSet(key, val) {
  _uiPrefs[key] = val;
  try { localStorage.setItem(key, val); } catch(e) {}
  clearTimeout(_uiPrefsSaveTimer);
  _uiPrefsSaveTimer = setTimeout(_uiPrefsSaveToServer, 1500);
}
async function _uiPrefsSaveToServer() {
  try {
    await fetch('/api/settings/ui-prefs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prefs: _uiPrefs}),
    });
  } catch(e) {}
}

// ── STATE ────────────────────────────────────────────────────────────────────
let elevRenderVersion = 0; // incremented each selectActivity call to cancel stale renders
let currentAct    = null;  // last selected activity object
// Session caches for Strava social data — cleared on page refresh, never re-fetched for same activity
const _kudosCountCache    = new Map();   // activity id → kudos count (null = fetch failed)
const _commentsCountCache = new Map();   // activity id → comment count (null = fetch failed)
const _kudosListCache     = new Map();   // activity id → athletes array
const _commentsListCache  = new Map();   // activity id → comments array
let currentCharts = null;  // last fetched chart data
let cachedProfile    = null;  // user profile (max_hr, ftp_watts) — fetched once per activity
let _hasAnthropicKey = false; // set from /api/me at init
let _cachedGear = null;       // { bikes:[{id,name}], shoes:[{id,name}] } — loaded once per session

// Strava sport_type enum (grouped for readability in the dropdown)
const STRAVA_SPORT_TYPES = [
  'EBikeRide','EMountainBikeRide','GravelRide','Handcycle','MountainBikeRide','Ride','RollerSki','Velomobile','VirtualRide',
  'Run','TrailRun','VirtualRun',
  'Hike','Walk',
  'Canoeing','Kayaking','Kitesurf','Rowing','Sail','StandUpPaddling','Surfing','Swim','VirtualRow','Windsurf',
  'AlpineSki','BackcountrySki','IceSkate','InlineSkate','NordicSki','Skateboard','Snowboard','Snowshoe',
  'Crossfit','Elliptical','HighIntensityIntervalTraining','Pilates','StairStepper','WeightTraining','Wheelchair','Workout','Yoga',
  'Badminton','Golf','Pickleball','Racquetball','RockClimbing','Soccer','Squash','TableTennis','Tennis',
];

async function fetchGear() {
  if (_cachedGear) return _cachedGear;
  try {
    const stored = localStorage.getItem('ascent-strava-gear');
    if (stored) {
      const { data, ts } = JSON.parse(stored);
      if (Date.now() - ts < 86400000) { _cachedGear = data; return _cachedGear; }
    }
  } catch(e) {}
  try {
    const r = await fetch('/api/strava/gear');
    if (r.ok) {
      _cachedGear = await r.json();
      localStorage.setItem('ascent-strava-gear', JSON.stringify({ data: _cachedGear, ts: Date.now() }));
    }
  } catch(e) {}
  return _cachedGear || { bikes: [], shoes: [] };
}

async function ensureProfile() {
  if (cachedProfile) return cachedProfile;
  try { const r = await fetch('/api/settings/training-zones'); cachedProfile = await r.json(); }
  catch(e) { cachedProfile = {}; }
  return cachedProfile;
}

// ── UNITS ─────────────────────────────────────────────────────────────────────
const U = {
  metric: false,  // false = statute, true = metric — loaded from DB on init

  // Conversion helpers
  dist:  (mi)  => U.metric ? +(mi * 1.60934).toFixed(2) : mi,
  distS: (mi)  => U.metric ? (+(mi * 1.60934).toFixed(2)) + ' km' : mi + ' mi',
  speed: (mph) => U.metric ? +(mph * 1.60934).toFixed(1) : mph,
  speedS:(mph) => U.metric ? (+(mph * 1.60934).toFixed(1)) + ' km/h' : (+mph).toFixed(1) + ' mph',
  alt:   (ft)  => U.metric ? Math.round(ft * 0.3048) : Math.round(ft),
  altS:  (ft)  => U.metric ? Math.round(ft * 0.3048) + ' m'  : Math.round(ft) + ' ft',
  climb: (ft)  => U.metric ? Math.round(ft * 0.3048) : Math.round(ft),
  climbS:(ft)  => U.metric ? Math.round(ft * 0.3048) + ' m'  : Math.round(ft) + ' ft',
  temp:  (f)   => U.metric ? +((f - 32) * 5/9).toFixed(1) : f,
  tempS: (f)   => U.metric ? +((f - 32) * 5/9).toFixed(1) + ' °C' : Math.round(f) + ' °F',

  // Label strings
  distUnit:  () => U.metric ? 'km'   : 'mi',
  speedUnit: () => U.metric ? 'km/h' : 'mph',
  altUnit:   () => U.metric ? 'm'    : 'ft',
  climbUnit: () => U.metric ? 'm'    : 'ft',
  tempUnit:  () => U.metric ? '°C'   : '°F',

  // Load from profile and trigger full redraw
  async load() {
    try {
      const p = await ensureProfile();
      U.metric = !!p.use_metric;
    } catch(e) { U.metric = false; }
    U.apply();
  },

  // Re-render everything that depends on units
  apply() {
    // Activity list columns — re-render
    buildColHead();
    renderVirtualList();
    // Detail panel — re-render if an activity is selected
    if (currentAct) {
      const det = document.getElementById('act-detail');
      if (det && det.style.display !== 'none') det.innerHTML = buildDetailHTML(currentAct);
      // Map badge
      const badge = document.getElementById('mapBadge');
      if (badge && badge.style.display !== 'none' && currentAct.distance_mi) {
        badge.textContent = `${U.distS(currentAct.distance_mi)} · ${U.climbS(currentAct.total_climb_ft||0)} climb`;
      }
    }
    // Elevation chart — redraw with new axis units
    if (elevChartData) drawElevation(elevChartData);
    // Transport bar labels
    const altLbl = document.getElementById('as-alt-lbl');
    const dstLbl = document.getElementById('as-dst-lbl');
    const spdLbl = document.getElementById('as-spd-lbl');
    if (altLbl) altLbl.textContent = U.altUnit();
    if (dstLbl) dstLbl.textContent = U.distUnit();
    if (spdLbl) spdLbl.textContent = U.speedUnit();
  },
};

function zoneColorFor(val, boundaries) {
  // boundaries: [z1max, z2max, z3max, z4max]
  if (!val || val <= 0 || !boundaries) return null;
  for (let i = 0; i < boundaries.length; i++) {
    if (val <= boundaries[i]) return ZONE_COLORS[i];
  }
  return ZONE_COLORS[4];
}

function hrBoundsFor(profile, act) {
  const maxHR = (profile && profile.max_hr) || (act && act.max_heartrate) || 190;
  return [maxHR*.60, maxHR*.70, maxHR*.80, maxHR*.90].map(Math.round);
}

function pwrBoundsFor(profile, act) {
  const ftp = (profile && profile.ftp_watts) || (act && act.avg_power ? Math.round(act.avg_power / 0.75) : 0);
  if (!ftp) return null;
  return [ftp*.55, ftp*.75, ftp*.90, ftp*1.05].map(Math.round);
}

const state = {
  all: [],          // full loaded list (all activities from server)
  filtered: [],     // after search/type/year filter applied client-side
  sortBy:  'start_time',
  sortDir: 'desc',
  search:  '',
  actType: '',
  year:    '',
  selectedId: null,
  selectedIds: new Set(), // multi-select set
  selectMode: false,      // touch long-press selection mode
};

// ── COLUMN DEFINITIONS ────────────────────────────────────────────────────────
// User lookup map: id → {username, avatar_url} (populated async during init)
const userMap = {};

function renderUserCell(a) {
  const u = userMap[a.user_id];
  if (!u) return '';
  const name = escHtml(u.username || '');
  if (u.avatar_url) {
    return `<img src="${u.avatar_url}" alt="" style="width:22px;height:22px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:5px;flex-shrink:0">${name}`;
  }
  return name;
}

const ALL_COLS = [
  {id:'user',      label:'User',        sort:'',             w:'110px', align:'left',  render: renderUserCell},
  {id:'date',      label:'Date/Time',   sort:'start_time',   w:'168px', align:'left',  render: a => fmtDate(a.start_time)},
  {id:'name',      label:'Title',       sort:'name',         w:'1fr',   align:'left',  render: a => escHtml(a.name) + (a.strava_activity_id?`<a href="https://www.strava.com/activities/${a.strava_activity_id}" target="_blank" class="ac-strava" title="View on Strava">↗</a>`:'')},
  {id:'dist',      label:'Dist',        sort:'distance_m',   w:'78px',  align:'right', unitLabel: () => U.distUnit(),  render: a => fmtN(U.dist(a.distance_mi),2)},
  {id:'active',    label:'MovTime',     sort:'active_time',  w:'76px',  align:'right', render: a => fmtHMS(a.active_time)},
  {id:'duration',  label:'Duration',    sort:'duration',     w:'76px',  align:'right', render: a => fmtHMS(a.duration)},
  {id:'climb',     label:'Climb',       sort:'total_climb_m',w:'72px',  align:'right', unitLabel: () => U.climbUnit(), render: a => a.total_climb_ft?U.climb(a.total_climb_ft):'—'},
  {id:'mvspd',     label:'MvSpd',       sort:'avg_speed_mps',w:'88px',  align:'right', unitLabel: () => U.speedUnit(), render: a => fmtN(U.speed(a.avg_speed_mph),1)},
  {id:'avgspd',    label:'AvgSpd',      sort:'',             w:'88px',  align:'right', unitLabel: () => U.speedUnit(), render: a => { const s=(a.duration&&a.distance_mi)?(a.distance_mi/(a.duration/3600)):0; return fmtN(U.speed(s),1); }},
  {id:'hr',        label:'HR',          sort:'avg_heartrate',w:'52px',  align:'right', render: a => a.avg_heartrate?Math.round(a.avg_heartrate):'—'},
  {id:'maxhr',     label:'MaxHR',       sort:'',             w:'58px',  align:'right', render: a => a.max_heartrate?Math.round(a.max_heartrate):'—'},
  {id:'power',     label:'Power',       sort:'',             w:'58px',  align:'right', render: a => a.avg_power?Math.round(a.avg_power)+' W':'—'},
  {id:'cadence',   label:'Cadence',     sort:'',             w:'62px',  align:'right', render: a => a.avg_cadence?Math.round(a.avg_cadence)+' rpm':'—'},
  {id:'calories',  label:'Cal',         sort:'calories',     w:'52px',  align:'right', render: a => a.calories?Math.round(a.calories):'—'},
  {id:'suffer',    label:'Suffer',      sort:'',             w:'52px',  align:'right', render: a => a.suffer_score?Math.round(a.suffer_score):'—'},
  {id:'pace',      label:'Pace',        sort:'',             w:'70px',  align:'right', render: a => a.avg_pace||'—'},
];

const DEFAULT_COL_IDS = ['date','user','name','dist','active','climb','mvspd','hr'];

function loadColPrefs() {
  try {
    const saved = JSON.parse(_uiPrefsGet('ascent-cols') || 'null');
    if (saved && Array.isArray(saved) && saved.length >= 2) {
      // Migrate: ensure 'user' sits immediately after 'date' whenever both are present
      const ui = saved.indexOf('user'), di = saved.indexOf('date');
      if (ui >= 0 && di >= 0 && ui !== di + 1) {
        saved.splice(ui, 1);                       // remove from current position
        saved.splice(saved.indexOf('date') + 1, 0, 'user'); // insert after 'date'
        saveColPrefs(saved);
      }
      return saved;
    }
  } catch(e) {}
  return DEFAULT_COL_IDS;
}
function saveColPrefs(ids) { _uiPrefsSet('ascent-cols', JSON.stringify(ids)); }

let activeColIds = loadColPrefs();

function getActiveCols() {
  return activeColIds.map(id => ALL_COLS.find(c => c.id === id)).filter(Boolean);
}

// Per-column width overrides (set by dragging separators)
let colWidthOverrides = {};
try { colWidthOverrides = JSON.parse(_uiPrefsGet('ascent-col-widths') || '{}'); } catch(e) {}
function saveColWidths() { _uiPrefsSet('ascent-col-widths', JSON.stringify(colWidthOverrides)); }

function getColW(col) {
  return colWidthOverrides[col.id] || col.w;
}

function buildColsTemplate() {
  return getActiveCols().map(c => getColW(c)).join(' ');
}

const COLS = buildColsTemplate(); // kept for backward compat but dynamic now
function currentCOLS() { return buildColsTemplate(); }
const ROW_H = 34;

// Event delegation for activity rows — handles click, cmd-click, shift-click
document.addEventListener('click', function(e) {
  // Suppress click fired immediately after a long-press
  if (_lpSuppressClick) { _lpSuppressClick = false; return; }
  // Let Strava links (and any other <a> inside a row) pass through normally
  if (e.target.closest('a')) return;
  const row = e.target.closest('.act-row');
  if (!row) return;
  const id = parseInt(row.dataset.id);
  if (isNaN(id)) return;
  handleRowClick(e, id);
}, true); // useCapture=true to fire before any other handlers


// ── HELPERS ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts*1000).toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'});
}
function fmtHMS(s) {
  if (!s) return '—';
  const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),ss=Math.floor(s%60);
  return h?`${h}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`:`${m}:${String(ss).padStart(2,'0')}`;
}
function fmtN(n,d=1){return (n&&Number(n)!==0)?Number(n).toFixed(d):'—'}

// ── VIRTUAL SCROLL ────────────────────────────────────────────────────────────
let vsScrollHandler  = null;
let vsResizeObserver = null;

function renderVirtualList() {
  const container = document.getElementById('act-list');
  const spacer    = document.getElementById('list-spacer');
  const rows      = document.getElementById('list-rows');
  const acts      = state.filtered;

  spacer.style.height = (acts.length * ROW_H) + 'px';

  function paint() {
    const scrollTop = container.scrollTop;
    const viewH     = container.clientHeight;
    const startIdx  = Math.max(0, Math.floor(scrollTop / ROW_H) - 5);
    const endIdx    = Math.min(acts.length, Math.ceil((scrollTop + viewH) / ROW_H) + 5);

    rows.style.transform = `translateY(${startIdx * ROW_H}px)`;

    let html = '';
    for (let i = startIdx; i < endIdx; i++) {
      const a   = acts[i];
      const isSel     = a.id === state.selectedId;
      const isMulti   = state.selectedIds.has(a.id);
      const selClass  = isSel ? ' selected' : (isMulti ? ' multi-sel' : '');
      const cols = getActiveCols();
      const colsT = currentCOLS();
      html += `<div class="act-row${selClass}" data-id="${a.id}" style="--cols:${colsT}">
        ${cols.map(col => `<div class="ac ${col.align==='right'?'ac-num':'ac-date'}" style="${col.id==='name'?'min-width:0':''}">${col.render(a)}</div>`).join('')}
      </div>`;
    }
    rows.innerHTML = html;
  }

  // remove old scroll listener
  if (vsScrollHandler) container.removeEventListener('scroll', vsScrollHandler);
  vsScrollHandler = paint;
  container.addEventListener('scroll', paint, {passive:true});

  // Repaint when the container is resized (handles iPad where clientHeight
  // may be 0 on first paint because the flex layout hasn't settled yet).
  if (vsResizeObserver) vsResizeObserver.disconnect();
  if (window.ResizeObserver) {
    vsResizeObserver = new ResizeObserver(paint);
    vsResizeObserver.observe(container);
  }

  paint();

  document.getElementById('actCount').textContent =
    acts.length === state.all.length ? `${acts.length} activities` : `${acts.length} of ${state.all.length}`;
  _updateMultiActions();
}

// ── SORT & FILTER ─────────────────────────────────────────────────────────────
const SORT_KEY = {
  start_time:    a => a.start_time || 0,
  name:          a => (a.name||'').toLowerCase(),
  distance_m:    a => a.distance_mi || 0,
  active_time:   a => a.active_time || 0,
  total_climb_m: a => a.total_climb_ft || 0,
  avg_speed_mps: a => a.avg_speed_mph || 0,
  avg_heartrate: a => a.avg_heartrate || 0,
  calories:      a => a.calories || 0,
};

function applyFilterAndSort() {
  let list = state.all.slice();

  // filter
  const q = state.search.toLowerCase();
  if (q)           list = list.filter(a => (a.name||'').toLowerCase().includes(q) || (a.notes||'').toLowerCase().includes(q));
  if (state.actType) list = list.filter(a => a.activity_type === state.actType);
  if (state.year)    list = list.filter(a => a.start_time && new Date(a.start_time*1000).getFullYear() === Number(state.year));

  // sort
  const keyFn = SORT_KEY[state.sortBy] || SORT_KEY.start_time;
  const dir    = state.sortDir === 'desc' ? -1 : 1;
  list.sort((a,b) => {
    const ka = keyFn(a), kb = keyFn(b);
    return ka < kb ? -dir : ka > kb ? dir : 0;
  });

  state.filtered = list;
  renderVirtualList();
}

function updateSortHeaders() {
  document.querySelectorAll('.ch[data-col]').forEach(el => {
    const col = el.dataset.col;
    const isSorted = col === state.sortBy;
    el.classList.toggle('sorted', isSorted);
    // rebuild content: text + arrow
    const label = el.textContent.replace(/[↑↓]/g,'').trim();
    el.innerHTML = isSorted
      ? `${label} <span class="sort-arr">${state.sortDir==='desc'?'↓':'↑'}</span>`
      : label;
  });
}

// ── LOAD ALL ACTIVITIES ───────────────────────────────────────────────────────
// Multi-user view state: selectedUserIds is a Set of user IDs to show.
// Empty set = show only own activities (myId).
const viewState = { myId: null, myUsername: null, selectedUserIds: new Set() };

function _viewUserIdsParam() {
  return viewState.selectedUserIds.size > 0
    ? [...viewState.selectedUserIds].join(',')
    : (viewState.myId !== null ? String(viewState.myId) : '');
}

function _updateViewBadge() {
  const badge = document.getElementById('viewing-user-badge');
  if (!badge) return;
  const ids = viewState.selectedUserIds.size > 0
    ? [...viewState.selectedUserIds]
    : (viewState.myId !== null ? [viewState.myId] : []);
  const names = ids.map(id => (userMap[id] && userMap[id].username) || '?');
  const label = names.join(', ');
  badge.textContent = label;
  badge.style.display = label ? 'inline' : 'none';
  // Keep mobile header user button in sync
  const mobBtn = document.getElementById('mob-user-btn');
  if (mobBtn) { mobBtn.textContent = label; mobBtn.style.display = 'flex'; }
}

async function loadAll() {
  buildColHead();
  const ids = _viewUserIdsParam();
  let url = '/activities/list?limit=2000&offset=0&sort_by=start_time&sort_dir=desc';
  if (ids) url += '&view_user_ids=' + ids;
  const r = await fetch(url);
  const d = await r.json();
  state.all = d.activities;
  applyFilterAndSort();
  const _pendingActId = sessionStorage.getItem('selectActivityId');
  if (_pendingActId) {
    sessionStorage.removeItem('selectActivityId');
    const _target = state.filtered.find(a => String(a.id) === _pendingActId);
    if (_target) { selectActivity(_target.id); scrollToSelected(); return; }
  }
  if (state.filtered.length) selectActivity(state.filtered[0].id);
}

// ── MAP ───────────────────────────────────────────────────────────────────────
let leafMap=null, trackLayer=null, startMark=null, endMark=null, tileLayer=null;

function refitMap() {
  if (!leafMap) return;
  leafMap.invalidateSize({animate: false});
  if (trackLayer) leafMap.fitBounds(trackLayer.getBounds(), {padding:[20,20]});
}

const MAP_STYLES = {
  'osm':        { label: 'Street',    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',                                          attr: '© OpenStreetMap' },
  'cycle':      { label: 'Cycling',   url: 'https://tile.waymarkedtrails.org/cycling/{z}/{x}/{y}.png',                                attr: '© OpenStreetMap, © Waymarked Trails' },
  'topo':       { label: 'Topo',      url: 'https://tile.opentopomap.org/{z}/{x}/{y}.png',                                            attr: '© OpenStreetMap, © OpenTopoMap' },
  'carto-dark': { label: 'Dark',      url: 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',                               attr: '© OpenStreetMap, © CARTO' },
  'carto-light':{ label: 'Light',     url: 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',                              attr: '© OpenStreetMap, © CARTO' },
  'esri-sat':   { label: 'Satellite', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr: '© Esri' },
};

const DEFAULT_STYLE = _uiPrefsGet('ascent-map-style') || 'osm';

function initMap() {
  leafMap = L.map('map',{zoomControl:true,attributionControl:false});
  setMapStyle(DEFAULT_STYLE, false);
}

function setMapStyle(styleKey, save=true) {
  const style = MAP_STYLES[styleKey] || MAP_STYLES['osm'];
  if (tileLayer) leafMap.removeLayer(tileLayer);
  tileLayer = L.tileLayer(style.url, { maxZoom: 19, attribution: style.attr });
  tileLayer.addTo(leafMap);
  if (save) _uiPrefsSet('ascent-map-style', styleKey);
  // Update selector UI
  document.querySelectorAll('.map-style-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.style === styleKey);
  });
}
function setCmpMapStyle(styleKey) {
  const style = MAP_STYLES[styleKey] || MAP_STYLES['osm'];
  _uiPrefsSet('ascent-map-style', styleKey);  // shared key with main map

  // Update main map too
  if (tileLayer && leafMap) {
    leafMap.removeLayer(tileLayer);
    tileLayer = L.tileLayer(style.url, { maxZoom: 19, attribution: style.attr });
    tileLayer.addTo(leafMap);
  }

  // Update compare overview map
  if (cmp.tileLayer && cmp.map) {
    cmp.map.removeLayer(cmp.tileLayer);
    cmp.tileLayer = L.tileLayer(style.url, { maxZoom: 19 });
    cmp.tileLayer.addTo(cmp.map);
  }
  // Update chase-cam map
  if (cmp.zoomTile && cmp.zoomMap) {
    cmp.zoomMap.removeLayer(cmp.zoomTile);
    cmp.zoomTile = L.tileLayer(style.url, { maxZoom: 19 });
    cmp.zoomTile.addTo(cmp.zoomMap);
  }

  // Update all style buttons (main map + compare map)
  document.querySelectorAll('.map-style-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.style === styleKey);
  });
}

function clearMap() {
  [trackLayer,startMark,endMark].forEach(l=>{ if(l) leafMap.removeLayer(l); });
  trackLayer=startMark=endMark=null;
}
function drawTrack(geojson) {
  clearMap();
  const coords = geojson?.geometry?.coordinates;
  if (!coords||coords.length<2) return;
  const valid = coords.filter(c=>Math.abs(c[1])<90&&Math.abs(c[0])<180&&!(c[0]===0&&c[1]===0));
  if (valid.length<2) return;
  const ll = valid.map(c=>[c[1],c[0]]);
  leafMap.invalidateSize({animate: false});
  trackLayer = L.polyline(ll,{color:'#ef4444',weight:3,opacity:.9,smoothFactor:1.5}).addTo(leafMap);

  // Green start flag SVG
  const startIcon = L.divIcon({
    html: `<svg width="15" height="20" viewBox="0 0 20 26" xmlns="http://www.w3.org/2000/svg" style="filter:drop-shadow(0 1px 3px rgba(0,0,0,.9))">
      <line x1="2" y1="0" x2="2" y2="26" stroke="white" stroke-width="2"/>
      <polygon points="2,2 19,7 2,12" fill="#22c55e" stroke="#15803d" stroke-width="1"/>
    </svg>`,
    className: '', iconSize: [15,20], iconAnchor: [2, 20]
  });
  const endIcon = L.divIcon({
    html: `<svg width="17" height="20" viewBox="0 0 22 26" xmlns="http://www.w3.org/2000/svg" style="filter:drop-shadow(0 1px 3px rgba(0,0,0,.9))">
      <line x1="2" y1="0" x2="2" y2="26" stroke="white" stroke-width="2"/>
      <rect x="2" y="2" width="20" height="12" fill="white" stroke="#333" stroke-width="0.5"/>
      <rect x="2"  y="2"  width="5" height="4" fill="black"/>
      <rect x="12" y="2"  width="5" height="4" fill="black"/>
      <rect x="7"  y="6"  width="5" height="4" fill="black"/>
      <rect x="17" y="6"  width="5" height="4" fill="black"/>
      <rect x="2"  y="10" width="5" height="4" fill="black"/>
      <rect x="12" y="10" width="5" height="4" fill="black"/>
    </svg>`,
    className: '', iconSize: [17,20], iconAnchor: [2, 20]
  });

  // Same-point: start and end within ~300m of each other
  const dLat = Math.abs(ll[0][0] - ll[ll.length-1][0]);
  const dLon = Math.abs(ll[0][1] - ll[ll.length-1][1]);
  const samePoint = dLat < 0.003 && dLon < 0.003;

  if (samePoint) {
    // Center both flags on the midpoint between start and end
    // Offset them horizontally: start to the left (west), end to the right (east)
    const midLat = (ll[0][0] + ll[ll.length-1][0]) / 2;
    const midLon = (ll[0][1] + ll[ll.length-1][1]) / 2;
    const offset = 0.0006;  // ~55m separation
    startMark = L.marker([midLat, midLon - offset], {icon: startIcon, zIndexOffset: 200}).addTo(leafMap);
    endMark   = L.marker([midLat, midLon + offset], {icon: endIcon,   zIndexOffset: 200}).addTo(leafMap);
  } else {
    startMark = L.marker(ll[0],           {icon: startIcon, zIndexOffset: 200}).addTo(leafMap);
    endMark   = L.marker(ll[ll.length-1], {icon: endIcon,   zIndexOffset: 200}).addTo(leafMap);
  }
  leafMap.fitBounds(trackLayer.getBounds(),{padding:[20,20]});
  document.getElementById('mapPlaceholder').style.display='none';
}


// ── ACTIVITY DETAIL ───────────────────────────────────────────────────────────
function handleRowClick(e, id) {
  // In touch select mode: tap toggles selection
  if (state.selectMode) {
    e.preventDefault();
    if (state.selectedIds.has(id)) state.selectedIds.delete(id);
    else state.selectedIds.add(id);
    if (state.selectedIds.size === 0) exitSelectMode();
    else { renderVirtualList(); _updateSelectBar(); }
    return;
  }
  if (e.shiftKey && state.selectedId !== null) {
    e.preventDefault();
    const fromIdx = state.filtered.findIndex(a => a.id === state.selectedId);
    const toIdx   = state.filtered.findIndex(a => a.id === id);
    if (fromIdx >= 0 && toIdx >= 0) {
      const lo = Math.min(fromIdx, toIdx), hi = Math.max(fromIdx, toIdx);
      state.selectedIds.clear();
      for (let i = lo; i <= hi; i++) state.selectedIds.add(state.filtered[i].id);
      renderVirtualList();
    }
    return;
  }
  if (e.metaKey || e.ctrlKey) {
    e.preventDefault();
    e.stopPropagation();
    if (state.selectedIds.has(id)) {
      state.selectedIds.delete(id);
      if (state.selectedId === id)
        state.selectedId = state.selectedIds.size > 0 ? [...state.selectedIds][0] : null;
    } else {
      if (state.selectedId !== null) state.selectedIds.add(state.selectedId);
      state.selectedIds.add(id);
    }
    renderVirtualList();
    return;
  }
  // Normal click
  state.selectedIds.clear();
  selectActivity(id);
}

function deleteSelected() {
  const ids = state.selectedIds.size > 0
    ? [...state.selectedIds]
    : (state.selectedId !== null ? [state.selectedId] : []);
  if (!ids.length) return;

  const count = ids.length;
  const noun  = count === 1 ? 'activity' : `${count} activities`;
  document.getElementById('delete-confirm-msg').textContent =
    `Permanently delete ${noun}? This cannot be undone.`;
  document.getElementById('delete-confirm-overlay').classList.add('open');

  document.getElementById('dcb-delete-btn').onclick = async () => {
    document.getElementById('delete-confirm-overlay').classList.remove('open');
    const r = await fetch('/api/activities', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids}),
    });
    if (!r.ok) { alert('Delete failed'); return; }
    // Remove from state
    state.all      = state.all.filter(a => !ids.includes(a.id));
    state.selectedIds.clear();
    if (ids.includes(state.selectedId)) {
      state.selectedId = null;
      document.getElementById('act-detail').style.display = 'none';
      document.getElementById('no-selection').style.display = 'flex';

      const _ctb = document.getElementById('chart-toolbar'); if (_ctb) _ctb.classList.remove('visible');
      clearMap();
    }
    applyFilterAndSort();
  };
}

async function selectActivity(id) {
  let elevChartDrawn = false;
  const myVersion = ++elevRenderVersion;
  state.selectedId = id;
  animReset();
  // Only clear elevation selection if not in multi-select mode
  if (state.selectedIds.size === 0) setElevSelection(null, null);
  elevZoomReset();
  splitsState.pts = null; splitsState.geo = null; clearSplitHighlights();
  renderVirtualList(); // re-render to update highlight without scroll reset

  document.getElementById('no-selection').style.display='none';
  const det=document.getElementById('act-detail');
  det.style.display='block';
  det.innerHTML='<div style="color:var(--muted);padding:.5rem">Loading…</div>';
  const _ctb = document.getElementById('chart-toolbar'); if (_ctb) _ctb.classList.add('visible');
  document.getElementById('mapPlaceholder').style.display='flex';
  clearMap();

  const act = await fetch(`/activities/${id}/json`).then(r=>r.json());

  // Bail if user clicked another activity while we were fetching
  if (myVersion !== elevRenderVersion) return;

  // Always fetch geo+charts — don't rely on points_saved flag (can be 0 even when points exist)
  const [gR, cR] = await Promise.all([
    fetch(`/api/activities/${id}/geojson`),
    fetch(`/api/activities/${id}/charts`),
  ]);
  const geo    = gR.ok ? await gR.json() : null;
  const charts = cR.ok ? await cR.json() : null;

  // Bail again after parallel fetches complete
  if (myVersion !== elevRenderVersion) return;

  currentAct    = act;
  currentCharts = charts;
  cachedProfile = null;  // reset so zones re-fetch profile for new activity
  ensureProfile(); // pre-fetch so zone colors are ready for chart overlay
  det.innerHTML = buildDetailHTML(act);
  loadPhotos(id); // async
  loadWeatherLocation(id); // async
  loadAISummary(id); // async
  if (act.strava_activity_id) loadKudos(id); // async
  if (act.strava_activity_id) loadComments(id); // async

  // Wait for layout to settle after DOM changes before any rendering
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  if (myVersion !== elevRenderVersion) return;
  _resizeAIChip();

  // Map
  const coords = geo?.geometry?.coordinates;
  if (coords && coords.length > 1) {
    drawTrack(geo);
  } else {
    const type = (act.activity_type || '').toLowerCase();
    const isIndoor = type.includes('zwift') || type.includes('virtual') || type.includes('indoor');

    if (isIndoor && !act.strava_activity_id) {
      document.getElementById('mapPlaceholder').innerHTML = '🖥 Virtual / indoor activity';
      document.getElementById('mapPlaceholder').style.display = 'flex';
    } else if (act.strava_activity_id) {
      // Has Strava ID but no local points — fetch them automatically
      document.getElementById('mapPlaceholder').innerHTML =
        '<span style="color:var(--muted)">⬇ Fetching GPS from Strava…</span>';
      document.getElementById('mapPlaceholder').style.display = 'flex';

      try {
        const fr = await fetch(`/api/activities/${id}/fetch-points`, { method: 'POST' });
        if (myVersion !== elevRenderVersion) return; // user moved on during GPS fetch
        if (fr.ok) {
          const fd = await fr.json();
          // Re-fetch activity (description may now be saved), geo, and charts
          const [aR2, gR2, cR2] = await Promise.all([
            fetch(`/activities/${id}/json`),
            fetch(`/api/activities/${id}/geojson`),
            fetch(`/api/activities/${id}/charts`),
          ]);
          if (myVersion !== elevRenderVersion) return; // user moved on during refetch
          if (aR2.ok) { const act2 = await aR2.json(); Object.assign(act, act2); currentAct = act; }
          const geo2    = gR2.ok ? await gR2.json() : null;
          const charts2 = cR2.ok ? await cR2.json() : null;

          // Rebuild detail HTML now that act may have description
          det.innerHTML = buildDetailHTML(act);
          loadPhotos(id); // re-load after panel rebuild
          loadKudos(id); // re-load after panel rebuild
          loadComments(id); // re-load after panel rebuild

          // Hide placeholder, invalidate map size
          document.getElementById('mapPlaceholder').style.display = 'none';
          if (leafMap) leafMap.invalidateSize({animate: false});

          // Replace canvas to clear any stale Chart.js state
          if (elevChart) { elevChart.destroy(); elevChart = null; }
          const oldCanvas = document.getElementById('elevChart');
          const newCanvas = document.createElement('canvas');
          newCanvas.id = 'elevChart';
          oldCanvas.parentNode.replaceChild(newCanvas, oldCanvas);
          const cl = document.querySelector('.chart-label');
          if (cl) cl.textContent = 'Elevation';

          // Wait two frames: one for DOM update, one for layout
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

          if (geo2?.geometry?.coordinates?.length > 1) {
            drawTrack(geo2);
          } else {
            document.getElementById('mapPlaceholder').innerHTML = '📍 No GPS data available';
            document.getElementById('mapPlaceholder').style.display = 'flex';
          }

          // Draw elevation using the fresh canvas
          if (charts2?.alt_ft?.some(v => v > 0)) {
            await drawElevation(charts2, myVersion);
            animInit(charts2, geo2);  // init animation
            splitsState.pts = charts2; splitsState.geo = geo2;
          } else {
            await drawElevationFromSummary(act, myVersion);
          }
          elevChartDrawn = true;
          loadWeatherLocation(id);
        } else {
          document.getElementById('mapPlaceholder').innerHTML = '📍 Could not fetch GPS from Strava';
          document.getElementById('mapPlaceholder').style.display = 'flex';
        }
      } catch(e) {
        document.getElementById('mapPlaceholder').innerHTML = '📍 GPS fetch failed';
        document.getElementById('mapPlaceholder').style.display = 'flex';
      }
    } else {
      document.getElementById('mapPlaceholder').innerHTML = '📍 No GPS data for this activity';
      document.getElementById('mapPlaceholder').style.display = 'flex';
    }
  }

  // Elevation chart — skip if already drawn inside the Strava fetch block
  if (!elevChartDrawn) {
    const hasAlt = charts && charts.alt_ft && charts.alt_ft.some(v => v > 0);
    if (hasAlt) {
      await drawElevation(charts, myVersion);
      animInit(charts, geo);  // init animation with real point data
  splitsState.pts = charts; splitsState.geo = geo;
    } else {
      await drawElevationFromSummary(act, myVersion);
    }
  }

  const badge=document.getElementById('mapBadge');
  if (geo?.geometry?.coordinates?.length>1) {
    badge.textContent=`${U.distS(act.distance_mi)} · ${U.climbS(act.total_climb_ft||0)} climb`;
    badge.style.display='block';
  } else { badge.style.display='none'; }
  mobilePushDetail(act.title || act.activity_type || 'Activity');
}

function buildDetailHTML(a) {
  const stravaLink = a.strava_activity_id
    ? `<a href="https://www.strava.com/activities/${a.strava_activity_id}" target="_blank" class="strava">View on Strava ↗</a>`
    : '';
  // kudos + comments buttons — hidden until load functions populate counts
  const kudosBtn = a.strava_activity_id
    ? `<button id="kudos-btn" onclick="toggleKudosList(${a.id}, event)" style="display:none;border:1px solid var(--border2);border-radius:4px;padding:3px 7px;font-size:11px;font-weight:600;cursor:pointer;background:transparent;color:var(--text);align-items:center;gap:4px;line-height:1;vertical-align:middle"></button>`
    : '';
  const commentsBtn = a.strava_activity_id
    ? `<button id="comments-btn" onclick="toggleCommentsList(${a.id}, event)" style="display:none;border:1px solid var(--border2);border-radius:4px;padding:3px 7px;font-size:11px;font-weight:600;cursor:pointer;background:transparent;color:var(--text);align-items:center;gap:4px;line-height:1;vertical-align:middle"></button>`
    : '';
  const isDirty  = Boolean(a.local_edited_at);
  const isOwner  = a.user_id === viewState.myId;
  const resyncBtn = a.strava_activity_id
    ? (isOwner
        ? `<button class="resync-btn${isDirty ? ' dirty' : ''}" id="resync-btn" onclick="resyncActivity(${a.id})" title="${isDirty ? 'Push local edits then re-sync from Strava' : 'Re-sync metadata from Strava'}"><span class="resync-icon">↻</span> Sync</button>`
        : `<button class="resync-btn" id="resync-btn" onclick="resyncActivity(${a.id})" title="Re-sync from Strava"><span class="resync-icon">↻</span> Sync</button>`)
    : '';
  const editBtn = (a.strava_activity_id && isOwner)
    ? `<button class="edit-btn" id="edit-act-btn" onclick="startEditActivity(${a.id})" title="Edit title, description, type & equipment">Edit…</button>`
    : '';
  const exportBtn = `<a href="/activities/${a.id}/export/gpx" class="edit-btn" style="text-decoration:none" title="Download as GPX file">↓ GPX</a>`;
  const saveRouteBtn = (a.points_count > 0 || a.points_saved)
    ? `<button class="edit-btn" id="save-route-btn" onclick="openSaveRouteDialog()" title="Save GPS track as a route">+ Route</button>`
    : '';

  // pace string helper: mph → "M:SS/mi" or "M:SS/km"
  function altPaceStr(mph, perMile) {
    if (!mph || mph <= 0) return null;
    const mins = perMile ? 60 / mph : 60 / (mph * 1.60934);
    const m = Math.floor(mins);
    const s = Math.round((mins - m) * 60);
    return `${m}:${String(s).padStart(2,'0')}/${perMile ? 'mi' : 'km'}`;
  }

  // chips: [label, primaryVal, secondaryVal|null]
  // secondaryVal is the alternate-unit line shown smaller below
  const chips = [
    ['Distance',  a.distance_mi       ? U.distS(a.distance_mi)                                         : null,
                  a.distance_mi       ? (U.metric ? (+a.distance_mi.toFixed(2))+' mi'
                                                  : (+(a.distance_mi*1.60934).toFixed(2))+' km') : null],
    ['Mov Time',  a.active_time       ? fmtHMS(a.active_time)                                          : null, null],
    ['Duration',  a.duration          ? fmtHMS(a.duration)                                             : null, null],
    ['Climb',     a.total_climb_ft    ? U.climbS(a.total_climb_ft)                                     : null,
                  a.total_climb_ft    ? (U.metric ? Math.round(a.total_climb_ft)+' ft'
                                                  : Math.round(a.total_climb_ft*0.3048)+' m') : null],
    ['Descent',   a.total_descent_ft  ? U.climbS(a.total_descent_ft)                                   : null,
                  a.total_descent_ft  ? (U.metric ? Math.round(a.total_descent_ft)+' ft'
                                                  : Math.round(a.total_descent_ft*0.3048)+' m') : null],
    ['Mov Spd',   a.avg_speed_mph     ? U.speedS(a.avg_speed_mph)                                      : null,
                  a.avg_speed_mph     ? (U.metric ? (+a.avg_speed_mph.toFixed(1))+' mph'
                                                  : (+(a.avg_speed_mph*1.60934).toFixed(1))+' km/h') : null],
    ['Avg Spd',   (a.duration&&a.distance_mi) ? U.speedS(+(a.distance_mi/(a.duration/3600)).toFixed(1)) : null,
                  (a.duration&&a.distance_mi) ? (()=>{ const mph=+(a.distance_mi/(a.duration/3600)).toFixed(1);
                                                        return U.metric ? mph+' mph' : (+(mph*1.60934).toFixed(1))+' km/h'; })() : null],
    ['Avg Pace',  a.avg_speed_mph     ? altPaceStr(a.avg_speed_mph, !U.metric)                         : null,
                  a.avg_speed_mph     ? altPaceStr(a.avg_speed_mph,  U.metric)                         : null],
    ['Avg HR',    a.avg_heartrate     ? Math.round(a.avg_heartrate)+' bpm'                             : null, null],
    ['Max HR',    a.max_heartrate     ? Math.round(a.max_heartrate)+' bpm'                             : null, null],
    ['Cadence',   a.avg_cadence       ? Math.round(a.avg_cadence)+' rpm'                               : null, null],
    ['Avg Pwr',   a.avg_power         ? Math.round(a.avg_power)+' W'                                   : null, null],
    ['Suffer',    a.suffer_score      ? Math.round(a.suffer_score)+''                                  : null, null],
    ['Type',      a.activity_type    ? escHtml(a.activity_type)                                        : null, null],
    ['Equipment', a.equipment        ? escHtml(a.equipment)                                            : null, null],
  ].filter(([,v])=>v);

  const meta = [
    ['Effort',   a.effort],
    ['Keyword 1',a.keyword1],['Keyword 2',a.keyword2],['Notes tag',a.custom],
  ].filter(([,v])=>v&&v!=='null'&&v!=='0');

  // Visibility display
  const visMap = {everyone:'Public', followers_only:'Followers Only', only_me:'Private'};
  const visIcon = {everyone:'🌍', followers_only:'👥', only_me:'🔒'};
  const effVis = a.strava_visibility || a.effective_visibility;
  const stravaEditLink = a.strava_activity_id ? `<a href="https://www.strava.com/activities/${a.strava_activity_id}/edit" target="_blank" style="opacity:.5;color:inherit;text-decoration:underline;text-underline-offset:2px">(change on Strava)</a>` : `<span style="opacity:.5">(change on Strava)</span>`;
  const visHtml = effVis ? `<div style="font-size:11px;color:var(--muted);margin-bottom:6px">${visIcon[effVis]||''} ${visMap[effVis]||effVis} ${stravaEditLink}</div>` : '';

  // Perceived exertion color: green→yellow→red across 1–10
  function rpeColor(v) {
    v = Math.max(1, Math.min(10, v));
    const t = (v - 1) / 9; // 0→1
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

  let rpeKudosHtml = '';
  if (a.perceived_exertion != null) {
    const v   = a.perceived_exertion;
    const col = rpeColor(v);
    const lbl = _rpeText(v).replace(/\s*\(\d+\)/, '');
    rpeKudosHtml = `<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">
      <div style="width:14px;height:14px;border-radius:3px;background:${col};flex-shrink:0"></div>
      <span style="font-size:12px;color:var(--text);font-weight:500">${lbl}</span>
      <span style="font-size:11px;color:var(--muted)">(${v}/10)</span>
      ${kudosBtn}${commentsBtn}
    </div>`;
  } else if (kudosBtn || commentsBtn) {
    rpeKudosHtml = `<div style="display:flex;align-items:center;gap:4px;margin-bottom:5px">${kudosBtn}${commentsBtn}</div>`;
  }

  const ownerAvatarUrl = a.user_id && userMap[a.user_id]?.avatar_url
    ? userMap[a.user_id].avatar_url.replace('?thumb=1', '') + '?thumb=1'
    : null;
  const avatarHtml = ownerAvatarUrl
    ? `<img src="${ownerAvatarUrl}" alt="" style="width:32px;height:32px;object-fit:cover;border-radius:4px;flex-shrink:0;margin-right:9px;margin-top:1px">`
    : '';
  return `
    <div class="act-title-bar">
      <div style="display:flex;align-items:flex-start;min-width:0;flex:1">${avatarHtml}<div class="act-title">${escHtml(a.name||'(unnamed)')}${isDirty?'<span style="color:#f97316;font-size:10px;margin-left:5px;font-weight:400;vertical-align:middle">edited</span>':''}</div></div>
      <div class="act-title-links">${editBtn}${resyncBtn}${exportBtn}${saveRouteBtn}${stravaLink}</div>
    </div>
    ${rpeKudosHtml}
    <div style="display:grid;grid-template-columns:auto 1fr;column-gap:20px;row-gap:4px;margin-bottom:6px;align-items:baseline">
      <span style="font-size:11.5px;color:var(--muted)">${fmtDate(a.start_time)}</span>
      <span id="wx-weather-text" style="font-size:11px;color:var(--muted)"></span>
      <span style="font-size:11px;color:var(--muted)">${effVis?`${visIcon[effVis]||''} ${visMap[effVis]||effVis} ${stravaEditLink}`:''}</span>
      <span id="wx-location-text" style="font-size:11px;color:var(--muted)"></span>
    </div>
    ${a.notes?`<div class="act-notes" style="margin-bottom:8px">${escHtml(a.notes)}</div>`:''}
    <div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:8px;max-width:100%;overflow:hidden">
      <div class="stats-grid">${chips.map(([l,v,sub])=>{const span=l==='Equipment'?Math.min(6,Math.max(1,Math.ceil(v.length/14))):1;const s=span>1?` style="grid-column:span ${span}"`:'';;return`<div class="stat-chip"${s}><div class="sc-label">${l}</div><div class="sc-val">${v}</div><div class="sc-sub">${sub||''}</div></div>`}).join('')}</div>
      ${_hasAnthropicKey?`<div class="stat-chip" id="ai-summary-chip" style="width:252px;flex-shrink:0;align-items:flex-start;justify-content:flex-start;padding:6px 8px;overflow-y:auto;box-sizing:border-box${isOwner?'':';display:none'}"><div class="sc-label" style="margin-bottom:3px;display:flex;align-items:center;justify-content:space-between;width:100%"><span>✦ AI Summary</span>${isOwner?`<button id="ai-summary-refresh" onclick="loadAISummary(${a.id},true)" title="Regenerate summary" style="background:none;border:none;cursor:pointer;color:var(--muted);font-size:11px;padding:0;line-height:1" tabindex="-1">↺</button>`:''}</div><div id="ai-summary-text" style="font-size:11px;color:var(--muted);line-height:1.45">${isOwner?'Loading…':''}</div></div>`:''}
    </div>
    ${meta.length?`<div class="meta-row" style="margin-top:4px">${meta.map(([l,v])=>`<div class="meta-field"><div class="mf-label">${l}</div><div class="mf-val">${escHtml(String(v))}</div></div>`).join('')}</div>`:''}
  `;
}

// ── FILTER CONTROLS ───────────────────────────────────────────────────────────
async function loadFilterOptions() {
  const ids = _viewUserIdsParam();
  let url = '/activities/filter-options';
  if (ids) url += '?view_user_ids=' + ids;
  const d = await fetch(url).then(r=>r.json());
  const tEl=document.getElementById('typeFilter');
  tEl.innerHTML = '<option value="">All types</option>';
  d.types.forEach(t=>{ const o=document.createElement('option');o.value=t;o.textContent=t;tEl.appendChild(o); });
  const yEl=document.getElementById('yearFilter');
  yEl.innerHTML = '<option value="">All years</option>';
  d.years.forEach(y=>{ const o=document.createElement('option');o.value=y;o.textContent=y;yEl.appendChild(o); });
}

document.getElementById('typeFilter').addEventListener('change',e=>{ state.actType=e.target.value; applyFilterAndSort(); });
document.getElementById('yearFilter').addEventListener('change',e=>{ state.year=e.target.value;    applyFilterAndSort(); });

let searchTimer=null;
document.getElementById('searchInput').addEventListener('input',e=>{
  clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>{ state.search=e.target.value; applyFilterAndSort(); },250);
});

// ── COLUMN SORT ───────────────────────────────────────────────────────────────
document.querySelectorAll('.ch[data-col]').forEach(el=>{
  el.addEventListener('click',()=>{
    const col=el.dataset.col;
    state.sortDir = (state.sortBy===col && state.sortDir==='desc') ? 'asc' : 'desc';
    state.sortBy  = col;
    updateSortHeaders();
    applyFilterAndSort();
  });
});

// ── RESIZE HANDLE ─────────────────────────────────────────────────────────────
const sidebar=document.getElementById('sidebar');
const handle=document.getElementById('resize-handle');
let resizing=false,rsX=0,rsW=0;

// addDrag: attach mouse+touch drag to any element
// axis: 'x' or 'y', onMove(delta), onEnd()
function addDrag(el, axis, onMove, onEnd, onStart) {
  el.style.touchAction = 'none';

  // Use Pointer Events as the primary path on all modern browsers (including
  // iPad Safari 13+). setPointerCapture routes all subsequent events to this
  // element even as the pointer moves outside, which is exactly what drag
  // handles need and eliminates the intermittent "touch starts but won't move"
  // bug on iPad caused by hit-testing misses on narrow elements.
  //
  // Touch event fallback only for browsers that don't support PointerEvent
  // (essentially IE11 and very old iOS <13).
  if (!window.PointerEvent) {
    el.addEventListener('touchstart', e => {
      e.preventDefault();
      e.stopPropagation();
      if (onStart) onStart();
      el.classList.add('dragging');
      const t0 = e.touches[0];
      const start = axis==='x' ? t0.clientX : t0.clientY;
      function tm(ev) {
        ev.preventDefault();
        const t = ev.touches[0];
        if (!t) return;
        onMove(axis==='x' ? t.clientX - start : t.clientY - start);
      }
      function tu() {
        el.classList.remove('dragging');
        document.body.style.cssText = '';
        if (onEnd) onEnd();
        document.removeEventListener('touchmove', tm);
        document.removeEventListener('touchend',  tu);
        document.removeEventListener('touchcancel', tu);
      }
      document.addEventListener('touchmove', tm, {passive: false});
      document.addEventListener('touchend',  tu);
      document.addEventListener('touchcancel', tu);
    }, {passive: false});
  }

  // Pointer Events (mouse + touch on modern browsers including iPad Safari 13+)
  el.addEventListener('pointerdown', e => {
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    if (onStart) onStart();
    try { el.setPointerCapture(e.pointerId); } catch(_) {}
    const start = axis==='x' ? e.clientX : e.clientY;
    el.classList.add('dragging');
    document.body.style.cssText = (axis==='x'?'cursor:col-resize;':'cursor:row-resize;') + 'user-select:none';
    function pm(ev){ onMove(axis==='x' ? ev.clientX-start : ev.clientY-start); }
    function pu(ev){
      try { el.releasePointerCapture(ev.pointerId); } catch(_) {}
      el.classList.remove('dragging');
      document.body.style.cssText='';
      if(onEnd) onEnd();
      el.removeEventListener('pointermove', pm);
      el.removeEventListener('pointerup',   pu);
      el.removeEventListener('pointercancel', pu);
    }
    el.addEventListener('pointermove',   pm);
    el.addEventListener('pointerup',     pu);
    el.addEventListener('pointercancel', pu);
  });
}

// Always attach — handle is CSS-hidden on portrait mobile; works on desktop and landscape phone
addDrag(handle, 'x',
  delta => {
    const minW = window.innerHeight <= 500 ? 160 : 380; // narrower min on landscape phone
    sidebar.style.width = Math.max(minW, Math.min(window.innerWidth * .65, rsW + delta)) + 'px';
    if (leafMap) leafMap.invalidateSize();
  },
  () => { savePaneSizes({sidebarW: sidebar.offsetWidth}); },
  () => { rsW = sidebar.offsetWidth; }
);

// Photo panel horizontal resize
(function() {
  const photoDragHandle = document.getElementById('photo-drag-handle');
  const infoRight = document.getElementById('info-right');
  if (!photoDragHandle || !infoRight) return;
  let _startW = 0;
  addDrag(photoDragHandle, 'x',
    delta => {
      // dragging left makes panel wider (delta is negative when dragging left)
      const newW = Math.max(80, Math.min(window.innerWidth * 0.5, _startW - delta));
      infoRight.style.width = newW + 'px';
    },
    () => { savePaneSizes({photoW: infoRight.offsetWidth}); },
    () => { _startW = infoRight.getBoundingClientRect().width; }
  );
})();

// ── KEYBOARD NAV ──────────────────────────────────────────────────────────────
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA') return;
  if(document.getElementById('coach-overlay')?.classList.contains('open')) {
    if(e.key==='Escape') closeCoach();
    return;
  }
  if (e.key==='Delete'||e.key==='Backspace') {
    if (state.selectedIds.size > 0 || state.selectedId !== null) { e.preventDefault(); deleteSelected(); }
    return;
  }
  if(!state.filtered.length) return;
  const idx=state.filtered.findIndex(a=>a.id===state.selectedId);
  if (e.key==='ArrowDown'&&idx<state.filtered.length-1){ e.preventDefault(); state.selectedIds.clear(); selectActivity(state.filtered[idx+1].id); scrollToSelected(); }
  if (e.key==='ArrowUp'  &&idx>0)                      { e.preventDefault(); state.selectedIds.clear(); selectActivity(state.filtered[idx-1].id); scrollToSelected(); }
});
function scrollToSelected(){
  const idx=state.filtered.findIndex(a=>a.id===state.selectedId);
  if(idx<0)return;
  const container=document.getElementById('act-list');
  const top=idx*ROW_H, bot=top+ROW_H;
  if(top<container.scrollTop) container.scrollTop=top-ROW_H;
  else if(bot>container.scrollTop+container.clientHeight) container.scrollTop=bot-container.clientHeight+ROW_H;
}

// ── AI SUMMARY ───────────────────────────────────────────────────────────────
function _resizeAIChip() {
  const chip  = document.getElementById('ai-summary-chip');
  const grid  = document.querySelector('#act-detail .stats-grid');
  if (!chip || !grid) return;
  const gridChips = grid.querySelectorAll('.stat-chip');
  if (!gridChips.length) return;
  // Bottom of row 3 = chip at index min(17, last). 6 cols × 3 rows = indices 0–17.
  const lastIdx  = Math.min(17, gridChips.length - 1);
  const gridTop  = grid.getBoundingClientRect().top;
  const rowBot   = gridChips[lastIdx].getBoundingClientRect().bottom;
  const h = rowBot - gridTop;
  if (h > 20) { chip.style.height = h + 'px'; chip.style.maxHeight = h + 'px'; }
}

async function loadAISummary(activityId, refresh = false) {
  if (!_hasAnthropicKey) return;
  const el   = document.getElementById('ai-summary-text');
  const chip = document.getElementById('ai-summary-chip');
  const btn  = document.getElementById('ai-summary-refresh');
  if (!el || !chip) return;
  if (refresh) {
    el.textContent = 'Generating…';
    if (btn) btn.disabled = true;
  }
  try {
    const model = coachGetModel();
    const url = `/api/activities/${activityId}/ai-summary?model=${encodeURIComponent(model)}${refresh ? '&refresh=true' : ''}`;
    const r = await fetch(url);
    if (r.status === 403) { chip.style.display = 'none'; return; } // not owner, no cached summary
    if (!r.ok) { el.textContent = '—'; return; }
    const d = await r.json();
    chip.style.display = '';
    el.style.color = 'var(--text)';
    el.textContent = d.summary || '—';
  } catch(e) {
    el.textContent = '—';
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── STRAVA KUDOS & COMMENTS ───────────────────────────────────────────────────
function _openPopover(id, anchorBtn) {
  // Close any existing popovers
  ['kudos-popover','comments-popover'].forEach(pid => { const el = document.getElementById(pid); if (el) el.remove(); });
  const pop = document.createElement('div');
  pop.id = id;
  pop.style.cssText = 'position:fixed;z-index:2000;background:var(--surface2);border:1px solid var(--border2);border-radius:6px;padding:8px 0;box-shadow:0 4px 16px rgba(0,0,0,.6);font-size:12px;min-width:160px;max-height:240px;overflow-y:auto';
  pop.innerHTML = '<div style="padding:4px 12px;color:var(--muted)">Loading…</div>';
  document.body.appendChild(pop);
  const rect = anchorBtn.getBoundingClientRect();
  pop.style.left = rect.left + 'px';
  pop.style.top  = (rect.bottom + 4) + 'px';
  const closeHandler = (e) => {
    if (!pop.contains(e.target) && e.target !== anchorBtn) {
      pop.remove();
      document.removeEventListener('click', closeHandler, true);
    }
  };
  setTimeout(() => document.addEventListener('click', closeHandler, true), 0);
  return pop;
}

function _applyKudosBtn(count) {
  const btn = document.getElementById('kudos-btn');
  if (!btn || count <= 0) return;
  btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle;flex-shrink:0"><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg> ${count}`;
  btn.style.display = 'inline-flex';
}

async function loadKudos(activityId) {
  if (_kudosCountCache.has(activityId)) {
    _applyKudosBtn(_kudosCountCache.get(activityId) || 0);
    return;
  }
  try {
    const r = await fetch(`/api/activities/${activityId}/strava-kudos`);
    if (!r.ok) return;
    const count = (await r.json()).kudos_count || 0;
    _kudosCountCache.set(activityId, count);
    _applyKudosBtn(count);
  } catch(e) {}
}

async function toggleKudosList(activityId, event) {
  event.stopPropagation();
  if (document.getElementById('kudos-popover')) { document.getElementById('kudos-popover').remove(); return; }
  const pop = _openPopover('kudos-popover', event.currentTarget);
  try {
    let athletes = _kudosListCache.get(activityId);
    if (!athletes) {
      const r = await fetch(`/api/activities/${activityId}/strava-kudos-list`);
      if (!r.ok) { pop.innerHTML = '<div style="padding:4px 12px;color:var(--muted)">Unable to load</div>'; return; }
      athletes = (await r.json()).athletes || [];
      _kudosListCache.set(activityId, athletes);
    }
    if (!athletes.length) { pop.innerHTML = '<div style="padding:4px 12px;color:var(--muted)">No kudos yet</div>'; return; }
    pop.innerHTML = athletes.map(a =>
      `<div style="padding:3px 12px;color:var(--text);white-space:nowrap">${escHtml(a.firstname)} ${escHtml(a.lastname)}</div>`
    ).join('');
  } catch(e) {
    pop.innerHTML = '<div style="padding:4px 12px;color:var(--muted)">Error loading</div>';
  }
}

function _applyCommentsBtn(count) {
  const btn = document.getElementById('comments-btn');
  if (!btn || count <= 0) return;
  btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle;flex-shrink:0"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg> ${count}`;
  btn.style.display = 'inline-flex';
}

async function loadComments(activityId) {
  if (_commentsCountCache.has(activityId)) {
    _applyCommentsBtn(_commentsCountCache.get(activityId) || 0);
    return;
  }
  try {
    const r = await fetch(`/api/activities/${activityId}/strava-comments`);
    if (!r.ok) return;
    const count = (await r.json()).comment_count || 0;
    _commentsCountCache.set(activityId, count);
    _applyCommentsBtn(count);
  } catch(e) {}
}

async function toggleCommentsList(activityId, event) {
  event.stopPropagation();
  if (document.getElementById('comments-popover')) { document.getElementById('comments-popover').remove(); return; }
  const pop = _openPopover('comments-popover', event.currentTarget);
  try {
    let comments = _commentsListCache.get(activityId);
    if (!comments) {
      const r = await fetch(`/api/activities/${activityId}/strava-comments-list`);
      if (!r.ok) { pop.innerHTML = '<div style="padding:4px 12px;color:var(--muted)">Unable to load</div>'; return; }
      comments = (await r.json()).comments || [];
      _commentsListCache.set(activityId, comments);
    }
    if (!comments.length) { pop.innerHTML = '<div style="padding:4px 12px;color:var(--muted)">No comments yet</div>'; return; }
    pop.innerHTML = comments.map((c, i) =>
      `<div style="padding:5px 12px${i > 0 ? ';border-top:1px solid var(--border)' : ''}">
        <div style="font-weight:600;color:var(--text);margin-bottom:2px">${escHtml(c.athlete_name)}</div>
        <div style="color:var(--muted);line-height:1.4;white-space:pre-wrap">${escHtml(c.text)}</div>
      </div>`
    ).join('');
  } catch(e) {
    pop.innerHTML = '<div style="padding:4px 12px;color:var(--muted)">Error loading</div>';
  }
}

// ── WEATHER & LOCATION ───────────────────────────────────────────────────────
async function loadWeatherLocation(activityId) {
  try {
    const r = await fetch(`/api/activities/${activityId}/weather-location`);
    if (!r.ok) return;
    const d = await r.json();

    const wxEl = document.getElementById('wx-weather-text');
    if (wxEl && d.weather?.description) {
      const w = d.weather;
      const details = [];
      if (w.avg_temp_f   != null) details.push(U.tempS(w.avg_temp_f));
      if (w.avg_wind_kph != null) details.push(`Wind ${Math.round(w.avg_wind_kph)} km/h`);
      if (w.avg_humidity != null) details.push(`${Math.round(w.avg_humidity)}% humidity`);
      if (w.precip_mm > 0)        details.push(`${w.precip_mm}mm precip`);
      wxEl.textContent = [w.description, ...details].join(' · ');
    }

    const locEl = document.getElementById('wx-location-text');
    if (locEl && d.locations) {
      locEl.textContent = d.locations;
    }
  } catch(e) {}
}



// ── PANE SIZE PERSISTENCE ────────────────────────────────────────────────────
function savePaneSizes(patch) {
  try {
    const cur = JSON.parse(_uiPrefsGet('ascent-pane-sizes') || '{}');
    _uiPrefsSet('ascent-pane-sizes', JSON.stringify(Object.assign(cur, patch)));
  } catch(e) {}
}
function loadPaneSizes() {
  try { return JSON.parse(_uiPrefsGet('ascent-pane-sizes') || '{}'); } catch(e) { return {}; }
}

const STATS_TITLE_H = 28;
function toggleStats() {
  if (_isPhoneLayout()) return; // no collapse on phone — info panel is a full-screen tab
  const panel   = document.getElementById('info-panel');
  const btn     = document.getElementById('stats-disclose');
  const content = panel.querySelector('.stats-content');
  const titlebar = document.getElementById('stats-titlebar');
  if (!panel || !btn) return;
  const isOpen = !btn.classList.contains('collapsed');
  if (isOpen) {
    savePaneSizes({infoH: panel.offsetHeight});
    panel.style.height = STATS_TITLE_H + 'px';
    if (content) content.style.display = 'none';
    btn.classList.add('collapsed');
    if (titlebar) titlebar.style.cursor = 'default';
  } else {
    const ps = loadPaneSizes();
    const h = Math.max(STATS_TITLE_H + 60, ps.infoH || 200);
    panel.style.height = h + 'px';
    if (content) content.style.display = '';
    btn.classList.remove('collapsed');
    if (titlebar) titlebar.style.cursor = 'row-resize';
  }
  setTimeout(() => {
    refitMap();
    const al = document.getElementById('act-list');
    if (al) al.dispatchEvent(new Event('scroll'));
  }, 220);
  try { _uiPrefsSet('ascent-stats-open', isOpen ? '0' : '1'); } catch(e) {}
}

// ── RESIZE HANDLES ───────────────────────────────────────────────────────────
(function() {
  function initDrag(handleId, topPaneId, storageKey, onDone) {
    const handle   = document.getElementById(handleId);
    const topPane  = document.getElementById(topPaneId);
    const detail   = document.getElementById('detail');
    if (!handle || !topPane) return;

    let _startH = 0;
    addDrag(handle, 'y',
      delta => {
        const newH = Math.max(60, Math.min(detail.getBoundingClientRect().height - 120,
                                           _startH + delta));
        topPane.style.height = newH + 'px';
        if (onDone) onDone();
      },
      () => {
        if (onDone) onDone();
        savePaneSizes({[storageKey]: topPane.offsetHeight});
      },
      () => { _startH = topPane.getBoundingClientRect().height; }
    );
  }

  // Stats titlebar — drag to resize; button clicks pass through
  (function(){
    const titlebar  = document.getElementById('stats-titlebar');
    const infoPanel = document.getElementById('info-panel');
    const rootEl    = document.getElementById('root');
    if (!titlebar || !infoPanel) return;

    function isBtn(e) { return !!e.target.closest('#stats-disclose'); }
    function isCollapsed() {
      const b = document.getElementById('stats-disclose');
      return b && b.classList.contains('collapsed');
    }
    function applyDelta(startH, delta) {
      if (isCollapsed()) {
        if (delta < -10) toggleStats(); // drag up while collapsed → expand
        return;
      }
      const rootH = rootEl.getBoundingClientRect().height;
      infoPanel.style.height = Math.max(48, Math.min(rootH - 120, startH - delta)) + 'px';
      if (leafMap) leafMap.invalidateSize();
    }
    function onDragEnd() {
      refitMap();
      if (!isCollapsed()) savePaneSizes({infoH: infoPanel.offsetHeight});
    }

    let touchActive = false;
    titlebar.addEventListener('touchstart', e => {
      if (isBtn(e)) return;
      e.preventDefault();
      touchActive = true;
      const startH = infoPanel.getBoundingClientRect().height;
      const startY = e.touches[0].clientY;
      function tm(ev) { ev.preventDefault(); if (ev.touches[0]) applyDelta(startH, ev.touches[0].clientY - startY); }
      function tu() { touchActive = false; onDragEnd(); document.removeEventListener('touchmove', tm); document.removeEventListener('touchend', tu); document.removeEventListener('touchcancel', tu); }
      document.addEventListener('touchmove', tm, {passive: false});
      document.addEventListener('touchend', tu);
      document.addEventListener('touchcancel', tu);
    }, {passive: false});

    titlebar.addEventListener('pointerdown', e => {
      if (touchActive || isBtn(e)) return;
      if (e.button !== undefined && e.button !== 0) return;
      e.preventDefault();
      const startH = infoPanel.getBoundingClientRect().height;
      const startY = e.clientY;
      try { titlebar.setPointerCapture(e.pointerId); } catch(_) {}
      function pm(ev) { applyDelta(startH, ev.clientY - startY); }
      function pu(ev) { try { titlebar.releasePointerCapture(ev.pointerId); } catch(_) {} onDragEnd(); titlebar.removeEventListener('pointermove', pm); titlebar.removeEventListener('pointerup', pu); titlebar.removeEventListener('pointercancel', pu); }
      titlebar.addEventListener('pointermove', pm);
      titlebar.addEventListener('pointerup', pu);
      titlebar.addEventListener('pointercancel', pu);
    });
  })();
})();

// Analysis titlebar — toggle collapse + drag to resize chart
const ANALYSIS_TITLE_H = 28;
function toggleAnalysis() {
  if (_isPhoneLayout()) return; // no collapse on phone — analysis is a full-screen tab
  const wrap = document.getElementById('chart-wrap');
  const btn  = document.getElementById('analysis-disclose');
  const tb   = document.getElementById('analysis-titlebar');
  if (!wrap || !btn) return;
  const isOpen = !btn.classList.contains('collapsed');
  const _repaintList = () => {
    const al = document.getElementById('act-list');
    if (al) al.dispatchEvent(new Event('scroll'));
  };
  if (isOpen) {
    savePaneSizes({chartH: wrap.offsetHeight});
    wrap.style.height = ANALYSIS_TITLE_H + 'px';
    btn.classList.add('collapsed');
    if (tb) tb.style.cursor = 'default';
    setTimeout(() => { refitMap(); _repaintList(); }, 220);
  } else {
    const ps = loadPaneSizes();
    const h = Math.max(ANALYSIS_TITLE_H + 60, ps.chartH || 200);
    wrap.style.height = h + 'px';
    btn.classList.remove('collapsed');
    if (tb) tb.style.cursor = 'row-resize';
    setTimeout(() => {
      if (elevChart) elevChart.resize();

      refitMap();
      _repaintList();
    }, 220);
  }
  try { _uiPrefsSet('ascent-analysis-open', isOpen ? '0' : '1'); } catch(e) {}
}

(function(){
  const titlebar  = document.getElementById('analysis-titlebar');
  const chartWrap = document.getElementById('chart-wrap');
  const root      = document.getElementById('root');
  if (!titlebar || !chartWrap) return;

  function isBtn(e) { return !!e.target.closest('#analysis-disclose') || !!e.target.closest('.sp-tb-btn'); }
  function isCollapsed() { const b = document.getElementById('analysis-disclose'); return b && b.classList.contains('collapsed'); }
  function applyDelta(startH, delta) {
    if (isCollapsed()) { if (delta < -10) toggleAnalysis(); return; }
    const rootH = root.getBoundingClientRect().height;
    chartWrap.style.height = Math.max(ANALYSIS_TITLE_H + 20, Math.min(rootH - 120, startH - delta)) + 'px';
    if (elevChart) elevChart.resize();
    if (leafMap) leafMap.invalidateSize();
  }
  function onDragEnd() {
    if (elevChart) elevChart.resize();
    refitMap();
    if (!isCollapsed()) savePaneSizes({chartH: chartWrap.offsetHeight});
  }

  let touchActive = false;
  titlebar.addEventListener('touchstart', e => {
    if (isBtn(e)) return;
    e.preventDefault();
    touchActive = true;
    const startH = chartWrap.getBoundingClientRect().height;
    const startY = e.touches[0].clientY;
    function tm(ev) { ev.preventDefault(); if (ev.touches[0]) applyDelta(startH, ev.touches[0].clientY - startY); }
    function tu() { touchActive = false; onDragEnd(); document.removeEventListener('touchmove', tm); document.removeEventListener('touchend', tu); document.removeEventListener('touchcancel', tu); }
    document.addEventListener('touchmove', tm, {passive: false});
    document.addEventListener('touchend', tu);
    document.addEventListener('touchcancel', tu);
  }, {passive: false});

  titlebar.addEventListener('pointerdown', e => {
    if (touchActive || isBtn(e)) return;
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    const startH = chartWrap.getBoundingClientRect().height;
    const startY = e.clientY;
    try { titlebar.setPointerCapture(e.pointerId); } catch(_) {}
    function pm(ev) { applyDelta(startH, ev.clientY - startY); }
    function pu(ev) { try { titlebar.releasePointerCapture(ev.pointerId); } catch(_) {} onDragEnd(); titlebar.removeEventListener('pointermove', pm); titlebar.removeEventListener('pointerup', pu); titlebar.removeEventListener('pointercancel', pu); }
    titlebar.addEventListener('pointermove', pm);
    titlebar.addEventListener('pointerup', pu);
    titlebar.addEventListener('pointercancel', pu);
  });
})();

// ── COLUMN MANAGER ───────────────────────────────────────────────────────────
function buildColHead() {
  const head = document.getElementById('col-head');
  const cols = getActiveCols();
  head.style.setProperty('--cols', currentCOLS());
  head.innerHTML = '';
  cols.forEach((col, i) => {
    const div = document.createElement('div');
    div.className = 'ch' + (state.sortBy === col.sort ? ' sorted' : '');
    div.dataset.col = col.sort || '';
    div.dataset.colId = col.id;
    div.style.justifyContent = col.align === 'right' ? 'flex-end' : '';
    div.draggable = true;
    const unitSuffix = col.unitLabel ? ` <span style="font-size:9px;font-weight:400;opacity:.6;text-transform:none;letter-spacing:0">(${col.unitLabel()})</span>` : '';
    div.innerHTML = col.label + unitSuffix + (state.sortBy === col.sort ? ` <span class="sort-arr">${state.sortDir==='desc'?'↓':'↑'}</span>` : '');

    // Sort on click (not drag)
    div.addEventListener('click', () => {
      if (_suppressColClick) { _suppressColClick = false; return; }
      if (!col.sort) return;
      if (state.sortBy === col.sort) state.sortDir = state.sortDir==='desc'?'asc':'desc';
      else { state.sortBy = col.sort; state.sortDir = 'desc'; }
      loadAll();
    });

    // Touch drag-to-reorder (iOS doesn't support HTML5 DnD)
    div.addEventListener('touchstart', e => {
      if (e.touches.length !== 1) return;
      // Don't start if touch is on the resize separator
      if (e.target.classList.contains('ch-sep')) return;
      const t = e.touches[0];
      _colDragPending = { colId: col.id, div, startX: t.clientX, startY: t.clientY };
    }, {passive: true});

    // Drag to reorder
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
      const fromId = e.dataTransfer.getData('text/plain');
      const toId   = col.id;
      if (fromId === toId) return;
      const fromIdx = activeColIds.indexOf(fromId);
      const toIdx   = activeColIds.indexOf(toId);
      if (fromIdx < 0 || toIdx < 0) return;
      activeColIds.splice(fromIdx, 1);
      activeColIds.splice(toIdx, 0, fromId);
      saveColPrefs(activeColIds);
      buildColHead();
      renderVirtualList();
    });

    // Add resize separator INSIDE header div (so it doesn't affect grid)
    if (i < cols.length - 1) {
      const sep = document.createElement('div');
      sep.className = 'ch-sep';
      sep.dataset.colId = col.id;
      sep.addEventListener('pointerdown', e => {
        e.preventDefault();
        e.stopPropagation();
        div.draggable = false;
        sep.setPointerCapture(e.pointerId);
        sep.classList.add('resizing');
        const startX = e.clientX;
        const colEls = head.querySelectorAll('.ch');
        const colEl  = colEls[i];
        const startPx = colEl ? colEl.offsetWidth : parseInt(getColW(col)) || 100;

        function onMove(me) {
          const delta = me.clientX - startX;
          const newPx = Math.max(40, startPx + delta);
          colWidthOverrides[col.id] = newPx + 'px';
          head.style.setProperty('--cols', buildColsTemplate());
          document.querySelectorAll('.act-row').forEach(r => r.style.setProperty('--cols', buildColsTemplate()));
        }
        function onUp() {
          div.draggable = true;
          sep.classList.remove('resizing');
          sep.removeEventListener('pointermove', onMove);
          sep.removeEventListener('pointerup',   onUp);
          sep.removeEventListener('pointercancel', onUp);
          saveColWidths();
          buildColHead();
          renderVirtualList();
        }
        sep.addEventListener('pointermove',  onMove);
        sep.addEventListener('pointerup',    onUp);
        sep.addEventListener('pointercancel', onUp);
      });
      div.appendChild(sep);
    }

    head.appendChild(div);
  });

  // Sync act-list min-width to header so #list-scroll overflows correctly
  // Double rAF ensures grid layout is complete before reading scrollWidth
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const w = head.scrollWidth;
    const list = document.getElementById('act-list');
    const spacer = document.getElementById('list-spacer');
    if (list)   list.style.minWidth   = w + 'px';
    if (spacer) spacer.style.minWidth = w + 'px';
    if (window._updateHScrollbar) window._updateHScrollbar();
    if (window._updateVScrollbar) window._updateVScrollbar();
  }));
}

// ── CUSTOM SCROLLBARS ────────────────────────────────────────────────────────
// macOS hides native scrollbars — we build always-visible ones instead.
(function initCustomScrollbars() {
  function setup() {
    const listScroll = document.getElementById('list-scroll');
    const actList    = document.getElementById('act-list');
    const hTrack     = document.getElementById('hscroll-track');
    const hThumb     = document.getElementById('hscroll-thumb');
    const vTrack     = document.getElementById('vscroll-track');
    const vThumb     = document.getElementById('vscroll-thumb');
    if (!listScroll || !actList || !hTrack || !hThumb) return;

    // ── Horizontal scrollbar ─────────────────────────────────────────────
    function updateHScrollbar() {
      const sw = listScroll.scrollWidth;
      const cw = listScroll.clientWidth;
      if (sw <= cw) { hTrack.style.display = 'none'; return; }
      hTrack.style.display = 'block';
      const ratio     = cw / sw;
      const thumbW    = Math.max(30, cw * ratio);
      const thumbLeft = (listScroll.scrollLeft / (sw - cw)) * (cw - thumbW);
      hThumb.style.width = thumbW + 'px';
      hThumb.style.left  = thumbLeft + 'px';
    }

    listScroll.addEventListener('scroll', updateHScrollbar, {passive: true});

    // Drag hThumb
    hThumb.addEventListener('pointerdown', e => {
      e.preventDefault();
      hThumb.setPointerCapture(e.pointerId);
      hThumb.classList.add('dragging');
      const startX     = e.clientX;
      const startLeft  = listScroll.scrollLeft;
      const sw = listScroll.scrollWidth;
      const cw = listScroll.clientWidth;
      const thumbW = hThumb.offsetWidth;
      function onMove(ev) {
        const delta     = ev.clientX - startX;
        const trackW    = cw - thumbW;
        const scrollMax = sw - cw;
        listScroll.scrollLeft = startLeft + (delta / trackW) * scrollMax;
      }
      function onUp() {
        hThumb.classList.remove('dragging');
        hThumb.removeEventListener('pointermove', onMove);
        hThumb.removeEventListener('pointerup',   onUp);
      }
      hThumb.addEventListener('pointermove', onMove);
      hThumb.addEventListener('pointerup',   onUp);
    });

    // ── Vertical scrollbar ───────────────────────────────────────────────
    function updateVScrollbar() {
      const sh = actList.scrollHeight;
      const ch = actList.clientHeight;
      if (sh <= ch) { vTrack.style.display = 'none'; return; }
      vTrack.style.display = 'block';
      // position vTrack over act-list
      const rect = actList.getBoundingClientRect();
      const pRect = actList.offsetParent ? actList.offsetParent.getBoundingClientRect() : {top:0,left:0};
      vTrack.style.top    = (rect.top  - pRect.top)  + 'px';
      vTrack.style.height = rect.height + 'px';
      const ratio     = ch / sh;
      const thumbH    = Math.max(30, ch * ratio);
      const thumbTop  = (actList.scrollTop / (sh - ch)) * (ch - thumbH);
      vThumb.style.height = thumbH + 'px';
      vThumb.style.top    = thumbTop + 'px';
    }

    actList.addEventListener('scroll', updateVScrollbar, {passive: true});

    // Drag vThumb
    vThumb.addEventListener('pointerdown', e => {
      e.preventDefault();
      vThumb.setPointerCapture(e.pointerId);
      vThumb.classList.add('dragging');
      const startY    = e.clientY;
      const startTop  = actList.scrollTop;
      const sh = actList.scrollHeight;
      const ch = actList.clientHeight;
      const thumbH = vThumb.offsetHeight;
      function onMove(ev) {
        const delta     = ev.clientY - startY;
        const trackH    = ch - thumbH;
        const scrollMax = sh - ch;
        actList.scrollTop = startTop + (delta / trackH) * scrollMax;
      }
      function onUp() {
        vThumb.classList.remove('dragging');
        vThumb.removeEventListener('pointermove', onMove);
        vThumb.removeEventListener('pointerup',   onUp);
      }
      vThumb.addEventListener('pointermove', onMove);
      vThumb.addEventListener('pointerup',   onUp);
    });

    // Update both on resize
    const ro = new ResizeObserver(() => { updateHScrollbar(); updateVScrollbar(); });
    ro.observe(listScroll);
    ro.observe(actList);

    // Initial update (after layout)
    requestAnimationFrame(() => requestAnimationFrame(() => {
      updateHScrollbar();
      updateVScrollbar();
    }));

    // Re-export so buildColHead can trigger update
    window._updateHScrollbar = updateHScrollbar;
    window._updateVScrollbar = updateVScrollbar;
  }

  document.addEventListener('DOMContentLoaded', setup);
})();

function buildColPicker() {
  const picker = document.getElementById('col-picker');
  picker.innerHTML = '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:2px 8px 6px">Visible Columns</div>';
  ALL_COLS.forEach(col => {
    const checked = activeColIds.includes(col.id);
    const item = document.createElement('label');
    item.className = 'cp-item';
    item.innerHTML = `<input type="checkbox" ${checked?'checked':''} data-col-id="${col.id}"> ${col.label}`;
    item.querySelector('input').addEventListener('change', e => {
      if (e.target.checked) {
        if (!activeColIds.includes(col.id)) {
          // Insert at the position from DEFAULT_COL_IDS rather than appending to end
          const defaultPos = DEFAULT_COL_IDS.indexOf(col.id);
          if (defaultPos <= 0) {
            activeColIds.push(col.id);
          } else {
            // Find the nearest preceding default column that's currently active
            let insertAfter = -1;
            for (let p = defaultPos - 1; p >= 0; p--) {
              const idx = activeColIds.indexOf(DEFAULT_COL_IDS[p]);
              if (idx >= 0) { insertAfter = idx; break; }
            }
            activeColIds.splice(insertAfter + 1, 0, col.id);
          }
        }
      } else {
        if (activeColIds.length <= 2) { e.target.checked = true; return; } // keep at least 2
        activeColIds = activeColIds.filter(id => id !== col.id);
      }
      saveColPrefs(activeColIds);
      buildColHead();
      renderVirtualList();
    });
    picker.appendChild(item);
  });
}

function toggleColPicker(e) {
  e.stopPropagation();
  const picker = document.getElementById('col-picker');
  const isOpen = picker.classList.contains('open');
  if (!isOpen) buildColPicker();
  picker.classList.toggle('open');
}

// Close picker on outside click
document.addEventListener('click', e => {
  if (!e.target.closest('#filter-bar')) {
    document.getElementById('col-picker')?.classList.remove('open');
  }
});

// ── INIT ──────────────────────────────────────────────────────────────────────
(async()=>{
  initMap();
  await U.load();  // fetch metric pref before first render

  // Load users first so userMap is ready before activities render
  try {
    const [mr, ur] = await Promise.all([fetch('/api/me'), fetch('/api/users')]);
    if (mr.ok && ur.ok) {
      const me    = await mr.json();
      const users = await ur.json();
      users.forEach(u => { userMap[u.id] = {username: u.username, avatar_url: u.avatar_url || null}; });
      _allUsers = users;
      viewState.myId       = me.id;
      viewState.myUsername = me.username;
      _hasAnthropicKey     = !!me.has_anthropic_key;
      // Restore saved user selection — keyed by user ID so different logins don't bleed into each other
      const _viewKey  = `ascent-view-user-ids-${me.id}`;
      viewState._viewKey = _viewKey;
      const _savedIds = JSON.parse(_uiPrefsGet(_viewKey) || 'null');
      const _validIds = _savedIds && _savedIds.filter(id => users.some(u => u.id === id));
      if (_validIds && _validIds.length > 0) {
        _validIds.forEach(id => viewState.selectedUserIds.add(id));
      } else {
        viewState.selectedUserIds.add(me.id);
      }
      _updateViewBadge();
    }
  } catch(e) {}

  buildColHead();
  await loadFilterOptions();
  await loadAll();
  loadChartPrefs();
  if (!_isPhoneLayout()) {
    // Restore pane sizes (desktop/tablet only — phones use full-screen tab layout)
    const ps = loadPaneSizes();
    if (ps.sidebarW) { const sb = document.getElementById('sidebar'); if (sb) sb.style.width = ps.sidebarW + 'px'; }
    if (ps.infoH)    { const el = document.getElementById('info-panel'); if (el) el.style.height = ps.infoH + 'px'; }
    if (ps.chartH)   { const el = document.getElementById('chart-wrap'); if (el) el.style.height = ps.chartH + 'px'; }
    if (ps.photoW)   { const el = document.getElementById('info-right'); if (el) el.style.width = ps.photoW + 'px'; }
    // Restore stats open/closed state
    try {
      if (_uiPrefsGet('ascent-stats-open') === '0') {
        const panel    = document.getElementById('info-panel');
        const btn      = document.getElementById('stats-disclose');
        const content  = panel && panel.querySelector('.stats-content');
        if (panel) panel.style.height = STATS_TITLE_H + 'px';
        if (content) content.style.display = 'none';
        if (btn) btn.classList.add('collapsed');
      } else {
        const titlebar = document.getElementById('stats-titlebar');
        if (titlebar) titlebar.style.cursor = 'row-resize';
      }
    } catch(e) {}
    // Restore analysis open/closed state
    try {
      if (_uiPrefsGet('ascent-analysis-open') === '0') {
        const wrap = document.getElementById('chart-wrap');
        const btn  = document.getElementById('analysis-disclose');
        const tb   = document.getElementById('analysis-titlebar');
        if (wrap) wrap.style.height = ANALYSIS_TITLE_H + 'px';
        if (btn) btn.classList.add('collapsed');
        if (tb) tb.style.cursor = 'default';
      }
    } catch(e) {}
  }
  // Auto-select activity from ?select= param
  const sel = new URLSearchParams(location.search).get('select');
  if (sel) selectActivity(parseInt(sel));
  // Check for new activities to show coach notification dot
  try {
    const cr = await fetch('/api/coach/state');
    const cd = await cr.json();
    if (cd.has_goal && cd.has_new_activities) {
      document.getElementById('coach-new-dot').style.display = 'inline-block';
    }
  } catch(e) {}
})();

// ── USER PICKER ───────────────────────────────────────────────────────────────
let _allUsers = [];  // cached from /api/users

async function openUserPicker() {
  const modal = document.getElementById('user-picker-modal');
  const list  = document.getElementById('user-picker-list');
  modal.style.display = 'flex';
  list.innerHTML = '<div style="color:#888;font-size:13px">Loading…</div>';
  try {
    if (!_allUsers.length) {
      const r = await fetch('/api/users');
      if (!r.ok) throw new Error('Failed to load users');
      _allUsers = await r.json();
    }
    _renderUserPicker();
  } catch(e) {
    list.innerHTML = '<div style="color:#e55;font-size:13px">Error loading users</div>';
  }
}

function _renderUserPicker() {
  const list = document.getElementById('user-picker-list');
  const effectiveIds = viewState.selectedUserIds.size > 0
    ? viewState.selectedUserIds
    : new Set(viewState.myId !== null ? [viewState.myId] : []);
  list.innerHTML = '';
  _allUsers.forEach(u => {
    const checked = effectiveIds.has(u.id);
    const row = document.createElement('label');
    row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;cursor:pointer;background:#2c2c2e;user-select:none';
    row.innerHTML = `<input type="checkbox" ${checked ? 'checked' : ''} style="accent-color:#f97316;width:16px;height:16px;cursor:pointer">
      <span style="font-size:14px;color:#f5f5f7">${escHtml(u.username)}</span>`;
    const cb = row.querySelector('input');
    cb.addEventListener('change', () => _toggleUserSelection(u.id, cb.checked));
    list.appendChild(row);
  });
  // Apply button
  const applyBtn = document.createElement('button');
  applyBtn.textContent = 'Apply';
  applyBtn.style.cssText = 'margin-top:4px;width:100%;padding:9px;background:#f97316;border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:13px;font-weight:600';
  applyBtn.onclick = () => applyUserSelection();
  list.appendChild(applyBtn);
}

function _toggleUserSelection(userId, checked) {
  if (checked) viewState.selectedUserIds.add(userId);
  else         viewState.selectedUserIds.delete(userId);
}

function closeUserPicker() {
  document.getElementById('user-picker-modal').style.display = 'none';
}

async function applyUserSelection() {
  closeUserPicker();
  // If nothing selected, default back to own activities
  if (viewState.selectedUserIds.size === 0 && viewState.myId !== null) {
    viewState.selectedUserIds.add(viewState.myId);
  }
  const _vk = viewState._viewKey || `ascent-view-user-ids-${viewState.myId}`;
  _uiPrefsSet(_vk, JSON.stringify([...viewState.selectedUserIds]));
  _updateViewBadge();
  // Reset filters
  state.search = ''; state.actType = ''; state.year = '';
  document.getElementById('searchInput').value = '';
  document.getElementById('typeFilter').value  = '';
  document.getElementById('yearFilter').value  = '';
  state.all = []; state.filtered = []; state.selected = null; state.selectedId = null;
  document.getElementById('act-detail').style.display = 'none';
  document.getElementById('no-selection').style.display = 'flex';
  const _ctbU = document.getElementById('chart-toolbar'); if (_ctbU) _ctbU.classList.remove('visible');
  clearMap();
  animReset();
  // Clear photo/video panel
  _panelDetach();
  showPhoto(null);
  photoState.media = []; photoState.idx = 0; photoState.activityId = null;
  // Clear elevation chart
  if (elevChart) { elevChart.destroy(); elevChart = null; }
  elevChartData = null;
  document.getElementById('act-list').scrollTop = 0;
  document.getElementById('list-rows').innerHTML = '';
  document.getElementById('list-spacer').style.height = '0';
  await loadFilterOptions();
  await loadAll();
}

// ── AUTO STRAVA SYNC ON RETURN ────────────────────────────────────────────────
(function() {
  const THRESHOLD_MS = 30 * 60 * 1000;  // 30 minutes away triggers a sync
  const STORAGE_KEY  = 'ascent-hidden-at';
  let syncRunning    = false;
  let toastTimer     = null;

  function saveHiddenTime() {
    localStorage.setItem(STORAGE_KEY, Date.now());
  }

  function showToast(html, autoHideMs) {
    const el = document.getElementById('autosync-toast');
    if (!el) return;
    el.innerHTML = html;
    el.classList.add('visible');
    clearTimeout(toastTimer);
    if (autoHideMs) toastTimer = setTimeout(() => el.classList.remove('visible'), autoHideMs);
  }

  async function runAutoSync() {
    if (syncRunning) return;
    try {
      const r = await fetch('/strava/status');
      if (!r.ok) return;
      const s = await r.json();
      if (!s.authorized) return;
    } catch(e) { return; }

    syncRunning = true;
    showToast('<div class="dot-spin"></div> Checking for new Strava activities…', 0);

    let imported = 0;
    try {
      const es = new EventSource('/strava/run-sync?mode=recent');
      await new Promise(resolve => {
        const guard = setTimeout(() => { es.close(); resolve(); }, 90000);
        es.onmessage = e => {
          const ev = JSON.parse(e.data);
          if (ev.type === 'imported') imported = ev.imported || imported;
          if (ev.type === 'done')     { imported = ev.imported || imported; clearTimeout(guard); es.close(); resolve(); }
          if (ev.type === 'error')    { clearTimeout(guard); es.close(); resolve(); }
        };
        es.onerror = () => { clearTimeout(guard); es.close(); resolve(); };
      });
    } catch(e) {}

    syncRunning = false;
    localStorage.removeItem(STORAGE_KEY);  // reset after every sync attempt

    if (imported > 0) {
      showToast(
        `<div class="toast-dot" style="background:#30d158"></div> ${imported} new activit${imported===1?'y':'ies'} synced`,
        5000
      );
      await loadAll();
    } else {
      document.getElementById('autosync-toast')?.classList.remove('visible');
    }
  }

  // force=true skips the threshold check (used on page load / refresh)
  function checkAndSync(force = false) {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (force || (stored && (Date.now() - parseInt(stored, 10)) >= THRESHOLD_MS)) {
      localStorage.removeItem(STORAGE_KEY);
      runAutoSync();
    }
  }

  // Save timestamp when tab hides or page unloads (refresh/close)
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) saveHiddenTime();
    else checkAndSync();          // tab return: respects 30-min threshold
  });
  window.addEventListener('pagehide', saveHiddenTime);

  // Page load / refresh: always sync
  checkAndSync(true);
})();

function openImportModal() {
  const ov = document.getElementById('import-overlay');
  ov.style.display = 'flex';
  document.getElementById('import-status').textContent = '';
  document.getElementById('import-file-input').value = '';
  document.getElementById('import-drop-zone').style.borderColor = '';
}
function closeImportModal() {
  document.getElementById('import-overlay').style.display = 'none';
}
function handleImportDrop(e) {
  e.preventDefault();
  document.getElementById('import-drop-zone').style.borderColor = '';
  if (e.dataTransfer.files.length) handleImportFiles(e.dataTransfer.files);
}
function handleImportFiles(files) {
  if (!files || !files.length) return;
  const valid = Array.from(files).filter(f => {
    const ext = f.name.split('.').pop().toLowerCase();
    return ext === 'gpx' || ext === 'fit';
  });
  if (!valid.length) {
    document.getElementById('import-status').style.color = '#ff453a';
    document.getElementById('import-status').textContent = 'Unsupported file type. Use .gpx or .fit';
    return;
  }
  _runImportQueue(valid);
}
async function _runImportQueue(files) {
  const statusEl = document.getElementById('import-status');
  let imported = 0, skipped = 0, failed = 0;

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const ext  = file.name.split('.').pop().toLowerCase();
    statusEl.style.color = 'var(--muted)';
    statusEl.textContent = files.length > 1
      ? `Uploading ${i + 1} of ${files.length}…`
      : 'Uploading…';

    const fd = new FormData();
    fd.append('file', file);
    const endpoint = ext === 'fit' ? '/import/fit' : '/import/gpx';

    try {
      const resp = await fetch(endpoint, { method: 'POST', body: fd });
      const data = await resp.json();
      if (resp.status === 201)      imported++;
      else if (resp.status === 409) skipped++;
      else                          failed++;
    } catch(err) {
      failed++;
    }
  }

  // Summary
  const parts = [];
  if (imported) parts.push(`${imported} imported`);
  if (skipped)  parts.push(`${skipped} duplicate`);
  if (failed)   parts.push(`${failed} failed`);
  statusEl.style.color = failed ? '#ff453a' : imported ? '#30d158' : '#ff9f0a';
  statusEl.textContent = parts.join(', ');

  if (imported) {
    setTimeout(() => closeImportModal(), 1200);
    await loadAll();
  }
}
// Close on backdrop click
document.getElementById('import-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeImportModal();
});

