# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/CodeGen/CodeGenerators.swift at commit be2811d00282320381fc8007d64873745772011d
# (control flow 2712-3137, BlockStatement/BlockBreak 3338-3351, WithStatement 3296, Eval 3323).
# Begin/End stubs are one Python block each (docs/design-generation-layer.md §7.2); the code between
# them is `nested(b)`. Weights from CodeGeneratorWeights.swift.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Control flow: conditionals, loops, breaks, try/catch, switch, blocks, with, eval."""

from __future__ import annotations

from hypothesis import strategies as st

from ..analysis import LOOP_BODIES, Storage
from ..builder import Builder
from ..environment import Builtins
from ..ops import BinaryOperator, Comparator, Context, DeclarationKind, ForEachKind, UnaryOperator
from ..program import Var
from ..strategies import generator, maybe, nested, pick, variable
from ..types import ANY, BOOLEAN, OBJECT, Type

LOOP_DECLARATIONS = st.sampled_from((DeclarationKind.VAR, DeclarationKind.LET))   # a for-header variable is reassigned by the afterthought


def loop_labels(b: Builder) -> list[Var]:
    """Visible labels of enclosing loops (not of blocks, ifs or switches: `break label` to those is
    a BlockBreak, and `continue label` needs a loop)."""
    return [v for v, i in b.labels.items() if b.in_scope(v) and v not in b._hidden and isinstance(b.code[i].op, LOOP_BODIES)]


@generator("if_else", family="control", weight=10, inputs=(BOOLEAN,), block=True, source="CodeGenerators.swift:2712")
@st.composite
def if_else(draw, b: Builder, cond: Var) -> None:
    """`if (cond) {...} else {...}` with generated bodies; the else is optional."""
    with b.if_(cond, inverted=draw(maybe(0.2))) as br:
        draw(nested(b))
        if draw(maybe(0.6)):
            with br.else_():
                draw(nested(b))


@generator("compare_with_if_else", family="control", weight=15, inputs=(ANY, ANY), block=True, source="CodeGenerators.swift:2727")
@st.composite
def compare_with_if_else(draw, b: Builder, lhs: Var, rhs: Var) -> None:
    """`if (lhs cmp rhs) {...} else {...}`."""
    cond = b.compare(lhs, rhs, draw(st.sampled_from(Comparator)))
    with b.if_(cond) as br:
        draw(nested(b))
        with br.else_():
            draw(nested(b))


@generator("while_loop", family="control", weight=15, block=True, source="CodeGenerators.swift:2808")
@st.composite
def while_loop(draw, b: Builder) -> None:
    """A counted while loop: `var i = 0; while (i < n) { ...; i++ }`."""
    i = b.declare(b.load_int(0), draw(st.sampled_from((DeclarationKind.VAR, DeclarationKind.LET))))
    with b.while_loop() as loop:
        cond = b.compare(i, b.load_int(draw(st.integers(0, 10))), Comparator.LESS)
        with loop.body(cond):
            draw(nested(b))
            b.unary(UnaryOperator.POST_INC, i)


@generator("do_while_loop", family="control", weight=15, block=True, source="CodeGenerators.swift:2831")
@st.composite
def do_while_loop(draw, b: Builder) -> None:
    """`var i = 0; do { ...; i++ } while (i < n)`."""
    i = b.declare(b.load_int(0), draw(st.sampled_from((DeclarationKind.VAR, DeclarationKind.LET))))
    with b.do_while_loop() as loop:
        draw(nested(b))
        b.unary(UnaryOperator.POST_INC, i)
        with loop.header() as h:
            h.done(b.compare(i, b.load_int(draw(st.integers(0, 10))), Comparator.LESS))


@generator("simple_for_loop", family="control", weight=10, block=True, source="CodeGenerators.swift:2845")
@st.composite
def simple_for_loop(draw, b: Builder) -> None:
    """`for (let i = 0; i < n; i++) {...}`."""
    with b.for_loop() as loop:
        start = b.load_int(0)
        with loop.condition([start], declaration=draw(LOOP_DECLARATIONS)) as (i,):
            cond = b.compare(i, b.load_int(draw(st.integers(0, 10))), Comparator.LESS)
        with loop.afterthought(cond) as (i2,):
            b.unary(UnaryOperator.POST_INC, i2)
        with loop.body():
            draw(nested(b))


@generator("complex_for_loop", family="control", weight=10, block=True, source="CodeGenerators.swift:2878")
@st.composite
def complex_for_loop(draw, b: Builder) -> None:
    """A for loop with two loop variables moving towards each other, or a count-down with an empty header part."""
    with b.for_loop() as loop:
        lo, hi = b.load_int(0), b.load_int(draw(st.integers(1, 10)))
        with loop.condition([lo, hi], declaration=draw(LOOP_DECLARATIONS)) as (i, j):
            cond = b.compare(i, j, Comparator.LESS)
        with loop.afterthought(cond) as (i2, j2):
            b.unary(UnaryOperator.POST_INC, i2)
            b.unary(UnaryOperator.POST_DEC, j2)
        with loop.body() as ((i3, j3), label):
            draw(nested(b))


@generator("for_in_loop", family="control", weight=10, inputs=(OBJECT,), block=True, source="CodeGenerators.swift:2901")
@st.composite
def for_in_loop(draw, b: Builder, obj: Var) -> None:
    """`for (var k in obj) {...}`."""
    with b.for_each(ForEachKind.IN, obj, declaration=draw(LOOP_DECLARATIONS)):
        draw(nested(b))


@generator("for_of_loop", family="control", weight=10, inputs=(Type.array(),), block=True, toggle="for-of", source="CodeGenerators.swift:2909")
@st.composite
def for_of_loop(draw, b: Builder, iterable: Var) -> None:
    """`for (var x of iterable) {...}`."""
    with b.for_each(ForEachKind.OF, iterable, declaration=draw(LOOP_DECLARATIONS)):
        draw(nested(b))


@generator("repeat_loop", family="control", weight=10, block=True, source="CodeGenerators.swift:2982")
@st.composite
def repeat_loop(draw, b: Builder) -> None:
    """`for (let i = 0; i < n; i++) {...}` for a drawn n."""
    with b.repeat(draw(st.integers(2, 100)), declaration=draw(LOOP_DECLARATIONS)):
        draw(nested(b))


@generator("loop_break", family="control", weight=5, context=Context.LOOP, source="CodeGenerators.swift:3032")
@st.composite
def loop_break(draw, b: Builder) -> None:
    """`break` or `break label` inside a loop."""
    labels = loop_labels(b)
    if labels and draw(maybe(0.2)):
        b.break_(pick(draw, labels))
    else:
        b.break_()


@generator("loop_continue", family="control", weight=5, context=Context.LOOP, source="CodeGenerators.swift:3037")
@st.composite
def loop_continue(draw, b: Builder) -> None:
    """`continue` or `continue label` inside a loop."""
    labels = loop_labels(b)
    if labels and draw(maybe(0.2)):
        b.continue_(pick(draw, labels))
    else:
        b.continue_()


@generator("try_catch_finally", family="control", weight=5, block=True, source="CodeGenerators.swift:3042")
@st.composite
def try_catch_finally(draw, b: Builder) -> None:
    """try/catch/finally around generated statements."""
    with b.try_() as t:
        draw(nested(b))
        with t.catch():
            draw(nested(b))
        with t.finally_():
            draw(nested(b))


@generator("try_catch", family="control", weight=5, block=True, source="CodeGenerators.swift:3074")
@st.composite
def try_catch(draw, b: Builder) -> None:
    """try/catch around generated statements, with a possible throw; the catch inspects the error."""
    with b.try_() as t:
        draw(nested(b))
        if draw(maybe(0.3)):
            b.throw(draw(variable(b)))
        with t.catch() as e:
            draw(nested(b))
            b.typeof(e)


@generator("try_finally", family="control", weight=5, block=True, source="CodeGenerators.swift:3099")
@st.composite
def try_finally(draw, b: Builder) -> None:
    """try/finally around generated statements."""
    with b.try_() as t:
        draw(nested(b))
        with t.finally_():
            draw(nested(b))


@generator("throw", family="control", weight=1, inputs=(ANY,), source="CodeGenerators.swift:3124")
def throw(b: Builder, x: Var) -> None:
    """`throw x`."""
    b.throw(x)


@generator("conditional_throw", family="control", weight=1, inputs=(BOOLEAN,), block=True, source="CodeGenerators.swift:3129")
@st.composite
def conditional_throw(draw, b: Builder, cond: Var) -> None:
    """`if (cond) throw x` (or `if (!cond)`)."""
    with b.if_(cond, inverted=draw(st.booleans())):
        b.throw(draw(variable(b)))


@generator("block_statement", family="control", weight=1, block=True, source="CodeGenerators.swift:3338")
@st.composite
def block_statement(draw, b: Builder) -> None:
    """A bare block `{ ... }`, possibly labelled and broken out of."""
    with b.block() as label:
        draw(nested(b))
        if draw(maybe(0.3)):
            b.block_break(label)


@generator("switch", family="control", weight=5, inputs=(ANY,), block=True, source="CodeGenerators.swift:2745")
@st.composite
def switch(draw, b: Builder, value: Var) -> None:
    """`switch (value)` with drawn cases, fallthrough, and an optional default."""
    cases = draw(st.lists(variable(b), max_size=3))
    with b.switch(value) as sw:
        for c in cases:
            with sw.case(c, falls_through=draw(maybe(0.1))):
                draw(nested(b))
        if draw(maybe(0.5)):
            with sw.default(falls_through=draw(maybe(0.1))):
                draw(nested(b))


@generator("switch_break", family="control", weight=5, context=Context.SWITCH_CASE, source="CodeGenerators.swift:2804")
def switch_break(b: Builder) -> None:
    """`break` inside a switch case."""
    b.switch_break()


@generator("with_statement", family="control", weight=3, inputs=(OBJECT,), block=True, sloppy_only=True, toggle="with",
           source="CodeGenerators.swift:3296")
@st.composite
def with_statement(draw, b: Builder, obj: Var) -> None:
    """`with (obj) {...}`: names inside resolve against the object (forces an activation)."""
    with b.with_(obj):
        draw(nested(b))


@generator("eval", family="control", weight=3, block=True, toggle="eval", source="CodeGenerators.swift:3323")
@st.composite
def eval_(draw, b: Builder) -> Var:
    """A generated program lifted to a string and evaluated with direct `eval`."""
    with b.code_string() as cs:
        draw(nested(b, lo=1))
    return b.call_function(b.builtin(Builtins.EVAL), [cs.result])
