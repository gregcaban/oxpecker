# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/CodeGen/CodeGenerators.swift at commit be2811d00282320381fc8007d64873745772011d
# (the value generators, lines 281-760, and BuiltinGenerator 1646), with the value pools of
# Sources/Fuzzilli/Environment/JavaScriptEnvironment.swift. Weights from CodeGeneratorWeights.swift.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Value generators: literals and simple objects that can be made with no visible variables.
Each is also the value maker for its type (the fallback of `variable(t)` on an empty pool)."""

from __future__ import annotations

from hypothesis import strategies as st

from ..builder import Builder
from ..environment import (
    BUILTIN_POOL,
    INTERESTING_FLOATS,
    INTERESTING_INTEGERS,
    INTERESTING_STRINGS,
    REGEXP_PATTERNS,
    TYPED_ARRAY_CONSTRUCTORS,
    Builtins,
)
from ..ops import FloatStyle, Parameters, Radix, RegExpFlag, UnaryOperator
from ..program import Var
from ..strategies import generator, maybe, pick, value_maker, variable
from ..types import (
    ANY,
    ARRAY,
    BIGINT,
    BOOLEAN,
    FLOAT,
    FUNCTION,
    INTEGER,
    NULL,
    NUMBER,
    OBJECT,
    STRING,
    SYMBOL,
    UNDEFINED,
    Type,
)

SMALL_INTEGERS = st.integers(0, 0x10000)
REGEXP_FLAG_SETS: tuple[RegExpFlag, ...] = (
    RegExpFlag.NONE, RegExpFlag.GLOBAL, RegExpFlag.IGNORE_CASE, RegExpFlag.GLOBAL | RegExpFlag.IGNORE_CASE,
    RegExpFlag.MULTILINE, RegExpFlag.STICKY, RegExpFlag.DOT_ALL, RegExpFlag.UNICODE,
)
INSTANCE_CONSTRUCTORS = (Builtins.OBJECT, Builtins.ARRAY, Builtins.MAP, Builtins.SET, Builtins.WEAK_MAP,
                         Builtins.WEAK_SET, Builtins.DATE)


@value_maker(INTEGER)
@value_maker(NUMBER)
@value_maker(ANY)
@generator("integer", family="values", weight=20, rank=0, produces=INTEGER, source="CodeGenerators.swift:281")
@st.composite
def integer(draw, b: Builder) -> Var:
    """An integer literal: an interesting value or a small one, in a drawn radix."""
    value = draw(st.one_of(st.sampled_from(INTERESTING_INTEGERS), SMALL_INTEGERS))
    return b.load_int(value, draw(st.sampled_from(Radix)))


@value_maker(BIGINT)
@generator("bigint", family="values", weight=4, produces=BIGINT, source="CodeGenerators.swift:285")
@st.composite
def bigint(draw, b: Builder) -> Var:
    """A BigInt literal."""
    return b.load_bigint(draw(st.one_of(st.sampled_from(INTERESTING_INTEGERS), SMALL_INTEGERS)))


@value_maker(FLOAT)
@generator("float", family="values", weight=10, rank=1, produces=FLOAT, source="CodeGenerators.swift:289")
@st.composite
def float_(draw, b: Builder) -> Var:
    """A float literal, plain or in exponent form."""
    value = draw(st.one_of(st.sampled_from(INTERESTING_FLOATS), st.floats(0.0, 1e6, allow_nan=False, allow_infinity=False)))
    return b.load_float(value, draw(st.sampled_from(FloatStyle)))


@generator("negative_number", family="values", weight=6, produces=NUMBER)
@st.composite
def negative_number(draw, b: Builder) -> Var:
    """`-x` of a number literal (JavaScript has no negative literals): -1, -0, -1.5, -0x10."""
    if draw(maybe(0.3)):
        x = b.load_float(draw(st.sampled_from(INTERESTING_FLOATS)))
    else:
        x = b.load_int(draw(st.sampled_from(INTERESTING_INTEGERS)), draw(st.sampled_from(Radix)))
    return b.unary(UnaryOperator.MINUS, x)


@generator("special_number", family="values", weight=4, produces=NUMBER)
@st.composite
def special_number(draw, b: Builder) -> Var:
    """NaN, Infinity or -Infinity."""
    v = b.builtin(draw(st.sampled_from((Builtins.NAN, Builtins.INFINITY))))
    if draw(maybe(0.3)):
        v = b.unary(UnaryOperator.MINUS, v)
    return v


@value_maker(STRING)
@generator("string", family="values", weight=10, rank=2, produces=STRING, source="CodeGenerators.swift:293")
@st.composite
def string(draw, b: Builder) -> Var:
    """A string literal: empty, numeric-looking, unicode, a lone surrogate, a property name."""
    return b.load_string(draw(st.one_of(st.sampled_from(INTERESTING_STRINGS), st.text(max_size=6))))


@value_maker(BOOLEAN)
@generator("boolean", family="values", weight=2, rank=3, produces=BOOLEAN, source="CodeGenerators.swift:314")
@st.composite
def boolean(draw, b: Builder) -> Var:
    """A boolean literal."""
    return b.load_bool(draw(st.booleans()))


@value_maker(UNDEFINED)
@generator("undefined", family="values", weight=1, produces=UNDEFINED, source="CodeGenerators.swift:319")
def undefined(b: Builder) -> Var:
    """`undefined`."""
    return b.load_undefined()


@value_maker(NULL)
@generator("null", family="values", weight=1, produces=NULL, source="CodeGenerators.swift:324")
def null(b: Builder) -> Var:
    """`null`."""
    return b.load_null()


@value_maker(ARRAY)
@generator("array", family="values", weight=10, produces=ARRAY, source="CodeGenerators.swift:329")
@st.composite
def array(draw, b: Builder) -> Var:
    """An array literal of visible values."""
    if not b.visible():
        return b.create_array([])
    return b.create_array(draw(st.lists(variable(b), max_size=5)))


@generator("int_array", family="values", weight=10, produces=ARRAY, source="CodeGenerators.swift:341")
@st.composite
def int_array(draw, b: Builder) -> Var:
    """An array literal of integers."""
    return b.create_int_array(draw(st.lists(st.one_of(st.sampled_from(INTERESTING_INTEGERS), st.integers(-10, 10)), min_size=1, max_size=10)))


@generator("float_array", family="values", weight=10, produces=ARRAY, source="CodeGenerators.swift:346")
@st.composite
def float_array(draw, b: Builder) -> Var:
    """An array literal of floats."""
    return b.create_float_array(draw(st.lists(st.sampled_from(INTERESTING_FLOATS), min_size=1, max_size=10)))


@generator("regexp", family="values", weight=5, produces=Type.object("RegExp"), source="CodeGenerators.swift:622")
@st.composite
def regexp(draw, b: Builder) -> Var:
    """A regular expression literal from the pattern table, with drawn flags."""
    pattern, incompatible = draw(st.sampled_from(REGEXP_PATTERNS))
    flags = draw(st.sampled_from(REGEXP_FLAG_SETS))
    return b.load_regexp(pattern, flags & ~incompatible)


@value_maker(OBJECT)
@generator("builtin_object_instance", family="values", weight=10, produces=OBJECT, source="CodeGenerators.swift:394")
@st.composite
def builtin_object_instance(draw, b: Builder) -> Var:
    """`new Object()`, `new Array(n)`, `new Map()`, `new Set()`, `new WeakMap()`, `new WeakSet()`, `new Date()`."""
    ctor = draw(st.sampled_from(INSTANCE_CONSTRUCTORS))
    args = [b.load_int(draw(st.integers(0, 16)))] if ctor is Builtins.ARRAY else []
    return b.construct(b.builtin(ctor), args)


@generator("typed_array", family="values", weight=20, produces=Type.object("TypedArray"), source="CodeGenerators.swift:450")
@st.composite
def typed_array(draw, b: Builder) -> Var:
    """A typed array of a drawn kind and length."""
    return b.construct(b.builtin(draw(st.sampled_from(TYPED_ARRAY_CONSTRUCTORS))), [b.load_int(draw(st.integers(0, 64)))])


@generator("trivial_function", family="values", weight=10, produces=FUNCTION, source="CodeGenerators.swift:748", block=True)
@st.composite
def trivial_function(draw, b: Builder) -> Var:
    """A function with no parameters returning a visible value."""
    with b.plain_function(Parameters(0)) as (f, _):
        if b.visible():
            b.do_return(pick(draw, b.visible()))
    return f


FUNCTION_BUILTINS = (Builtins.PARSE_INT, Builtins.PARSE_FLOAT, Builtins.STRING, Builtins.NUMBER, Builtins.BOOLEAN,
                     Builtins.OBJECT, Builtins.ARRAY, Builtins.IS_NAN, Builtins.MAP, Builtins.ERROR)


@value_maker(FUNCTION)
@generator("builtin_function", family="values", weight=4, produces=FUNCTION)
@st.composite
def builtin_function(draw, b: Builder) -> Var:
    """A builtin function or constructor as a value: the function maker that needs no block."""
    return b.builtin(draw(st.sampled_from(FUNCTION_BUILTINS)))


@generator("builtin", family="values", weight=10, source="CodeGenerators.swift:1646")
@st.composite
def builtin(draw, b: Builder) -> Var:
    """A reference to a global builtin."""
    return b.builtin(draw(st.sampled_from(BUILTIN_POOL)))


@value_maker(SYMBOL)
@generator("symbol", family="values", weight=2, produces=SYMBOL)
@st.composite
def symbol(draw, b: Builder) -> Var:
    """A symbol: fresh, registered, or well-known."""
    sym = b.builtin(Builtins.SYMBOL)
    kind = draw(st.integers(0, 2))
    if kind == 0:
        return b.call_function(sym, [b.load_string(draw(st.sampled_from(INTERESTING_STRINGS)))])
    if kind == 1:
        from ..environment import Methods
        return b.call_method(sym, Methods.SYMBOL_CTOR_FOR, [b.load_string(draw(st.sampled_from(INTERESTING_STRINGS)))])
    from ..environment import Keys
    return b.get_property(sym, draw(st.sampled_from((Keys.ITERATOR, Keys.TO_PRIMITIVE, Keys.HAS_INSTANCE, Keys.TO_STRING_TAG))))
