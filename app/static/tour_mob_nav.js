// ── Tours mobile nested-nav controller — shared by tour.html & tour_share.html ──
// Owns the phone-only two-level nav. It reparents the existing desktop DOM (#map,
// #stage-list-wrap, #stage-detail, #stage-elev-strip) into the panels defined by
// _tour_mob_nav.html and swaps between:
//   level 1 (no stage) → Entire Route / Stages / Info
//   level 2 (stage)    → Map / Analysis / Photos / Forecast
//
// Page contract (globals each tour page must provide):
//   getStageMap()        → the Leaflet map instance (may be null early)
//   onTourMobBack()      → deselect the current stage (level-2 back). Optional; if
//                          absent the controller just returns to level 1 visually.
//   TourNav.homeHref     → where level-1 Back navigates (set per page)
//   window.redrawStageElev() → optional elevation redraw hook, called on Analysis show
//
// The page's selectStage() calls TourNav.showStage(hasStage) after rendering so the
// controller can enter/leave level 2. renderDetail() writes stage photos into
// #stage-photos-body and the forecast into #stage-forecast-body (both created here).

const TourNav = (function () {
  'use strict';

  // Phone layout = the same breakpoint the CSS uses (portrait phone OR short landscape).
  function isPhone() {
    return window.innerWidth <= 767 ||
           (window.innerHeight <= 500 && window.innerWidth > window.innerHeight);
  }

  const $ = id => document.getElementById(id);
  function move(el, parent, prepend) {
    if (!el || !parent || el.parentNode === parent) return;
    if (prepend) parent.insertBefore(el, parent.firstChild);
    else parent.appendChild(el);
  }

  let _ready = false;      // panels wired up
  let _l1 = 'route';       // active level-1 tab
  let _l2 = 'map';         // active level-2 tab
  const api = { homeHref: '/' };

  function _refitMap() {
    const m = (typeof getStageMap === 'function') ? getStageMap() : null;
    if (!m) return;
    // Re-fit bounds after the panel is visible: fitBounds computed against a hidden
    // (0-size) panel picks a bogus zoom, so invalidateSize alone leaves it wrong.
    requestAnimationFrame(() => {
      m.invalidateSize({ animate: false });
      if (typeof window.refitTourMap === 'function') window.refitTourMap();
    });
  }
  function _redrawElev() {
    if (typeof window.redrawStageElev === 'function') {
      requestAnimationFrame(() => window.redrawStageElev());
    }
  }

  // ── iOS standalone: pin the sub-nav to the PHYSICAL bottom ──────────────────
  // #tour-subnav is position:fixed;bottom:0, but on these fixed-layout tour pages
  // (no has-mob-topbar) bottom:0 anchors to the SHORT layout viewport, so the bar and
  // its panels float too high with a gap below. MobNavAnchor re-pins the bar to the
  // physical screen bottom (same helper the global nav uses); we then stretch the
  // panels down to meet it. Done here because the global nav is hidden on tour.html
  // and absent from tour_share.html, so the sub-nav needs its own repositioning.
  function _placeChrome() {
    const nav = $('tour-subnav'), panels = $('tour-panels');
    if (!nav || !panels || !window.MobNavAnchor) return;
    const navTop = window.MobNavAnchor.anchorBottomBar(nav, 58);
    if (navTop === null) {   // not standalone / landscape → fall back to CSS
      nav.style.top = nav.style.height = nav.style.bottom = nav.style.paddingBottom = '';
      panels.style.top = panels.style.height = panels.style.bottom = '';
      return;
    }
    const panelTop = 52 + window.MobNavAnchor.safeInset('top');  // .mob-topbar height
    panels.style.bottom = 'auto';
    panels.style.top = panelTop + 'px';
    panels.style.height = (navTop - panelTop) + 'px';
  }

  // One-time reparent of shared elements into their home panels.
  function _wire() {
    if (_ready) return;
    const panels = ['route', 'stages', 'info', 'allphotos', 'map', 'analysis', 'photos', 'forecast']
      .reduce((o, k) => (o[k] = $('tour-panel-' + k), o), {});
    if (!panels.route) return;

    // Photos / Forecast bodies — renderDetail() targets these by id.
    if (!$('stage-photos-body')) {
      const p = document.createElement('div'); p.id = 'stage-photos-body';
      panels.photos.appendChild(p);
    }
    // All-stages Photos body (level-1 overview tab).
    if (!$('tour-allphotos-body')) {
      const a = document.createElement('div'); a.id = 'tour-allphotos-body';
      panels.allphotos.appendChild(a);
    }
    if (!$('stage-forecast-body')) {
      const f = document.createElement('div'); f.id = 'stage-forecast-body';
      panels.forecast.appendChild(f);
    }

    // The chart-settings popover must escape the (display:none) desktop #page so the
    // gear button can open it on phone. It's position:fixed, so body is fine.
    move($('stage-chart-settings'), document.body);
    // Stage list always lives in the Stages panel (also shown pinned-left in landscape).
    move($('stage-list-wrap'), panels.stages);
    // The follower opt-in (tour_share only) rides above it, as it does on desktop.
    move($('notify-optin'), panels.stages, true);
    // Tour-management controls (tour.html only) sit above the map on Entire Route.
    move($('tour-controls'), panels.route);
    move($('map'), panels.route);
    // Info: the shared-tour attribution card (tour_share only) above the overview/detail.
    move($('tour-header'), panels.info);
    move($('stage-detail'), panels.info);

    document.body.classList.add('tour-mob-nav');
    // Pages without the global bottom/side nav (tour_share) get no 60px landscape gutter.
    if (!$('mob-global-nav')) document.body.classList.add('tour-no-gnav');
    _ready = true;
  }

  // Analysis panel layout: elevation strip, draggable divider, then the scrolling
  // info section. Appended in order so the divider sits between chart and info.
  function _layoutAnalysis() {
    const panel = $('tour-panel-analysis');
    if (!panel) return;
    const strip  = $('stage-elev-strip');
    const handle = $('elev-resize-handle');
    const detail = $('stage-detail');
    if (strip)  panel.appendChild(strip);
    if (handle) panel.appendChild(handle);
    if (detail) panel.appendChild(detail);
  }

  function _setActivePanel(name) {
    document.querySelectorAll('#tour-panels .tour-panel').forEach(p => p.classList.remove('active'));
    const p = $('tour-panel-' + name);
    if (p) p.classList.add('active');
  }
  function _setActiveBtn(attr, val) {
    document.querySelectorAll(`#tour-subnav .tmob-btn[${attr}]`).forEach(b =>
      b.classList.toggle('active', b.getAttribute(attr) === val));
  }

  function _placeholder(panel, text) {
    // Only fill a placeholder when the panel has no real content.
    if (!panel) return;
    const real = panel.querySelector(':scope > :not(.tour-tab-empty)');
    if (real) { const ph = panel.querySelector('.tour-tab-empty'); if (ph) ph.remove(); return; }
    if (!panel.querySelector('.tour-tab-empty')) {
      const d = document.createElement('div');
      d.className = 'tour-tab-empty';
      d.textContent = text;
      panel.appendChild(d);
    }
  }

  // ── Level 1 ──
  function navL1(tab) {
    if (!_ready) return;
    _l1 = tab;
    _setActiveBtn('data-l1', tab);
    if (tab === 'route') move($('map'), $('tour-panel-route'));
    if (tab === 'info')  move($('stage-detail'), $('tour-panel-info'));
    // All-stages photos: re-render on each open so it always reflects the current tour.
    if (tab === 'allphotos') {
      const body = $('tour-allphotos-body');
      if (body && typeof window.renderTourPhotosTab === 'function') window.renderTourPhotosTab(body);
    }
    _setActivePanel(tab);
    if (tab === 'route') _refitMap();
  }

  // ── Level 2 ──
  function navL2(tab) {
    if (!_ready) return;
    _l2 = tab;
    _setActiveBtn('data-l2', tab);
    if (tab === 'map') move($('map'), $('tour-panel-map'));
    if (tab === 'analysis') _layoutAnalysis();
    if (tab === 'photos')   _lazyTab('photos',   window.renderStagePhotosTab,   'No photos for this stage.');
    if (tab === 'forecast') _lazyTab('forecast', window.renderStageForecastTab, 'No forecast available.');
    _setActivePanel(tab);
    if (tab === 'map') _refitMap();
    if (tab === 'analysis') _redrawElev();
  }

  // Photos/Forecast are rendered lazily by page hooks into their body element, once
  // per stage selection. Falls back to a placeholder when the hook renders nothing.
  function _lazyTab(name, hook, emptyText) {
    const body = $('stage-' + name + '-body');
    if (!body) return;
    if (body.dataset.loaded) return;
    body.dataset.loaded = '1';
    body.innerHTML = '';
    if (typeof hook === 'function') hook(body);
    // If the hook rendered nothing (sync), show a placeholder.
    if (!body.childNodes.length) {
      body.innerHTML = '<div class="tour-tab-empty">' + emptyText + '</div>';
    }
  }

  // Enter / leave the stage (level 2). Called by the page's selectStage().
  function showStage(hasStage) {
    if (!isPhone()) return;
    _wire();
    if (hasStage) {
      document.body.classList.add('tour-l2');
      // New stage → photos/forecast tabs must re-render on next open.
      ['stage-photos-body', 'stage-forecast-body'].forEach(id => {
        const b = $(id); if (b) { delete b.dataset.loaded; b.innerHTML = ''; }
      });
      move($('map'), $('tour-panel-map'));
      _layoutAnalysis();
      navL2('map');
    } else {
      document.body.classList.remove('tour-l2');
      move($('map'), $('tour-panel-route'));
      move($('stage-detail'), $('tour-panel-info'));
      navL1(_l1 === 'stages' ? 'stages' : 'route');
    }
  }

  function back() {
    if (document.body.classList.contains('tour-l2')) {
      if (typeof window.onTourMobBack === 'function') window.onTourMobBack();
      else showStage(false);
    } else if (window.history.length > 1) {
      window.history.back();
    } else {
      window.location.href = api.homeHref;
    }
  }

  // Boot: wire panels on phone once the DOM is ready.
  function init() {
    if (!isPhone()) return;
    _wire();
    navL1('route');
    _placeChrome();
    if (window.visualViewport) window.visualViewport.addEventListener('resize', _placeChrome);
    window.addEventListener('load', _placeChrome);
    setTimeout(_placeChrome, 800);   // failsafe: env() can resolve after first paint
    // Refit the map after orientation / viewport changes.
    let t = null;
    window.addEventListener('resize', () => {
      _placeChrome();
      if (!isPhone() || !_ready) return;
      clearTimeout(t);
      t = setTimeout(() => {
        const mapPanel = document.body.classList.contains('tour-l2')
          ? $('tour-panel-map') : $('tour-panel-route');
        if (mapPanel && mapPanel.classList.contains('active')) _refitMap();
      }, 200);
    });
  }

  api.init = init;
  api.navL1 = navL1;
  api.navL2 = navL2;
  api.showStage = showStage;
  api.back = back;
  api.isPhone = isPhone;
  return api;
})();

// Global handlers referenced by _tour_mob_nav.html and the Back button.
function tourNavL1(tab) { TourNav.navL1(tab); }
function tourNavL2(tab) { TourNav.navL2(tab); }
function tourMobBack()  { TourNav.back(); }

document.addEventListener('DOMContentLoaded', () => TourNav.init());
