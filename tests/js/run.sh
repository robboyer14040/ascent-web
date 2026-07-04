#!/bin/sh
# Runs the static-JS unit tests under macOS JavaScriptCore (jsc).
# There's no node/npm in this project; jsc ships with macOS. Files are passed as
# multiple args to a single jsc invocation so they share one global scope: the
# harness stubs load first, then the app modules define their helpers, then the
# test files run, then finalize.js reports and sets the exit code.
#
# Override the jsc path with JSC=/path/to/jsc if needed.
set -e

JSC="${JSC:-/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc}"
DIR="$(cd "$(dirname "$0")" && pwd)"
S="$DIR/../../app/static"

if [ ! -x "$JSC" ]; then
  echo "jsc not found at: $JSC" >&2
  echo "Install Xcode/CLT or set JSC=/path/to/jsc" >&2
  exit 127
fi

exec "$JSC" \
  "$DIR/harness.js" \
  "$S/common.js" \
  "$S/tour_stage.js" \
  "$S/activity_chips.js" \
  "$DIR/test_common.js" \
  "$DIR/test_tour_stage.js" \
  "$DIR/test_activity_chips.js" \
  "$DIR/finalize.js"
