"""Runs the static-JS unit suite (tests/js/) under macOS JavaScriptCore.

There's no node/npm in this project, so the frontend helpers are exercised with
the system `jsc` binary. This wrapper lets `pytest` run the JS suite alongside
the Python tests. It's skipped automatically where jsc isn't available (e.g.
Linux CI); run the JS suite directly with `tests/js/run.sh`.
"""
import os
import subprocess

import pytest

JSC = os.environ.get(
    "JSC",
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc",
)
RUN_SH = os.path.join(os.path.dirname(__file__), "js", "run.sh")


@pytest.mark.skipif(not os.path.exists(JSC), reason="JavaScriptCore (jsc) not available")
def test_static_js_suite():
    proc = subprocess.run(["/bin/sh", RUN_SH], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail("static JS suite failed:\n" + proc.stdout + proc.stderr)
