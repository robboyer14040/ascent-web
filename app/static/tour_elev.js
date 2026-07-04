'use strict';
/* tour_elev.js — shared stage-elevation-strip wiring for the tour pages
   (tour.html, tour_share.html). Owns the elevation-strip state plus all the
   draw / rebuild / settings / mobile-collapse logic.

   Load AFTER common.js (_haversineMi), elev_gradient.js (computeStageGradFromRawPts)
   and stage_elev_chart.js (buildStageElevChart, stageElevInteraction), and BEFORE
   each page's inline <script>.

   Uses these PER-PAGE globals, defined in each page's inline script (they differ
   between the two pages, so they stay there):
     getStageMap()          -> the Leaflet map (tourMap / shareMap)
     tourChromeH()           -> top-chrome height (.mob-topbar vs fixed 52)
     _syncStageListHeight()  -> repositions the stage list (#tour-controls vs #tour-header)
     mapH                    -> current map height in px
     U                       -> units object (reads U.metric) */

// ── Elevation-strip state ─────────────────────────────────────────────────────
let _elevChart = null, _elevRawPts = null, _elevHoverDot = null;
let _elevChartPts = null, _elevGradeOn = true, _elevPeaksOn = true;
let _elevGradPts = null; // pre-computed gradient [{x,y}] from full-res raw pts
let _elevActData = null; // per-point chart data for completed stages (null unless the page fills it)
let _elevSettingsOpen = false;
let _mobElevCollapsed = false;
let _mobInfoCollapsed = false;

// ── Strip height persistence + vertical resize ────────────────────────────────
const _ELEV_H_KEY = 'ascent-stage-elev-h';
const _ELEV_H_MIN = 60, _ELEV_H_MAX = 400, _ELEV_H_DEF = 100;
function _elevHSaved() {
  try { const v = parseInt(localStorage.getItem(_ELEV_H_KEY)); return (v >= _ELEV_H_MIN && v <= _ELEV_H_MAX) ? v : _ELEV_H_DEF; } catch(e) { return _ELEV_H_DEF; }
}
function _elevHSave(h) { try { localStorage.setItem(_ELEV_H_KEY, h); } catch(e) {} }

(function() {
  const handle = document.getElementById('elev-resize-handle');
  if (!handle) return;
  let drag = false, startY = 0, startH = 0;
  function startDrag(y) {
    const strip = document.getElementById('stage-elev-strip');
    if (!strip) return;
    drag = true; startY = y; startH = strip.offsetHeight;
    handle.classList.add('dragging');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  }
  function moveDrag(y) {
    if (!drag) return;
    const strip = document.getElementById('stage-elev-strip');
    if (!strip) return;
    const newH = Math.max(_ELEV_H_MIN, Math.min(_ELEV_H_MAX, startH + (y - startY)));
    strip.style.height = newH + 'px';
    _elevChart?.resize?.();
    if (document.body.classList.contains('mob-stage-view') && isPortrait()) {
      document.getElementById('stage-detail').style.top = _stageDetailTop();
    }
  }
  function endDrag() {
    if (!drag) return;
    drag = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const strip = document.getElementById('stage-elev-strip');
    if (strip) _elevHSave(strip.offsetHeight);
    _elevChart?.resize?.();
  }
  handle.addEventListener('mousedown',  e => { startDrag(e.clientY); e.preventDefault(); });
  document.addEventListener('mousemove', e => moveDrag(e.clientY));
  document.addEventListener('mouseup',   endDrag);
  handle.addEventListener('touchstart', e => { startDrag(e.touches[0].clientY); e.preventDefault(); }, {passive:false});
  handle.addEventListener('touchmove',  e => { moveDrag(e.touches[0].clientY); e.preventDefault(); }, {passive:false});
  handle.addEventListener('touchend',   endDrag);
})();

function toggleMobStats() {
  const mps = document.getElementById('mob-portrait-stats');
  const btn = document.getElementById('stats-toggle');
  if (!mps || !btn) return;
  const collapsed = mps.classList.toggle('stats-collapsed');
  btn.textContent = collapsed ? '▸' : '▾';
  _syncStageListHeight();
}

// ── Chart prefs + settings popover ────────────────────────────────────────────
function _saveElevChartPrefs() {
  try { localStorage.setItem('ascent-stage-chart-prefs', JSON.stringify({ gradeOn: _elevGradeOn, peaksOn: _elevPeaksOn })); } catch(e) {}
}
function _loadElevChartPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem('ascent-stage-chart-prefs') || 'null');
    if (!p) return;
    if (p.gradeOn !== undefined) { _elevGradeOn = !!p.gradeOn; const cb = document.getElementById('stage-grade-cb'); if (cb) cb.checked = _elevGradeOn; }
    if (p.peaksOn !== undefined) { _elevPeaksOn = !!p.peaksOn; const cb = document.getElementById('stage-peaks-cb'); if (cb) cb.checked = _elevPeaksOn; }
  } catch(e) {}
}
function onStagePeaksCbChange(cb) {
  _elevPeaksOn = cb.checked;
  _saveElevChartPrefs();
  if (_elevChartPts) _rebuildElevChart();
}
function onStageGradeCbChange(cb) {
  _elevGradeOn = cb.checked;
  _saveElevChartPrefs();
  if (_elevChartPts) _rebuildElevChart();
}
function toggleStageSettings(e) {
  e.stopPropagation();
  const pop = document.getElementById('stage-chart-settings');
  if (!pop) return;
  _elevSettingsOpen = !_elevSettingsOpen;
  if (_elevSettingsOpen) {
    const btn = document.getElementById('stage-settings-btn');
    const r = btn.getBoundingClientRect();
    pop.style.top   = (r.bottom + 4) + 'px';
    pop.style.right = (window.innerWidth - r.right) + 'px';
    pop.style.left  = 'auto';
    pop.style.display = 'block';
  } else {
    pop.style.display = 'none';
  }
}
document.addEventListener('click', function(e) {
  if (!_elevSettingsOpen) return;
  const pop = document.getElementById('stage-chart-settings');
  const btn = document.getElementById('stage-settings-btn');
  if (pop && !pop.contains(e.target) && e.target !== btn) {
    pop.style.display = 'none';
    _elevSettingsOpen = false;
  }
});
function _rebuildElevChart() {
  const canvas = document.getElementById('stage-elev-canvas');
  if (!canvas || !_elevChartPts) return;
  if (_elevChart) { _elevChart.destroy(); _elevChart = null; }
  _elevChart = buildStageElevChart(canvas, _elevChartPts, { gradeOn: _elevGradeOn, gradPts: _elevGradPts, showPeaks: _elevPeaksOn });
  _elevChart._chartPts = _elevChartPts;
  window._stageElevReapplySel?.();
}

// ── Draw / hide the elevation strip ───────────────────────────────────────────
function drawStageElevation(pts) {
  const strip=document.getElementById('stage-elev-strip'), canvas=document.getElementById('stage-elev-canvas');
  if(!strip||!canvas) return;
  _elevActData=null;
  window._stageElevClearSel?.();
  if(_elevChart){_elevChart.destroy();_elevChart=null;}
  if(_elevHoverDot){_elevHoverDot.remove();_elevHoverDot=null;}
  _elevRawPts=null; _elevChartPts=null; _elevGradPts=null;
  // Reset mobile collapse state for new stage
  _mobElevCollapsed = false;
  _mobInfoCollapsed = false;
  const _mehBtn=document.getElementById('mob-elev-disclose');
  if(_mehBtn){const _a=_mehBtn.querySelector('.mob-arr');if(_a)_a.classList.remove('collapsed');}
  const _mihBtn=document.getElementById('mob-info-disclose');
  if(_mihBtn){const _a=_mihBtn.querySelector('.mob-arr');if(_a)_a.classList.remove('collapsed');}
  const _detailEl=document.getElementById('stage-detail');
  if(_detailEl) _detailEl.style.display='';
  if(!pts?.length||pts[0].length<3||!pts.some(p=>p[2]&&p[2]!==0)){strip.style.display='none';return;}
  const step=Math.max(1,Math.floor(pts.length/600));
  const distConv=U.metric?mi=>+(mi*1.60934).toFixed(3):mi=>+mi.toFixed(3);
  const altConv=U.metric?ft=>Math.round(ft*0.3048):ft=>Math.round(ft);
  let cum=0; const chartPts=[], rawPts=[];
  for(let i=0;i<pts.length;i++){
    if(i>0) cum+=_haversineMi(pts[i-1][0],pts[i-1][1],pts[i][0],pts[i][1]);
    if(i%step===0||i===pts.length-1){chartPts.push({x:distConv(cum),y:altConv(pts[i][2]||0)});rawPts.push(pts[i]);}
  }
  if(!chartPts.length){strip.style.display='none';return;}
  _elevRawPts=rawPts; _elevChartPts=chartPts;
  _elevGradPts=computeStageGradFromRawPts(pts, step, distConv);
  strip.style.height=_elevHSaved()+'px';
  strip.style.display='block';
  const _elevHandle=document.getElementById('elev-resize-handle');
  if(_elevHandle) _elevHandle.style.display='';
  _rebuildElevChart();
  stageElevInteraction({
    getChart:    () => _elevChart,
    getChartPts: () => _elevChartPts,
    getGradPts:  () => _elevGradPts,
    getRawPts:   () => _elevRawPts,
    getActData:  () => _elevActData,
    getMap:      () => getStageMap(),
    getMapDot:   () => _elevHoverDot,
    setMapDot:   m  => { _elevHoverDot = m; },
  });
}
function hideStageElevation() {
  const strip=document.getElementById('stage-elev-strip');
  if(strip) strip.style.display='none';
  const _elevHandle=document.getElementById('elev-resize-handle');
  if(_elevHandle) _elevHandle.style.display='none';
  window._stageElevClearSel?.();
  if(_elevChart){_elevChart.destroy();_elevChart=null;}
  if(_elevHoverDot){_elevHoverDot.remove();_elevHoverDot=null;}
  _elevRawPts=null; _elevChartPts=null; _elevGradPts=null; _elevActData=null;
  // Reset mobile collapse state
  _mobElevCollapsed=false; _mobInfoCollapsed=false;
  const _mehBtn2=document.getElementById('mob-elev-disclose');
  if(_mehBtn2){const _a=_mehBtn2.querySelector('.mob-arr');if(_a)_a.classList.remove('collapsed');}
  const _mihBtn2=document.getElementById('mob-info-disclose');
  if(_mihBtn2){const _a=_mihBtn2.querySelector('.mob-arr');if(_a)_a.classList.remove('collapsed');}
  const _detailEl2=document.getElementById('stage-detail');
  if(_detailEl2) _detailEl2.style.display='';
}

// ── Mobile helpers (shared subset; tourChromeH + _syncStageListHeight stay per-page) ──
const isMobTour  = () => Math.min(window.innerWidth, window.innerHeight) <= 767;
const isPortrait = () => window.innerWidth < window.innerHeight;
function _stageDetailTop() {
  const strip = document.getElementById('stage-elev-strip');
  const rh    = document.getElementById('resize-handle');
  const erh   = document.getElementById('elev-resize-handle');
  const rhH   = rh  ? rh.offsetHeight  : 6;
  const elevH = (strip && strip.style.display !== 'none') ? strip.offsetHeight : 0;
  const erhH  = erh ? erh.offsetHeight : 0;
  return (tourChromeH() + mapH + rhH + elevH + erhH) + 'px';
}
function toggleMobElev() {
  if (!isMobTour()) return;
  const strip = document.getElementById('stage-elev-strip');
  const btn   = document.getElementById('mob-elev-disclose');
  const arr   = btn && btn.querySelector('.mob-arr');
  if (!strip || (strip.style.display === 'none' && !_mobElevCollapsed)) return;
  _mobElevCollapsed = !_mobElevCollapsed;
  strip.style.display = _mobElevCollapsed ? 'none' : '';
  if (arr) arr.classList.toggle('collapsed', _mobElevCollapsed);
  if (document.body.classList.contains('mob-stage-view') && isPortrait()) {
    const sd = document.getElementById('stage-detail');
    if (sd) sd.style.top = _stageDetailTop();
  }
}
function toggleMobInfo() {
  if (!isMobTour()) return;
  const detail = document.getElementById('stage-detail');
  const btn    = document.getElementById('mob-info-disclose');
  const arr    = btn && btn.querySelector('.mob-arr');
  if (!detail) return;
  _mobInfoCollapsed = !_mobInfoCollapsed;
  detail.style.display = _mobInfoCollapsed ? 'none' : '';
  if (arr) arr.classList.toggle('collapsed', _mobInfoCollapsed);
}

// ── Elevation-help hint auto-dismiss ──────────────────────────────────────────
(function(){
  const strip = document.getElementById('stage-elev-strip');
  const help  = document.getElementById('stage-elev-help');
  if (!strip || !help) return;
  function dismiss() {
    help.classList.add('hidden');
    strip.removeEventListener('mousedown',  dismiss);
    strip.removeEventListener('touchstart', dismiss);
  }
  strip.addEventListener('mousedown',  dismiss);
  strip.addEventListener('touchstart', dismiss, {passive:true});
})();
