#!/bin/sh
# Long differential-testing campaigns in the background, one entry point so nothing needs ad hoc shell.
# Usage:
#   scripts/campaign.sh start NAME HOURS [--fresh] [fuzz args]  build the harness jar, launch into runs/NAME;
#                                                               seeded with the previous campaign's corpus
#                                                               minimised (oxpecker cmin), or empty with --fresh
#                                                               OXPECKER_RHINO=TARGET runs on that Rhino target
#   scripts/campaign.sh status NAME                             pid liveness, last progress lines, findings
#   scripts/campaign.sh stop NAME                               stop the run
#   scripts/campaign.sh extract NAME                            findings/hard.jsonl entries -> runs/NAME/unreduced/*.js
#   scripts/campaign.sh reduce NAME FILE... [-- BUDGET]          replay each file (oxpecker diff), shrink the ones that reproduce
set -u
cd "$(dirname "$0")/.."
CMD="${1:-}"; NAME="${2:-}"
[ -n "$CMD" ] && [ -n "$NAME" ] || { sed -n '3,11p' "$0"; exit 2; }
OUT="runs/$NAME"
PIDFILE="$OUT/fuzz.pid"
# The Rhino target (rhino-targets.toml) this process runs against, as oxpecker.targets.current().
DEFAULT_TARGET="$(sed -n 's/^default = "\(.*\)"/\1/p' rhino-targets.toml)"
TARGET="${OXPECKER_RHINO:-$DEFAULT_TARGET}"

alive() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "$CMD" in
start)
    HOURS="${3:?hours}"; shift 3
    FRESH=0
    if [ "${1:-}" = "--fresh" ]; then FRESH=1; shift; fi
    alive && { echo "$NAME already running (pid $(cat "$PIDFILE"))"; exit 1; }
    [ -e "$OUT/corpus/corpus.jsonl" ] && { echo "$OUT already has a corpus; pick a new NAME"; exit 1; }
    PREV=""
    if [ "$FRESH" = 0 ] && [ -f runs/.last-campaign ]; then
        PREV="$(cat runs/.last-campaign)"
        [ -f "runs/$PREV/corpus/corpus.jsonl" ] || { echo "previous campaign runs/$PREV has no corpus; use --fresh"; exit 1; }
        if [ -f "runs/$PREV/fuzz.pid" ] && kill -0 "$(cat "runs/$PREV/fuzz.pid")" 2>/dev/null; then
            echo "previous campaign $PREV is still running; stop it first or use --fresh"; exit 1
        fi
    fi
    . .venv/bin/activate
    if [ "$TARGET" = "$DEFAULT_TARGET" ] && [ -z "${OXPECKER_RHINO:-}" ]; then
        (cd harness-java && ./gradlew -q fatJar) || exit 1
    else
        oxpecker build-harness "$TARGET" || exit 1
    fi
    mkdir -p "$OUT"
    echo "$TARGET" > "$OUT/target.txt"
    SECONDS_TOTAL=$(awk "BEGIN { printf \"%d\", $HOURS * 3600 }")
    FUZZ="oxpecker fuzz -o $OUT/corpus --seconds $SECONDS_TOTAL --findings-dir $OUT/findings $*"
    if [ -n "$PREV" ]; then
        # The minimised corpus is kept next to the campaign it came from (corpus.min) and reused
        # while it is newer than that campaign's corpus; the new campaign gets a copy to grow.
        # Coverage is per Rhino build, so a corpus is minimised again for another target, kept apart.
        MIN="runs/$PREV/corpus.min"
        [ "$TARGET" = "$DEFAULT_TARGET" ] || MIN="runs/$PREV/corpus.min.$TARGET"
        if [ -f "$MIN/corpus.jsonl" ] && [ "$MIN/corpus.jsonl" -nt "runs/$PREV/corpus/corpus.jsonl" ]; then
            SEED="cp -R $MIN $OUT/corpus"
            echo "seeded from $MIN (minimised earlier)" > "$OUT/seed.txt"
        else
            rm -rf "$MIN"
            SEED="oxpecker cmin runs/$PREV/corpus -o $MIN && cp -R $MIN $OUT/corpus"
            echo "seeded from runs/$PREV/corpus, minimised into $MIN" > "$OUT/seed.txt"
        fi
    else
        SEED="true"
        echo "fresh (empty corpus)" > "$OUT/seed.txt"
    fi
    printf '%s\n%s\n' "$SEED" "$FUZZ" > "$OUT/command.txt"
    # one background job: minimise the seed corpus, then (exec, same pid) the campaign
    nohup sh -c "$SEED && exec $FUZZ" >> "$OUT/fuzz.log" 2>&1 &
    echo $! > "$PIDFILE"
    echo "$NAME" > runs/.last-campaign
    date '+%Y-%m-%d %H:%M:%S' > "$OUT/started.txt"
    echo "started $NAME pid $(cat "$PIDFILE"), $SECONDS_TOTAL s, $(cat "$OUT/seed.txt"), log $OUT/fuzz.log"
    ;;
status)
    [ -d "$OUT" ] || { echo "no run $OUT"; exit 1; }
    if alive; then echo "running (pid $(cat "$PIDFILE")) on Rhino $(cat "$OUT/target.txt" 2>/dev/null || echo "$DEFAULT_TARGET"), started $(cat "$OUT/started.txt" 2>/dev/null), $(cat "$OUT/seed.txt" 2>/dev/null)"
    else echo "not running"; fi
    tail -n "${3:-5}" "$OUT/fuzz.log"
    echo "findings recorded:"
    ls "$OUT/findings" 2>/dev/null || echo "  (none)"
    ;;
stop)
    # the job is `sh -c "cmin && exec fuzz"`: while cmin runs, it is the shell's child
    alive && { pkill -P "$(cat "$PIDFILE")" 2>/dev/null; kill "$(cat "$PIDFILE")"; } && echo "stopped $NAME" \
        || echo "$NAME not running"
    ;;
extract)
    # One .js per recorded signature, named by a hash of the signature, the signature in the header.
    .venv/bin/python - "$OUT" <<'EOF'
import hashlib, json, sys
from pathlib import Path
out = Path(sys.argv[1])
dest = out / "unreduced"
dest.mkdir(exist_ok=True)
for line in (out / "findings" / "hard.jsonl").read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    d = json.loads(line)
    sig = d["signature"]
    path = dest / f"hard-{hashlib.sha1(sig.encode()).hexdigest()[:10]}.js"
    path.write_text(f"// oxpecker-expect: hard\n// SIGNATURE: {sig[:200].replace(chr(10), ' ')}\n" + d["source"],
                    encoding="utf-8")
    print(f"{path}  {sig[:100]}")
EOF
    ;;
reduce)
    shift 2
    BUDGET=300
    . .venv/bin/activate
    for f in "$@"; do
        case "$f" in --) continue ;; esac
        if [ "$f" -eq "$f" ] 2>/dev/null; then BUDGET="$f"; continue; fi
    done
    for f in "$@"; do
        case "$f" in --) continue ;; esac
        [ "$f" -eq "$f" ] 2>/dev/null && continue
        echo "=== $f"
        echo "--- replay"
        oxpecker diff "$f" 2>&1 | tail -n 15
        echo "--- shrink (budget $BUDGET)"
        oxpecker shrink --budget "$BUDGET" "$f" 2>&1 | tail -n 15
    done
    ;;
*)
    sed -n '3,10p' "$0"; exit 2
    ;;
esac
