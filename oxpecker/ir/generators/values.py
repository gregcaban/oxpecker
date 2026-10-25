"""Value generators and value makers (port of `gen/generators/values.py`).

Primitive literals are `LoadLiteral`s with a drawn `inline` flag, so a value is sometimes a typed
local and sometimes spelled out at its uses. Composite values with an interesting *spelling*
(`[1, , 3]`, `{valueOf() { return 7; }}`, `new Map([[1, 2]])`) are spelled literals too (decision
D3); plain arrays and objects are built from elements so mutations can reach inside them.
"""

from __future__ import annotations

from ...gen.generators.values import BIGINTS, INT32S, NUMBERS, REGEXPS, STRINGS, SYMBOLS
from ...gen.types import T
from ..builder import Builder
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

POOLS = {T.NUMBER: NUMBERS, T.INT32: INT32S, T.BIGINT: BIGINTS, T.STRING: STRINGS, T.BOOLEAN: BOOLEANS,
         T.UNDEFINED: UNDEFINEDS, T.NULL: NULLS, T.SYMBOL: SYMBOLS, T.REGEXP: REGEXPS}


@value_maker(T.NUMBER)
@generator("number", family="values", weight=4, output=T.NUMBER)
def gen_number(b: Builder):
    """A number literal, biased towards boundaries (-0, NaN, 2**31, 2**53, denormals)."""
    return b.literal(T.NUMBER, NUMBERS)


@value_maker(T.INT32)
@generator("int32", family="values", weight=4, output=T.INT32)
def gen_int32(b: Builder):
    """A small integer literal (int32 range), the compiler's favourite representation."""
    return b.literal(T.INT32, INT32S)


@value_maker(T.BIGINT)
@generator("bigint", family="values", weight=2, output=T.BIGINT)
def gen_bigint(b: Builder):
    """A BigInt literal."""
    return b.literal(T.BIGINT, BIGINTS)


@value_maker(T.STRING)
@generator("string", family="values", weight=4, output=T.STRING)
def gen_string(b: Builder):
    """A string literal: empty, numeric-looking, unicode, lone surrogates, property names."""
    return b.literal(T.STRING, STRINGS)


@value_maker(T.BOOLEAN)
@generator("boolean", family="values", weight=2, output=T.BOOLEAN)
def gen_boolean(b: Builder):
    """A boolean literal."""
    return b.literal(T.BOOLEAN, BOOLEANS)


@value_maker(T.UNDEFINED)
@generator("undefined", family="values", weight=1, output=T.UNDEFINED)
def gen_undefined(b: Builder):
    """undefined / void 0."""
    return b.literal(T.UNDEFINED, UNDEFINEDS)


@value_maker(T.NULL)
@generator("null", family="values", weight=1, output=T.NULL)
def gen_null(b: Builder):
    """null."""
    return b.literal(T.NULL, NULLS)


@value_maker(T.SYMBOL)
@generator("symbol", family="values", weight=1, output=T.SYMBOL)
def gen_symbol(b: Builder):
    """A symbol: fresh, registered, or well-known."""
    return b.literal(T.SYMBOL, SYMBOLS)


@value_maker(T.REGEXP)
@generator("regexp", family="values", weight=1, output=T.REGEXP)
def gen_regexp(b: Builder):
    """A regular expression literal."""
    return b.literal(T.REGEXP, REGEXPS)


@value_maker(T.ARRAY)
def make_array(b: Builder) -> int:
    if b.maybe(0.3):
        return b.emit(LoadLiteral(T.ARRAY, b.choice(SPELLED[T.ARRAY])))
    elems = [b.pick_or_make(b.choice(ELEMENT_TYPES)) for _ in range(b.int(0, 4))]
    holes = (b.int(0, len(elems)),) if elems and b.maybe(0.2) else ()
    return b.emit(CreateArray(holes), elems)


@value_maker(T.OBJECT)
def make_object(b: Builder) -> int:
    if b.maybe(0.3):
        return b.emit(LoadLiteral(T.OBJECT, b.choice(SPELLED[T.OBJECT])))
    keys: list[str] = []
    for _ in range(b.int(0, 3)):
        k = b.choice(KEYS)
        if k not in keys:
            keys.append(k)
    return b.emit(CreateObject(tuple(keys)), [b.pick_or_make(b.choice(ELEMENT_TYPES)) for _ in keys])


def _spelled(t: T):
    def make(b: Builder) -> int:
        return b.emit(LoadLiteral(t, b.choice(SPELLED[t])))
    return make


for _t in (T.FUNCTION, T.TYPEDARRAY, T.MAP, T.SET):
    value_maker(_t)(_spelled(_t))


@value_maker(T.UNKNOWN)
def make_unknown(b: Builder) -> int:
    return b.make(b.choice([T.NUMBER, T.INT32, T.STRING, T.BOOLEAN, T.ARRAY, T.OBJECT, T.UNDEFINED, T.NULL,
                            T.BIGINT]))
