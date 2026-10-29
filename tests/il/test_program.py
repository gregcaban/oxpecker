"""Instructions, check(), blocks, renumbering, signatures."""

from __future__ import annotations

import pytest

from oxpecker.il import ops
from oxpecker.il.ops import Context, Lift
from oxpecker.il.options import GenOptions
from oxpecker.il.program import (
    InvalidProgram,
    Instruction,
    Program,
    Var,
    block_begin,
    block_end,
    block_group,
    block_groups,
    check,
    contexts,
    dump,
    enclosing_subroutine,
    is_valid,
    renumber,
    signature,
)
from oxpecker.il.types import Key


def I(op, *inouts, index=-1, **meta):
    return Instruction(op, tuple(Var(v) for v in inouts), index=index, **meta)


def prog(*instrs):
    return tuple(i.with_(index=n) for n, i in enumerate(instrs))


def test_instruction_layout():
    i = I(ops.BinaryOperation(ops.BinaryOperator.ADD), 0, 1, 2)
    assert i.inputs == (0, 1) and i.outputs == (2,) and i.output == 2 and i.inner_outputs == ()
    f = I(ops.BeginPlainFunction(ops.Parameters(2)), 3, 4, 5)
    assert f.outputs == (3,) and f.inner_outputs == (4, 5) and f.all_outputs == (3, 4, 5)
    with pytest.raises(ValueError):
        I(ops.BinaryOperation(ops.BinaryOperator.ADD), 0, 1)
    with pytest.raises(ValueError):
        I(ops.Return(), 4).output  # noqa: B018  (no output)
    c = I(ops.CallFunction(2), 0, 1, 2, 3)
    assert c.variadic_inputs == (1, 2)


def test_check_accepts_a_small_valid_program():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.LoadInteger(2), 1),
        I(ops.BinaryOperation(ops.BinaryOperator.ADD), 0, 1, 2),
        I(ops.BeginPlainFunction(ops.Parameters(1)), 3, 4),
        I(ops.Return(), 4),
        I(ops.EndPlainFunction()),
        I(ops.CallFunction(1), 3, 2, 5),
    )
    check(p)
    assert is_valid(p)


@pytest.mark.parametrize("kind, bad", [
    ("index", lambda: (I(ops.LoadInteger(1), 0, index=5),)),
    ("numbering", lambda: prog(I(ops.LoadInteger(1), 1))),
    ("numbering", lambda: prog(I(ops.LoadInteger(1), 0), I(ops.LoadInteger(1), 0))),
    ("undefined", lambda: prog(I(ops.TypeOf(), 7, 0))),
    ("context", lambda: prog(I(ops.LoadInteger(1), 0), I(ops.Return(), 0))),
    ("context", lambda: prog(I(ops.LoadInteger(1), 0), I(ops.ObjectLiteralAddProperty(Key("a")), 0))),
    ("block_mismatch", lambda: prog(I(ops.BeginTry()), I(ops.EndIf()))),
    ("block_mismatch", lambda: prog(I(ops.EndIf()),)),
    ("block_mismatch", lambda: prog(I(ops.BeginTry()),)),
    ("for_header", lambda: prog(I(ops.LoadInteger(0), 0), I(ops.BeginForLoopInitializer()),
                                I(ops.BeginForLoopCondition(1), 0, 1), I(ops.LoadBoolean(True), 2),
                                I(ops.BeginForLoopAfterthought(2), 2, 3, 4))),
])
def test_check_rejects(kind, bad):
    with pytest.raises(InvalidProgram) as e:
        check(bad())
    assert e.value.kind == kind


def test_check_rejects_a_variable_used_outside_its_block():
    p = prog(
        I(ops.LoadBoolean(True), 0),
        I(ops.BeginIf(), 0, 1),
        I(ops.LoadInteger(3), 2),
        I(ops.EndIf()),
        I(ops.TypeOf(), 2, 3),
    )
    with pytest.raises(InvalidProgram) as e:
        check(p)
    assert e.value.kind == "invisible"


def test_function_variable_is_visible_outside_but_parameters_are_not():
    p = prog(
        I(ops.BeginPlainFunction(ops.Parameters(1)), 0, 1),
        I(ops.EndPlainFunction()),
        I(ops.CallFunction(1), 0, 1, 2),
    )
    with pytest.raises(InvalidProgram) as e:
        check(p)
    assert e.value.kind == "invisible"


def test_contexts_follow_blocks_and_switch_cases_resume():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.BeginPlainFunction(ops.Parameters(0)), 1),
        I(ops.BeginRepeatLoop(3), 2, 3),
        I(ops.BeginSwitch(), 0, 4),
        I(ops.BeginSwitchCase(), 0),
        I(ops.SwitchBreak()),
        I(ops.EndSwitchCase()),
        I(ops.EndSwitch()),
        I(ops.LoopBreak()),
        I(ops.EndRepeatLoop()),
        I(ops.Return(has_value=False)),
        I(ops.EndPlainFunction()),
    )
    check(p)
    cs = contexts(p)
    assert cs[2] == Context.JAVASCRIPT | Context.SUBROUTINE
    assert cs[3] & Context.LOOP
    assert cs[4] == Context.SWITCH_BLOCK                      # inside the switch, before a case: no javascript
    assert Context.SWITCH_CASE in cs[5] and Context.JAVASCRIPT in cs[5] and Context.SUBROUTINE in cs[5]
    assert Context.LOOP not in cs[5]                           # a case drops the loop context
    assert Context.LOOP in cs[8]


def test_blocks():
    p = prog(
        I(ops.LoadBoolean(True), 0),
        I(ops.BeginIf(), 0, 1),
        I(ops.BeginTry()),
        I(ops.BeginCatch(), 2),
        I(ops.EndTryCatchFinally()),
        I(ops.BeginElse(), 3),
        I(ops.EndIf()),
    )
    check(p)
    assert block_end(p, 1) == 5 and block_end(p, 5) == 6 and block_end(p, 2) == 3
    assert block_begin(p, 6) == 5 and block_begin(p, 4) == 3
    assert block_group(p, 1) == [1, 5, 6] and block_group(p, 2) == [2, 3, 4]
    assert block_groups(p) == [[2, 3, 4], [1, 5, 6]]
    assert enclosing_subroutine(p, 3, ops.FUNCTION_BEGINS) is None


def test_enclosing_subroutine():
    p = prog(
        I(ops.BeginPlainFunction(ops.Parameters(0)), 0),
        I(ops.BeginTry()),
        I(ops.LoadInteger(1), 1),
        I(ops.BeginFinally()),
        I(ops.EndTryCatchFinally()),
        I(ops.EndPlainFunction()),
        I(ops.LoadInteger(2), 2),
    )
    check(p)
    assert enclosing_subroutine(p, 2, ops.FUNCTION_BEGINS) == 0
    assert enclosing_subroutine(p, 6, ops.FUNCTION_BEGINS) is None


def test_renumber_makes_numbers_contiguous_and_keeps_metadata():
    p = (
        I(ops.LoadInteger(1), 5, gen="int", lift=Lift.VAR),
        I(ops.LoadInteger(2), 9),
        I(ops.BinaryOperation(ops.BinaryOperator.MUL), 5, 9, 12, step=3),
    )
    r = renumber(p)
    check(r)
    assert [i.inouts for i in r] == [(0,), (1,), (0, 1, 2)]
    assert r[0].gen == "int" and r[0].lift is Lift.VAR and r[2].step == 3
    with pytest.raises(InvalidProgram):
        renumber((I(ops.TypeOf(), 3, 4),))


def test_signature_ignores_provenance_but_not_lifting():
    a = prog(I(ops.LoadInteger(1), 0, gen="x"), I(ops.TypeOf(), 0, 1))
    b = prog(I(ops.LoadInteger(1), 0, gen="y", origin=4), I(ops.TypeOf(), 0, 1))
    c = prog(I(ops.LoadInteger(1), 0, lift=Lift.CONST), I(ops.TypeOf(), 0, 1))
    assert signature(a) == signature(b) != signature(c)
    assert Program(a, GenOptions()).signature() == signature(a)


def test_dump_is_indented_and_shows_variables():
    p = prog(I(ops.BeginPlainFunction(ops.Parameters(1)), 0, 1), I(ops.Return(), 1), I(ops.EndPlainFunction()))
    text = dump(p)
    lines = text.splitlines()
    assert lines[1].startswith("    ") and lines[1].endswith("v1") and lines[0].startswith("v0 <- BeginPlainFunction")
    assert not lines[2].startswith(" ")
