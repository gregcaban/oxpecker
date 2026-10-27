"""Compiler-targeted generators: the shapes Rhino's bytecode compiler treats differently from the
interpreter (docs/design-generation-layer.md §5.3 and §7.7). They are built against the Rhino
model: number variables (every assignment NumberType), activation triggers, direct calls, call
sites re-linked with new types, property sites changing shape, ConsStrings, int32 boundaries,
`super` in object literals, constant folding, negative zero."""

from __future__ import annotations

from typing import Callable

from hypothesis import strategies as st

from ..builder import Builder
from ..environment import INTERESTING_FLOATS, INTERESTING_INTEGERS, INTERESTING_STRINGS, Builtins, Keys, Methods
from ..ops import BinaryOperator, Comparator, Lift, LoadFloat, LoadInteger, Parameters, PropertyFlag, PropertyType, Radix, UnaryOperator
from ..program import Var
from ..strategies import assignable, declaration_kinds, generator, maybe, nested, pick, variable
from ..types import BIGINT, FUNCTION, NUMBER, OBJECT, STRING, UNDEFINED, Type

INT32_BOUNDARIES = (2 ** 31 - 1, 2 ** 31, 2 ** 32 - 1, 2 ** 30, 65536, 0)
SMALL_INTS = (0, 1, 2, 3, 7, 10, 255, 256, 1000, 2 ** 31 - 1)

# Every entry assigns the target an expression Rhino's optimizer types as NumberType when its
# inputs are number variables or literals (Block.findExpressionType; design §5.3).
NUMERIC_STEPS: tuple[Callable[[Builder, Var, Var, Var], None], ...] = (
    lambda b, t, x, y: b.reassign(t, b.binary(x, y, BinaryOperator.ADD)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, y, BinaryOperator.MUL)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, y, BinaryOperator.SUB)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, y, BinaryOperator.DIV)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, y, BinaryOperator.MOD)),
    lambda b, t, x, y: b.unary(UnaryOperator.POST_INC, t),
    lambda b, t, x, y: b.update(t, x, BinaryOperator.ADD),
    lambda b, t, x, y: b.reassign(t, b.binary(x, b.load_int(0), BinaryOperator.BIT_OR)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, b.load_int(0), BinaryOperator.U_RSHIFT)),
    lambda b, t, x, y: b.reassign(t, b.unary(UnaryOperator.BITWISE_NOT, x)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, b.load_int(1), BinaryOperator.LSHIFT)),
    lambda b, t, x, y: b.reassign(t, b.unary(UnaryOperator.MINUS, x)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, b.load_int(2), BinaryOperator.EXP)),
    lambda b, t, x, y: b.reassign(t, b.call_method(b.builtin(Builtins.MATH), Methods.MATH_IMUL, [x, y])),
    lambda b, t, x, y: b.reassign(t, b.ternary(b.compare(x, y, Comparator.LESS), x, y)),
)
PERTURBING_TYPES = (STRING, OBJECT, BIGINT, UNDEFINED, FUNCTION)


@st.composite
def numeric_body(draw, b: Builder, params: tuple[Var, ...], n_locals: int) -> list[Var]:
    """Locals initialised from integer literals, then arithmetic steps over locals and parameters:
    the optimizer types every local as a number variable."""
    locals_ = [b.declare(b.load_int(draw(st.sampled_from(SMALL_INTS))), draw(declaration_kinds(b.options, mutable=True))) for _ in range(n_locals)]
    pool = locals_ + list(params)
    for step in draw(st.lists(st.sampled_from(NUMERIC_STEPS), min_size=1, max_size=5)):
        step(b, pick(draw, locals_), pick(draw, pool), pick(draw, pool))
    return locals_


@st.composite
def perturbing_value(draw, b: Builder) -> Var:
    """A value whose Rhino type is AnyType: a string, object, BigInt, undefined, or a function."""
    return draw(variable(b, draw(st.sampled_from(PERTURBING_TYPES))))


def guarded_call(b: Builder, f: Var, args: list[Var], acc: Var) -> None:
    """`acc.push(f(args))` inside a top-level try/catch whose catch records the error name
    (Rhino: a catch at script level does not touch any function's activation)."""
    with b.try_() as t:
        b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, args)])
        with t.catch() as e:
            b.call_method(acc, Methods.ARRAY_PUSH, [b.get_property(e, Keys.NAME)])


@generator("numeric_local_type_flip", family="compiler-targeted", weight=6, produces=FUNCTION, block=True)
@st.composite
def numeric_local_type_flip(draw, b: Builder) -> Var:
    """Number-var locals (every assignment NumberType to Rhino's optimizer), one of them flipped
    to a non-number late in the function; called with ints, doubles and a perturbing value."""
    with b.plain_function(Parameters(2), named=draw(maybe(0.5)), types=(NUMBER, NUMBER)) as (f, params):
        locals_ = draw(numeric_body(b, params, draw(st.integers(1, 3))))
        if draw(maybe(0.8)):
            victim = pick(draw, locals_)
            b.reassign(victim, draw(perturbing_value(b)))
            b.reassign(pick(draw, locals_), b.binary(victim, pick(draw, locals_ + list(params)), BinaryOperator.ADD))
        b.do_return(b.create_array(locals_))
    acc = b.declare(b.create_array([]))
    ints = [b.load_int(draw(st.sampled_from(SMALL_INTS))), b.load_int(draw(st.sampled_from(SMALL_INTS)))]
    guarded_call(b, f, ints, acc)
    floats = [b.load_float(draw(st.sampled_from(INTERESTING_FLOATS))), b.load_float(draw(st.sampled_from(INTERESTING_FLOATS)))]
    guarded_call(b, f, floats, acc)
    guarded_call(b, f, [draw(perturbing_value(b)), b.load_int(draw(st.sampled_from(SMALL_INTS)))], acc)
    return f


ACTIVATION_TRIGGERS: tuple[Callable[[Builder], None], ...] = (
    lambda b: b.declare(b.get_property(b.arguments(), Keys.LENGTH)),
    lambda b: b.declare(b.get_element(b.arguments(), 0)),
    lambda b: _empty_arrow(b),
    lambda b: _empty_try_catch(b),
)


def _empty_arrow(b: Builder) -> None:
    with b.arrow_function():
        pass


def _empty_try_catch(b: Builder) -> None:
    with b.try_() as t:
        with t.catch():
            pass


@generator("activation_pair", family="compiler-targeted", weight=5, block=True)
@st.composite
def activation_pair(draw, b: Builder) -> None:
    """The same numeric body twice: once plain (the optimizer runs) and once with one activation
    trigger inserted (`arguments`, a nested arrow, a catch clause), so the compiled paths differ
    while the results must not."""
    with b.plain_function(Parameters(2), types=(NUMBER, NUMBER)) as (fa, params):
        body_start = len(b.code)
        locals_ = draw(numeric_body(b, params, 2))
        b.do_return(b.binary(locals_[0], locals_[1], BinaryOperator.ADD))
        body_end = len(b.code)
    with b.plain_function(Parameters(2), types=(NUMBER, NUMBER)) as (fb, params2):
        draw(st.sampled_from(ACTIVATION_TRIGGERS))(b)
        b.replay(body_start, body_end, dict(zip(params, params2)))
    x = b.load_int(draw(st.sampled_from(SMALL_INTS)))
    y = draw(variable(b, draw(st.sampled_from((NUMBER, NUMBER, STRING, OBJECT)))))
    b.declare(b.create_array([b.call_function(fa, [x, y]), b.call_function(fb, [x, y])]))


RETURN_FORMS: tuple[Callable[[Builder, Var, Var], Var], ...] = (
    lambda b, p, q: b.binary(p, q, BinaryOperator.ADD),
    lambda b, p, q: b.create_array([p, q]),
    lambda b, p, q: b.binary(b.binary(p, b.load_int(2), BinaryOperator.MUL), b.binary(q, b.load_int(0), BinaryOperator.BIT_OR), BinaryOperator.ADD),
    lambda b, p, q: b.typeof(q),
    lambda b, p, q: b.ternary(b.compare(p, b.load_undefined(), Comparator.STRICT_EQUAL), b.unary(UnaryOperator.MINUS, b.load_int(1)), p),
)


@generator("direct_call_arity", family="compiler-targeted", weight=5, block=True)
@st.composite
def direct_call_arity(draw, b: Builder) -> None:
    """A named top-level function called from inside another function with its exact arity (a
    Rhino direct call), then with fewer, more and no arguments, then possibly redefined."""
    form = draw(st.sampled_from(RETURN_FORMS))
    with b.plain_function(Parameters(2), named=True) as (f, (p, q)):
        b.do_return(form(b, p, q))
    acc = b.declare(b.create_array([]))
    one, two = b.load_int(draw(st.sampled_from(SMALL_INTS))), b.load_int(draw(st.sampled_from(SMALL_INTS)))
    with b.plain_function(Parameters(0), named=True) as (caller, _):
        b.do_return(b.call_function(f, [one, two]))
    b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(caller, [])])
    b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [b.load_float(draw(st.sampled_from(INTERESTING_FLOATS)))])])
    b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [one, two, draw(perturbing_value(b))])])
    b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [])])
    if draw(maybe(0.5)):
        with b.plain_function(Parameters(2)) as (g, (p2, q2)):
            b.do_return(draw(st.sampled_from(RETURN_FORMS))(b, p2, q2))
        b.reassign(f, g)
        b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(caller, [])])


SITE_FORMS: tuple[Callable[[Builder, Var, Var], Var], ...] = (
    lambda b, x, y: b.binary(x, y, BinaryOperator.ADD),
    lambda b, x, y: b.binary(x, y, BinaryOperator.SUB),
    lambda b, x, y: b.binary(x, y, BinaryOperator.MUL),
    lambda b, x, y: b.compare(x, y, Comparator.LESS),
    lambda b, x, y: b.compare(x, y, Comparator.EQUAL),
    lambda b, x, y: b.get_property(x, Keys.LENGTH),
    lambda b, x, y: b.get_element(x, 0),
    lambda b, x, y: b.binary(x, b.load_string(""), BinaryOperator.ADD),
    lambda b, x, y: b.binary(x, y, BinaryOperator.BIT_OR),
    lambda b, x, y: b.binary(x, y, BinaryOperator.DIV),
    lambda b, x, y: b.binary(x, y, BinaryOperator.MOD),
    lambda b, x, y: b.unary(UnaryOperator.MINUS, x),
)


@generator("callsite_relink", family="compiler-targeted", weight=6, block=True)
@st.composite
def callsite_relink(draw, b: Builder) -> None:
    """One function with a single operation over its parameters, called in a loop with one type
    of argument, then with another: exercises the invokedynamic linkers' relinking."""
    form = draw(st.sampled_from(SITE_FORMS))
    with b.plain_function(Parameters(2)) as (f, (x, y)):
        b.do_return(form(b, x, y))
    acc = b.declare(b.create_array([]))
    first = draw(variable(b, draw(st.sampled_from((NUMBER, NUMBER, STRING, Type.array(), OBJECT)))))
    with b.repeat(draw(st.integers(2, 6))) as (i, _):
        b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [first, i])])
    second = draw(perturbing_value(b))
    b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [second, b.load_int(draw(st.sampled_from(SMALL_INTS)))])])
    b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [first, second])])


@generator("property_site_shape_change", family="compiler-targeted", weight=4, block=True)
@st.composite
def property_site_shape_change(draw, b: Builder) -> None:
    """A property read site warmed on one object shape; then the object loses the key, gains an
    accessor, changes prototype, or is replaced by an array."""
    key = draw(st.sampled_from((Keys.X, Keys.Y, Keys.LENGTH, Keys.A)))
    initial = b.load_int(draw(st.sampled_from(SMALL_INTS)))
    with b.object_literal() as lit:
        lit.add_property(key, initial)
    o = b.declare(lit.result, draw(declaration_kinds(b.options, mutable=True)))
    with b.plain_function(Parameters(1), types=(OBJECT,)) as (f, (obj,)):
        b.do_return(b.binary(b.get_property(obj, key), b.load_int(1), BinaryOperator.ADD))
    acc = b.declare(b.create_array([]))
    with b.repeat(3):
        b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [o])])
    change = draw(st.integers(0, 4))
    if change == 0:
        b.delete_property(o, key)
    elif change == 1:
        with b.arrow_function() as (g, _):
            b.do_return(b.load_float(draw(st.sampled_from(INTERESTING_FLOATS))))
        b.configure_property(o, key, PropertyFlag.CONFIGURABLE, PropertyType.GETTER, g)
    elif change == 2:
        replacement = draw(perturbing_value(b))
        with b.object_literal() as proto:
            proto.add_property(key, replacement)
        b.call_method(b.builtin(Builtins.OBJECT), Methods.OBJECT_CTOR_SET_PROTOTYPE_OF, [o, proto.result])
        b.delete_property(o, key)
    elif change == 3:
        b.set_property(o, key, draw(perturbing_value(b)))
    else:
        b.reassign(o, b.create_int_array([draw(st.sampled_from(SMALL_INTS))]))
    b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, [o])])


@generator("string_concat_chain", family="compiler-targeted", weight=4, produces=STRING, block=True)
@st.composite
def string_concat_chain(draw, b: Builder) -> Var:
    """`s += x` in a bounded loop (a ConsString), then compared, indexed, converted."""
    s = b.declare(b.load_string(draw(st.sampled_from(("", "a", "0")))), draw(declaration_kinds(b.options, mutable=True)))
    with b.repeat(draw(st.integers(1, 8))) as (i, _):
        piece = draw(st.integers(0, 3))
        value = i if piece == 0 else b.load_string("b") if piece == 1 else b.get_property(s, Keys.LENGTH) if piece == 2 else b.load_float(1.5)
        b.update(s, value, BinaryOperator.ADD)
    use = draw(st.integers(0, 4))
    if use == 0:
        return b.compare(s, b.get_property(s, Keys.LENGTH), Comparator.EQUAL)
    if use == 1:
        return b.binary(s, b.load_int(1), BinaryOperator.ADD)
    if use == 2:
        return b.get_element(s, 2)
    if use == 3:
        return b.call_method(s, Methods.STRING_CHAR_CODE_AT, [b.load_int(0)])
    return b.call_function(b.builtin(Builtins.NUMBER), [s])


BOUNDARY_STEPS: tuple[Callable[[Builder, Var, Var], None], ...] = (
    lambda b, x, p: b.reassign(x, b.binary(x, p, BinaryOperator.ADD)),
    lambda b, x, p: b.reassign(x, b.binary(x, p, BinaryOperator.MUL)),
    lambda b, x, p: b.unary(UnaryOperator.POST_INC, x),
    lambda b, x, p: b.reassign(x, b.binary(x, b.load_int(1), BinaryOperator.LSHIFT)),
    lambda b, x, p: b.reassign(x, b.binary(x, p, BinaryOperator.SUB)),
    lambda b, x, p: b.reassign(x, b.binary(b.binary(x, b.load_int(1), BinaryOperator.ADD), b.load_int(0), BinaryOperator.BIT_OR)),
    lambda b, x, p: b.reassign(x, b.binary(x, x, BinaryOperator.MUL)),
)


@generator("int32_boundary_arith", family="compiler-targeted", weight=4, produces=NUMBER, block=True)
@st.composite
def int32_boundary_arith(draw, b: Builder) -> Var:
    """A number-var local at an int32 boundary, one arithmetic step with a parameter, returned
    with `x | 0`, `x >>> 0` and `x === (x | 0)`."""
    with b.plain_function(Parameters(1), types=(NUMBER,)) as (f, (p,)):
        x = b.declare(b.load_int(draw(st.sampled_from(INT32_BOUNDARIES)), draw(st.sampled_from(Radix))), draw(declaration_kinds(b.options, mutable=True)))
        draw(st.sampled_from(BOUNDARY_STEPS))(b, x, p)
        b.do_return(b.create_array([x, b.binary(x, b.load_int(0), BinaryOperator.BIT_OR), b.binary(x, b.load_int(0), BinaryOperator.U_RSHIFT),
                                    b.compare(x, b.binary(x, b.load_int(0), BinaryOperator.BIT_OR), Comparator.STRICT_EQUAL)]))
    arg = draw(st.integers(0, 3))
    value = (b.load_int(1), b.load_float(0.5), b.load_int(2 ** 31 - 1), b.load_string("1"))[arg]
    return b.declare(b.call_function(f, [value]))


SUPER_FORMS: tuple[Callable[[Builder, Var, Var], Var], ...] = (
    lambda b, this, k: b.get_super_property(Keys.A),
    lambda b, this, k: b.get_computed_super_property(k),
    lambda b, this, k: b.get_super_property(Keys.G),
    lambda b, this, k: b.get_super_property(Keys.B),
)


@generator("super_in_object_literal", family="compiler-targeted", weight=4, block=True)
@st.composite
def super_in_object_literal(draw, b: Builder) -> None:
    """`super.p`, `super[k] += 1`, `super.p = v` from an object-literal method whose prototype may
    be swapped by the key's `toString` (the interpreted-only test262 failure shape)."""
    initial = b.load_int(draw(st.sampled_from(SMALL_INTS)))
    with b.object_literal() as proto:
        proto.add_property(Keys.A, initial)
        with proto.getter(Keys.G) as this:
            b.do_return(b.binary(b.get_property(this, Keys.A), b.load_int(2), BinaryOperator.MUL))
    other = b.load_float(draw(st.sampled_from(INTERESTING_FLOATS)))
    with b.object_literal() as proto2:
        proto2.add_property(Keys.A, other)
    form = draw(st.integers(0, 5))
    with b.object_literal() as lit:
        lit.set_prototype(proto.result)
        with lit.method(Keys.M, Parameters(1)) as (this, (k,)):
            if form == 0:
                b.do_return(b.get_super_property(Keys.A))
            elif form == 1:
                b.update_super_property(Keys.A, b.load_int(1), BinaryOperator.ADD)
                b.do_return(b.get_super_property(Keys.A))
            elif form == 2:
                b.do_return(b.get_computed_super_property(k))
            elif form == 3:
                b.set_computed_super_property(k, b.binary(b.get_computed_super_property(k), b.load_int(1), BinaryOperator.ADD))
                b.do_return(b.get_property(this, Keys.A))
            elif form == 4:
                b.set_super_property(Keys.A, b.load_int(5))
                b.do_return(b.get_property(this, Keys.A))
            else:
                b.do_return(b.get_super_property(Keys.G))
    o = b.declare(lit.result)
    if draw(maybe(0.5)):
        with b.object_literal() as keyobj:
            with keyobj.method(Keys.TO_STRING) as (this2, _):
                b.call_method(b.builtin(Builtins.OBJECT), Methods.OBJECT_CTOR_SET_PROTOTYPE_OF, [o, proto2.result])
                b.do_return(b.load_string(Keys.A.name))
        key = keyobj.result
    else:
        key = b.load_string(Keys.A.name)
    b.declare(b.call_computed_method(o, b.load_string(Keys.M.name), [key]))
    b.declare(b.create_array([b.get_property(o, Keys.A), b.get_property(proto.result, Keys.A), b.get_property(proto2.result, Keys.A)]))


CONST_FORMS: tuple[Callable[[Builder, Var], Var], ...] = (
    lambda b, x: b.binary(x, b.load_int(1), BinaryOperator.ADD),
    lambda b, x: b.binary(x, b.load_int(0), BinaryOperator.MUL),
    lambda b, x: b.binary(x, b.load_int(0), BinaryOperator.DIV),
    lambda b, x: b.binary(x, b.load_int(1), BinaryOperator.MOD),
    lambda b, x: b.unary(UnaryOperator.MINUS, x),
    lambda b, x: b.compare(x, x, Comparator.STRICT_EQUAL),
    lambda b, x: b.binary(x, b.load_int(0), BinaryOperator.BIT_OR),
    lambda b, x: b.binary(x, b.load_int(0), BinaryOperator.U_RSHIFT),
    lambda b, x: b.binary(b.load_string(""), x, BinaryOperator.ADD),
    lambda b, x: b.binary(x, b.load_float(0.5), BinaryOperator.EXP),
    lambda b, x: b.typeof(x),
)


@generator("const_site", family="compiler-targeted", weight=2)
@st.composite
def const_site(draw, b: Builder) -> Var:
    """An operation on literal constants only, which the compiler may fold or link as constants."""
    if draw(maybe(0.3)):
        x = b.emit(LoadFloat(draw(st.sampled_from(INTERESTING_FLOATS))), lift=Lift.INLINE).output
    else:
        x = b.emit(LoadInteger(draw(st.sampled_from(INTERESTING_INTEGERS)), draw(st.sampled_from(Radix))), lift=Lift.INLINE).output
    return b.declare(draw(st.sampled_from(CONST_FORMS))(b, x))


@generator("negative_zero", family="compiler-targeted", weight=3, block=True)
@st.composite
def negative_zero(draw, b: Builder) -> None:
    """`n * 0`, `~n`, `-n` on a top-level and a local number variable, observed with `Object.is(v, -0)`."""
    n = b.declare(b.load_int(draw(st.sampled_from((0, 1, 2, 7)))), draw(declaration_kinds(b.options, mutable=True)))
    if draw(maybe(0.5)):
        b.reassign(n, b.unary(UnaryOperator.BITWISE_NOT, n))
    neg0 = b.unary(UnaryOperator.MINUS, b.load_int(0))
    obj = b.builtin(Builtins.OBJECT)
    top = b.binary(n, b.load_int(0), BinaryOperator.MUL)
    b.declare(b.call_method(obj, Methods.OBJECT_CTOR_IS, [top, neg0]))
    with b.plain_function(Parameters(1), types=(NUMBER,)) as (f, (p,)):
        m = b.declare(b.load_int(draw(st.sampled_from((0, 1, 2, 7)))), draw(declaration_kinds(b.options, mutable=True)))
        b.reassign(m, b.binary(m, p, BinaryOperator.MUL))
        b.do_return(b.call_method(b.builtin(Builtins.OBJECT), Methods.OBJECT_CTOR_IS, [b.binary(m, b.load_int(0), BinaryOperator.MUL), b.unary(UnaryOperator.MINUS, b.load_int(0))]))
    b.declare(b.call_function(f, [b.unary(UnaryOperator.MINUS, b.load_int(1))]))
