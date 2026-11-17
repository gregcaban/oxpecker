"""Rhino version as the oracle (oxpecker/crossver.py): the verdict over two targets' runs, the
capability intersection the generators draw from, and the reproducer a cross-version finding
writes. The harness tests need both targets' jars (oxpecker build-harness master)."""

from __future__ import annotations

import json

import pytest

from oxpecker import crossver, environment, findings, targets
from oxpecker.crossver import FIXED, CrossClient, CrossRun, cross_verdict
from oxpecker.harness.client import Outcome, RunResult
from oxpecker.harness.compare import EQUAL, HARD, SOFT


def run(i: Outcome, c: Outcome) -> RunResult:
    return RunResult("x", {"interpreted": i, "compiled": c})


def ok(mode: str, value: str, **kw) -> Outcome:
    return Outcome(mode, "ok", value=value, **kw)


def crash(mode: str) -> Outcome:
    return Outcome(mode, "crash", engineException={"clazz": "java.lang.VerifyError", "message": "Bad type"})


def cross(base: RunResult, cand: RunResult) -> CrossRun:
    return CrossRun("1.9.2", "master", base, cand)


def test_a_mode_that_changed_is_a_regression_even_when_the_candidates_modes_agree():
    v = cross_verdict(cross(run(ok("interpreted", "1"), ok("compiled", "1")),
                            run(ok("interpreted", "2"), ok("compiled", "2"))))
    assert v.kind == HARD
    assert v.reasons[0] == "1.9.2->master interpreted: value 1 vs 2"
    assert "1.9.2->master compiled: value 1 vs 2" in v.reasons
    assert v.coarse_signature() == "hard:1.9.2->master interpreted: value"


def test_a_fixed_mode_difference_is_counted_not_reported():
    v = cross_verdict(cross(run(ok("interpreted", "1"), crash("compiled")),
                            run(ok("interpreted", "1"), ok("compiled", "1"))))
    assert v.kind == FIXED and not v.is_finding


def test_a_fix_to_the_wrong_value_is_still_a_regression():
    v = cross_verdict(cross(run(ok("interpreted", "1"), crash("compiled")),
                            run(ok("interpreted", "1"), ok("compiled", "3"))))
    assert v.kind == HARD and v.reasons == ["1.9.2->master compiled: 1.9.2 crashed: java.lang.VerifyError: Bad type"]


def test_a_bug_both_versions_share_is_the_candidates_own_mode_verdict():
    both = run(ok("interpreted", "1"), ok("compiled", "2"))
    v = cross_verdict(cross(both, both))
    assert v.kind == HARD and v.reasons == ["value 1 vs 2"]


def test_a_crash_new_on_the_candidate_keeps_the_target_in_its_coarse_signature():
    v = cross_verdict(cross(run(ok("interpreted", "1"), ok("compiled", "1")),
                            run(ok("interpreted", "1"), crash("compiled"))))
    assert v.coarse_signature() == "hard:1.9.2->master compiled: master crashed: java.lang.VerifyError"


def test_a_reworded_error_message_is_soft():
    def threw(mode: str, msg: str) -> Outcome:
        return Outcome(mode, "throw", thrown={"name": "TypeError", "value": "TypeError", "message": msg})
    v = cross_verdict(cross(run(threw("interpreted", "a"), threw("compiled", "a")),
                            run(threw("interpreted", "b"), threw("compiled", "b"))))
    assert v.kind == SOFT and v.reasons[0].startswith("1.9.2->master interpreted: ")


def test_the_generators_draw_only_what_both_targets_have():
    a = environment.load(targets.capabilities("master"))
    b = environment.load(targets.capabilities("1.9.2"))
    both = environment.intersect(a, b)
    assert "Float16Array" in a["globals"] and "Float16Array" not in both["globals"]
    assert "ArrayIterator" in a["globals"] and "ArrayIterator" not in both["globals"]
    assert "__GeneratorFunction" in b["globals"] and "__GeneratorFunction" not in both["globals"]
    assert set(both["protos"]["Array"]) == set(a["protos"]["Array"]) & set(b["protos"]["Array"])
    assert not both["syntax"]["spread-call"]["supported"]          # master parses it, then crashes


def test_a_cross_version_reproducer_expects_each_targets_own_verdict(tmp_path):
    class Prog:
        source, template = "__probe(typeof ArrayIterator);\n", "t"
    r = cross(run(ok("interpreted", "undefined"), ok("compiled", "undefined")),
              run(ok("interpreted", "object"), ok("compiled", "object")))
    v = cross_verdict(r)
    path = findings.record(Prog(), r, v, out_dir=tmp_path, reproducer=True)
    text = path.read_text()
    assert targets.expected_kind(text, "1.9.2") == EQUAL and targets.expected_kind(text, "master") == EQUAL
    assert "oxpecker xver --targets 1.9.2,master" in text
    rec = json.loads((tmp_path / "hard.jsonl").read_text())
    assert rec["baseline"] == "1.9.2" and "1.9.2/compiled" in rec["results"]


@pytest.fixture(scope="module")
def cross_client():
    for name in ("1.9.2", "master"):
        if not targets.jar(name).exists():
            pytest.skip(f"no harness for Rhino target {name}: oxpecker build-harness {name}")
    from oxpecker.harness.client import HarnessClient
    with HarnessClient(quiet=True, target="master") as cand, CrossClient(cand, "1.9.2") as c:
        yield c


def test_on_the_harness_masters_global_iterator_prototypes_are_a_regression(cross_client):
    v = cross_verdict(cross_client.run("__probe(typeof ArrayIterator);"))
    assert v.kind == HARD and v.coarse_signature() == "hard:1.9.2->master interpreted: trace[] arg"


def test_on_the_harness_a_compiled_bug_master_fixed_is_fixed(cross_client):
    v = cross_verdict(cross_client.run("__probe(typeof (() => 1).prototype);"))
    assert v.kind == FIXED


def test_the_baseline_is_named_by_its_environment_variable(monkeypatch):
    monkeypatch.delenv(crossver.BASELINE_ENV, raising=False)
    assert crossver.baseline() is None
    monkeypatch.setenv(crossver.BASELINE_ENV, "1.9.2")
    assert crossver.baseline() == "1.9.2"


def test_known_version_changes_are_not_findings():
    """The first campaign's deliberate or implementation-defined differences (crossver.VERSION_CANON)."""
    pairs = [
        ('@0function{name="m";length=0;src="() {\\n    }"}', '@0function{name="m";length=0;src="m() {\\n}"}'),
        ("function parseInt() {\n\t[native code]\n}\nx", "function parseInt() {\n\t[native code]\n}x"),
        (r'"function parseInt() {\n\t[native code]\n}\nx"', r'"function parseInt() {\n\t[native code]\n}x"'),
        ("[3,4.9e-324]", "[3,5e-324]"),
        ('@1Math<Object.prototype>{"toSource"{---}:"Math";"abs"{-cw}:@[native abs/1]}',
         '@1Math<Object.prototype>{"abs"{-cw}:@[native abs/1];"toSource"{---}:"Math"}'),
    ]
    for old, new in pairs:
        v = cross_verdict(cross(run(ok("interpreted", "u", trace=[old]), ok("compiled", "u", trace=[old])),
                                run(ok("interpreted", "u", trace=[new]), ok("compiled", "u", trace=[new]))))
        assert v.kind == EQUAL, (old, v.pretty())
    # a user object's property order is still compared
    v = cross_verdict(cross(run(ok("interpreted", "u", trace=['@1Object{"a":1;"b":2}']), ok("compiled", "u", trace=['@1Object{"a":1;"b":2}'])),
                            run(ok("interpreted", "u", trace=['@1Object{"b":2;"a":1}']), ok("compiled", "u", trace=['@1Object{"b":2;"a":1}']))))
    assert v.kind == HARD


VERSION_CHANGES = sorted((__import__("pathlib").Path(__file__).parent / "version-changes").glob("*.js"))


@pytest.mark.parametrize("path", VERSION_CHANGES, ids=lambda p: p.stem)
def test_known_version_changes_still_differ(cross_client, path):
    """Master's fixes the cross-version loop found: still a difference (else drop the file),
    and the loop counts their signatures as known (related.known_index)."""
    v = cross_verdict(cross_client.run(path.read_text(encoding="utf-8"), source_name=path.name))
    assert v.kind == HARD and v.reasons[0].startswith("1.9.2->master ")
