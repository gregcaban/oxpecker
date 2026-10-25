"""The IR core (oxpecker/ir): analysis, verification, lifting, and generated programs. No JVM.

    pytest tests/test_ir.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, seed, settings
from hypothesis import strategies as st

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.types import T
from oxpecker.ir.analysis import InvalidProgram, analyse, verify
from oxpecker.ir.lift import lift
from oxpecker.ir.ops import (BeginCatch, BeginElse, BeginFor, BeginFunction, BeginIf, BeginTry, Binary, CallFunction,
                             CallMethod, CompoundAssign, Construct, DeclareVar, EndFor, EndFunction, EndIf, EndTry,
                             Eval, GetProperty, Instr, LoadBuiltin, LoadLiteral, Probe, Reassign, Return, Unary,
                             Update)
from oxpecker.ir.registry import GENERATORS, load_all
from oxpecker.ir.strategies import ir_programs, programs

RELAXED = settings(deadline=None, database=None, suppress_health_check=list(HealthCheck))
NODE = shutil.which("node")


def I(op, inputs=(), outputs=(), inner=(), **kw):
    return Instr(op, tuple(inputs), tuple(outputs), tuple(inner), **kw)


def lit(v, text, t=T.INT32, **kw):
    return I(LoadLiteral(t, text), (), (v,), **kw)


def body(prog, **kw) -> str:
    return lift(prog, probes=False, comments=False, **kw).source


# ---------------------------------------------------------------------------- lifting

def test_precedence_comes_from_the_table():
    prog = (lit(0, "-1"), lit(1, "2"), I(Binary("**"), (0, 1), (2,)), I(Binary("*"), (2, 1), (3,)),
            I(DeclareVar(), (3,), (4,)))
    assert body(prog) == "var n1 = (-1) ** 2 * 2;\n"


def test_nullish_never_mixes_with_logical_without_parentheses():
    prog = (lit(0, "1"), lit(1, "2"), I(Binary("||"), (0, 1), (2,)), I(Binary("??"), (2, 1), (3,)),
            I(DeclareVar(), (3,), (4,)))
    assert "(1 || 2) ?? 2" in body(prog)


def test_number_receiver_and_new_callee_are_parenthesised():
    prog = (lit(0, "1", keep=False), I(CallMethod("toString"), (0,), (1,)),
            I(LoadBuiltin("f", T.FUNCTION), (), (2,)), I(CallFunction(), (2,), (3,)), I(Construct(), (3,), (4,)),
            I(DeclareVar(), (1,), (5,)))
    out = body(prog)
    assert "new (f())()" in out
    assert "(1).toString()" in out


def test_unused_literal_stays_a_variable_and_keep_stops_inlining():
    prog = (lit(0, "5"), lit(1, "6", keep=True), I(Binary("+"), (1, 1), (2,)))
    out = lift(prog, comments=False).source
    assert "var i1 = 5;" in out and "var i2 = 6;" in out and "i2 + i2" in out


def test_fold_only_into_the_next_statement_and_never_reorder_effects():
    # v2 = o.p is used by the next statement but not in its first-evaluated slot, and o's type is
    # unknown (a getter may run): it must not be folded past the call into `f() + o.p`.
    prog = (I(LoadBuiltin("o", T.UNKNOWN), (), (0,), keep=True), I(LoadBuiltin("f", T.FUNCTION), (), (1,), keep=True),
            I(CallFunction(), (1,), (2,)), I(GetProperty("p"), (0,), (3,)), I(Binary("+"), (2, 3), (4,)),
            I(DeclareVar(), (4,), (5,)))
    out = body(prog)
    assert "var x2 = f1();" in out          # the call stays a statement, before the read ...
    assert "x2 + x1.p" in out               # ... and the read may move past `x2`: a temporary, it cannot change
    assert "f1() + " not in out             # never: the call folded past the read


def test_property_read_is_never_folded_into_a_callee():
    prog = (I(LoadBuiltin("o", T.OBJECT), (), (0,), keep=True), I(GetProperty("m"), (0,), (1,)),
            I(CallFunction(), (1,), (2,)), I(DeclareVar(), (2,), (3,)))
    assert "o1.m()" not in body(prog)


def test_declaration_in_a_block_lifts_as_an_expression():
    prog = (lit(0, "true", T.BOOLEAN, keep=True), I(BeginIf(), (0,)), I(BeginFunction(0), (), (1,)),
            I(EndFunction()), I(EndIf()))
    assert "var f1 = function() {" in body(prog)


def test_dead_pure_temporaries_are_dropped_without_probes():
    prog = (lit(0, "1", keep=True), I(Binary("+"), (0, 0), (1,), keep=True), I(Probe()))
    assert "i1 + i1" in lift(prog, comments=False).source          # probed: kept and observed
    assert body(prog).strip() == ""                                 # plain: nothing observable


# ---------------------------------------------------------------------------- analysis

def test_types_are_flow_sensitive_for_declared_variables():
    # var x = 1; x = "s"; (x is a string here); if (c) { x = 1n } ; (x is unknown after the join)
    prog = (lit(0, "1"), I(DeclareVar(), (0,), (1,)), lit(2, '"s"', T.STRING), I(Reassign(), (1, 2)),
            I(Probe()), lit(3, "true", T.BOOLEAN), I(BeginIf(), (3,)), lit(4, "1n", T.BIGINT),
            I(Reassign(), (1, 4)), I(EndIf()), I(Probe()))
    an = analyse(prog)
    assert an.type_at(4, 1) == T.STRING
    assert an.type_at(10, 1) == T.UNKNOWN
    assert an.types[1] == T.UNKNOWN


def test_loop_bodies_reach_a_fixpoint():
    # var n = 1; for (...) { n = n + "" }  -> at the top of the body n may already be a string
    prog = (lit(0, "1"), I(DeclareVar(), (0,), (1,)), I(BeginFor(3), (), (), (2,)), lit(3, '""', T.STRING),
            I(Binary("+"), (1, 3), (4,)), I(Reassign(), (1, 4)), I(EndFor()))
    an = analyse(prog)
    assert an.type_at(4, 1) == T.UNKNOWN
    assert an.types[4] == T.STRING


def test_storage_and_activation():
    prog = (lit(0, "1"), I(DeclareVar(), (0,), (1,)),
            I(BeginFunction(1), (), (2,), (3,)), lit(4, "2"), I(DeclareVar(), (4,), (5,)), I(Return(), (5,)),
            I(EndFunction()),
            I(BeginFunction(0), (), (6,)), lit(7, "3"), I(DeclareVar(), (7,), (8,)),
            I(Eval("arguments.length", T.INT32), (), (9,)), I(Return(), (8,)), I(EndFunction()))
    an = analyse(prog)
    assert an.storage[1] == "top-var"
    assert an.storage[5] == "local-var" and an.storage[3] == "param"
    assert an.activation[6] and not an.activation[2]
    assert an.storage[8] == "activation-local-var"
    assert an.returns[2] == T.INT32


def test_variables_assigned_in_a_nested_function_escape():
    prog = (lit(0, "1"), I(DeclareVar(), (0,), (1,)), I(BeginFunction(0), (), (2,)), lit(3, '"s"', T.STRING),
            I(Reassign(), (1, 3)), I(EndFunction()), I(Probe()))
    an = analyse(prog)
    assert 1 in an.escaping
    assert an.type_at(6, 1) == T.UNKNOWN         # the function may have been called


@pytest.mark.parametrize("prog, why", [
    ((lit(0, "1"), lit(1, "2"), I(Reassign(), (0, 1))), "temporary"),
    ((lit(0, "1"), I(DeclareVar("const"), (0,), (1,)), I(Reassign(), (1, 0))), "const"),
    ((I(BeginFor(2), (), (), (0,)), I(Update("++", value=False), (0,)), I(EndFor())), "loop counter"),
    ((I(BeginTry()), I(EndTry())), "closes"),
    ((I(Return()),), "outside a function"),
    ((lit(0, "1"), I(Binary("+"), (0, 9), (1,))), "not visible"),
])
def test_verify_rejects(prog, why):
    with pytest.raises(InvalidProgram, match=why):
        verify(prog)


def test_stale_type_is_caught_after_a_mutation():
    """f returns a function, so `c = f()` is callable; rewire f's return to a number and `c()` is a
    call of a non-function -- only a fresh analysis notices (proposal §3.2)."""
    base = (lit(0, "1", T.NUMBER), I(BeginFunction(0), (), (1,)), I(BeginFunction(0), (), (2,)),
            I(Return(), (0,)), I(EndFunction()), I(Return(), (2,)), I(EndFunction()),
            I(CallFunction(), (1,), (3,)), I(CallFunction(), (3,), (4,)))
    assert analyse(base).types[3] == T.FUNCTION
    verify(base)
    mutated = base[:5] + (I(Return(), (0,)),) + base[6:]
    with pytest.raises(InvalidProgram, match="callee"):
        verify(mutated)


# ---------------------------------------------------------------------------- generated programs

@RELAXED
@given(programs(GenOptions()))
def test_generated_programs_verify(prog):
    verify(prog.ir)


def test_every_ir_generator_fires():
    load_all()
    seen: set[str] = set()

    @seed(11)
    @settings(RELAXED, max_examples=400)
    @given(ir_programs(GenOptions(swarm=False, max_statements=12)))
    def collect(pm):
        seen.update(pm[1]["usage"])

    collect()
    assert set(GENERATORS) - seen == set()


def test_generation_is_deterministic_for_a_seed():
    out: list[list[str]] = []
    for _ in range(2):
        got: list[str] = []

        @seed(5)
        @settings(RELAXED, max_examples=20)
        @given(programs(GenOptions()))
        def collect(p):
            got.append(p.source)

        collect()
        out.append(got)
    assert out[0] == out[1]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_lifted_programs_parse_with_node(tmp_path: Path):
    files: list[Path] = []

    @seed(1)
    @settings(RELAXED, max_examples=60)
    @given(programs(GenOptions()))
    def collect(p):
        f = tmp_path / f"c{len(files)}.js"
        f.write_text(p.source)
        files.append(f)

    collect()
    for f in files:
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f.read_text() + r.stderr
