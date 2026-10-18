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
from oxpecker.harness.compare import HARD, SOFT, compare_run

FINDINGS = Path(__file__).parent / "findings"
EXAMPLES = int(os.environ.get("OXPECKER_EXAMPLES", "200"))
TOGGLES = frozenset(t for t in os.environ.get("OXPECKER_TOGGLES", "").split(",") if t)
OPTS = GenOptions(toggles=TOGGLES)


def signature(prog: Program, verdict) -> str:
    key = "|".join(verdict.reasons[:1]) + "|" + prog.template
    return hashlib.sha1(key.encode()).hexdigest()[:10]


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

    if verdict.kind == SOFT:
        FINDINGS.mkdir(exist_ok=True)
        with (FINDINGS / "soft.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"verdict": verdict.to_json(), "source": prog.source_plain}) + "\n")
    if verdict.kind == HARD:
        FINDINGS.mkdir(exist_ok=True)
        path = FINDINGS / f"{signature(prog, verdict)}.js"
        body = "// " + verdict.pretty().replace("\n", " ") + "\n"
        body += "// " + json.dumps({m: {"status": o.status, "value": o.value} for m, o in run.results.items()}) + "\n"
        body += "// Replay: bin/harness --replay " + str(path.relative_to(Path.cwd())) + "\n" + prog.source_plain
        path.write_text(body, encoding="utf-8")   # overwritten by each shrink step: ends up minimal
        raise AssertionError(f"{verdict.pretty()}\n{prog.source_plain}")
