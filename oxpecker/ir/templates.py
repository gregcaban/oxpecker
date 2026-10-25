"""Program templates (port of `gen/templates.py`): the overall shape of a program, as strategies.
A template drives the top-level statement list (one `st.lists` draw) and places probes.

Every template's MAIN statement list has the same floor, `opts.min_statements` (decision D28).
Hypothesis minimises choices, not instructions, and a template's fixed scaffolding costs almost no
choices; a template that needed fewer generated statements than `plain` would therefore be the
shrinker's favourite place to put any failure, wrapped in boilerplate it does not need."""

from __future__ import annotations

from hypothesis import strategies as st

from ..gen.types import T
from .builder import Builder, nested, pick, pick_or_make, statements, target
from .ops import CompoundAssign, CreateArray, GetElement, GetProperty
from .registry import template


def top_level(b: Builder, lo: int | None = None, hi: int | None = None) -> st.SearchStrategy:
    """The top-level statement list, with a probe after each statement when probing is on."""
    lo = b.opts.min_statements if lo is None else lo
    hi = b.opts.max_statements if hi is None else hi
    return statements(b, lo, hi, probe=bool(b.opts.probe_every))


@template("plain", weight=6)
@st.composite
def plain(draw, b: Builder):
    """A flat list of generated statements with a probe after each."""
    draw(top_level(b))
    b.probe("end")


@template("warmup_then_type_change", weight=4)
@st.composite
def warmup_then_type_change(draw, b: Builder):
    """Define a function over numeric parameters, call it in a loop with one type, then once
    with another type (string/object/undefined/BigInt), probing every result."""
    from .generators.compiler import INT32_ARGS, NUMBER_ARGS, PERTURB, numeric_body, push, spelled

    third = max(0, b.opts.max_statements // 3)
    draw(top_level(b, hi=max(b.opts.min_statements, third)))                 # main list: the common floor
    with b.function(2, param_types=[T.NUMBER, T.NUMBER]) as (f, params):
        locals_ = draw(numeric_body(b, params, 1, 3))
        draw(nested(b, 2))
        b.ret(draw(pick(locals_)))
    acc = b.emit(CreateArray(), (), hint="acc")
    first = draw(spelled(b, INT32_ARGS + [("1.5", T.NUMBER), ("0.1", T.NUMBER)]))
    with b.for_(draw(st.integers(2, 8))) as i:
        push(b, acc, b.call(f, first, i))
    b.probe("warm")

    @st.composite
    def changed(draw):
        x = draw(spelled(b, PERTURB + NUMBER_ARGS))
        y = draw(spelled(b, PERTURB + INT32_ARGS))
        push(b, acc, b.call(f, x, y))
        b.probe("changed")

    draw(st.lists(changed(), min_size=1, max_size=3))
    draw(top_level(b, 0, third))
    b.probe("end")


@template("mutate_between_calls", weight=3)
@st.composite
def mutate_between_calls(draw, b: Builder):
    """Call a function on an object repeatedly while generated statements mutate the object's
    properties and prototype between calls."""
    from .generators.compiler import push

    draw(top_level(b, hi=max(b.opts.min_statements, 3)))
    o = draw(pick_or_make(b, T.OBJECT))
    with b.function(1, param_types=[T.OBJECT]) as (f, (p,)):
        b.ret(b.emit(CreateArray(), (
            b.emit(GetProperty("x"), (p,)), b.emit(GetProperty("a"), (p,)),
            b.unary("typeof", b.emit(GetProperty("length"), (p,))),
            b.emit(GetElement(), (p, b.const(T.INT32, "0"))),
            b.binary("in", b.const(T.STRING, '"x"'), p))))
    acc = b.emit(CreateArray(), (), hint="acc")

    @st.composite
    def round_(draw):
        push(b, acc, b.call(f, o))
        draw(nested(b, 2))
        b.probe()

    draw(st.lists(round_(), min_size=2, max_size=4))
    push(b, acc, b.call(f, o))
    b.probe("end")


@template("deep_closures", weight=2)
@st.composite
def deep_closures(draw, b: Builder):
    """Nested functions several levels deep, each capturing and mutating outer variables."""
    from ..gen.generators.values import INT32S

    draw(top_level(b, hi=max(b.opts.min_statements, 4)))
    captured = [draw(target(b, T.NUMBER)), draw(target(b, T.NUMBER))]
    depth = draw(st.integers(2, max(2, min(4, b.opts.max_depth + 1))))
    arg = lambda: b.const(T.INT32, draw(st.sampled_from(INT32S)))

    def level(k: int) -> int:
        with b.function(1, param_types=[T.NUMBER]) as (f, (a,)):
            b.emit(CompoundAssign("+"), (captured[k % 2], a))
            draw(nested(b, 1))
            if k + 1 < depth:
                inner = level(k + 1)
                b.ret(b.call(inner, arg()))
            else:
                b.ret(b.binary("+", captured[0], captured[1]))
        return f

    outer = level(0)
    b.call(outer, arg())
    b.probe("end")


@template("strict_vs_sloppy_body", weight=2)
@st.composite
def strict_vs_sloppy_body(draw, b: Builder):
    """The same generated statements inside a strict and a sloppy function, results compared."""
    from .generators.compiler import push

    # `with` / direct eval are SyntaxErrors inside the strict copy: no sloppy-only generators.
    saved, b.pool = b.pool, [g for g in b.pool if not g.sloppy_only] or b.pool
    with b.function(0, strict=True) as (fs, _):
        start = len(b.instrs)
        lo = b.opts.min_statements
        draw(statements(b, lo, max(lo, b.opts.max_statements // 2)))
        body = b.instrs[start:]
        inner = list(b.frames[-1].vars)
        b.ret(b.emit(CreateArray(), tuple(inner)))
    b.pool = saved
    with b.function(0) as (fl, _):
        rn = b.replay(body, {})
        b.ret(b.emit(CreateArray(), tuple(rn[v] for v in inner)))
    b.pin(start)                                    # both copies lift the same way (decision D16)
    res = b.emit(CreateArray(), (), hint="acc")
    for f in (fs, fl):
        with b.try_() as t:
            push(b, res, b.call(f))
            with t.catch() as e:
                push(b, res, b.binary("+", b.const(T.STRING, '"threw:"'), b.error_name(e)))
    b.probe("end")
