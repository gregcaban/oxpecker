"""Value generators: interesting literals of every primitive type."""

from __future__ import annotations

from ..builder import ProgramBuilder
from ..registry import generator, value_maker
from ..types import T

NUMBERS = [
    "0", "-0", "1", "-1", "2", "3", "10", "0.5", "0.1", "0.2", "-0.5", "1.5", "NaN", "Infinity", "-Infinity",
    "2147483647", "-2147483648", "2147483648", "-2147483649", "4294967295", "4294967296",
    "9007199254740991", "9007199254740992", "9007199254740993", "1e21", "1e-7", "5e-324", "1e-320",
    "1.7976931348623157e308", "0.30000000000000004", "255", "256", "65535", "65536", "1/3", "-1/3",
    "0x7fffffff", "0xffffffff", "1e3", "123.456", "-123.456",
]
INT32S = ["0", "1", "-1", "2", "3", "7", "10", "100", "255", "256", "1000", "1024", "65535",
          "2147483647", "-2147483648", "-7", "-100"]
BIGINTS = ["0n", "1n", "-1n", "2n", "255n", "9007199254740993n", "18446744073709551616n",
           "-12345678901234567890n", "0x7fffffffffffffffn"]
STRINGS = [
    '""', '"a"', '"abc"', '"1"', '"0"', '"-1"', '"0x10"', '" 1 "', '"1e3"', '"NaN"', '"-0"', '"Infinity"',
    '"true"', '"null"', '"undefined"', '"[object Object]"', '"\\u0000"', '"\\ud83d\\ude00"', '"\\ud800"',
    '"ß"', '"αβγ"', '"\\n"', '"10"', '"1.5"', '"length"', '"constructor"', '"__proto__"', '"toString"',
    '"valueOf"', '"x"', '"y"', '"0.1"', '"  "', '"1,2"', '"a,b"',
]
SYMBOLS = ['Symbol("s")', 'Symbol()', 'Symbol.iterator', 'Symbol.toPrimitive', 'Symbol.toStringTag',
           'Symbol.for("k")', 'Symbol.hasInstance', 'Symbol.species']
REGEXPS = ['/a+/g', '/^(\\d+)$/', '/x/y', '/[A-Z]/i', '/(?:)/', '/(a)(b)?/', '/\\w+/u', '/./s', '/a|b/']


def number_literal(b: ProgramBuilder, _t: T = T.NUMBER) -> str:
    return b.choice(NUMBERS)


def int32_literal(b: ProgramBuilder, _t: T = T.INT32) -> str:
    return b.choice(INT32S)


def string_literal(b: ProgramBuilder, _t: T = T.STRING) -> str:
    return b.choice(STRINGS)


def literal(b: ProgramBuilder, t: T) -> str:
    """A literal of type t, for use in expressions without creating a variable."""
    if t is T.NUMBER:
        return number_literal(b)
    if t is T.INT32:
        return int32_literal(b)
    if t is T.BIGINT:
        return b.choice(BIGINTS)
    if t is T.STRING:
        return string_literal(b)
    if t is T.BOOLEAN:
        return b.choice(["true", "false"])
    if t is T.UNDEFINED:
        return b.choice(["undefined", "void 0"])
    if t is T.NULL:
        return "null"
    if t is T.SYMBOL:
        return b.choice(SYMBOLS)
    if t is T.REGEXP:
        return b.choice(REGEXPS)
    if t is T.ARRAY:
        return b.choice(["[]", "[1, 2, 3]", "[1, , 3]", '["a", "b"]', "[0.5, -0, NaN]", "[[1], [2]]"])
    if t is T.OBJECT:
        return b.choice(["{}", "{a: 1}", '{a: 1, b: "x"}', "{0: 1, 1: 2, length: 2}", "{valueOf() { return 7; }}"])
    if t is T.FUNCTION:
        return b.choice(["function (x) { return x; }", "(x) => x", "function () { return arguments.length; }",
                         "Math.max", "Object", "Array"])
    if t is T.TYPEDARRAY:
        return b.choice(["new Int32Array(4)", "new Float64Array([0.5, 1.5])", "new Uint8Array([255, 256, -1])",
                         "new Int8Array(2)", "new Uint8ClampedArray([300, -5])"])
    if t is T.MAP:
        return b.choice(["new Map()", "new Map([[1, 2]])", 'new Map([["a", 1], [NaN, 2]])'])
    if t is T.SET:
        return b.choice(["new Set()", "new Set([1, 2, 2, 3])", 'new Set(["a", -0, 0])'])
    return number_literal(b)


# -- value makers: used by pick_or_make when no suitable variable is in scope ----------------

@value_maker(T.NUMBER)
@generator("number", family="values", weight=4, output=T.NUMBER)
def gen_number(b: ProgramBuilder):
    """A number literal, biased towards boundaries (-0, NaN, 2**31, 2**53, denormals)."""
    return b.fresh(T.NUMBER, number_literal(b))


@value_maker(T.INT32)
@generator("int32", family="values", weight=4, output=T.INT32)
def gen_int32(b: ProgramBuilder):
    """A small integer literal (int32 range), the compiler's favourite representation."""
    return b.fresh(T.INT32, int32_literal(b))


@value_maker(T.BIGINT)
@generator("bigint", family="values", weight=2, output=T.BIGINT)
def gen_bigint(b: ProgramBuilder):
    """A BigInt literal."""
    return b.fresh(T.BIGINT, b.choice(BIGINTS))


@value_maker(T.STRING)
@generator("string", family="values", weight=4, output=T.STRING)
def gen_string(b: ProgramBuilder):
    """A string literal: empty, numeric-looking, unicode, lone surrogates, property names."""
    return b.fresh(T.STRING, string_literal(b))


@value_maker(T.BOOLEAN)
@generator("boolean", family="values", weight=2, output=T.BOOLEAN)
def gen_boolean(b: ProgramBuilder):
    """A boolean literal."""
    return b.fresh(T.BOOLEAN, b.choice(["true", "false"]))


@value_maker(T.UNDEFINED)
@generator("undefined", family="values", weight=1, output=T.UNDEFINED)
def gen_undefined(b: ProgramBuilder):
    """undefined / void 0."""
    return b.fresh(T.UNDEFINED, b.choice(["undefined", "void 0"]))


@value_maker(T.NULL)
@generator("null", family="values", weight=1, output=T.NULL)
def gen_null(b: ProgramBuilder):
    """null."""
    return b.fresh(T.NULL, "null")


@value_maker(T.SYMBOL)
@generator("symbol", family="values", weight=1, output=T.SYMBOL)
def gen_symbol(b: ProgramBuilder):
    """A symbol: fresh, registered, or well-known."""
    return b.fresh(T.SYMBOL, b.choice(SYMBOLS))


@value_maker(T.REGEXP)
@generator("regexp", family="values", weight=1, output=T.REGEXP)
def gen_regexp(b: ProgramBuilder):
    """A regular expression literal."""
    return b.fresh(T.REGEXP, b.choice(REGEXPS))


@value_maker(T.UNKNOWN)
def make_unknown(b: ProgramBuilder):
    t = b.choice([T.NUMBER, T.INT32, T.STRING, T.BOOLEAN, T.ARRAY, T.OBJECT, T.UNDEFINED, T.NULL, T.BIGINT])
    return b.make_value(b, t)
