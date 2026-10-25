"""Operator generators (port of `gen/generators/operations.py`).

No parenthesisation here: `-1 ** 2`, `--1` and friends are the lifter's problem (decision D12).
"""

from __future__ import annotations

from hypothesis import strategies as st

from ...gen.types import T
from ..builder import Builder, maybe, pick_or_make, target
from ..ops import ARITH, BITWISE, COMPARE, LOGICAL, CallFunction, CallMethod, CompoundAssign, Conditional, GetProperty, Update
from ..registry import generator

UNARY = ["-", "+", "!", "~", "typeof", "void"]
NUMERIC_TYPES = [T.NUMBER, T.INT32, T.STRING, T.BOOLEAN, T.UNDEFINED, T.NULL, T.OBJECT, T.ARRAY]


@st.composite
def operand(draw, b: Builder, prefer: T = T.NUMBER) -> int:
    """Mostly numbers; sometimes something that converts interestingly. Shrinks towards `prefer`."""
    t = draw(st.sampled_from(NUMERIC_TYPES)) if draw(maybe(0.25)) else prefer
    return draw(pick_or_make(b, t))


@generator("unary", family="operations", weight=3, output=T.UNKNOWN)
@st.composite
def unary(draw, b: Builder):
    """Unary operator applied to an existing value or literal."""
    op = draw(st.sampled_from(UNARY))
    return b.unary(op, draw(operand(b, draw(st.sampled_from([T.NUMBER, T.INT32, T.STRING])))))


@generator("binary_arith", family="operations", weight=5, output=T.NUMBER)
@st.composite
def binary_arith(draw, b: Builder):
    """Arithmetic on two operands; `+` doubles as concatenation when a string sneaks in."""
    op = draw(st.sampled_from(ARITH))
    return b.binary(op, draw(operand(b)), draw(operand(b)))


@generator("binary_bitwise", family="operations", weight=3, output=T.INT32)
@st.composite
def binary_bitwise(draw, b: Builder):
    """Bitwise/shift operators, which force int32 conversion."""
    op = draw(st.sampled_from(BITWISE))
    return b.binary(op, draw(operand(b)), draw(operand(b, T.INT32)))


@generator("int32_coerce", family="operations", weight=2, output=T.INT32)
@st.composite
def int32_coerce(draw, b: Builder):
    """`x | 0`, `x >>> 0`, `~~x` on numbers near the int32 boundaries."""
    x = draw(pick_or_make(b, T.NUMBER))
    form = draw(st.sampled_from(["|0", ">>>0", "~~", ">>0", "&0xffffffff"]))
    if form == "~~":
        return b.unary("~", b.unary("~", x))
    op = {"|0": "|", ">>>0": ">>>", ">>0": ">>", "&0xffffffff": "&"}[form]
    return b.binary(op, x, b.const(T.INT32, "0xffffffff" if op == "&" else "0"))


@generator("compare", family="operations", weight=3, output=T.BOOLEAN)
@st.composite
def compare(draw, b: Builder):
    """Relational or (strict) equality comparison, with mixed types some of the time."""
    op = draw(st.sampled_from(COMPARE))
    left = draw(operand(b, draw(st.sampled_from([T.NUMBER, T.STRING]))))
    return b.binary(op, left, draw(operand(b, draw(st.sampled_from([T.NUMBER, T.STRING])))))


@generator("logical", family="operations", weight=2, output=T.UNKNOWN)
@st.composite
def logical(draw, b: Builder):
    """Short-circuit operators returning one of their operands."""
    op = draw(st.sampled_from(LOGICAL))
    return b.binary(op, draw(pick_or_make(b, T.UNKNOWN)), draw(pick_or_make(b, T.UNKNOWN)))


@generator("compound_assign", family="operations", weight=4)
@st.composite
def compound_assign(draw, b: Builder):
    """`x op= y` on an existing variable, possibly changing its type."""
    op = draw(st.sampled_from(ARITH + BITWISE))
    t = draw(target(b, T.NUMBER))
    b.emit(CompoundAssign(op), (t, draw(operand(b))))


@generator("update", family="operations", weight=4)
@st.composite
def update(draw, b: Builder):
    """Prefix/postfix ++/-- on an existing variable, using the result some of the time."""
    t = draw(target(b, T.NUMBER))
    b.emit(Update(draw(st.sampled_from(["++", "--"])), draw(maybe()), value=draw(maybe())), (t,))


@generator("conditional", family="operations", weight=2, output=T.UNKNOWN)
@st.composite
def conditional(draw, b: Builder):
    """`c ? a : b` with a non-boolean condition some of the time."""
    cond = draw(pick_or_make(b, T.UNKNOWN if draw(maybe(0.4)) else T.BOOLEAN))
    return b.emit(Conditional(), (cond, draw(pick_or_make(b, T.UNKNOWN)), draw(pick_or_make(b, T.UNKNOWN))))


@generator("comma", family="operations", weight=1, output=T.UNKNOWN)
@st.composite
def comma(draw, b: Builder):
    """Comma expression; the value is the last operand."""
    x, y = draw(pick_or_make(b, T.UNKNOWN)), draw(pick_or_make(b, T.UNKNOWN))
    return b.eval("({0}, {1})", x, y, out=T.UNKNOWN, pure=True)


@generator("template_literal", family="operations", weight=2, output=T.STRING)
@st.composite
def template_literal(draw, b: Builder):
    """Template literal interpolating existing values (ToString on each)."""
    parts = draw(st.lists(pick_or_make(b, T.UNKNOWN), min_size=1, max_size=3))
    prefix = draw(st.sampled_from(["v=", "", "<"]))
    # `${` + placeholder `{k}` + `}`: the lifter substitutes `{k}` and leaves the template's own braces.
    code = "`" + prefix + "-".join("${{%d}}" % k for k in range(len(parts))) + "`"
    return b.eval(code, *parts, out=T.STRING, pure=True)


@generator("string_concat", family="operations", weight=3, output=T.STRING)
@st.composite
def string_concat(draw, b: Builder):
    """`s + x` where x may be a number, object, or another string (ConsString territory)."""
    s = draw(pick_or_make(b, T.STRING))
    x = draw(pick_or_make(b, draw(st.sampled_from([T.STRING, T.NUMBER, T.OBJECT, T.ARRAY]))))
    return b.binary("+", s, x)


@generator("number_conversion", family="operations", weight=2, output=T.NUMBER)
@st.composite
def number_conversion(draw, b: Builder):
    """Explicit conversions: Number(x), parseInt, parseFloat, +x, parseInt(x, radix)."""
    x = draw(pick_or_make(b, draw(st.sampled_from([T.STRING, T.NUMBER, T.BOOLEAN, T.OBJECT]))))
    form = draw(st.sampled_from(["Number", "parseInt", "parseFloat", "+", "parseInt/radix"]))
    if form == "+":
        return b.unary("+", x)
    if form == "parseInt/radix":
        radix = b.const(T.INT32, draw(st.sampled_from(["2", "8", "16", "36"])))
        return b.emit(CallFunction(), (b.builtin("parseInt", T.FUNCTION), x, radix), t=T.NUMBER)
    return b.emit(CallFunction(), (b.builtin(form, T.FUNCTION), x), t=T.NUMBER)


@generator("number_to_string", family="operations", weight=2, output=T.STRING)
@st.composite
def number_to_string(draw, b: Builder):
    """Number formatting: String(x), x.toString(radix), x.toFixed, x.toPrecision, x.toExponential."""
    x = draw(pick_or_make(b, T.NUMBER))
    form = draw(st.sampled_from(["String", "toString", "toFixed", "toPrecision", "toExponential"]))
    arg = {"toString": st.sampled_from(["2", "10", "16", "36"]), "toFixed": st.integers(0, 5).map(str),
           "toPrecision": st.integers(1, 6).map(str)}.get(form)
    arg = draw(arg) if arg is not None else None
    with b.try_() as t:
        if form == "String":
            b.emit(CallFunction(), (b.builtin("String", T.FUNCTION), x), t=T.STRING)
        elif arg is None:
            b.emit(CallMethod(form), (x,), t=T.STRING)
        else:
            b.emit(CallMethod(form), (x, b.const(T.INT32, arg)), t=T.STRING)
        with t.catch() as e:
            b.emit(GetProperty("name"), (e,))
            b.probe("conv")
