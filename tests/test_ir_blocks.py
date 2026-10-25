"""IR control-flow constructs added for the remaining families: bounded while / do-while, for-of /
for-in, switch with fallthrough, with, bare and labelled blocks, break / continue (labelled),
generator functions with yield, spread arguments. Lifting, typing, verification, builder."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.types import T
from oxpecker.ir.analysis import InvalidProgram, analyse, verify
from oxpecker.ir.builder import Builder
from oxpecker.ir.lift import lift
from oxpecker.ir.ops import (BeginBlock, BeginFor, BeginForEach, BeginFunction, BeginSwitch, BeginWhile, BeginWith,
                             Break, CallFunction, Case, Continue, CreateArray, DeclareVar, EndBlock, EndFor,
                             EndForEach, EndFunction, EndSwitch, EndWhile, EndWith, Instr, LoadLiteral, Probe,
                             Reassign, Return, Yield)


def I(op, inputs=(), outputs=(), inner=(), **kw):
    return Instr(op, tuple(inputs), tuple(outputs), tuple(inner), **kw)


def lit(v, text, t=T.INT32, **kw):
    return I(LoadLiteral(t, text), (), (v,), **kw)


def body(prog) -> str:
    return lift(prog, probes=False, comments=False, dce=False).source


# ---------------------------------------------------------------------------- lifting

def test_while_declares_and_decrements_its_own_counter():
    prog = (I(BeginWhile(3), inner=(0,)), I(Probe()), I(EndWhile()))
    assert body(prog) == "var i1 = 3;\nwhile (i1 > 0) {\n  i1--;\n}\n"


def test_do_while_puts_the_bound_in_the_footer():
    prog = (I(BeginWhile(2, do=True, label="L"), inner=(0,)), I(EndWhile()))
    assert body(prog) == "var i1 = 2;\nL: do {\n} while (i1-- > 0);\n"


def test_for_of_and_for_in():
    prog = (lit(0, "[1, 2]", T.ARRAY), I(BeginForEach("of"), (0,), (), (1,)), I(EndForEach()),
            I(BeginForEach("in", label="K"), (0,), (), (2,)), I(EndForEach()))
    out = body(prog)
    assert "for (var el1 of [1, 2]) {" in out or "for (var el1 of a1) {" in out
    assert "K: for (var k1 in " in out


def test_switch_layout_with_fallthrough_and_default():
    prog = (lit(0, "2", keep=True), lit(1, "1"), I(BeginSwitch(), (0,)), I(Case(), (1,)), I(Break()), I(Case()),
            I(Probe()), I(EndSwitch()))
    assert body(prog) == "var i1 = 2;\nswitch (i1) {\n  case 1:\n    break;\n  default:\n}\n"


def test_labelled_block_with_break_and_with_statement():
    prog = (I(BeginBlock("B")), I(Break("B")), I(EndBlock()), lit(0, "({})", T.OBJECT, keep=True),
            I(BeginWith(), (0,)), I(EndWith()))
    out = body(prog)
    assert "B: {\n  break B;\n}" in out and "with (o1) {\n}" in out


def test_labelled_for_with_continue():
    prog = (I(BeginFor(2, label="O"), inner=(0,)), I(BeginFor(2), inner=(1,)), I(Continue("O")), I(EndFor()),
            I(EndFor()))
    assert "O: for (var i1 = 0; i1 < 2; i1++) {\n  for (var i2" in body(prog) and "continue O;" in body(prog)


def test_generator_function_and_yield_forms():
    prog = (I(BeginFunction(0, "generator"), (), (0,)), lit(1, "1"), I(Yield(), (1,), (2,)),
            I(Yield(delegate=True, value=False), (2,)), I(Yield(value=False)), I(EndFunction()))
    out = body(prog)
    assert out.startswith("function* f1() {") and "var x1 = yield 1;" in out
    assert "yield* x1;" in out and "\n  yield;\n" in out


def test_generator_in_a_block_is_an_expression():
    prog = (lit(0, "true", T.BOOLEAN, keep=True), I(BeginBlock()), I(BeginFunction(0, "generator"), (), (1,)),
            I(EndFunction()), I(EndBlock()))
    assert "var f1 = function*() {" in body(prog)


def test_spread_arguments_and_elements():
    prog = (lit(0, "[1]", T.ARRAY, keep=True), I(LoadLiteral(T.FUNCTION, "Math.max"), (), (1,), keep=True),
            I(CallFunction(spread=(1,)), (1, 0, 0), (2,)), I(CreateArray(spread=(0,)), (0, 0), (3,)),
            I(DeclareVar(), (2,), (4,)), I(DeclareVar(), (3,), (5,)))
    out = body(prog)
    assert "f1(a1, ...a1)" in out and "[...a1, a1]" in out


# ---------------------------------------------------------------------------- analysis

def test_break_flows_to_the_loop_exit():
    # var x = 1; for (...) { x = "s"; break; x = 1n; }  -> after the loop x may be a string
    prog = (lit(0, "1"), I(DeclareVar(), (0,), (1,)), I(BeginFor(2), inner=(2,)), lit(3, '"s"', T.STRING),
            I(Reassign(), (1, 3)), I(Break()), lit(4, "1n", T.BIGINT), I(Reassign(), (1, 4)), I(EndFor()), I(Probe()))
    an = analyse(prog)
    assert an.type_at(9, 1) == T.UNKNOWN          # int | string | bigint joined; string reached only via break


def test_continue_flows_back_to_the_head():
    # var x = 1; while (...) { if-less: x read at top; x = "s"; continue; }
    prog = (lit(0, "1"), I(DeclareVar(), (0,), (1,)), I(BeginWhile(3), inner=(2,)), I(Probe()),
            lit(3, '"s"', T.STRING), I(Reassign(), (1, 3)), I(Continue()), I(EndWhile()))
    assert analyse(prog).type_at(3, 1) == T.UNKNOWN    # second iteration: string


def test_switch_cases_join_fallthrough_and_entry():
    prog = (lit(0, "1"), I(DeclareVar(), (0,), (1,)), lit(2, "1"), I(BeginSwitch(), (1,)), I(Case(), (2,)),
            lit(3, '"s"', T.STRING), I(Reassign(), (1, 3)), I(Case()), I(Probe()), I(EndSwitch()), I(Probe()))
    an = analyse(prog)
    assert an.type_at(8, 1) == T.UNKNOWN          # default reached directly (int) or by fallthrough (string)
    assert an.type_at(10, 1) == T.UNKNOWN


def test_for_in_keys_are_strings_and_calling_a_generator_gives_an_object():
    prog = (lit(0, "({})", T.OBJECT), I(BeginForEach("in"), (0,), (), (1,)), I(EndForEach()),
            I(BeginFunction(0, "generator"), (), (2,)), I(EndFunction()), I(CallFunction(), (2,), (3,)))
    an = analyse(prog)
    assert an.types[1] == T.STRING and an.types[3] == T.OBJECT
    assert an.storage[1] == "loopvar"


@pytest.mark.parametrize("prog, why", [
    ((I(Break()),), "no target"),
    ((I(BeginBlock()), I(Continue()), I(EndBlock())), "no loop target"),
    ((lit(0, "1"), I(BeginSwitch(), (0,)), I(Case()), I(Continue()), I(EndSwitch())), "no loop target"),
    ((I(BeginFor(1), inner=(0,)), I(Break("X")), I(EndFor())), "no target"),
    ((I(BeginBlock("L")), I(BeginFor(1, label="L"), inner=(0,)), I(EndFor()), I(EndBlock())), "shadows"),
    ((I(BeginFor(1), inner=(0,)), I(BeginFunction(0), (), (1,)), I(Break()), I(EndFunction()), I(EndFor())),
     "no target"),
    ((lit(0, "1"), I(BeginSwitch(), (0,)), lit(1, "2"), I(Case(), (1,)), I(EndSwitch())), "outside any case"),
    ((lit(0, "1"), I(BeginSwitch(), (0,)), I(Case()), I(Case()), I(EndSwitch())), "second default"),
    ((lit(0, "1"), I(BeginSwitch(), (0,)), I(Case()), lit(1, "2"), I(Case(), (1,)), I(EndSwitch())),
     "before the switch"),
    ((lit(0, "1"), I(Case(), (0,))), "outside a switch"),
    ((lit(0, "1"), I(Yield(value=False), (0,))), "outside a generator"),
    ((I(BeginFunction(0), (), (0,)), I(Yield(value=False)), I(EndFunction())), "outside a generator"),
    ((I(BeginWhile(2), inner=(0,)), I(Reassign(), (0, 0)), I(EndWhile())), "loop counter"),
])
def test_verify_rejects(prog, why):
    with pytest.raises(InvalidProgram, match=why):
        verify(prog)


def test_break_inside_if_in_loop_and_inside_switch_case_are_fine():
    prog = (lit(0, "true", T.BOOLEAN), I(BeginFor(1), inner=(1,)), I(BeginBlock()), I(Break()), I(EndBlock()),
            I(EndFor()), I(BeginSwitch(), (0,)), I(Case()), I(Break()), I(EndSwitch()))
    verify(prog)


# ---------------------------------------------------------------------------- builder

def test_builder_blocks_verify_lift_and_parse(tmp_path):
    b = Builder(GenOptions())
    one = b.const(T.INT32, "1")
    acc = b.declare(b.emit(CreateArray(), ()))
    label = b.fresh_label()
    with b.while_(3, label=label) as c:
        with b.for_each(acc) as x:
            b.do_method(acc, "push", x)
            b.continue_(label)
        with b.switch_(c) as sw:
            with sw.case(one):
                b.break_()
            with sw.case(None):
                b.do_method(acc, "push", c)
    with b.block(b.fresh_label()) as _:
        pass
    with b.function(1, style="generator") as (g, (p,)):
        got = b.yield_(p)
        b.yield_(got, result=False)
        b.yield_(b.emit(CreateArray(), (p,)), delegate=True, result=False)
    gen = b.call(g, one)
    b.declare(b.emit(CreateArray(spread=(0,)), (gen,)))
    prog = b.program()
    verify(prog)
    assert not b.in_loop
    src = lift(prog).source
    if shutil.which("node"):
        f = tmp_path / "blocks.js"
        f.write_text(src)
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + src


def test_replay_of_new_blocks_verifies():
    b = Builder(GenOptions())
    one = b.const(T.INT32, "1")
    start = len(b.instrs)
    with b.while_(2, do=True) as c:
        with b.switch_(c) as sw:
            with sw.case(one):
                b.break_()
    body_ = b.instrs[start:]
    b.replay(body_, {})
    verify(b.program())


def test_yield_outside_a_generator_is_refused_by_the_builder():
    b = Builder(GenOptions())
    with b.function(0) as _:
        with pytest.raises(AssertionError):
            b.yield_(None)
