"""Value generators and value makers (port of `gen/generators/values.py`).

As *statements* (generators), these declare a variable -- `var n1 = -0.5;` -- which later statements
read and assign (`compound_assign`, `update`), as today's generators do. As *value makers* (a value
made inside an expression), they emit a temporary literal, which the lifter spells out at its uses
unless its `keep` flag (drawn after generation) pins it. Composite values with an interesting
*spelling* (`[1, , 3]`, `{valueOf() { return 7; }}`, `new Map([[1, 2]])`) are spelled literals too
(decision D3); plain arrays and objects are built from elements so mutations can reach inside.
"""

from __future__ import annotations

from hypothesis import strategies as st

from ...gen.generators.values import BIGINTS, INT32S, NUMBERS, REGEXPS, STRINGS, SYMBOLS
from ...gen.types import T
from ..builder import Builder, literal, make, maybe, pick_or_make
from ..ops import CreateArray, CreateObject, LoadLiteral
from ..registry import generator, value_maker

BOOLEANS = ["true", "false"]
UNDEFINEDS = ["undefined", "void 0"]
NULLS = ["null"]
ELEMENT_TYPES = [T.INT32, T.NUMBER, T.STRING, T.BOOLEAN, T.UNDEFINED, T.NULL]
KEYS = ["a", "b", "x", "y", "length", "0", "1", "valueOf", "__proto__", "constructor"]

SPELLED = {
    T.ARRAY: ["[]", "[1, , 3]", "[[1], [2]]", "[0.5, -0, NaN]"],
    T.OBJECT: ["{}", "{0: 1, 1: 2, length: 2}", "{valueOf() { return 7; }}", "{toString() { return \"4\"; }}"],
    T.FUNCTION: ["function (x) { return x; }", "(x) => x", "function () { return arguments.length; }",
                 "Math.max", "Object", "Array"],
    T.TYPEDARRAY: ["new Int32Array(4)", "new Float64Array([0.5, 1.5])", "new Uint8Array([255, 256, -1])",
                   "new Int8Array(2)", "new Uint8ClampedArray([300, -5])"],
    T.MAP: ["new Map()", "new Map([[1, 2]])", 'new Map([["a", 1], [NaN, 2]])'],
    T.SET: ["new Set()", "new Set([1, 2, 2, 3])", 'new Set(["a", -0, 0])'],
}


@value_maker(T.NUMBER)
def number_value(b: Builder):
    """A number literal, biased towards boundaries (-0, NaN, 2**31, 2**53, denormals)."""
    return literal(b, T.NUMBER, NUMBERS)


@generator("number", family="values", weight=4, output=T.NUMBER)
@st.composite
def number(draw, b: Builder):
    """A number literal, biased towards boundaries (-0, NaN, 2**31, 2**53, denormals)."""
    return b.declare(draw(number_value(b)))


@value_maker(T.INT32)
def int32_value(b: Builder):
    """A small integer literal (int32 range), the compiler's favourite representation."""
    return literal(b, T.INT32, INT32S)


@generator("int32", family="values", weight=4, output=T.INT32)
@st.composite
def int32(draw, b: Builder):
    """A small integer literal (int32 range), the compiler's favourite representation."""
    return b.declare(draw(int32_value(b)))


@value_maker(T.BIGINT)
def bigint_value(b: Builder):
    """A BigInt literal."""
    return literal(b, T.BIGINT, BIGINTS)


@generator("bigint", family="values", weight=2, output=T.BIGINT)
@st.composite
def bigint(draw, b: Builder):
    """A BigInt literal."""
    return b.declare(draw(bigint_value(b)))


@value_maker(T.STRING)
def string_value(b: Builder):
    """A string literal: empty, numeric-looking, unicode, lone surrogates, property names."""
    return literal(b, T.STRING, STRINGS)


@generator("string", family="values", weight=4, output=T.STRING)
@st.composite
def string(draw, b: Builder):
    """A string literal: empty, numeric-looking, unicode, lone surrogates, property names."""
    return b.declare(draw(string_value(b)))


@value_maker(T.BOOLEAN)
def boolean_value(b: Builder):
    """A boolean literal."""
    return literal(b, T.BOOLEAN, BOOLEANS)


@generator("boolean", family="values", weight=2, output=T.BOOLEAN)
@st.composite
def boolean(draw, b: Builder):
    """A boolean literal."""
    return b.declare(draw(boolean_value(b)))


@value_maker(T.UNDEFINED)
def undefined_value(b: Builder):
    """undefined / void 0."""
    return literal(b, T.UNDEFINED, UNDEFINEDS)


@generator("undefined", family="values", weight=1, output=T.UNDEFINED)
@st.composite
def undefined(draw, b: Builder):
    """undefined / void 0."""
    return b.declare(draw(undefined_value(b)))


@value_maker(T.NULL)
def null_value(b: Builder):
    """null."""
    return literal(b, T.NULL, NULLS)


@generator("null", family="values", weight=1, output=T.NULL)
@st.composite
def null(draw, b: Builder):
    """null."""
    return b.declare(draw(null_value(b)))


@value_maker(T.SYMBOL)
def symbol_value(b: Builder):
    """A symbol: fresh, registered, or well-known."""
    return literal(b, T.SYMBOL, SYMBOLS)


@generator("symbol", family="values", weight=1, output=T.SYMBOL)
@st.composite
def symbol(draw, b: Builder):
    """A symbol: fresh, registered, or well-known."""
    return b.declare(draw(symbol_value(b)))


@value_maker(T.REGEXP)
def regexp_value(b: Builder):
    """A regular expression literal."""
    return literal(b, T.REGEXP, REGEXPS)


@generator("regexp", family="values", weight=1, output=T.REGEXP)
@st.composite
def regexp(draw, b: Builder):
    """A regular expression literal."""
    return b.declare(draw(regexp_value(b)))


@value_maker(T.ARRAY)
@st.composite
def array(draw, b: Builder) -> int:
    if draw(maybe(0.3)):
        return draw(literal(b, T.ARRAY, SPELLED[T.ARRAY]))
    elems = draw(st.lists(st.sampled_from(ELEMENT_TYPES).flatmap(lambda t: pick_or_make(b, t)), max_size=4))
    holes = (draw(st.integers(0, len(elems))),) if elems and draw(maybe(0.2)) else ()
    return b.emit(CreateArray(holes), elems)


@value_maker(T.OBJECT)
@st.composite
def object_(draw, b: Builder) -> int:
    if draw(maybe(0.3)):
        return draw(literal(b, T.OBJECT, SPELLED[T.OBJECT]))
    # (key, value) pairs in ONE list, so deleting a property deletes its value too (rule 1). No
    # `unique_by`: it would discard a duplicate after its value was emitted; a repeated key just
    # leaves an unused value behind, which is still a valid program.
    pairs = draw(st.lists(st.tuples(st.sampled_from(KEYS),
                                    st.sampled_from(ELEMENT_TYPES).flatmap(lambda t: pick_or_make(b, t))),
                          max_size=3))
    first: dict[str, int] = {}            # first occurrence of each key wins
    for k, v in pairs:
        first.setdefault(k, v)
    return b.emit(CreateObject(tuple(first)), list(first.values()))


def _spelled(t: T):
    def spelled(b: Builder):
        return literal(b, t, SPELLED[t])
    spelled.__doc__ = f"A spelled {t.value} value."
    return spelled


for _t in (T.FUNCTION, T.TYPEDARRAY, T.MAP, T.SET):
    value_maker(_t)(_spelled(_t))


@value_maker(T.UNKNOWN)
def unknown(b: Builder):
    """A value of some type."""
    return st.sampled_from([T.NUMBER, T.INT32, T.STRING, T.BOOLEAN, T.ARRAY, T.OBJECT, T.UNDEFINED, T.NULL,
                            T.BIGINT]).flatmap(lambda t: make(b, t))
