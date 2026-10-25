"""Per-run probe sets from the harness (decision D36): which probes a run hit for the first time,
and which probes of a required set P a run reaches -- what shrink-on-insert needs, because on a
re-run nothing is new any more. Needs the harness jar and the JaCoCo agent."""

from __future__ import annotations

import pytest

from oxpecker import HARNESS_BIN
from oxpecker.harness.client import HarnessClient

AGENT = HARNESS_BIN.parent.parent / "harness-java" / "build" / "libs" / "jacocoagent.jar"
pytestmark = pytest.mark.skipif(not (HARNESS_BIN.exists() and AGENT.exists()), reason="harness/agent not built")

PROGRAM = """
var b = 1n; var n = 0;
for (var i = 0; i < 3; i++) { n += i; }
var s = String(b * 3n) + [1, 2, 3].map(function (x) { return x * 2; }).join(",");
"""


def union(results, field: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for o in results.values():
        for cls, ids in (getattr(o, field) or {}).items():
            out.setdefault(cls, set()).update(ids)
    return out


def size(probes: dict[str, set[int]]) -> int:
    return sum(len(v) for v in probes.values())


@pytest.fixture()   # a fresh JVM per test: "new" is relative to everything this JVM ran
def cov():
    with HarnessClient(quiet=True, coverage=True) as c:
        if not c.info().get("coverage"):
            pytest.skip("coverage agent not attached")
        c.run("1;")                                  # warm up: JVM- and Rhino-startup probes out of the way
        yield c


def test_new_probe_ids_add_up_to_the_new_probe_count(cov):
    run = cov.run(PROGRAM, coverage_new_ids=True)
    for o in run.results.values():
        assert o.newProbeIds is not None
        assert sum(len(v) for v in o.newProbeIds.values()) == o.newProbes
    assert size(union(run.results, "newProbeIds")) > 0


def test_a_rerun_reaches_its_probes_though_none_are_new(cov):
    first = cov.run(PROGRAM + "\nvar again = 1;", coverage_new_ids=True)
    p = {cls: sorted(ids) for cls, ids in union(first.results, "newProbeIds").items()}
    rerun = cov.run(PROGRAM + "\nvar again = 1;", coverage_require=p)
    assert all(o.newProbes == 0 for o in rerun.results.values())
    reached = union(rerun.results, "requiredReached")
    assert all(set(ids) <= set(p[cls]) for cls, ids in reached.items())
    share = size(reached) / max(1, size({k: set(v) for k, v in p.items()}))
    # What is not reached again is once-per-JVM (class init, lazy caches): the share shrink-on-insert
    # must filter out before using P as its property (critique §4.3).
    print(f"re-run reaches {share:.0%} of the probes that were new")
    assert share >= 0.5


def test_a_different_program_reaches_much_less_of_the_set(cov):
    first = cov.run(PROGRAM + "\nvar third = [].concat([1n]);", coverage_new_ids=True)
    p = {cls: sorted(ids) for cls, ids in union(first.results, "newProbeIds").items()}
    full = cov.run(PROGRAM + "\nvar third = [].concat([1n]);", coverage_require=p)
    other = cov.run("var z = 2;", coverage_require=p)
    assert size(union(other.results, "requiredReached")) < size(union(full.results, "requiredReached"))
