'use strict';
/* Loaded LAST under jsc. Prints the run summary and throws on any failure so the
   process exits non-zero (jsc's quit(code) does not propagate an exit code, but
   an uncaught exception does). */
print('');
print('JS tests: ' + _T.pass + ' passed, ' + _T.fail + ' failed');
if (_T.fail) {
  _T.fails.forEach(function (f) { print('  ✗ ' + f); });
  throw new Error(_T.fail + ' JS test(s) failed');
}
print('✓ all ' + _T.pass + ' JS tests passed');
