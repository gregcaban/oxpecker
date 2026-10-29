"""Operations: attributes, arities, block matching, and guard test 1 (no text-carrying operation)."""

from __future__ import annotations

import dataclasses
import inspect
import typing

import pytest

from oxpecker.il import ops
from oxpecker.il.ops import (
    END_OF,
    Attr,
    BeginForEachLoop,
    BeginObjectLiteralMethod,
    BeginPlainFunction,
    Binding,
    CallMethod,
    ComputedTarget,
    ConfigureProperty,
    Context,
    CreateTemplateString,
    Destruct,
    DestructAndReassign,
    ElementTarget,
    ForEachKind,
    LoadFloat,
    LoadInteger,
    ObjectPattern,
    ObjectProperty,
    Operation,
    Parameters,
    PropertyFlag,
    PropertyTarget,
    PropertyType,
    ArrayElement,
    ArrayPattern,
    UnaryOperator,
    all_operation_classes,
    matches_end,
    num_bindings,
    num_extra_inputs,
)
from oxpecker.il.types import Key, KeyKind

# Guard test 1 (design §10 item 1): the only string-typed fields in the operation set.
ALLOWED_TEXT_FIELDS = {
    ("LoadString", "value"),             # the characters of a string literal
    ("LoadRegExp", "pattern"),           # a regexp pattern
    ("CreateTemplateString", "parts"),   # the fixed parts of a template literal
}


def _is_str_type(annotation: object) -> bool:
    if annotation is str:
        return True
    origin = typing.get_origin(annotation)
    return origin is tuple and str in typing.get_args(annotation)


def test_no_text_carrying_operation_exists():
    found = set()
    hints_cache = {}
    for cls in all_operation_classes():
        hints = hints_cache.setdefault(cls, typing.get_type_hints(cls, globalns={**vars(ops), "Builtin": object, "Method": object}))
        for f in dataclasses.fields(cls):
            if _is_str_type(hints.get(f.name)):
                found.add((cls.__name__, f.name))
    assert found == ALLOWED_TEXT_FIELDS, f"unexpected text fields: {found - ALLOWED_TEXT_FIELDS}"


def test_no_eval_operation():
    names = {cls.__name__ for cls in all_operation_classes()}
    assert "Eval" not in names and "Print" not in names and "Fixup" not in names


def test_every_operation_is_a_frozen_dataclass_with_consistent_counts():
    for cls in all_operation_classes():
        assert dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen, cls
        if cls.first_variadic is not None:
            assert cls.attrs & Attr.VARIADIC, cls
        if cls.attrs & Attr.VARIADIC:
            assert cls.first_variadic is not None, cls
        if cls.context_opened:
            assert cls.attrs & Attr.BLOCK_START, cls
        if cls.attrs & (Attr.PROPAGATES_CONTEXT | Attr.RESUMES_CONTEXT):
            assert cls.attrs & Attr.BLOCK_START, cls


def test_every_block_start_has_an_end_and_vice_versa():
    starts = {cls for cls in all_operation_classes() if cls.attrs & Attr.BLOCK_START}
    ends = {cls for cls in all_operation_classes() if cls.attrs & Attr.BLOCK_END}
    assert starts == set(END_OF), starts ^ set(END_OF)
    closed_by = {e for es in END_OF.values() for e in es}
    assert ends == closed_by, ends ^ closed_by
    assert matches_end(ops.BeginIf(), ops.BeginElse()) and matches_end(ops.BeginIf(), ops.EndIf())
    assert not matches_end(ops.BeginIf(), ops.EndWhileLoop())


def test_literals_hold_values_not_spellings():
    assert LoadInteger(5).value == 5
    with pytest.raises(ValueError):
        LoadInteger(-1)
    with pytest.raises(ValueError):
        LoadFloat(float("nan"))
    with pytest.raises(ValueError):
        LoadFloat(-0.0)
    with pytest.raises(ValueError):
        LoadInteger(2 ** 54)
    assert LoadFloat(0.0).value == 0.0


def test_parameters_and_function_arities():
    p = Parameters(3, has_rest=True, default_indices=(0,))
    f = BeginPlainFunction(p)
    assert f.inputs() == 1 and f.outputs() == 1 and f.inner_outputs() == 3
    with pytest.raises(ValueError):
        Parameters(2, has_rest=True, default_indices=(1,))
    with pytest.raises(ValueError):
        Parameters(2, default_indices=(1, 0))
    m = BeginObjectLiteralMethod(Key("m"), Parameters(2), generator=True)
    assert m.inner_outputs() == 3                       # this + 2 params
    assert Context.GENERATOR_FUNCTION in m.opened() and Context.METHOD in m.opened()
    assert Context.GENERATOR_FUNCTION not in BeginObjectLiteralMethod(Key("m")).opened()


def test_variadic_arities():
    c = CallMethod(method=None, num_arguments=3)  # type: ignore[arg-type]
    assert c.inputs() == 4 and c.first_variadic == 1
    assert CreateTemplateString(("a", "b", "c")).inputs() == 2
    with pytest.raises(ValueError):
        CreateTemplateString(())
    assert ConfigureProperty(Key("k"), PropertyFlag.WRITABLE, PropertyType.GETTER_SETTER).inputs() == 3
    assert ConfigureProperty(Key("k"), PropertyFlag.NONE, PropertyType.VALUE).inputs() == 2


def test_destructuring_counts():
    pat = ObjectPattern((
        ObjectProperty(Key("a"), Binding()),
        ObjectProperty(None, Binding(), has_default=True),            # computed key + default: 2 extra inputs
        ObjectProperty(Key("b"), PropertyTarget(Key("x"))),           # member target: 1 extra input, 0 bindings
        ObjectProperty(Key("c"), ArrayPattern((ArrayElement(Binding()), ArrayElement(None), ArrayElement(ComputedTarget())))),
    ), has_rest=True)
    assert num_bindings(pat) == 1 + 1 + 0 + 1 + 1
    assert num_extra_inputs(pat) == 2 + 1 + 2
    d = Destruct(pat)
    assert d.inputs() == 1 + 5 and d.outputs() == 4
    r = DestructAndReassign(pat)
    assert r.inputs() == 1 + 4 + 5
    arr = ArrayPattern((ArrayElement(Binding(), has_default=True), ArrayElement(ElementTarget(0))), rest=Binding())
    assert num_bindings(arr) == 2 and num_extra_inputs(arr) == 2


def test_for_each_header():
    plain = BeginForEachLoop(ForEachKind.OF)
    assert plain.inputs() == 1 and plain.inner_outputs() == 2       # variable + label
    pat = ArrayPattern((ArrayElement(Binding()), ArrayElement(Binding())))
    destr = BeginForEachLoop(ForEachKind.OF, pat)
    assert destr.inner_outputs() == 3
    with pytest.raises(ValueError):
        BeginForEachLoop(ForEachKind.IN, pat)


def test_unary_operator_groups():
    assert UnaryOperator.POST_INC.reassigns_input and UnaryOperator.POST_INC.is_postfix
    assert not UnaryOperator.MINUS.reassigns_input
    assert ops.ARITHMETIC.isdisjoint(ops.BITWISE) and ops.BITWISE.isdisjoint(ops.LOGICAL)
    assert ops.ARITHMETIC | ops.BITWISE | ops.LOGICAL == frozenset(ops.BinaryOperator)
    assert ops.EQUALITY | ops.RELATIONAL == frozenset(ops.Comparator)


def test_operation_classes_are_many_and_named_after_fuzzil():
    names = {cls.__name__ for cls in all_operation_classes()}
    for expected in ("LoadInteger", "BeginObjectLiteral", "EndObjectLiteral", "BeginPlainFunction", "CallMethod",
                     "BeginForLoopCondition", "BeginSwitch", "Destruct", "BeginCodeString", "Probe", "Declare", "LoadBuiltin"):
        assert expected in names
    assert len(names) > 100


def test_operations_are_hashable_and_comparable():
    assert LoadInteger(1) == LoadInteger(1) and hash(LoadInteger(1)) == hash(LoadInteger(1))
    assert LoadInteger(1) != LoadInteger(2)
    assert isinstance(inspect.getdoc(Operation), str)
