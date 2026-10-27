"""Java interop generators: values crossing the JS/Java boundary through the harness's `J` object
(harness-java/.../JavaProbe.java, registered as `Builtins.J` with `Methods.J_*`). Every `J.*`
call records the Java runtime class and value it received, so Integer-vs-Double and conversion
differences between the modes surface as `java-types` findings."""

from __future__ import annotations

from typing import Callable

from hypothesis import strategies as st

from ..builder import Builder
from ..environment import INTERESTING_FLOATS, Builtins, Keys, Methods
from ..ops import BinaryOperator, Comparator, ForEachKind, Parameters, UnaryOperator
from ..program import Var
from ..strategies import declaration_kinds, generator, maybe, variable
from ..types import ANY, ARRAY, BIGINT, BOOLEAN, FUNCTION, NUMBER, STRING

TYPED_PARAMETERS = (
    (Methods.J_TAKES_INT, NUMBER), (Methods.J_TAKES_LONG, NUMBER), (Methods.J_TAKES_SHORT, NUMBER),
    (Methods.J_TAKES_BYTE, NUMBER), (Methods.J_TAKES_CHAR, STRING), (Methods.J_TAKES_FLOAT, NUMBER),
    (Methods.J_TAKES_DOUBLE, NUMBER), (Methods.J_TAKES_BOOLEAN, BOOLEAN), (Methods.J_TAKES_STRING, STRING),
    (Methods.J_TAKES_INTEGER, NUMBER), (Methods.J_TAKES_NUMBER, NUMBER), (Methods.J_TAKES_BIG_INTEGER, BIGINT),
    (Methods.J_TAKES_CHAR_SEQUENCE, STRING),
)
RETURNING = (Methods.J_AN_INT, Methods.J_A_LONG, Methods.J_MAX_LONG, Methods.J_A_FLOAT, Methods.J_A_DOUBLE, Methods.J_NEG_ZERO,
             Methods.J_NAN, Methods.J_A_SHORT, Methods.J_A_BYTE, Methods.J_A_CHAR, Methods.J_BOXED_INT, Methods.J_BOXED_LONG,
             Methods.J_BIG_INTEGER, Methods.J_A_STRING, Methods.J_NOTHING, Methods.J_BUMP)
EDGE_INTEGERS = (0, 1, 127, 128, 255, 256, 65536, 2 ** 31 - 1, 2 ** 31, 2 ** 32 - 1, 2 ** 53, 2 ** 53 - 1)
EDGE_FLOATS = (0.0, 0.1, 1.5, 1e21, 0.5)


@st.composite
def edge_number(draw, b: Builder) -> Var:
    """An integer or float at a representation boundary, negative sometimes; NaN or Infinity sometimes."""
    kind = draw(st.integers(0, 3))
    if kind == 0:
        v = b.load_int(draw(st.sampled_from(EDGE_INTEGERS)))
    elif kind == 1:
        v = b.load_float(draw(st.sampled_from(EDGE_FLOATS)))
    elif kind == 2:
        v = b.builtin(draw(st.sampled_from((Builtins.NAN, Builtins.INFINITY))))
    else:
        v = b.load_int(draw(st.sampled_from(EDGE_INTEGERS)))
        return b.unary(UnaryOperator.MINUS, v)
    if draw(maybe(0.3)):
        v = b.unary(UnaryOperator.MINUS, v)
    return v


def recorded(b: Builder, make: Callable[[], Var]) -> None:
    """`var r = <J call>` inside a top-level try/catch whose catch records the conversion error."""
    with b.try_() as t:
        b.declare(make())
        with t.catch() as e:
            b.declare(b.get_property(e, Keys.NAME))


@generator("java_typed_call", family="java-interop", weight=6, block=True)
@st.composite
def java_typed_call(draw, b: Builder) -> None:
    """Pass a value to a Java method with a primitive or boxed parameter type; Rhino converts it."""
    method, t = draw(st.sampled_from(TYPED_PARAMETERS))
    arg = draw(edge_number(b)) if t == NUMBER and draw(maybe(0.5)) else draw(variable(b, t if draw(maybe(0.7)) else ANY))
    j = b.builtin(Builtins.J)
    recorded(b, lambda: b.call_method(j, method, [arg]))


@generator("java_echo", family="java-interop", weight=6, inputs=(ANY,))
@st.composite
def java_echo(draw, b: Builder, v: Var) -> Var:
    """`J.echo(x)`, `J.typeOf(x)`, `J.echo(x) === x`: which Java class the value arrives as."""
    j = b.builtin(Builtins.J)
    kind = draw(st.integers(0, 2))
    if kind == 0:
        return b.declare(b.call_method(j, Methods.J_ECHO, [v]))
    if kind == 1:
        return b.declare(b.call_method(j, Methods.J_TYPE_OF, [v]))
    return b.declare(b.compare(b.call_method(j, Methods.J_ECHO, [v]), v, Comparator.STRICT_EQUAL))


ARITH_FORMS: tuple[Callable[[Builder, Var, Var], Var], ...] = (
    lambda b, x, y: b.binary(x, y, BinaryOperator.ADD),
    lambda b, x, y: b.binary(x, y, BinaryOperator.MUL),
    lambda b, x, y: b.binary(x, y, BinaryOperator.SUB),
    lambda b, x, y: b.binary(x, y, BinaryOperator.DIV),
    lambda b, x, y: b.binary(x, b.load_int(0), BinaryOperator.BIT_OR),
    lambda b, x, y: b.binary(x, b.load_int(0), BinaryOperator.U_RSHIFT),
    lambda b, x, y: b.unary(UnaryOperator.MINUS, x),
    lambda b, x, y: b.binary(x, y, BinaryOperator.MOD),
    lambda b, x, y: b.binary(x, b.load_int(2), BinaryOperator.EXP),
    lambda b, x, y: b.call_method(b.builtin(Builtins.MATH), Methods.MATH_FLOOR, [x]),
    lambda b, x, y: b.binary(b.binary(x, y, BinaryOperator.ADD), b.load_int(0), BinaryOperator.BIT_OR),
    lambda b, x, y: b.binary(x, b.load_int(1), BinaryOperator.LSHIFT),
    lambda b, x, y: b.unary(UnaryOperator.BITWISE_NOT, x),
)


@generator("java_echo_arith", family="java-interop", weight=5)
@st.composite
def java_echo_arith(draw, b: Builder) -> Var:
    """The result of an arithmetic expression (where the compiler may keep an int) goes to Java."""
    x, y = draw(edge_number(b)), draw(edge_number(b))
    value = draw(st.sampled_from(ARITH_FORMS))(b, x, y)
    j = b.builtin(Builtins.J)
    r = b.declare(b.call_method(j, Methods.J_TYPE_OF, [value]))
    if draw(maybe(0.5)):
        b.declare(b.call_method(j, Methods.J_ECHO, [value]))
    return r


@generator("java_overload", family="java-interop", weight=5, block=True)
@st.composite
def java_overload(draw, b: Builder) -> None:
    """`J.over(x)` is overloaded on int/long/double/String/boolean/Object: which one Rhino picks."""
    arg = draw(edge_number(b)) if draw(maybe(0.5)) else draw(variable(b))
    j = b.builtin(Builtins.J)
    recorded(b, lambda: b.call_method(j, Methods.J_OVER, [arg]))


USE_FORMS: tuple[Callable[[Builder, Var], Var], ...] = (
    lambda b, r: b.binary(r, b.load_int(1), BinaryOperator.ADD),
    lambda b, r: b.binary(r, b.load_int(2), BinaryOperator.MUL),
    lambda b, r: b.compare(r, r, Comparator.STRICT_EQUAL),
    lambda b, r: b.typeof(r),
    lambda b, r: b.binary(r, b.load_string(""), BinaryOperator.ADD),
    lambda b, r: b.binary(r, b.load_int(0), BinaryOperator.BIT_OR),
    lambda b, r: b.compare(r, b.load_int(7), Comparator.EQUAL),
    lambda b, r: b.call_function(b.builtin(Builtins.NUMBER), [r]),
    lambda b, r: b.compare(r, b.load_int(2 ** 31 - 1), Comparator.GREATER),
    lambda b, r: b.get_property(b.call_function(b.builtin(Builtins.STRING), [r]), Keys.LENGTH),
    lambda b, r: b.binary(r, b.load_int(3), BinaryOperator.DIV),
    lambda b, r: b.compare(r, b.load_null(), Comparator.STRICT_EQUAL),
    lambda b, r: b.call_method(b.builtin(Builtins.JSON), Methods.JSON_STRINGIFY, [r]),
    lambda b, r: b.call_method(b.builtin(Builtins.OBJECT), Methods.OBJECT_CTOR_IS, [r, b.unary(UnaryOperator.MINUS, b.load_int(0))]),
)


@generator("java_returned_value", family="java-interop", weight=5, block=True)
@st.composite
def java_returned_value(draw, b: Builder) -> None:
    """A Java primitive or boxed value (a long beyond 2^53, a float, a byte, a char, a BigInteger,
    null) comes back into JavaScript and is used in arithmetic, comparison or concatenation."""
    j = b.builtin(Builtins.J)
    r = b.declare(b.call_method(j, draw(st.sampled_from(RETURNING)), []))
    form = draw(st.sampled_from(USE_FORMS))
    recorded(b, lambda: form(b, r))


@generator("java_array", family="java-interop", weight=4, block=True)
@st.composite
def java_array(draw, b: Builder) -> None:
    """Java arrays (int[], double[], Object[], String[]) read and written from JavaScript, and a
    JavaScript array passed to a Java array parameter."""
    j = b.builtin(Builtins.J)
    kind = draw(st.integers(0, 3))
    n = b.load_int(draw(st.integers(0, 5)))
    if kind == 0:
        a = b.declare(b.call_method(j, Methods.J_INT_ARRAY, [n]))
        v = draw(edge_number(b))
        index = draw(st.integers(0, 2))
        recorded(b, lambda: _store_then_read(b, a, index, v))
        b.declare(b.create_array([b.get_property(a, Keys.LENGTH), b.call_method(j, Methods.J_TYPE_OF, [b.get_element(a, 0)]),
                                  b.call_method(j, Methods.J_SUM, [a])]))
    elif kind == 1:
        a = b.declare(b.call_method(j, Methods.J_DOUBLE_ARRAY, [n]))
        v = draw(variable(b, NUMBER))
        recorded(b, lambda: b.binary(_store_then_read(b, a, 0, v), b.load_int(1), BinaryOperator.ADD))
    elif kind == 2:
        a = b.declare(b.call_method(j, Methods.J_OBJECT_ARRAY, [n]))
        v = draw(variable(b))
        recorded(b, lambda: b.call_method(j, Methods.J_TYPE_OF, [_store_then_read(b, a, 0, v)]))
    else:
        arr = draw(variable(b, ARRAY))
        recorded(b, lambda: b.call_method(j, Methods.J_SUM, [arr]))
        b.declare(b.get_property(b.call_method(j, Methods.J_STRING_ARRAY, []), Keys.LENGTH))


def _store_then_read(b: Builder, a: Var, index: int, v: Var) -> Var:
    b.set_element(a, index, v)
    return b.get_element(a, 0)


@generator("java_collections", family="java-interop", weight=3, block=True)
@st.composite
def java_collections(draw, b: Builder) -> None:
    """java.util.List / Map seen from JavaScript: indexing, size, get, iteration with for-in."""
    j = b.builtin(Builtins.J)
    kind = draw(st.integers(0, 2))
    if kind == 0:
        lst = b.declare(b.call_method(j, Methods.J_LIST, []))
        form = draw(st.integers(0, 2))
        if form == 0:
            b.declare(b.create_array([b.call_method(j, Methods.J_SIZE, [lst]), b.call_method(j, Methods.J_TYPE_OF, [b.get_element(lst, 0)])]))
        elif form == 1:
            b.declare(b.binary(b.get_element(lst, 0), b.get_element(lst, 1), BinaryOperator.ADD))
        else:
            b.declare(b.call_function(b.builtin(Builtins.STRING), [b.get_element(lst, 0)]))
    elif kind == 1:
        m = b.declare(b.call_method(j, Methods.J_MAP, []))
        k = b.load_string(draw(st.sampled_from(("k", "d", "missing"))))
        b.declare(b.call_method(j, Methods.J_TYPE_OF, [b.call_method(j, Methods.J_GET, [m, k])]))
    else:
        acc = b.declare(b.create_array([]))
        with b.for_each(ForEachKind.IN, b.call_method(j, Methods.J_LIST, [])) as ((key,), _):
            b.call_method(acc, Methods.ARRAY_PUSH, [key])


@generator("java_numeric_pipeline", family="java-interop", weight=5, produces=FUNCTION, block=True)
@st.composite
def java_numeric_pipeline(draw, b: Builder) -> Var:
    """A number-var function whose intermediate values are handed to `J.typeOf` at several points:
    the int or double representation the compiler chose becomes visible."""
    j = b.builtin(Builtins.J)
    with b.plain_function(Parameters(2), types=(NUMBER, NUMBER)) as (f, (p, q)):
        x = b.declare(b.binary(p, q, BinaryOperator.ADD), draw(declaration_kinds(b.options, mutable=True)))
        t1 = b.declare(b.call_method(j, Methods.J_TYPE_OF, [x]))
        b.reassign(x, draw(st.sampled_from(ARITH_FORMS))(b, x, p))
        t2 = b.declare(b.call_method(j, Methods.J_TYPE_OF, [x]))
        b.unary(UnaryOperator.POST_INC, x)
        b.do_return(b.create_array([t1, t2, b.call_method(j, Methods.J_TYPE_OF, [x]), b.call_method(j, Methods.J_ECHO, [x]),
                                    b.call_method(j, Methods.J_OVER, [x])]))
    b.declare(b.call_function(f, [draw(edge_number(b)), draw(edge_number(b))]))
    b.declare(b.call_function(f, [b.load_int(draw(st.integers(1, 3))), b.load_int(draw(st.integers(1, 3)))]))
    b.declare(b.call_function(f, [b.load_float(draw(st.sampled_from(EDGE_FLOATS))), draw(variable(b))]))
    return f
