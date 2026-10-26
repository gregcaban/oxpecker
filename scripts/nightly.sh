#!/bin/sh
# Local "nightly": regression check and corpus replays. (The property run and the inspector export
# were removed with the old generation layer on 2026-10-25; they return with the FuzzIL-based one.)
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
pytest tests/test_harness.py tests/test_harness_coverage.py -q 2>&1 | tail -1 | tee "$OUT/tests.txt"

echo "== corpora"
oxpecker corpus rhino-jstests -o "$OUT/jstests" 2>&1 | tail -12 | tee "$OUT/jstests.txt"
oxpecker corpus test262 --filter language -o "$OUT/t262-language" 2>&1 | tail -20 | tee "$OUT/t262.txt"

echo "== summary in $OUT"
