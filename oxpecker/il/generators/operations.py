# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/CodeGen/CodeGenerators.swift at commit be2811d00282320381fc8007d64873745772011d
# (operators 2193-2487, ChainedReassignments 3001, NumberComputationGenerator 3355,
# TemplateStringGenerator 1615, ConcatenatedStringGenerator 297). Weights from CodeGeneratorWeights.swift.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Operators, comparisons, assignments, conversions."""

from __future__ import annotations

from hypothesis import strategies as st

from ..builder import Builder
from ..environment import INTERESTING_FLOATS, INTERESTING_INTEGERS, INTERESTING_STRINGS, JS_TYPE_NAMES, Builtins, Methods
from ..ops import BinaryOperator, Comparator, Radix, UnaryOperator
from ..program import Var
from ..strategies import assignable, generator, maybe, pick, variable
from ..types import ANY, BOOLEAN, FUNCTION, INTEGER, NUMBER, OBJECT, STRING, Type

MATH_UNARY = (Methods.MATH_ABS, Methods.MATH_FLOOR, Methods.MATH_SQRT, Methods.MATH_ROUND, Methods.MATH_TRUNC)
MATH_BINARY = (Methods.MATH_IMUL, Methods.MATH_MAX, Methods.MATH_MIN, Methods.MATH_POW)


@generator("unary", family="operations", weight=10, inputs=(ANY,), source="CodeGenerators.swift:2463")
@st.composite
def unary(draw, b: Builder, x: Var) -> Var:
    """A unary operator on a value; increments need an assignable variable."""
    op = draw(st.sampled_from(UnaryOperator))
    if op.reassigns_input:
        x = draw(assignable(b, NUMBER))
    return b.unary(op, x)


@generator("binary", family="operations", weight=40, inputs=(ANY, ANY), source="CodeGenerators.swift:2467")
@st.composite
def binary(draw, b: Builder, lhs: Var, rhs: Var) -> Var:
    """`lhs op rhs` for a drawn binary operator."""
    return b.binary(lhs, rhs, draw(st.sampled_from(BinaryOperator)))


@generator("ternary", family="operations", weight=5, inputs=(ANY, ANY), source="CodeGenerators.swift:2471")
@st.composite
def ternary(draw, b: Builder, lhs: Var, rhs: Var) -> Var:
    """`lhs cmp rhs ? lhs : rhs`."""
    cond = b.compare(lhs, rhs, draw(st.sampled_from(Comparator)))
    return b.ternary(cond, lhs, rhs)


@generator("compare", family="operations", weight=10, inputs=(ANY, ANY), source="CodeGenerators.swift:2580")
@st.composite
def compare(draw, b: Builder, lhs: Var, rhs: Var) -> Var:
    """A relational or equality comparison."""
    return b.compare(lhs, rhs, draw(st.sampled_from(Comparator)))


@generator("update", family="operations", weight=20, source="CodeGenerators.swift:2477")
@st.composite
def update(draw, b: Builder) -> None:
    """`v op= w` on an assignable variable with a value of its type."""
    v = draw(assignable(b))
    w = draw(variable(b, b.type_of(v)))
    b.update(v, w, draw(st.sampled_from(BinaryOperator)))


@generator("reassignment", family="operations", weight=40, source="CodeGenerators.swift:2487")
@st.composite
def reassignment(draw, b: Builder) -> None:
    """`v = w` on an assignable variable with a value of its type (or any value, sometimes)."""
    v = draw(assignable(b))
    w = draw(variable(b, b.type_of(v) if draw(maybe(0.7)) else ANY))
    if w != v:
        b.reassign(v, w)


@generator("chained_reassignments", family="operations", weight=5, source="CodeGenerators.swift:3001")
@st.composite
def chained_reassignments(draw, b: Builder) -> None:
    """A chain `a = b; b = c; ...` over assignable variables, in one of two directions."""
    vs = draw(st.lists(assignable(b), min_size=2, max_size=5))
    forward = draw(st.booleans())
    for x, y in zip(vs, vs[1:]):
        if x != y:
            b.reassign(y, x) if forward else b.reassign(x, y)


@generator("type_test", family="operations", weight=5, inputs=(ANY,), source="CodeGenerators.swift:2193")
@st.composite
def type_test(draw, b: Builder, x: Var) -> Var:
    """`typeof x === "<type name>"`."""
    t = b.typeof(x)
    return b.compare(t, b.load_string(draw(st.sampled_from(JS_TYPE_NAMES))), Comparator.STRICT_EQUAL)


@generator("void", family="operations", weight=1, inputs=(ANY,), source="CodeGenerators.swift:2201")
def void(b: Builder, x: Var) -> Var:
    """`void x`."""
    return b.void(x)


@generator("instance_of", family="operations", weight=5, inputs=(ANY, FUNCTION), source="CodeGenerators.swift:2257")
def instance_of(b: Builder, x: Var, ctor: Var) -> Var:
    """`x instanceof C`."""
    return b.test_instance_of(x, ctor)


@generator("in", family="operations", weight=3, inputs=(OBJECT,), source="CodeGenerators.swift:2263")
@st.composite
def in_(draw, b: Builder, obj: Var) -> Var:
    """`k in obj` for a visible key value."""
    return b.test_in(draw(variable(b)), obj)


@generator("number_computation", family="operations", weight=40, source="CodeGenerators.swift:3355")
@st.composite
def number_computation(draw, b: Builder) -> Var:
    """A few steps of arithmetic, unary operators and Math calls over visible values and fresh literals."""
    math = b.builtin(Builtins.MATH)
    values = draw(st.lists(variable(b), min_size=1, max_size=3))
    values += [b.load_int(draw(st.sampled_from(INTERESTING_INTEGERS))) for _ in range(draw(st.integers(1, 2)))]
    if draw(maybe(0.5)):
        values.append(b.load_float(draw(st.sampled_from(INTERESTING_FLOATS))))
    last = values[-1]
    for step in draw(st.lists(st.integers(0, 2), min_size=1, max_size=7)):
        if step == 0:
            last = b.binary(pick(draw, values), pick(draw, values), draw(st.sampled_from(BinaryOperator)))
        elif step == 1:
            op = draw(st.sampled_from((UnaryOperator.MINUS, UnaryOperator.PLUS, UnaryOperator.BITWISE_NOT, UnaryOperator.LOGICAL_NOT)))
            last = b.unary(op, pick(draw, values))
        else:
            if draw(st.booleans()):
                last = b.call_method(math, draw(st.sampled_from(MATH_UNARY)), [pick(draw, values)])
            else:
                last = b.call_method(math, draw(st.sampled_from(MATH_BINARY)), [pick(draw, values), pick(draw, values)])
        values.append(last)
    return last


INT32_COERCIONS: tuple[tuple[BinaryOperator, int, Radix], ...] = (          # x | 0, x >>> 0, x >> 0, x & 0xffffffff
    (BinaryOperator.BIT_OR, 0, Radix.DEC), (BinaryOperator.U_RSHIFT, 0, Radix.DEC),
    (BinaryOperator.RSHIFT, 0, Radix.DEC), (BinaryOperator.BIT_AND, 0xFFFFFFFF, Radix.HEX),
)


@generator("int32_coerce", family="operations", weight=4, produces=INTEGER)
@st.composite
def int32_coerce(draw, b: Builder) -> Var:
    """`x | 0`, `x >>> 0`, `x >> 0`, `x & 0xffffffff` or `~~x` on a number."""
    x = draw(variable(b, NUMBER))
    if draw(maybe(0.2)):
        return b.unary(UnaryOperator.BITWISE_NOT, b.unary(UnaryOperator.BITWISE_NOT, x))
    op, k, radix = draw(st.sampled_from(INT32_COERCIONS))
    return b.binary(x, b.load_int(k, radix), op)


@generator("template_string", family="operations", weight=1, produces=STRING, source="CodeGenerators.swift:1615")
@st.composite
def template_string(draw, b: Builder) -> Var:
    """A template literal interpolating visible values between drawn string parts."""
    values = draw(st.lists(variable(b), max_size=3))
    parts = [draw(st.sampled_from(INTERESTING_STRINGS)) for _ in range(len(values) + 1)]
    return b.create_template(parts, values)


@generator("concatenated_string", family="operations", weight=2, required=(STRING,), produces=STRING, source="CodeGenerators.swift:297")
@st.composite
def concatenated_string(draw, b: Builder, s: Var) -> Var:
    """`(c ? s : t) + u` over string values: a ConsString whose halves depend on a condition."""
    other = pick(draw, b.pool(STRING).exact)
    cond = draw(variable(b))
    select = b.ternary(cond, s, other)
    third = pick(draw, b.pool(STRING).exact)
    return b.binary(select, third, BinaryOperator.ADD) if draw(st.booleans()) else b.binary(third, select, BinaryOperator.ADD)


@generator("number_conversion", family="operations", weight=3, inputs=(ANY,), produces=NUMBER)
@st.composite
def number_conversion(draw, b: Builder, x: Var) -> Var:
    """`Number(x)`, `parseInt(x)`, `parseFloat(x)` or `+x`."""
    kind = draw(st.integers(0, 3))
    if kind == 0:
        return b.unary(UnaryOperator.PLUS, x)
    ctor = (Builtins.NUMBER, Builtins.PARSE_INT, Builtins.PARSE_FLOAT)[kind - 1]
    return b.call_function(b.builtin(ctor), [x])


@generator("number_to_string", family="operations", weight=3, required=(NUMBER,), produces=STRING)
@st.composite
def number_to_string(draw, b: Builder, n: Var) -> Var:
    """`String(n)`, `n.toString(radix)`, `n.toFixed(d)` or `n.toPrecision(p)`."""
    kind = draw(st.integers(0, 3))
    if kind == 0:
        return b.call_function(b.builtin(Builtins.STRING), [n])
    if kind == 1:
        return b.call_method(n, Methods.NUMBER_TO_STRING, [b.load_int(draw(st.sampled_from((10, 2, 16, 36, 8))))])
    if kind == 2:
        return b.call_method(n, Methods.NUMBER_TO_FIXED, [b.load_int(draw(st.integers(0, 20)))])
    return b.call_method(n, Methods.NUMBER_TO_PRECISION, [b.load_int(draw(st.integers(1, 21)))])
