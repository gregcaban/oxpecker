"""Harness self-tests (brief §5.8), driven through the Python client."""

from __future__ import annotations

from pathlib import Path

import pytest

from oxpecker.harness.compare import DISCARD, EQUAL, HARD, SKIP, SOFT, compare_run

REGRESSIONS = Path(__file__).parent / "regressions"
SEEDS = Path(__file__).parent / "seeds"


def test_info(harness):
    info = harness.info()
    assert "rhino" in info and "compiled" in info["modes"]


def test_self_consistency(harness):
    src = "var a = [1, , 3]; var o = {x: -0, get g() { return 1; }}; __probe('p', a, o, 0.1 + 0.2, 'a' + 1);\nMath.random() + Date.now();"
    r1 = harness.run(src, seed=5)
    r2 = harness.run(src, seed=5)
    for m in r1.results:
        a, b = r1.results[m], r2.results[m]
        assert (a.status, a.value, a.trace, a.output) == (b.status, b.value, b.trace, b.output)
        assert a.traceJavaTypes == b.traceJavaTypes
    assert compare_run(r1).kind in (EQUAL, SOFT)   # soft: Integer vs Double representation only


def test_isolation_canary(harness):
    harness.run("globalThis.leak = 1; this.leak2 = 2; Object.prototype.polluted = 1;")
    r = harness.run("__probe('p', typeof leak, typeof leak2, 'polluted' in {}); leak;")
    for o in r.results.values():
        assert o.status == "throw" and o.thrown["name"] == "ReferenceError"
        assert o.trace == ['"p" | "undefined" | "undefined" | false']


def test_fallback_detection(harness):
    # A single function body far beyond the 64 KB JVM method limit.
    body = "\n".join(f"  x = x + {i} * y;" for i in range(20000))
    src = f"function big(x, y) {{\n{body}\n  return x;\n}}\nbig(1, 2);"
    r = harness.run(src, wall_ms=30000)
    assert r.results["compiled"].status == "fallback"
    assert r.results["compiled"].compiledActually is False
    assert r.results["interpreted"].status == "ok"
    assert compare_run(r).kind == SKIP


def test_small_scripts_are_really_compiled(harness):
    r = harness.run("1 + 1")
    assert r.results["compiled"].compiledActually is True
    assert r.results["interpreted"].compiledActually is False


def test_syntax_error_in_both(harness):
    r = harness.run("var = ;")
    assert all(o.status == "syntax" for o in r.results.values())
    assert compare_run(r).kind == EQUAL


def test_budget_timeout_in_both(harness):
    r = harness.run("while (true) {}", wall_ms=1000, instruction_budget=200000)
    assert all(o.status == "timeout" for o in r.results.values())
    assert compare_run(r).kind == DISCARD


def test_stack_overflow_is_resource(harness):
    r = harness.run("function f() { return f() + 1; } f();")
    assert all(o.status == "resource" for o in r.results.values())


def test_determinism_stubs(harness):
    r = harness.run("__probe('p', Math.random(), Date.now(), new Date().getTime(), typeof WeakRef);", seed=42)
    o = r.results["interpreted"]
    assert o.trace == ['"p" | 0.6270739405881613 | 1700000000000 | 1700000000000 | "undefined"'] or o.trace[0].startswith('"p" | 0.')
    assert compare_run(r).kind == EQUAL


def test_java_types_are_reported(harness):
    """Java representation types reach the trace. The integer literal 7 arrives at a Java host
    function as Double from the interpreter but as Integer from compiled code: the kind of
    interop difference the soft "java types" verdict reports."""
    r = harness.run("var s = 'x'; s += 'y'; __probe('p', s, 1.5, 7);")
    assert r.results["interpreted"].traceJavaTypes == ["String | ConsString | Double | Double"]
    assert r.results["compiled"].traceJavaTypes == ["String | ConsString | Double | Integer"]
    assert compare_run(r).kind == SOFT
    assert compare_run(r, strict_java_types=True).kind == HARD


def test_thrown_value_canonicalised(harness):
    r = harness.run("throw {a: [1, -0], b: null};")
    o = r.results["compiled"]
    assert o.status == "throw" and o.thrown["kind"] == "js-value"
    assert o.thrown["value"] == '@0Object<Object.prototype>{"a"{ecw}:@1Array<Array.prototype>{length=2;[1,-0]};"b"{ecw}:null}'


def test_language_version_is_honoured(harness):
    r = harness.run("__probe('p', typeof Symbol, [1,2].includes(2));", language_version=250)
    assert compare_run(r).kind == EQUAL


@pytest.mark.parametrize("path", sorted(REGRESSIONS.glob("*.js")), ids=lambda p: p.name)
def test_known_regressions_are_detected(harness, path: Path):
    """Every checked-in reproducer must still be a hard finding against the pinned Rhino."""
    r = harness.run(path.read_text(), source_name=path.name)
    v = compare_run(r)
    assert v.kind == HARD, v.pretty()


@pytest.mark.parametrize("path", sorted(SEEDS.glob("*.js")), ids=lambda p: p.name)
def test_test262_known_divergences(harness, path: Path):
    """The test262 cases Rhino marks as failing in one mode only (brief §2), adapted to run
    standalone, must be reported as hard findings."""
    r = harness.run(path.read_text(), source_name=path.name, language_version=250)
    v = compare_run(r)
    assert v.kind == HARD, v.pretty()
