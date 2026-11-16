"""engine262 as the judge of findings (oxpecker/judge.py, judge/host.mjs): which side of a
difference agrees with the spec. The engine262 tests need `npm install --prefix judge`; the
harness ones a built harness (both targets for the cross-version one)."""

from __future__ import annotations

from pathlib import Path

import pytest

from oxpecker import judge, targets
from oxpecker.harness.client import Outcome

HERE = Path(__file__).parent
needs_engine262 = pytest.mark.skipif(not judge.available(), reason="engine262 not installed: npm install --prefix judge")


def ok(value: str, *trace: str) -> Outcome:
    return Outcome("x", "ok", value=value, trace=list(trace))


def test_only_where_the_two_sides_differ_counts():
    # both Rhino modes render an extra own property engine262 lacks; only arg1 differs between them
    a = ok("u", '"p" | "undefined" | ["length","name","arity"]')
    b = ok("u", '"p" | "object" | ["length","name","arity"]')
    spec = ok("u", '"p" | "undefined" | ["length","name"]')
    assert judge.sides(a, b, spec) == (True, False, ["trace[0] arg1"])


def test_without_an_exact_match_the_side_whose_change_engine262_has_wins():
    a = ok("u", '@0Object{"a":1;"arity":0;"b":2}')
    b = ok("u", '@0Object{"a":1;"arity":0;"b":3}')
    spec = ok("u", '@0Object{"a":1;"b":2}')
    a_ok, b_ok, _ = judge.sides(a, b, spec)
    assert (a_ok, b_ok) == (True, False)


def test_rhinos_arity_and_function_source_are_not_compared():
    a = ok("u", '@0function{name="f";length=0;src="function f() {\\n}";"length"{-c-}:0;"arity"{-c-}:0}')
    spec = ok("u", '@0function{name="f";length=0;src="function f(){}";"length"{-c-}:0}')
    assert judge.observations(a) == judge.observations(spec)


def test_what_engine262_cannot_run_is_not_judged():
    assert judge.unsupported_reason("var x = J.intValue(1);") == "Java (J)"
    assert judge.unsupported_reason("__pause(1);") == "continuations"
    assert judge.unsupported_reason("// java.lang.ClassCastException\nvar j = 1;") is None


@needs_engine262
def test_engine262_renders_values_with_the_harness_canonicaliser():
    o = judge.run_engine262('__probe("step", [1, 2], {a: 1}); print("x", null); Promise.resolve(3).then(function (v) { __probe("then", v); }); 7')
    assert o.status == "ok" and o.value == "7"
    assert o.trace == ['"step" | @0Array<Array.prototype>{length=2;[1,2]} | @0Object<Object.prototype>{"a"{ecw}:1}', '"then" | 3']
    assert o.output == ["x null"]
    assert judge.run_engine262("var = 1").status == "syntax"
    t = judge.run_engine262("null.x")
    assert t.status == "throw" and t.thrown["name"] == "TypeError"


@pytest.fixture(scope="module")
def harness_client():
    from oxpecker.harness.client import HarnessClient
    with HarnessClient(quiet=True) as c:
        yield c


@needs_engine262
@pytest.mark.parametrize("name, label", [
    ("compiled-increment-int32-overflow", "compiled-wrong"),
    ("t262-apply-rest-array-like-wrong", "interpreted-wrong"),
    ("t262-string-iterator-prototype-confusion-npe", "all-wrong"),          # both modes crash, the spec throws
    ("callsite-tostring-npe-both-modes", "rhino-extension"),               # CallSite is Rhino's own
])
def test_known_regressions_are_judged_as_triaged_by_hand(harness_client, name, label):
    src = (HERE / "regressions" / f"{name}.js").read_text(encoding="utf-8")
    assert judge.judge(src, harness_client.run(src)).label == label


@needs_engine262
def test_a_version_change_engine262_agrees_with_is_a_fix():
    for name in ("1.9.2", "master"):
        if not targets.jar(name).exists():
            pytest.skip(f"no harness for Rhino target {name}: oxpecker build-harness {name}")
    from oxpecker.crossver import CrossClient
    from oxpecker.harness.client import HarnessClient
    src = (HERE / "version-changes" / "array-length-undefined-rangeerror.js").read_text(encoding="utf-8")
    with HarnessClient(quiet=True, target="master") as cand, CrossClient(cand, "1.9.2") as c:
        j = judge.judge(src, c.run(src))
    assert j.label == "fix" and j.agrees == {"1.9.2/interpreted": False, "interpreted": True,
                                             "1.9.2/compiled": False, "compiled": True}
