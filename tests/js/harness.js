'use strict';
/* harness.js — loaded FIRST under jsc (see run.sh). Provides:
     1. minimal browser/Leaflet globals the static JS touches at load time, so
        files like common.js (which builds L.divIcon markers up top) can load.
     2. a tiny test framework (test / eq / ok / approx / countOccurrences).
   The result counters live on the shared global scope; finalize.js reports them.

   No node/npm in this project — the frontend helpers are exercised with the
   system `jsc` binary. DOM-heavy behavior can't be fully validated here; these
   stubs only cover the small surface the pure helpers actually use. */

// ── browser / Leaflet stubs ───────────────────────────────────────────────────
var L = {
  divIcon:   function (o) { return o; },
  tileLayer: function () { return { addTo: function () { return this; } }; },
};

// Minimal element stub. Two consumers:
//   * common.js `esc` — sets textContent, reads innerHTML back; that round-trip
//     must mirror the browser's HTML-text escaping of & < >.
//   * coach.js `coachAppendMessage` — sets className/dataset/innerHTML and
//     appends into a container.
function _element(tag) {
  var el = {
    tagName: String(tag || 'div').toUpperCase(),
    className: '',
    dataset: {},
    children: [],
    _html: '',
    set textContent(v) {
      this._html = String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
    get textContent() { return this._html; },
    set innerHTML(v) { this._html = String(v == null ? '' : v); },
    get innerHTML() { return this._html; },
    appendChild: function (c) { this.children.push(c); return c; },
    remove: function () {},
    classList: {
      _c: [],
      add: function () {}, remove: function () {},
      contains: function () { return false; },
      toggle: function () {},
    },
  };
  return el;
}

var document = {
  _byId: {},                                 // tests register elements here
  createElement: function (tag) { return _element(tag); },
  addEventListener: function () {},          // location_summary.js wires a click handler at load
  getElementById: function (id) { return this._byId[id] || null; },
  querySelectorAll: function () { return []; },
  head: { appendChild: function () {} },
  body: { appendChild: function () {} },
};
var _storage = function () {
  return {
    _s: {},
    getItem: function (k) { return k in this._s ? this._s[k] : null; },
    setItem: function (k, v) { this._s[k] = String(v); },
    removeItem: function (k) { delete this._s[k]; },
  };
};
var localStorage   = _storage();
var sessionStorage = _storage();
var location = { search: '', href: '' };
var window = {};
window.self = window;
window.top  = window;          // coach.js checks for iframe embedding at load

// coach.js reads the openCoach query param at load time.
function URLSearchParams(qs) {
  this._q = String(qs || '');
  this.get = function () { return null; };
}

// coach.js starts a state-polling timer at load.
function setInterval() { return 0; }
function clearInterval() {}
function setTimeout(fn) { return 0; }
function clearTimeout() {}

// escHtml lives in main.js, which is too DOM-heavy to load here; coach.js only
// calls it at runtime, so the harness supplies the same implementation.
function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── tiny test framework ───────────────────────────────────────────────────────
var _T = { pass: 0, fail: 0, fails: [] };

function test(name, fn) {
  try { fn(); _T.pass++; }
  catch (e) { _T.fail++; _T.fails.push(name + ': ' + (e && e.message ? e.message : e)); }
}

// Deep-equality via JSON (sufficient for the plain arrays/objects tested here).
function eq(actual, expected, msg) {
  var a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a !== b) throw new Error((msg ? msg + ' — ' : '') + 'expected ' + b + ' got ' + a);
}

function ok(cond, msg) { if (!cond) throw new Error(msg || 'expected truthy'); }

function approx(actual, expected, tol, msg) {
  tol = tol == null ? 1e-6 : tol;
  if (Math.abs(actual - expected) > tol)
    throw new Error((msg ? msg + ' — ' : '') + 'expected ~' + expected + ' (±' + tol + ') got ' + actual);
}

function countOccurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
}
