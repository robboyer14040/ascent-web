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

// document.createElement('div') backing for common.js `esc`: setting textContent
// then reading innerHTML mirrors the browser's HTML-text escaping of & < >.
function _escElement() {
  var _html = '';
  return {
    set textContent(v) {
      _html = String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
    get innerHTML() { return _html; },
  };
}
var document = { createElement: function () { return _escElement(); } };
var window = {};
var localStorage = {
  _s: {},
  getItem: function (k) { return k in this._s ? this._s[k] : null; },
  setItem: function (k, v) { this._s[k] = String(v); },
  removeItem: function (k) { delete this._s[k]; },
};

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
