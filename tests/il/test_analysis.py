"""Scopes, def-use, the typer (branch joins, two-pass loops, try/catch), the Rhino model, verify()."""

from __future__ import annotations

import pytest

from oxpecker.il import ops
from oxpecker.il.analysis import (
    ActivationReason,
    RhinoType,
    Storage,
    analyse,
    binary_result,
    verify,
)
from oxpecker.il.environment import Builtins, Keys, Methods
from oxpecker.il.ops import BinaryOperator as B
from oxpecker.il.ops import Comparator as C
from oxpecker.il.ops import DeclarationKind as DK
from oxpecker.il.ops import UnaryOperator as U
from oxpecker.il.program import InvalidProgram, Instruction, Var
from oxpecker.il.types import (
    ANY,
    BIGINT,
    BOOLEAN,
    FLOAT,
    FUNCTION,
    INTEGER,
    NUMBER,
    OBJECT,
    PRIMITIVE,
    STRING,
    UNDEFINED,
    Kind,
    Type,
)


def I(op, *inouts):
    return Instruction(op, tuple(Var(v) for v in inouts))


def prog(*instrs):
    return tuple(i.with_(index=n) for n, i in enumerate(instrs))


# ------------------------------------------------------------------ structure
def test_storage_kinds_and_visibility():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),
        I(ops.Declare(DK.LET), 0, 2),
        I(ops.BeginPlainFunction(ops.Parameters(1)), 3, 4),
        I(ops.Declare(DK.VAR), 4, 5),
        I(ops.Declare(DK.CONST), 4, 6),
        I(ops.BeginIf(), 4, 7),
        I(ops.Declare(DK.LET), 4, 8),
        I(ops.EndIf()),
        I(ops.BeginTry()),
        I(ops.BeginCatch(), 9),
        I(ops.EndTryCatchFinally()),
        I(ops.EndPlainFunction()),
        I(ops.BeginRepeatLoop(2), 10, 11),
        I(ops.EndRepeatLoop()),
    )
    an = analyse(p)
    s = an.storage
    assert s[0] is Storage.TEMP and s[1] is Storage.TOP_VAR and s[2] is Storage.TOP_LET
    assert s[3] is Storage.FUNCTION and s[4] is Storage.PARAM
    assert s[5] is Storage.LOCAL_VAR and s[6] is Storage.LOCAL_CONST and s[8] is Storage.BLOCK_LET
    assert s[7] is Storage.LABEL and s[9] is Storage.CATCH and s[10] is Storage.COUNTER and s[11] is Storage.LABEL
    assert an.assignable == {1, 2, 3, 4, 5, 8, 9}
    assert an.visible_at[4] == (4, 3, 2, 1, 0)          # most recent first; the function sees its own variable
    assert an.visible_at[13] == (3, 2, 1, 0)
    assert an.function_of[4] == 3 and an.function_of[13] is None
    assert an.defs[5] == 4 and an.uses[4] == [4, 5, 6, 7]
    assert an.declared_visible_at(13) == (3, 2, 1)


def test_def_use_counts_reassignments():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),
        I(ops.Reassign(), 1, 0),
        I(ops.Update(B.ADD), 1, 0),
        I(ops.UnaryOperation(U.POST_INC), 1, 2),
    )
    an = analyse(p)
    assert an.assignments[1] == [1, 2, 3, 4] and an.num_assignments(1) == 4
    assert an.num_assignments(0) == 1 and an.num_uses(0) == 3


# ------------------------------------------------------------------ typer
def test_literal_and_operator_types():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.LoadFloat(1.5), 1),
        I(ops.LoadString("a"), 2),
        I(ops.BinaryOperation(B.ADD), 0, 1, 3),
        I(ops.BinaryOperation(B.ADD), 0, 2, 4),
        I(ops.BinaryOperation(B.BIT_OR), 1, 0, 5),
        I(ops.Compare(C.LESS), 0, 1, 6),
        I(ops.TypeOf(), 2, 7),
        I(ops.LoadBigInt(1), 8),
        I(ops.BinaryOperation(B.MUL), 8, 8, 9),
        I(ops.UnaryOperation(U.MINUS), 8, 10),
        I(ops.TernaryOperation(), 6, 0, 2, 11),
    )
    an = analyse(p)
    ty = an.types
    assert ty[0] == INTEGER and ty[1] == FLOAT and ty[2] == STRING
    assert ty[3] == NUMBER and ty[4] == STRING and ty[5] == INTEGER and ty[6] == BOOLEAN and ty[7] == STRING
    assert ty[9] == BIGINT and ty[10] == BIGINT
    assert ty[11].possible == Kind.INTEGER | Kind.STRING
    assert binary_result(B.ADD, ANY, ANY).Is(PRIMITIVE.join(BIGINT))


def test_reassign_is_a_strong_update_and_branches_join():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),            # n: integer
        I(ops.LoadString("s"), 2),
        I(ops.LoadBoolean(True), 3),
        I(ops.BeginIf(), 3, 4),
        I(ops.Reassign(), 1, 2),                 # n = "s" in the then branch
        I(ops.TypeOf(), 1, 5),
        I(ops.EndIf()),
        I(ops.TypeOf(), 1, 6),
    )
    an = analyse(p)
    assert an.type_at(6, Var(1)) == STRING                           # inside: strong update
    assert an.type_at(8, Var(1)).possible == Kind.INTEGER | Kind.STRING   # after: joined with the untaken branch


def test_if_else_joins_both_branches_only():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),
        I(ops.LoadString("s"), 2),
        I(ops.LoadBoolean(True), 3),
        I(ops.BeginIf(), 3, 4),
        I(ops.Reassign(), 1, 2),
        I(ops.BeginElse(), 5),
        I(ops.Reassign(), 1, 3),
        I(ops.EndIf()),
        I(ops.TypeOf(), 1, 6),
    )
    an = analyse(p)
    assert an.type_at(9, Var(1)).possible == Kind.STRING | Kind.BOOLEAN   # not integer: both branches assign


def test_loop_body_is_analysed_twice():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),
        I(ops.LoadString("s"), 2),
        I(ops.BeginRepeatLoop(3), 3, 4),
        I(ops.TypeOf(), 1, 5),                   # sees integer on the first pass, integer|string on the second
        I(ops.Reassign(), 1, 2),
        I(ops.EndRepeatLoop()),
        I(ops.TypeOf(), 1, 6),
    )
    an = analyse(p)
    assert an.type_at(4, Var(1)).possible == Kind.INTEGER | Kind.STRING
    assert an.type_at(7, Var(1)).possible == Kind.INTEGER | Kind.STRING
    assert an.types[3] == INTEGER


def test_catch_entry_joins_everything_reached_in_try():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),
        I(ops.LoadString("s"), 2),
        I(ops.BeginTry()),
        I(ops.Reassign(), 1, 2),
        I(ops.BeginCatch(), 3),
        I(ops.TypeOf(), 1, 4),                   # entered from before or after the reassignment
        I(ops.EndTryCatchFinally()),
        I(ops.TypeOf(), 1, 5),
    )
    an = analyse(p)
    assert an.type_at(6, Var(1)).possible == Kind.INTEGER | Kind.STRING
    assert an.type_at(8, Var(1)).possible == Kind.INTEGER | Kind.STRING
    assert an.types[3] == ANY


def test_try_finally_without_catch():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),
        I(ops.LoadString("s"), 2),
        I(ops.BeginTry()),
        I(ops.Reassign(), 1, 2),
        I(ops.BeginFinally()),
        I(ops.TypeOf(), 1, 3),
        I(ops.EndTryCatchFinally()),
        I(ops.TypeOf(), 1, 4),
    )
    an = analyse(p)
    assert an.type_at(6, Var(1)).possible == Kind.INTEGER | Kind.STRING
    assert an.type_at(8, Var(1)).possible == Kind.INTEGER | Kind.STRING


def test_function_signature_and_call_result():
    p = prog(
        I(ops.BeginPlainFunction(ops.Parameters(1)), 0, 1),
        I(ops.LoadInteger(2), 2),
        I(ops.BinaryOperation(B.MUL), 1, 2, 3),
        I(ops.Return(), 3),
        I(ops.EndPlainFunction()),
        I(ops.LoadInteger(5), 4),
        I(ops.CallFunction(1), 0, 4, 5),
        I(ops.BeginPlainFunction(ops.Parameters(0)), 6),
        I(ops.EndPlainFunction()),
        I(ops.CallFunction(0), 6, 7),
        I(ops.BeginGeneratorFunction(ops.Parameters(0)), 8),
        I(ops.Yield(), 4, 9),
        I(ops.EndGeneratorFunction()),
        I(ops.CallFunction(0), 8, 10),
    )
    an = analyse(p, param_types={0: (NUMBER,)})
    assert an.types[1] == NUMBER
    assert an.returns[0] == NUMBER and an.types[5] == NUMBER
    assert an.returns[6] == UNDEFINED and an.types[7] == UNDEFINED
    assert an.types[10].group == "Generator" and an.types[10].element == INTEGER
    assert an.types[0].Is(FUNCTION) and an.final_types[Var(0)].signature.returns == NUMBER


def test_function_body_changes_outer_variables_conditionally():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.Declare(DK.VAR), 0, 1),
        I(ops.LoadString("s"), 2),
        I(ops.BeginPlainFunction(ops.Parameters(0)), 3),
        I(ops.Reassign(), 1, 2),
        I(ops.EndPlainFunction()),
        I(ops.TypeOf(), 1, 4),
    )
    an = analyse(p)
    assert an.type_at(6, Var(1)).possible == Kind.INTEGER | Kind.STRING


def test_builtins_methods_and_object_literals():
    p = prog(
        I(ops.LoadBuiltin(Builtins.MAP), 0),
        I(ops.Construct(0), 0, 1),
        I(ops.LoadBuiltin(Builtins.MATH), 2),
        I(ops.LoadInteger(3), 3),
        I(ops.CallMethod(Methods.MATH_IMUL, 2), 2, 3, 3, 4),
        I(ops.LoadBuiltin(Builtins.PARSE_INT), 5),
        I(ops.LoadString("7"), 6),
        I(ops.CallFunction(1), 5, 6, 7),
        I(ops.BeginObjectLiteral()),
        I(ops.ObjectLiteralAddProperty(Keys.A), 3),
        I(ops.EndObjectLiteral(), 8),
        I(ops.GetProperty(Keys.A), 8, 9),
        I(ops.GetProperty(Keys.B), 8, 10),
        I(ops.SetProperty(Keys.B), 8, 6),
        I(ops.GetProperty(Keys.B), 8, 11),
        I(ops.CreateArray(2), 3, 6, 12),
        I(ops.GetElement(0), 12, 13),
        I(ops.GetProperty(Keys.LENGTH), 12, 14),
    )
    an = analyse(p)
    assert an.types[1].group == "Map"
    assert an.types[4] == INTEGER and an.types[7] == NUMBER
    assert an.types[8].group.startswith("_Object") and Keys.A in an.types[8].props
    assert an.types[9] == INTEGER and an.types[10] == ANY
    assert Keys.B in an.type_at(14, Var(8)).props
    assert an.types[13].possible == Kind.INTEGER | Kind.STRING and an.types[14] == INTEGER


def test_for_loop_header_variables_alias_and_join():
    p = prog(
        I(ops.LoadInteger(0), 0),
        I(ops.LoadFloat(0.5), 1),
        I(ops.BeginForLoopInitializer()),
        I(ops.BeginForLoopCondition(1), 0, 2),
        I(ops.LoadInteger(3), 3),
        I(ops.Compare(C.LESS), 2, 3, 4),
        I(ops.BeginForLoopAfterthought(1), 4, 5),
        I(ops.Update(B.ADD), 5, 1),              # i += 0.5
        I(ops.BeginForLoopBody(1), 6, 7),
        I(ops.TypeOf(), 6, 8),
        I(ops.EndForLoop()),
    )
    an = analyse(p)
    assert an.storage[2] is Storage.LOOPVAR and an.storage[7] is Storage.LABEL
    assert an.type_at(9, Var(6)) == NUMBER       # integer joined with the float update on the second pass


def test_for_each_and_destructuring_types():
    p = prog(
        I(ops.LoadInteger(1), 0),
        I(ops.CreateArray(1), 0, 1),
        I(ops.BeginForEachLoop(ops.ForEachKind.OF), 1, 2, 3),
        I(ops.EndForEachLoop()),
        I(ops.BeginForEachLoop(ops.ForEachKind.IN), 1, 4, 5),
        I(ops.EndForEachLoop()),
        I(ops.Destruct(ops.ArrayPattern((ops.ArrayElement(ops.Binding()),), rest=ops.Binding())), 1, 6, 7),
    )
    an = analyse(p)
    assert an.types[2] == INTEGER and an.types[4] == STRING
    assert an.types[6] == INTEGER and an.types[7].group == "Array"
    assert an.storage[2] is Storage.FOREACH and an.storage[6] is Storage.TOP_VAR


# ------------------------------------------------------------------ Rhino model
def numeric_function(*body, named=True, params=1):
    return [I(ops.BeginPlainFunction(ops.Parameters(params), named=named), 0, *range(1, params + 1)), *body, I(ops.EndPlainFunction())]


def test_number_vars_follow_rhino_rules():
    # function f(a) { var n = 1; n = n * 2; var m = a + 1; var k = -b; }  with b a BigInt local
    p = prog(
        I(ops.BeginPlainFunction(ops.Parameters(1), named=True), 0, 1),
        I(ops.LoadInteger(1), 2),
        I(ops.Declare(DK.VAR), 2, 3),                 # n = 1
        I(ops.LoadInteger(2), 4),
        I(ops.BinaryOperation(B.MUL), 3, 4, 5),
        I(ops.Reassign(), 3, 5),                      # n = n * 2        -> number var
        I(ops.BinaryOperation(B.ADD), 1, 2, 6),
        I(ops.Declare(DK.VAR), 6, 7),                 # m = a + 1        -> any (parameter)
        I(ops.LoadBigInt(1), 8),
        I(ops.Declare(DK.VAR), 8, 9),                 # b = 1n           -> any
        I(ops.UnaryOperation(U.MINUS), 9, 10),
        I(ops.Declare(DK.VAR), 10, 11),               # k = -b           -> number (the BigInt bug shape)
        I(ops.LoadString("s"), 12),
        I(ops.Declare(DK.VAR), 2, 13),
        I(ops.Reassign(), 13, 12),                    # x = 1; x = "s"   -> any
        I(ops.Declare(DK.LET), 2, 14),                # let: not predicted
        I(ops.EndPlainFunction()),
    )
    an = analyse(p)
    r = an.rhino
    assert r.activation[0] == ()
    assert r.number_var == {3, 11}
    assert r.expr_type[4] is RhinoType.NUMBER and r.expr_type[6] is RhinoType.ANY and r.expr_type[10] is RhinoType.NUMBER


def test_activation_reasons():
    def fn(*body):
        return prog(I(ops.BeginPlainFunction(ops.Parameters(0)), 0), *body, I(ops.EndPlainFunction()))

    assert analyse(fn(I(ops.LoadArguments(), 1))).rhino.activation[0] == (ActivationReason.ARGUMENTS,)
    assert analyse(fn(I(ops.BeginTry()), I(ops.BeginCatch(), 1), I(ops.EndTryCatchFinally()))).rhino.activation[0] == (ActivationReason.CATCH,)
    assert analyse(fn(I(ops.BeginArrowFunction(ops.Parameters(0)), 1), I(ops.EndArrowFunction()))).rhino.activation[0] == (ActivationReason.NESTED_FUNCTION,)
    ev = fn(I(ops.LoadBuiltin(Builtins.EVAL), 1), I(ops.LoadString("1"), 2), I(ops.CallFunction(1), 1, 2, 3))
    assert analyse(ev).rhino.activation[0] == (ActivationReason.EVAL,)
    with_ = fn(I(ops.LoadBuiltin(Builtins.MATH), 1), I(ops.BeginWith(), 1), I(ops.EndWith()))
    assert analyse(with_).rhino.activation[0] == (ActivationReason.WITH,)
    gen = prog(I(ops.BeginGeneratorFunction(ops.Parameters(0)), 0), I(ops.EndGeneratorFunction()))
    assert ActivationReason.YIELD in analyse(gen).rhino.activation[0]
    plain = fn(I(ops.LoadInteger(1), 1), I(ops.Declare(DK.VAR), 1, 2))
    assert analyse(plain).rhino.activation[0] == () and analyse(plain).rhino.optimizer_runs(0)
    assert analyse(plain).rhino.number_var == {2}


def test_direct_calls_need_a_named_top_level_function_called_from_a_function_with_exact_arity():
    p = prog(
        I(ops.BeginPlainFunction(ops.Parameters(1), named=True), 0, 1),
        I(ops.Return(), 1),
        I(ops.EndPlainFunction()),
        I(ops.LoadInteger(1), 2),
        I(ops.CallFunction(1), 0, 2, 3),              # from script level: not a direct call
        I(ops.BeginPlainFunction(ops.Parameters(0)), 4),
        I(ops.CallFunction(1), 0, 2, 5),              # direct call
        I(ops.CallFunction(2), 0, 2, 2, 6),           # wrong arity
        I(ops.EndPlainFunction()),
    )
    r = analyse(p).rhino
    assert r.direct_call_target == {0} and r.direct_call_site == {6}
    anon = prog(I(ops.BeginPlainFunction(ops.Parameters(1)), 0, 1), I(ops.EndPlainFunction()),
                I(ops.BeginPlainFunction(ops.Parameters(0)), 2), I(ops.LoadInteger(1), 3), I(ops.CallFunction(1), 0, 3, 4),
                I(ops.EndPlainFunction()))
    assert analyse(anon).rhino.direct_call_target == frozenset()


# ------------------------------------------------------------------ verify
def test_verify_accepts_and_rejects():
    ok = prog(I(ops.LoadInteger(1), 0), I(ops.Declare(DK.VAR), 0, 1), I(ops.Reassign(), 1, 0))
    verify(ok)
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.LoadInteger(1), 0), I(ops.Reassign(), 0, 0)))           # a temporary
    assert e.value.kind == "assignment"
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.LoadInteger(1), 0), I(ops.Declare(DK.CONST), 0, 1), I(ops.Update(B.ADD), 1, 0)))
    assert e.value.kind == "assignment"
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.LoadInteger(1), 0), I(ops.CallFunction(0), 0, 1)))       # an integer is never callable
    assert e.value.kind == "type"
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.LoadNull(), 0), I(ops.GetProperty(Keys.A), 0, 1)))
    assert e.value.kind == "type"
    verify(prog(I(ops.LoadNull(), 0), I(ops.GetProperty(Keys.A, receiver_optional=True), 0, 1)))
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.BeginRepeatLoop(1), 0, 1), I(ops.TypeOf(), 1, 2), I(ops.EndRepeatLoop())))   # label as value
    assert e.value.kind == "label"
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.BeginRepeatLoop(1), 0, 1), I(ops.Reassign(), 0, 0), I(ops.EndRepeatLoop())))  # counter
    assert e.value.kind == "assignment"
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.BeginObjectLiteral()), I(ops.EndObjectLiteral(), 0), I(ops.BeginObjectLiteral()),
                    I(ops.ObjectLiteralSetPrototype(), 0), I(ops.ObjectLiteralSetPrototype(), 0), I(ops.EndObjectLiteral(), 1)))
    assert e.value.kind == "singular"
    with pytest.raises(InvalidProgram) as e:
        verify(prog(I(ops.LoadBuiltin(Builtins.MATH), 0), I(ops.BeginWith(), 0), I(ops.EndWith())), strict=True)
    assert e.value.kind == "strict"


def test_verify_allows_what_merely_may_throw():
    p = prog(I(ops.LoadBuiltin(Builtins.MAP), 0), I(ops.Construct(0), 0, 1), I(ops.LoadInteger(1), 2),
             I(ops.BinaryOperation(B.ADD), 1, 2, 3), I(ops.CallMethod(Methods.ARRAY_PUSH, 1), 1, 2, 4))
    verify(p)      # pushing on a Map throws at run time in both modes; that is a test, not an error
