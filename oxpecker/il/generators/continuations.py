"""Continuation generators (toggle: continuations). Rhino implements continuations only in the
interpreter; the user wants the mode difference observed.

`__pause(v)` is a harness host function (`Builtins.PAUSE`): in an interpreted script run through
Context.executeScriptWithContinuations it captures the continuation, the harness records
`pause(v)` and resumes with `v` as the call's result; in compiled mode the capture fails inside
Rhino, which the comparator reports. `Continuation` is Rhino's legacy JS-level API."""

from __future__ import annotations

from hypothesis import strategies as st

from ..builder import Builder
from ..environment import INTERESTING_STRINGS, Builtins, Keys, Methods
from ..ops import BinaryOperator, Parameters
from ..program import Var
from ..strategies import declaration_kinds, generator, maybe, variable
from ..types import NUMBER


def _pause(b: Builder, value: Var) -> Var:
    return b.call_function(b.builtin(Builtins.PAUSE), [value])


@generator("pause_in_function", family="continuations", weight=4, toggle="continuations", block=True)
@st.composite
def pause_in_function(draw, b: Builder) -> Var:
    """A function that pauses mid-way; locals before and after the pause are observed."""
    with b.plain_function(Parameters(1), types=(NUMBER,)) as (f, (p,)):
        n = b.declare(b.binary(p, b.load_int(1), BinaryOperator.ADD), draw(declaration_kinds(b.options, mutable=True)))
        what = draw(st.integers(0, 3))
        value = (n, b.load_string(draw(st.sampled_from(INTERESTING_STRINGS))), b.create_array([n, p]), b.load_undefined())[what]
        r = b.declare(_pause(b, value))
        b.reassign(n, b.binary(n, b.load_int(2), BinaryOperator.MUL))
        b.do_return(b.create_array([r, n]))
    return b.declare(b.call_function(f, [draw(variable(b, NUMBER))]))


@generator("pause_in_loop", family="continuations", weight=3, toggle="continuations", block=True)
@st.composite
def pause_in_loop(draw, b: Builder) -> Var:
    """Several pauses from inside a loop, accumulating the resumed values."""
    acc = b.declare(b.create_array([]))
    with b.repeat(draw(st.integers(1, 4))) as (i, _):
        b.call_method(acc, Methods.ARRAY_PUSH, [_pause(b, i)])
    return b.declare(b.get_property(acc, Keys.LENGTH))


@generator("pause_in_try_finally", family="continuations", weight=3, toggle="continuations", block=True)
@st.composite
def pause_in_try_finally(draw, b: Builder) -> None:
    """Pause inside try/catch/finally: does finally run once, and in which order?"""
    log = b.declare(b.create_array([]))
    with b.try_() as t:
        b.call_method(log, Methods.ARRAY_PUSH, [b.load_string("try")])
        b.call_method(log, Methods.ARRAY_PUSH, [_pause(b, b.load_string("in-try"))])
        if draw(maybe(0.4)):
            b.throw(b.load_string("after-pause"))
        with t.catch() as e:
            b.call_method(log, Methods.ARRAY_PUSH, [b.binary(b.load_string("catch:"), e, BinaryOperator.ADD)])
            if draw(maybe(0.5)):
                b.call_method(log, Methods.ARRAY_PUSH, [_pause(b, b.load_string("in-catch"))])
        with t.finally_():
            b.call_method(log, Methods.ARRAY_PUSH, [b.load_string("finally")])


@generator("pause_through_frames", family="continuations", weight=2, toggle="continuations", block=True)
@st.composite
def pause_through_frames(draw, b: Builder) -> None:
    """Capturing across a non-JavaScript frame (`map` callback, `call`, `apply`, `eval`) is not
    allowed; what each mode reports for it is the question. No try/catch: Rhino's refusal is an
    IllegalStateException the harness reports as `unsupported(continuations)`, which the
    comparator knows; caught in script it becomes an InternalError that reads like a bug."""
    form = draw(st.integers(0, 3))
    if form == 0:
        with b.plain_function(Parameters(1)) as (cb, (x,)):
            b.do_return(_pause(b, x))
        b.declare(b.call_method(b.create_int_array([1, 2]), Methods.ARRAY_MAP, [cb]))
    elif form == 1:
        with b.plain_function(Parameters(0)) as (g, _):
            b.do_return(_pause(b, b.load_int(2)))
        b.declare(b.call_method(g, Methods.FUNCTION_CALL, [b.load_null()]))
    elif form == 2:
        b.declare(b.call_method(b.builtin(Builtins.PAUSE), Methods.FUNCTION_APPLY, [b.load_null(), b.create_int_array([4])]))
    else:
        with b.code_string() as cs:
            _pause(b, b.load_int(1))
        b.declare(b.call_function(b.builtin(Builtins.EVAL), [cs.result]))


@generator("native_continuation", family="continuations", weight=2, toggle="continuations", block=True)
@st.composite
def native_continuation(draw, b: Builder) -> None:
    """Rhino's legacy `new Continuation()` object: constructing it, converting it, calling it.
    No try/catch (see `pause_through_frames`): in the compiled mode the construction fails with
    an exception the harness classifies as unsupported, a known mode difference."""
    form = draw(st.integers(0, 3))
    ctor = b.builtin(Builtins.CONTINUATION)
    if form == 0:
        b.declare(b.typeof(b.construct(ctor)))
    elif form == 1:
        b.declare(b.call_function(b.builtin(Builtins.STRING), [b.construct(ctor)]))
    else:
        with b.plain_function(Parameters(0)) as (g, _):
            k = b.declare(b.construct(ctor))
            if form == 3:
                b.call_function(k, [b.load_int(7)])
            b.do_return(b.typeof(k))
        b.declare(b.call_function(g, []))
