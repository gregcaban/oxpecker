"""The property test: for every generated program, interpreted == compiled.

    pytest tests/test_differential.py -x                 # default 200 examples
    OXPECKER_EXAMPLES=2000 pytest tests/test_differential.py
    OXPECKER_TOGGLES=let,generators pytest ...

Hard findings raise AssertionError (so Hypothesis shrinks them) and the shrunk reproducer is
written to tests/findings/<signature>.js. Soft findings are appended to tests/findings/soft.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from hypothesis import HealthCheck, given, note, settings, target
from hypothesis.database import DirectoryBasedExampleDatabase

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.strategies import Program, programs
from oxpecker.harness.compare import HARD, JAVA_TYPES, SOFT, compare_run

FINDINGS = Path(__file__).parent / "findings"
EXAMPLES = int(os.environ.get("OXPECKER_EXAMPLES", "200"))
TOGGLES = frozenset(t for t in os.environ.get("OXPECKER_TOGGLES", "").split(",") if t)
OPTS = GenOptions(toggles=TOGGLES)


def signature(prog: Program, verdict) -> str:
    return hashlib.sha1(verdict.signature().encode()).hexdigest()[:10]


def record(prog: Program, run, verdict, *, fail: bool) -> None:
    """Writes the finding under tests/findings/. Java-type and soft findings are appended to a
    jsonl log (deduplicated by signature) so a long run never loses them; failing kinds are also
    written as a .js reproducer that each Hypothesis shrink step overwrites with a smaller one."""
    FINDINGS.mkdir(exist_ok=True)
    log = FINDINGS / f"{verdict.kind}.jsonl"
    sig = verdict.signature()
    seen = set()
    if log.exists():
        seen = {json.loads(ln)["signature"] for ln in log.read_text().splitlines() if ln.strip()}
    if sig not in seen:
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"signature": sig, "verdict": verdict.to_json(), "source": prog.source_plain,
                                "results": run.to_json()["results"]}) + "\n")
    if fail:
        path = FINDINGS / f"{verdict.kind}-{signature(prog, verdict)}.js"
        body = f"// oxpecker-expect: {verdict.kind}\n// " + verdict.pretty().replace("\n", " ") + "\n"
        body += "// " + json.dumps({m: {"status": o.status, "value": o.value} for m, o in run.results.items()}) + "\n"
        body += "// Replay: bin/harness --replay " + str(path.relative_to(Path.cwd())) + "\n" + prog.source_plain
        path.write_text(body, encoding="utf-8")
        raise AssertionError(f"{verdict.pretty()}\n{prog.source_plain}")


@settings(max_examples=EXAMPLES, deadline=None, database=DirectoryBasedExampleDatabase(".hypothesis/oxpecker"),
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
          print_blob=True)
@given(programs(OPTS))
def test_interpreted_equals_compiled(harness, prog: Program):
    run = harness.run(prog.source, seed=1)
    verdict = compare_run(run)
    note(prog.source)
    note(verdict.pretty())
    # Reward programs that get further before throwing or timing out.
    depth = min(len(o.trace) for o in run.results.values())
    target(float(depth), label="trace depth")

    if verdict.kind in (SOFT, JAVA_TYPES):
        record(prog, run, verdict, fail=False)
    if verdict.kind == HARD:
        record(prog, run, verdict, fail=True)


@settings(max_examples=EXAMPLES, deadline=None, database=DirectoryBasedExampleDatabase(".hypothesis/oxpecker-java"),
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
          print_blob=True)
@given(programs(OPTS))
def test_java_representation_matches(harness, prog: Program):
    """Stricter oracle: Java representation types (Integer vs Double, ConsString vs String) must
    match too. Fails and shrinks on the first java-types divergence, so run it separately:
        pytest tests/test_differential.py -k java_representation
    """
    run = harness.run(prog.source, seed=1)
    verdict = compare_run(run, strict_java_types=True)
    note(prog.source)
    note(verdict.pretty())
    if verdict.kind == HARD:
        record(prog, run, verdict, fail=True)
