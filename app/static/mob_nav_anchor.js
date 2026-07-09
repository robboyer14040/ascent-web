// ── Shared iOS-standalone bottom-bar anchor ────────────────────────────────────
// A position:fixed;bottom:0 bar anchors to window.innerHeight — but in an iOS
// standalone (Home Screen) WKWebView, fixed-layout pages report innerHeight ~24px
// SHORTER than the physical screen, so the bar floats too high with a gap below it.
// Anchor to the stable screen height instead: place the bar's top at
// (screenHeight − barHeight). Used by the global nav (_mob_global_nav.html) and the
// Tours sub-nav (tour_mob_nav.js), which both sit at the bottom on fixed-layout pages.
(function () {
  if (window.MobNavAnchor) return;   // idempotent: both includes may load this
  window.MobNavAnchor = {
    // Measure an env(safe-area-inset-*) value in px via a throwaway probe. Reads
    // immediately, unlike an element's own padding which resolves after first paint.
    safeInset: function (side) {
      var p = document.createElement('div');
      p.style.cssText = 'position:fixed;left:0;' + side +
        ':0;width:0;height:env(safe-area-inset-' + side +
        ',0px);visibility:hidden;pointer-events:none';
      document.body.appendChild(p);
      var h = p.offsetHeight;
      document.body.removeChild(p);
      return h;
    },
    screenH: function () { return Math.max(window.screen.width, window.screen.height); },
    // Anchoring is needed only in iOS standalone, portrait phone. Landscape uses a
    // side-strip layout and browser tabs already use the correct visual viewport.
    needsAnchor: function () {
      return !!window.navigator.standalone &&
        window.innerWidth <= 767 && window.innerHeight > window.innerWidth;
    },
    // Pin a fixed;bottom:0 bar of CSS height calc(base + safe-area-bottom) to the
    // physical screen bottom. Returns the bar's top Y in px (so callers can align a
    // sibling panel to it), or null when no anchoring applies — in which case the
    // caller should clear the inline styles and fall back to CSS.
    anchorBottomBar: function (el, base) {
      if (!this.needsAnchor()) return null;
      var safeB = this.safeInset('bottom');
      var h = base + safeB;
      var top = this.screenH() - h;
      el.style.bottom = 'auto';
      el.style.top = top + 'px';
      el.style.height = h + 'px';
      el.style.paddingBottom = safeB + 'px';
      return top;
    }
  };
})();
