"""The property test: for every generated program, interpreted == compiled.

    pytest tests/test_differential.py -x                 # default 200 examples
    OXPECKER_EXAMPLES=2000 pytest tests/test_differential.py
    OXPECKER_TOGGLES=let,generators pytest ...

Hard findings raise AssertionError (so Hypothesis shrinks them) and the shrunk reproducer is
written to tests/findings/<signature>.js. Soft findings are appended to tests/findings/soft.jsonl.
"""

from __future__ import annotations

import os
from pathlib import Path

from hypothesis import HealthCheck, given, note, settings, target
from hypothesis.database import DirectoryBasedExampleDatabase

from oxpecker import findings
from oxpecker.gen.builder import GenOptions
from oxpecker.gen.strategies import Program, programs
from oxpecker.harness.compare import HARD, JAVA_TYPES, SOFT, compare_run

FINDINGS = Path(__file__).parent / "findings"
# Signatures of findings already captured in tests/regressions (computed by the `known` fixture in
# conftest.py, plus tests/known_signatures.txt): logged, not failed, so a long run keeps looking
# for new divergences instead of stopping at the first known one.
KNOWN: set[str] = set()
EXAMPLES = int(os.environ.get("OXPECKER_EXAMPLES", "200"))
TOGGLES = frozenset(t for t in os.environ.get("OXPECKER_TOGGLES", "").split(",") if t)
OPTS = GenOptions(toggles=TOGGLES)


_FINDING_CLASSES: dict[str, type] = {}


def finding_error(sig: str) -> type:
    """One exception class per signature: Hypothesis treats different exception types as distinct
    bugs, so after shrinking one finding it keeps searching for others instead of stopping."""
    cls = _FINDING_CLASSES.get(sig)
    if cls is None:
        name = "Finding_" + "".join(c if c.isalnum() else "_" for c in sig)[:60]
        cls = type(name, (AssertionError,), {"signature": sig})
        _FINDING_CLASSES[sig] = cls
    return cls


def record(prog: Program, run, verdict, *, fail: bool) -> None:
    """Logs the finding (oxpecker.findings); failing kinds also get a .js reproducer that each
    Hypothesis shrink step overwrites with a smaller one, and raise."""
    findings.record(prog, run, verdict, out_dir=FINDINGS, reproducer=fail)
    if fail:
        raise finding_error(verdict.signature())(f"{verdict.pretty()}\n{prog.source}")


@settings(max_examples=EXAMPLES, deadline=None, database=DirectoryBasedExampleDatabase(".hypothesis/oxpecker"),
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
          print_blob=True)
@given(programs(OPTS))
def test_interpreted_equals_compiled(harness, known, prog: Program):
    KNOWN.update(known)
    run = harness.run(prog.source, seed=1, coverage_bucket=prog.template)
    verdict = compare_run(run)
    note(prog.source)
    note(verdict.pretty())
    # Reward programs that get further before throwing or timing out, and (with OXPECKER_COVERAGE=1)
    # programs that reach Rhino code nothing before them reached.
    depth = min(len(o.trace) for o in run.results.values())
    target(float(depth), label="trace depth")
    fresh = sum(o.newProbes or 0 for o in run.results.values())
    if fresh or harness.coverage:
        target(float(fresh), label="new Rhino probes")

    if verdict.kind in (SOFT, JAVA_TYPES):
        record(prog, run, verdict, fail=False)
    if verdict.kind == HARD:
        record(prog, run, verdict, fail=verdict.signature() not in KNOWN)


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
