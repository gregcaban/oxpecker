"""Compiler-targeted generators (port of `gen/generators/compiler.py`): what Rhino's bytecode compiler
does differently from the interpreter -- number-type inference on locals, activation, direct calls,
invokedynamic relinking, ConsString, super in object literals. These get extra weight.

Typed locals are declared variables (`b.declare`, decision D15): they are what the optimizer
specialises, and the numeric bodies reassign them in place.
"""

from __future__ import annotations

import json

from hypothesis import strategies as st

from ...gen.generators.values import INT32S, NUMBERS
from ...gen.types import T
from ..builder import Builder, literal, maybe, pick
from ..ops import CompoundAssign, Conditional, CreateArray, CreateObject, GetElement, GetProperty, LoadLiteral, SetElement, Update
from ..registry import generator

PERTURB = [('"7"', T.STRING), ('"abc"', T.STRING), ("undefined", T.UNDEFINED), ("null", T.NULL),
           ("{valueOf() { return 3; }}", T.OBJECT), ("[2]", T.ARRAY), ("true", T.BOOLEAN), ("10n", T.BIGINT),
           ("NaN", T.NUMBER), ("-0", T.NUMBER), ("2147483648", T.NUMBER), ("0.5", T.NUMBER),
           ('{toString() { return "4"; }}', T.OBJECT), ("Symbol.iterator", T.SYMBOL)]
INT32_ARGS = [(x, T.INT32) for x in INT32S]
NUMBER_ARGS = [(x, T.NUMBER) for x in NUMBERS]
LOCAL_INIT = INT32S + ["0", "1", "1.5", "2147483647"]
STEP_FORMS = ["+", "*", "-", "/", "%", "++", "+=", "|0", ">>>0", "~", "<<1", "==?1:0", "neg", "**2", "imul", "min"]


@st.composite
def spelled(draw, b: Builder, pool):
    """A literal from a pool of (spelling, type) pairs."""
    text, t = draw(st.sampled_from(pool))
    return b.emit(LoadLiteral(t, text))


def perturb(b: Builder):
    """A value that perturbs a numeric site: strings, objects with valueOf, BigInt, -0, 2**31, ..."""
    return spelled(b, PERTURB)


def push(b: Builder, acc: int, value: int) -> None:
    b.do_method(acc, "push", value)


def push_guarded(b: Builder, acc: int, compute) -> None:
    """`try { acc.push(compute()) } catch (e) { acc.push(e && e.name) }` -- keeps the program alive
    past a throw (BigInt/Symbol perturbations make arithmetic throw). `compute` emits, never draws."""
    with b.try_() as t:
        push(b, acc, compute())
        with t.catch() as e:
            push(b, acc, b.error_name(e))


# ---------------------------------------------------------------------------- numeric bodies

@st.composite
def numeric_local(draw, b: Builder) -> int:
    """`var n = <int-ish literal>;` -- a local the optimizer can type as a number."""
    return b.declare(b.emit(LoadLiteral(T.NUMBER, draw(st.sampled_from(LOCAL_INIT)))))


@st.composite
def numeric_step(draw, b: Builder, locals_: list[int], pool: list[int]) -> None:
    """One arithmetic statement assigning to a numeric local."""
    t, x, y = draw(pick(locals_)), draw(pick(pool)), draw(pick(pool))
    form = draw(st.sampled_from(STEP_FORMS))
    one = lambda text: b.const(T.INT32, text)
    if form in ("+", "*", "-", "/", "%"):
        b.reassign(t, b.binary(form, x, y))
    elif form == "++":
        b.emit(Update("++", value=False), (t,))
    elif form == "+=":
        b.emit(CompoundAssign("+"), (t, x))
    elif form == "|0":
        b.reassign(t, b.binary("|", x, one("0")))
    elif form == ">>>0":
        b.reassign(t, b.binary(">>>", x, one("0")))
    elif form == "~":
        b.reassign(t, b.unary("~", x))
    elif form == "<<1":
        b.reassign(t, b.binary("<<", x, one("1")))
    elif form == "==?1:0":
        b.reassign(t, b.emit(Conditional(), (b.binary("==", x, y), one("1"), one("0"))))
    elif form == "neg":
        b.reassign(t, b.unary("-", x))
    elif form == "**2":
        b.reassign(t, b.binary("**", x, one("2")))
    elif form == "imul":
        b.reassign(t, b.method(b.builtin("Math"), "imul", x, y))
    else:                                                      # min
        b.reassign(t, b.emit(Conditional(), (b.binary("<", x, y), x, y)))


@st.composite
def numeric_body(draw, b: Builder, params: list[int], lo: int, hi: int) -> list[int]:
    """Locals that are provably numeric to the optimizer, plus arithmetic over them."""
    locals_ = draw(st.lists(numeric_local(b), min_size=lo, max_size=hi))
    draw(st.lists(numeric_step(b, locals_, locals_ + params), min_size=1, max_size=5))
    return locals_


def sum_of(b: Builder, vs: list[int]) -> int:
    total = vs[0]
    for v in vs[1:]:
        total = b.binary("+", total, v)
    return total


# ---------------------------------------------------------------------------- generators

@generator("numeric_local_type_flip", family="compiler-targeted", weight=6, output=T.FUNCTION)
@st.composite
def numeric_local_type_flip(draw, b: Builder):
    """Numeric-only function locals (optimizer infers number types), then one local is switched
    to a non-number late in the function, and the function is called with ints then doubles."""
    with b.function(2, param_types=[T.NUMBER, T.NUMBER]) as (f, params):
        locals_ = draw(numeric_body(b, params, 1, 3))
        if draw(maybe(0.8)):
            victim = draw(pick(locals_))
            b.reassign(victim, draw(perturb(b)))                    # type perturbation
            dest, other = draw(pick(locals_)), draw(pick(locals_ + params))
            b.reassign(dest, b.binary("+", victim, other))
        b.ret(sum_of(b, locals_))
    acc = b.emit(CreateArray(), (), hint="acc")
    for left, right in ((INT32_ARGS, INT32_ARGS), (NUMBER_ARGS, NUMBER_ARGS), (PERTURB, INT32_ARGS)):
        x, y = draw(spelled(b, left)), draw(spelled(b, right))
        push_guarded(b, acc, lambda: b.call(f, x, y))


@generator("activation_pair", family="compiler-targeted", weight=5)
@st.composite
def activation_pair(draw, b: Builder):
    """The same numeric body twice: once plain (optimizer runs) and once with an `arguments`
    read / eval / with inserted, which forces activation and disables the optimizer. Both results
    are compared."""
    with b.function(2, param_types=[T.NUMBER, T.NUMBER]) as (fa, pa):
        start = len(b.instrs)
        locals_ = draw(numeric_body(b, pa, 2, 2))
        body = b.instrs[start:]
        b.ret(b.binary("+", locals_[0], locals_[1]))
    forms = ["var z = arguments.length"] if b.strict else \
        ["var z = arguments.length", "eval('1')", "with ({}) { }", "var z = arguments[0]"]
    activation = draw(st.sampled_from(forms))
    with b.function(2) as (fb, pb):
        b.eval(activation)                                          # forces activation: optimizer skipped
        rn = b.replay(body, {pa[0]: pb[0], pa[1]: pb[1]})
        b.ret(b.binary("+", rn[locals_[0]], rn[locals_[1]]))
    # Both copies lift the same way: pin their undecided lifting choices (decision D16).
    b.pin(start)
    x = draw(spelled(b, NUMBER_ARGS + INT32_ARGS))
    y = draw(spelled(b, NUMBER_ARGS + INT32_ARGS + PERTURB))
    b.emit(CreateArray(), (b.call(fa, x, y), b.call(fb, x, y)))


DIRECT_FORMS = ["a0 + a1", "[a0, a1]", "a0 * 2 + (a1 | 0)", "typeof a1", "a0 === undefined ? -1 : a0"]


@generator("direct_call_arity", family="compiler-targeted", weight=5)
@st.composite
def direct_call_arity(draw, b: Builder):
    """A top-level function called directly with matching, fewer and extra arguments, then
    redefined between calls (the compiler specialises direct calls)."""
    form = draw(st.sampled_from(DIRECT_FORMS))
    with b.function(2) as (f, (a0, a1)):
        if form == "a0 + a1":
            v = b.binary("+", a0, a1)
        elif form == "[a0, a1]":
            v = b.emit(CreateArray(), (a0, a1))
        elif form == "a0 * 2 + (a1 | 0)":
            v = b.binary("+", b.binary("*", a0, b.const(T.INT32, "2")), b.binary("|", a1, b.const(T.INT32, "0")))
        elif form == "typeof a1":
            v = b.unary("typeof", a1)
        else:
            undef = b.const(T.UNDEFINED, "undefined")
            v = b.emit(Conditional(), (b.binary("===", a0, undef), b.const(T.INT32, "-1"), a0))
        b.ret(v)
    res = b.emit(CreateArray(), (), hint="acc")
    calls = [(INT32_ARGS, INT32_ARGS), (NUMBER_ARGS,), (INT32_ARGS, NUMBER_ARGS, PERTURB), ()]
    for pools in calls:
        args = [draw(spelled(b, pool)) for pool in pools]
        push(b, res, b.call(f, *args))
    if draw(maybe(0.5)):
        new_form = draw(st.sampled_from(["a1", "-1", "a0 - a1"]))
        with b.function(2, style="expr") as (g, (c0, c1)):
            b.ret({"a1": lambda: c1, "-1": lambda: b.const(T.INT32, "-1"),
                   "a0 - a1": lambda: b.binary("-", c0, c1)}[new_form]())
        b.reassign(f, g)                                            # redefine the direct-call target
        x, y = draw(spelled(b, INT32_ARGS)), draw(spelled(b, INT32_ARGS))
        push(b, res, b.call(f, x, y))


RELINK_FORMS = ["x + y", "x - y", "x * y", "x < y", "x == y", "x.length", "x[0]", 'x + ""', "x | y", "x / y",
                "x % y", "-x"]
RELINK_FIRST = [("1", T.INT32), ("1.5", T.NUMBER), ('"s"', T.STRING), ('"a" + "b"', T.STRING), ("[1, 2]", T.ARRAY),
                ("2147483647", T.INT32), ("true", T.BOOLEAN)]
RELINK_SECOND = PERTURB + [("[3]", T.ARRAY), ('"x" + 1', T.STRING), ("0.1", T.NUMBER)]


@generator("callsite_relink", family="compiler-targeted", weight=6)
@st.composite
def callsite_relink(draw, b: Builder):
    """One call/property site hit repeatedly with one observed type (int, double, string,
    ConsString, dense array, constant), then with another: exercises invokedynamic relinking."""
    form = draw(st.sampled_from(RELINK_FORMS))
    with b.function(2) as (f, (x, y)):
        if form == "x.length":
            v = b.emit(GetProperty("length"), (x,))
        elif form == "x[0]":
            v = b.emit(GetElement(), (x, b.const(T.INT32, "0")))
        elif form == 'x + ""':
            v = b.binary("+", x, b.const(T.STRING, '""'))
        elif form == "-x":
            v = b.unary("-", x)
        else:
            v = b.binary(form.split()[1], x, y)
        b.ret(v)
    acc = b.emit(CreateArray(), (), hint="acc")
    first = draw(spelled(b, RELINK_FIRST))
    second = draw(spelled(b, RELINK_SECOND))
    with b.for_(draw(st.integers(2, 6))) as i:
        push(b, acc, b.call(f, first, i))
    push(b, acc, b.call(f, second, draw(spelled(b, INT32_ARGS))))
    push(b, acc, b.call(f, first, second))


SHAPE_CHANGES = ["delete", "getter", "proto", "overwrite", "new_object", "array"]


@generator("property_site_shape_change", family="compiler-targeted", weight=4)
@st.composite
def property_site_shape_change(draw, b: Builder):
    """A property get site warmed on one shape, then the object gains/loses the property, gets a
    new prototype, or the data property becomes an accessor."""
    key = draw(st.sampled_from(["x", "y", "0", "length"]))
    o = b.declare(b.emit(CreateObject((key,)), (draw(spelled(b, INT32_ARGS)),)))
    with b.function(1) as (f, (obj,)):
        v = (b.emit(GetElement(), (obj, b.const(T.INT32, "0"))) if key == "0"
             else b.emit(GetProperty(key), (obj,)))
        b.ret(b.binary("+", v, b.const(T.INT32, "1")))
    acc = b.emit(CreateArray(), (), hint="acc")
    with b.for_(3):
        push(b, acc, b.call(f, o))
    change = draw(st.sampled_from(SHAPE_CHANGES))
    k = repr(key)
    if change == "delete":
        b.eval(f"delete {{0}}[{k}]", o)
    elif change == "getter":
        num = draw(st.sampled_from(NUMBERS))
        b.eval(f"Object.defineProperty({{0}}, {k}, {{get() {{ return {num}; }}}})", o)
    elif change == "proto":
        text, _ = draw(st.sampled_from(PERTURB))
        b.eval(f"Object.setPrototypeOf({{0}}, {{{key}: {text}}}); delete {{0}}[{k}]", o)
    elif change == "overwrite":
        b.emit(SetElement(), (o, b.const(T.STRING, json.dumps(key)), draw(perturb(b))))
    elif change == "new_object":
        num = draw(spelled(b, NUMBER_ARGS))
        b.reassign(o, b.emit(CreateObject((key, "extra")), (num, b.const(T.INT32, "1"))))
    else:
        b.reassign(o, b.emit(CreateArray(), (draw(spelled(b, INT32_ARGS)),)))
    push(b, acc, b.call(f, o))


CHAIN_PARTS = ["i", '"b"', "s.length", "1.5", "null", "[]"]
CHAIN_USES = ["s == s.length", "s + 1", "s[2]", 's < "b"', "s.charCodeAt(0)", 's === s + ""', "Number(s)",
              "s.length", 's.indexOf("b")']


@generator("string_concat_chain", family="compiler-targeted", weight=4, output=T.STRING)
@st.composite
def string_concat_chain(draw, b: Builder):
    """Long concatenation chains (ConsString), then used in comparison, +number, indexing."""
    s = b.declare(draw(literal(b, T.STRING, ['""', '"a"', '"0"'])))
    part = draw(st.sampled_from(CHAIN_PARTS))
    with b.for_(draw(st.integers(1, 8))) as i:
        value = {"i": lambda: i, '"b"': lambda: b.const(T.STRING, '"b"'),
                 "s.length": lambda: b.emit(GetProperty("length"), (s,)),
                 "1.5": lambda: b.const(T.NUMBER, "1.5"), "null": lambda: b.const(T.NULL, "null"),
                 "[]": lambda: b.emit(CreateArray(), ())}[part]()
        b.emit(CompoundAssign("+"), (s, value))
    use = draw(st.sampled_from(CHAIN_USES))
    if use == "s == s.length":
        b.binary("==", s, b.emit(GetProperty("length"), (s,)))
    elif use == "s + 1":
        b.binary("+", s, b.const(T.INT32, "1"))
    elif use == "s[2]":
        b.emit(GetElement(), (s, b.const(T.INT32, "2")))
    elif use == 's < "b"':
        b.binary("<", s, b.const(T.STRING, '"b"'))
    elif use == "s.charCodeAt(0)":
        b.method(s, "charCodeAt", b.const(T.INT32, "0"))
    elif use == 's === s + ""':
        b.binary("===", s, b.binary("+", s, b.const(T.STRING, '""')))
    elif use == "Number(s)":
        b.call(b.builtin("Number", T.FUNCTION), s)
    elif use == "s.length":
        b.emit(GetProperty("length"), (s,))
    else:
        b.method(s, "indexOf", b.const(T.STRING, '"b"'))


BOUNDARY_INIT = ["2147483647", "-2147483648", "4294967295", "1073741824", "65536"]
BOUNDARY_FORMS = ["x + p", "x * p", "x++", "x << 1", "x - p", "(x + 1) | 0", "x * x"]
BOUNDARY_ARGS = [("1", T.INT32), ("2", T.INT32), ("-1", T.INT32), ("0.5", T.NUMBER), ("2147483647", T.INT32),
                 ('"1"', T.STRING)]


@generator("int32_boundary_arith", family="compiler-targeted", weight=4, output=T.NUMBER)
@st.composite
def int32_boundary_arith(draw, b: Builder):
    """Arithmetic that overflows int32 inside a numeric-typed function."""
    init = draw(st.sampled_from(BOUNDARY_INIT))
    form = draw(st.sampled_from(BOUNDARY_FORMS))
    with b.function(1, param_types=[T.NUMBER]) as (f, (p,)):
        x = b.declare(b.emit(LoadLiteral(T.NUMBER, init)))
        if form == "x++":
            b.emit(Update("++", value=False), (x,))
        elif form == "x << 1":
            b.reassign(x, b.binary("<<", x, b.const(T.INT32, "1")))
        elif form == "(x + 1) | 0":
            b.reassign(x, b.binary("|", b.binary("+", x, b.const(T.INT32, "1")), b.const(T.INT32, "0")))
        elif form == "x * x":
            b.reassign(x, b.binary("*", x, x))
        else:
            b.reassign(x, b.binary(form.split()[1], x, p))
        zero = lambda: b.const(T.INT32, "0")
        b.ret(b.emit(CreateArray(), (x, b.binary("|", x, zero()), b.binary(">>>", x, zero()),
                                     b.binary("===", x, b.binary("|", x, zero())))))
    b.call(f, draw(spelled(b, BOUNDARY_ARGS)))


SUPER_OPS = ["return super.p;", "return ++super.p;", "return super.p++;", "return super[k] += 1;",
             "return super.g;", "super.p = 5; return this.p;", "return super[k]--;", "return super.q;"]


@generator("super_in_object_literal", family="compiler-targeted", weight=4)
@st.composite
def super_in_object_literal(draw, b: Builder):
    """`super.x` / `super[k]` property access and compound updates from an object-literal method,
    with a key whose toString swaps the prototype (the interpreted-only test262 failure)."""
    p_int = draw(st.sampled_from(INT32S))
    p_num = draw(st.sampled_from(NUMBERS))
    op = draw(st.sampled_from(SUPER_OPS))
    proto = b.emit(LoadLiteral(T.OBJECT, "{p: %s, get g() { return this.p * 2; }}" % p_int))
    proto2 = b.emit(LoadLiteral(T.OBJECT, "{p: %s}" % p_num))
    o = b.declare(b.eval("{__proto__: {0}, m(k) { %s }}" % op, proto, out=T.OBJECT))
    if draw(maybe(0.5)):
        key = b.eval('{toString() { Object.setPrototypeOf({0}, {1}); return "p"; }}', o, proto2, out=T.OBJECT)
    else:
        key = b.const(T.STRING, '"p"')
    b.method(o, "m", key)
    b.emit(CreateArray(), tuple(b.emit(GetProperty("p"), (v,)) for v in (o, proto, proto2)))


CONST_FORMS = ["x + 1", "x * 0", "x / 0", "x % 1", "-(x)", "x === x", "x | 0", "x >>> 0", '"" + x', "(x) ** 0.5",
               "typeof x", 'x == "x"']


@generator("const_site", family="compiler-targeted", weight=2)
@st.composite
def const_site(draw, b: Builder):
    """Operations on literal constants that the compiler may fold or link as constants."""
    text = draw(st.sampled_from(NUMBERS))
    form = draw(st.sampled_from(CONST_FORMS))
    x = b.emit(LoadLiteral(T.NUMBER, text), keep=False)            # always spelled at its uses
    c = lambda t, s: b.const(t, s)
    if form == "-(x)":
        b.unary("-", x)
    elif form == "typeof x":
        b.unary("typeof", x)
    elif form == "x === x":
        b.binary("===", x, x)
    elif form == '"" + x':
        b.binary("+", c(T.STRING, '""'), x)
    elif form == "(x) ** 0.5":
        b.binary("**", x, c(T.NUMBER, "0.5"))
    elif form == 'x == "x"':
        b.binary("==", x, c(T.STRING, json.dumps(text)))
    else:
        op, k = form.split()[1], form.split()[2]
        b.binary(op, x, c(T.INT32, k))
