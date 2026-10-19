#!/bin/sh
# Local "nightly": regression check, a long property run, corpus replays and an inspector export.
# Usage: scripts/nightly.sh [examples] [out-dir]      (defaults: 2000, runs/nightly-<date>)
set -u
cd "$(dirname "$0")/.."
EXAMPLES="${1:-2000}"
OUT="${2:-runs/nightly-$(date +%Y-%m-%d)}"
mkdir -p "$OUT"
. .venv/bin/activate

echo "== build"
(cd harness-java && ./gradlew -q fatJar) || exit 1

echo "== regressions must still be detected"
pytest tests/test_harness.py tests/test_generator.py -q 2>&1 | tail -1 | tee "$OUT/tests.txt"

echo "== property test ($EXAMPLES examples, coverage-steered)"
OXPECKER_COVERAGE=1 OXPECKER_EXAMPLES="$EXAMPLES" pytest tests/test_differential.py -q -k interpreted_equals \
  -p no:cacheprovider 2>&1 | tail -15 | tee "$OUT/property.txt"
[ -d tests/findings ] && cp -r tests/findings "$OUT/findings"

echo "== corpora"
oxpecker corpus rhino-jstests -o "$OUT/jstests" 2>&1 | tail -12 | tee "$OUT/jstests.txt"
oxpecker corpus test262 --filter language -o "$OUT/t262-language" 2>&1 | tail -20 | tee "$OUT/t262.txt"

echo "== inspector export"
oxpecker export -o ui/data --per-generator 20 --per-template 20 --coverage --worst 15 2>&1 | tail -22 | tee "$OUT/export.txt"

echo "== summary in $OUT"
