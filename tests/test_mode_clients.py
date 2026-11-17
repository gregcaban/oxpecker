"""One JVM per mode (harness.client.ModeClients): the same outcomes as one JVM running the modes
in turn, coverage kept per JVM and put together at the end."""

from __future__ import annotations

from pathlib import Path

import pytest

from oxpecker import HARNESS_BIN
from oxpecker.harness.client import HarnessClient, ModeClients
from oxpecker.harness.compare import compare_run

REGRESSIONS = Path(__file__).parent / "regressions"
PROGRAMS = ["1 + 1;", "var a = [1, 2, 3]; __probe('a', a, a.length); a.map(function (x) { return x * 2; });",
            "throw new TypeError('x');", "function f(n) { return n < 2 ? n : f(n - 1) + f(n - 2); } f(15);",
            "for (;;) {}", "var o = {get x() { return J; }}; __probe('o', o.x);"]


def plain(outcome):
    d = outcome.to_json()
    for k in ("ms", "newProbes", "newProbeIds", "requiredReached", "hitProbes"):
        d.pop(k)
    return d


@pytest.fixture(scope="module")
def both():
    if not HARNESS_BIN.exists():
        pytest.skip("harness not built: cd harness-java && ./gradlew fatJar")
    with HarnessClient(quiet=True) as one, ModeClients(quiet=True) as two:
        yield one, two


def test_each_mode_in_its_own_jvm_gives_the_outcomes_of_one_jvm(both):
    one, two = both
    sources = PROGRAMS + [p.read_text(encoding="utf-8") for p in sorted(REGRESSIONS.glob("*.js"))[:12]]
    for src in sources:
        a, b = one.run(src, wall_ms=2000), two.run(src, wall_ms=2000)
        assert list(b.results) == ["interpreted", "compiled"] == list(a.results)
        assert {m: plain(o) for m, o in a.results.items()} == {m: plain(o) for m, o in b.results.items()}, src[:80]
        assert compare_run(a).signature() == compare_run(b).signature()
    only = two.run("2 * 3;", modes=("compiled",))
    assert list(only.results) == ["compiled"] and only.results["compiled"].value == "6"


def test_coverage_is_kept_per_jvm_and_ends_as_one_store(tmp_path: Path):
    if not HARNESS_BIN.exists():
        pytest.skip("harness not built: cd harness-java && ./gradlew fatJar")
    exec_file = tmp_path / "coverage.exec"
    with ModeClients(quiet=True, coverage=True) as two:
        if not two.info().get("coverage"):
            pytest.skip("coverage agent not attached")
        two.coverage_checkpoint = str(exec_file)
        first = two.run(PROGRAMS[1], coverage_new_ids=True)
        again = two.run(PROGRAMS[1], coverage_new_ids=True)
        assert all(o.newProbes > 0 for o in first.results.values())
        assert sum(o.newProbes for o in again.results.values()) < sum(o.newProbes for o in first.results.values()) / 20
        two.coverage_save(str(exec_file))
        assert exec_file.exists() and (tmp_path / "coverage.exec.compiled").exists()
        each = {m: c.coverage_report()["coveredProbes"] for m, c in two.clients.items()}
        union = two.coverage_report()["coveredProbes"]            # merges into the first JVM and saves that
        assert union > max(each.values())                          # each mode reached code the other did not
    with HarnessClient(quiet=True, coverage=True) as one:          # a single JVM reads the union back
        assert one.coverage_load(str(exec_file)) == union
        assert sum(o.newProbes for o in one.run(PROGRAMS[1]).results.values()) < 50
    with ModeClients(quiet=True, coverage=True) as two:            # and two JVMs resume from their own stores
        two.coverage_load(str(exec_file))
        assert sum(o.newProbes for o in two.run(PROGRAMS[1]).results.values()) < 50
