# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/CodeGen/CodeGenerators.swift at commit be2811d00282320381fc8007d64873745772011d
# (functions 1673-1805, calls 2268-2455, ThisGenerator 788, ArgumentsAccessGenerator 792,
# FunctionWithArgumentsAccessGenerator 796, LoadNewTargetGenerator 3599, Api*CallGenerator 3604-3631).
# Weights from CodeGeneratorWeights.swift. See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Functions and calls."""

from __future__ import annotations

from hypothesis import strategies as st

from ..builder import Builder
from ..environment import methods_of
from ..ops import Context, Parameters, LoadBuiltin
from ..program import Var
from ..strategies import arguments_for, generator, maybe, nested, pick, variable
from ..types import ANY, ARRAY, FUNCTION, NUMBER, OBJECT, STRING, Type


@st.composite
def parameters(draw, b: Builder, simple: bool = False) -> tuple[Parameters, list[Type]]:
    """A parameter list sized by the visible variables, with a rest parameter sometimes
    (ProgramBuilder.swift:635-701) and types drawn from what is visible. `simple` forbids the
    rest parameter: a function with a "use strict" directive must have a simple parameter list."""
    n_visible = len(b.visible())
    count = 0 if n_visible <= 1 else draw(st.integers(1, 2)) if n_visible <= 5 else draw(st.integers(2, 4))
    has_rest = count > 0 and not simple and draw(maybe(0.2))
    candidates = [NUMBER, STRING, OBJECT, FUNCTION, ARRAY]
    types = [ANY if draw(maybe(0.2)) else draw(st.sampled_from(candidates)) for _ in range(count)]
    return Parameters(count, has_rest=has_rest), types


@generator("plain_function", family="calls", weight=15, block=True, produces=FUNCTION, source="CodeGenerators.swift:1673")
@st.composite
def plain_function(draw, b: Builder) -> Var:
    """A function with drawn parameters and a generated body that returns a value; then a call."""
    params, types = draw(parameters(b))
    with b.plain_function(params, named=draw(maybe(0.5)), types=types) as (f, args):
        draw(nested(b))
        if b.visible():
            b.do_return(pick(draw, b.visible()))
    b.call_function(f, draw(arguments_for(b, f)))
    return f


@generator("strict_function", family="calls", weight=3, block=True, produces=FUNCTION, source="CodeGenerators.swift:1703")
@st.composite
def strict_function(draw, b: Builder) -> Var:
    """A strict-mode function with a generated body, then a call."""
    params, types = draw(parameters(b, simple=True))
    with b.plain_function(params, named=draw(maybe(0.5)), strict=True, types=types) as (f, args):
        draw(nested(b))
        if b.visible():
            b.do_return(pick(draw, b.visible()))
    b.call_function(f, draw(arguments_for(b, f)))
    return f


@generator("arrow_function", family="calls", weight=3, block=True, produces=FUNCTION, source="CodeGenerators.swift:1736")
@st.composite
def arrow_function(draw, b: Builder) -> Var:
    """An arrow function with a generated body (no call: arrows are mostly used as arguments)."""
    params, types = draw(parameters(b))
    with b.arrow_function(params, types=types) as (f, args):
        draw(nested(b))
        if b.visible():
            b.do_return(pick(draw, b.visible()))
    return f


@generator("generator_function", family="calls", weight=3, block=True, toggle="generators", produces=FUNCTION,
           source="CodeGenerators.swift:1765")
@st.composite
def generator_function(draw, b: Builder) -> Var:
    """A generator function yielding values, then called."""
    params, types = draw(parameters(b))
    with b.generator_function(params, named=draw(maybe(0.5)), types=types) as (f, args):
        draw(nested(b))
        if draw(st.booleans()):
            b.yield_(draw(variable(b)))
        else:
            b.yield_each(b.create_array(draw(st.lists(variable(b), min_size=1, max_size=5))))
        if b.visible():
            b.do_return(pick(draw, b.visible()))
    b.call_function(f, draw(arguments_for(b, f)))
    return f


@generator("function_call", family="calls", weight=30, inputs=(FUNCTION,), source="CodeGenerators.swift:2342")
@st.composite
def function_call(draw, b: Builder, f: Var) -> Var | None:
    """Call a function with arguments drawn for its signature; a builtin constructor the registry
    knows is not callable without `new` (Set, Map, Promise, ...) is skipped."""
    if not b.type_of(f).MayBe(FUNCTION) or not_callable(b, f):
        return None
    return b.call_function(f, draw(arguments_for(b, f)), optional=draw(maybe(0.05)))


def not_callable(b: Builder, f: Var) -> bool:
    definition = b.code[b.defs[f]].op if f in b.defs else None
    return isinstance(definition, LoadBuiltin) and definition.builtin.calls is None and definition.builtin.constructs is not None


@generator("constructor_call", family="calls", weight=20, inputs=(FUNCTION,), source="CodeGenerators.swift:2347")
@st.composite
def constructor_call(draw, b: Builder, c: Var) -> Var | None:
    """`new C(args)`; a builtin the registry knows is not constructible (Symbol, BigInt) is skipped."""
    if not b.type_of(c).MayBe(FUNCTION):
        return None
    definition = b.code[b.defs[c]].op if c in b.defs else None
    if isinstance(definition, LoadBuiltin) and definition.builtin.constructs is None and definition.builtin.calls is not None:
        return None
    return b.construct(c, draw(arguments_for(b, c)))


@generator("method_call", family="calls", weight=30, inputs=(OBJECT,), source="CodeGenerators.swift:2268")
@st.composite
def method_call(draw, b: Builder, obj: Var) -> Var | None:
    """Call a method the receiver's type is known to have, with arguments drawn for its parameters."""
    methods = methods_of(b.type_of(obj).group)
    if not methods:
        return None
    m = pick(draw, methods)
    args = [draw(variable(b, t)) for t in m.params] if m.params else draw(st.lists(variable(b), max_size=m.arity))
    return b.call_method(obj, m, args, receiver_optional=draw(maybe(0.05)))


@generator("computed_method_call", family="calls", weight=10, inputs=(OBJECT,), source="CodeGenerators.swift:2305")
@st.composite
def computed_method_call(draw, b: Builder, obj: Var) -> Var | None:
    """`obj[name](args)` for a method name of the receiver's type."""
    methods = methods_of(b.type_of(obj).group)
    if not methods:
        return None
    m = pick(draw, methods)
    key = b.load_string(m.name)
    return b.call_computed_method(obj, key, draw(st.lists(variable(b), max_size=m.arity)))


@generator("return", family="calls", weight=3, inputs=(ANY,), context=Context.SUBROUTINE, source="CodeGenerators.swift:2429")
@st.composite
def return_(draw, b: Builder, x: Var) -> None:
    """`return x` (or a bare return)."""
    b.do_return(x if draw(maybe(0.9)) else None)


@generator("yield", family="calls", weight=5, inputs=(ANY,), context=Context.GENERATOR_FUNCTION, toggle="generators",
           source="CodeGenerators.swift:2439")
@st.composite
def yield_(draw, b: Builder, x: Var) -> Var:
    """`yield x`."""
    return b.yield_(x if draw(maybe(0.9)) else None)


@generator("yield_each", family="calls", weight=5, required=(ARRAY,), context=Context.GENERATOR_FUNCTION, toggle="generators",
           source="CodeGenerators.swift:2448")
def yield_each(b: Builder, arr: Var) -> None:
    """`yield* iterable`."""
    b.yield_each(arr)


@generator("this", family="calls", weight=3, source="CodeGenerators.swift:788")
def this(b: Builder) -> Var:
    """`this`."""
    return b.this()


@generator("arguments_access", family="calls", weight=3, context=Context.SUBROUTINE, source="CodeGenerators.swift:792")
def arguments_access(b: Builder) -> Var:
    """`arguments` (forces an activation in Rhino)."""
    return b.arguments()


@generator("function_with_arguments_access", family="calls", weight=2, block=True, produces=FUNCTION, source="CodeGenerators.swift:796")
@st.composite
def function_with_arguments_access(draw, b: Builder) -> Var:
    """A function reading `arguments`, called with a drawn number of arguments."""
    params, types = draw(parameters(b))
    with b.plain_function(params, types=types) as (f, args):
        a = b.arguments()
        draw(nested(b))
        b.do_return(pick(draw, b.visible()))
    b.call_function(f, draw(st.lists(variable(b), max_size=5)))
    return f


@generator("new_target", family="calls", weight=3, context=Context.SUBROUTINE, source="CodeGenerators.swift:3599")
def new_target(b: Builder) -> Var:
    """`new.target`."""
    return b.new_target()


@generator("unbound_function_call", family="calls", weight=10, inputs=(FUNCTION,), source="CodeGenerators.swift:2381")
@st.composite
def unbound_function_call(draw, b: Builder, f: Var) -> Var:
    """`f.call(receiver, args)`, `f.apply(receiver, [args])` or `f.bind(receiver, args)`."""
    from ..environment import Methods
    receiver = draw(variable(b, OBJECT))
    args = draw(arguments_for(b, f))
    kind = draw(st.integers(0, 2))
    if kind == 0:
        return b.call_method(f, Methods.FUNCTION_CALL, [receiver, *args])
    if kind == 1:
        return b.call_method(f, Methods.FUNCTION_APPLY, [receiver, b.create_array(args)])
    return b.call_method(f, Methods.FUNCTION_BIND, [receiver, *args[: draw(st.integers(0, len(args)))]])


@generator("api_method_call", family="api", weight=15, inputs=(OBJECT,), source="CodeGenerators.swift:3617")
@st.composite
def api_method_call(draw, b: Builder, obj: Var) -> Var | None:
    """Call a probed method of a builtin receiver with arguments of its declared types, or any values."""
    methods = methods_of(b.type_of(obj).group)
    if not methods:
        return None
    m = pick(draw, methods)
    args = [draw(variable(b, t)) for t in m.params] or draw(st.lists(variable(b), max_size=min(m.arity, 3)))
    return b.call_method(obj, m, args)


@generator("api_function_call", family="api", weight=15, inputs=(FUNCTION,), source="CodeGenerators.swift:3631")
@st.composite
def api_function_call(draw, b: Builder, f: Var) -> Var | None:
    """Call a builtin function with drawn arguments (not a constructor that needs `new`)."""
    if not b.type_of(f).MayBe(FUNCTION) or not_callable(b, f):
        return None
    return b.call_function(f, draw(arguments_for(b, f)))
