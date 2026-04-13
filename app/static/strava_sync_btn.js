// strava_sync_btn.js — Strava sync icon in navbar
// Self-contained IIFE; injects CSS, inserts button(s) after .nav-version elements.
(function () {
  'use strict';

  // ── CSS ───────────────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
@keyframes ssb-spin { to { transform: rotate(360deg); } }
.strava-sync-btn {
  background: none;
  border: none;
  padding: 0 0 0 5px;
  margin: 0;
  cursor: pointer;
  color: inherit;
  line-height: 0;
  vertical-align: middle;
  opacity: 0.55;
  transition: opacity .15s;
}
.strava-sync-btn:hover { opacity: 1; }
.strava-sync-btn.spinning svg { animation: ssb-spin .9s linear infinite; }
`;
  document.head.appendChild(style);

  // ── SVG icon ──────────────────────────────────────────────────────────────────
  function makeSVG() {
    // 14×14 circular refresh arrows
    return `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 2v6h-6"/>
  <path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>
  <path d="M3 22v-6h6"/>
  <path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
</svg>`;
  }

  // ── State ─────────────────────────────────────────────────────────────────────
  let _running = false;
  let _buttons = [];
  let _es = null;
  let _pendingSpin = false;

  // ── Spin helpers ──────────────────────────────────────────────────────────────
  function _setSpin(on) {
    _pendingSpin = on;
    _buttons.forEach(b => b.classList.toggle('spinning', on));
  }

  // ── Public: start() ───────────────────────────────────────────────────────────
  function start() {
    if (_running) return;
    _running = true;
    _setSpin(true);

    let imported = 0;
    _es = new EventSource('/strava/run-sync?mode=recent');
    const guard = setTimeout(() => { _es && _es.close(); _finish(imported); }, 90000);

    _es.onmessage = e => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === 'imported') imported = ev.imported || imported;
        if (ev.type === 'done')  { imported = ev.imported || imported; clearTimeout(guard); _es.close(); _finish(imported); }
        if (ev.type === 'error') { clearTimeout(guard); _es.close(); _finish(imported); }
      } catch (_) {}
    };
    _es.onerror = () => { clearTimeout(guard); _es && _es.close(); _finish(imported); };
  }

  function _finish(imported) {
    _running = false;
    _es = null;
    _setSpin(false);
    document.dispatchEvent(new CustomEvent('stravasynccomplete', { detail: { imported } }));
  }

  // ── Public: stop() ────────────────────────────────────────────────────────────
  function stop() {
    if (_es) { _es.close(); _es = null; }
    _running = false;
    _setSpin(false);
  }

  // ── Button injection ──────────────────────────────────────────────────────────
  function _makeButton() {
    const btn = document.createElement('button');
    btn.className = 'strava-sync-btn';
    btn.title = 'Sync Strava activities';
    btn.innerHTML = makeSVG();
    btn.addEventListener('click', () => start());
    return btn;
  }

  function _injectButtons() {
    const targets = [
      ...document.querySelectorAll('.nav-version'),
      document.getElementById('mob-header-version'),
    ].filter(Boolean);

    targets.forEach(el => {
      const btn = _makeButton();
      el.after(btn);
      _buttons.push(btn);
    });
    if (_pendingSpin) _setSpin(true);
  }

  // ── Init ──────────────────────────────────────────────────────────────────────
  async function _init() {
    try {
      const r = await fetch('/strava/status');
      if (!r.ok) return;
      const s = await r.json();
      if (!s.authorized) return;
    } catch (_) { return; }

    _injectButtons();
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  // ── Public API ────────────────────────────────────────────────────────────────
  window.stravaSync = {
    start,
    stop,
    spin: _setSpin,
    get isRunning() { return _running; },
  };
})();
