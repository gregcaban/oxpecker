# The JIT templates are translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License
# 2.0), Sources/Fuzzilli/CodeGen/ProgramTemplates.swift at commit
# be2811d00282320381fc8007d64873745772011d (JIT1Function 299, JIT2Functions 338, JITTrickyFunction 398).
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Program templates: the overall shape of a generated program. Every template shares the same
statement floor (D28) and places probes."""

from __future__ import annotations

from hypothesis import strategies as st

from . import generators  # noqa: F401  (registration)
from .builder import Builder
from .generators.calls import parameters
from .ops import BinaryOperator, Comparator, ProbeLabel
from .strategies import arguments_for, nested, one_statement, pick, pools, prefix, template
from .types import FUNCTION


@st.composite
def call_if_still_callable(draw, b: Builder, f: Var) -> None:
    """A function variable is assignable (`f = 1` is a shape worth testing), so a template calls
    its function only while the types at this point allow it."""
    if b.type_of(f).MayBe(FUNCTION):
        b.call_function(f, draw(arguments_for(b, f)))


@st.composite
def _probed(draw, b: Builder) -> int:
    """Top-level statements (one `st.lists` draw) with a probe after every `probe_every` of them."""
    pool = pools(b.options).for_context(b.context)
    every = b.options.probe_every
    count = [0]

    @st.composite
    def one(draw2):
        draw2(one_statement(b, pool))
        count[0] += 1
        if every and count[0] % every == 0:
            b.probe()

    draw(st.lists(one(), min_size=b.options.min_statements, max_size=b.options.max_statements))
    return count[0]


@template("plain", weight=6)
@st.composite
def plain(draw, b: Builder) -> None:
    """A prefix of values, then a flat list of generated statements with probes."""
    draw(prefix(b))
    draw(_probed(b))
    b.probe(ProbeLabel.END)


@template("jit1_function", weight=3)
@st.composite
def jit1_function(draw, b: Builder) -> None:
    """A function with a generated body called a hundred times in a loop, then with other arguments."""
    draw(prefix(b))
    draw(_probed(b))
    params, types = draw(parameters(b))
    with b.plain_function(params, named=True, types=types) as (f, args):
        draw(nested(b, n=8, lo=1))
        b.do_return(pick(draw, b.visible()))
    with b.repeat(draw(st.sampled_from((10, 100)))):
        draw(call_if_still_callable(b, f))
    b.probe(ProbeLabel.WARM)
    draw(_probed(b))
    draw(call_if_still_callable(b, f))
    b.probe(ProbeLabel.CHANGED)
    b.probe(ProbeLabel.END)


@template("jit2_functions", weight=3)
@st.composite
def jit2_functions(draw, b: Builder) -> None:
    """Two generated functions warmed up in loops, then called in alternation."""
    draw(prefix(b))
    draw(_probed(b))
    fs = []
    for _ in range(2):
        params, types = draw(parameters(b))
        with b.plain_function(params, named=True, types=types) as (f, args):
            draw(nested(b, n=6, lo=1))
            b.do_return(pick(draw, b.visible()))
        fs.append(f)
    for f in fs:
        with b.repeat(draw(st.sampled_from((10, 100)))):
            draw(call_if_still_callable(b, f))
    b.probe(ProbeLabel.WARM)
    for f in reversed(fs):
        draw(call_if_still_callable(b, f))
    b.probe(ProbeLabel.END)


@template("jit_tricky_function", weight=2)
@st.composite
def jit_tricky_function(draw, b: Builder) -> None:
    """A function whose body has a branch taken only in some iterations of the warm-up loop."""
    draw(prefix(b))
    draw(_probed(b))
    params, types = draw(parameters(b))
    with b.plain_function(params, named=True, types=types) as (f, args):
        draw(nested(b, n=4))
        i = b.declare(b.load_int(0))
        b.do_return(pick(draw, b.visible()))
    iterations = draw(st.sampled_from((10, 100)))
    with b.repeat(iterations) as (i, label):
        if draw(st.booleans()):
            cond = b.compare(i, b.load_int(draw(st.integers(0, iterations - 1))), Comparator.EQUAL)
        else:
            cond = b.compare(b.binary(i, b.load_int(draw(st.sampled_from((2, 5, 10)))), BinaryOperator.MOD), b.load_int(0), Comparator.EQUAL)
        with b.if_(cond):
            draw(nested(b, n=3))
        draw(call_if_still_callable(b, f))
    b.probe(ProbeLabel.END)
