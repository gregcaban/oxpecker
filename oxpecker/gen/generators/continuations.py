"""Continuation generators (toggle: continuations). Rhino implements continuations only in the
interpreter; the brief put them out of scope but the user wants the mode difference observed.

`__pause(v)` is a harness host function: in an interpreted script run through
Context.executeScriptWithContinuations it captures the continuation, the harness records
`pause(v)` and resumes with v as the call's result. In compiled mode the capture fails inside
Rhino, which the comparator reports. `Continuation` is Rhino's legacy JS-level API."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal


@generator("pause_in_function", family="continuations", weight=4, toggle="continuations", output=T.UNKNOWN)
def gen_pause_in_function(b: ProgramBuilder):
    """A function that pauses mid-way; locals before and after the pause are observed."""
    with b.define_function(1, param_types=[T.NUMBER]) as (f, params):
        n = b.declare(T.NUMBER)
        b.emit(f"var {n} = {params[0]} + 1;")
        r = b.declare(T.UNKNOWN)
        b.emit(f"var {r} = __pause({b.choice([str(n), literal(b, T.STRING), f'[{n}, {params[0]}]', 'undefined'])});")
        b.emit(f"{n} = {n} * 2;")
        b.emit(f"return [{r}, {n}];")
    b.fresh(T.UNKNOWN, f"{f}({literal(b, T.NUMBER)})")


@generator("pause_in_loop", family="continuations", weight=3, toggle="continuations")
def gen_pause_in_loop(b: ProgramBuilder):
    """Several pauses from inside a loop, accumulating the resumed values."""
    acc = b.fresh(T.ARRAY, "[]")
    with b.for_loop(b.rand_int(1, 4)) as i:
        b.emit(f"{acc}.push(__pause({i}));")
    b.fresh(T.UNKNOWN, f"{acc}.length")


@generator("pause_in_try_finally", family="continuations", weight=3, toggle="continuations")
def gen_pause_in_try_finally(b: ProgramBuilder):
    """Pause inside try/catch/finally: does finally run once, and in which order?"""
    log = b.fresh(T.ARRAY, "[]")
    tb = b.try_block()
    with tb.body():
        b.emit(f'{log}.push("try");')
        b.emit(f'{log}.push(__pause("in-try"));')
        if b.maybe(0.4):
            b.emit('throw "after-pause";')
    with tb.catch("e"):
        b.emit(f'{log}.push("catch:" + e);')
        if b.maybe(0.5):
            b.emit(f'{log}.push(__pause("in-catch"));')
    with tb.finally_():
        b.emit(f'{log}.push("finally");')
    tb.end()


@generator("pause_through_eval", family="continuations", weight=2, toggle="continuations", sloppy_only=True)
def gen_pause_through_eval(b: ProgramBuilder):
    """Capturing across a non-JS frame (eval / Function.prototype.call / Array.prototype.map) is
    not allowed; what each mode reports for it is the question."""
    form = b.choice(['eval("__pause(1)")', '[1, 2].map(function (x) { return __pause(x); })',
                     '(function () { return __pause(2); }).call(null)', 'new Function("return __pause(3)")()',
                     'Function.prototype.apply.call(__pause, null, [4])'])
    r = b.fresh(T.UNKNOWN, "null")
    tb = b.try_block()
    with tb.body():
        b.emit(f"{r} = {form};")
    with tb.catch("e"):
        b.emit(f"{r} = \"threw:\" + (e && e.name);")
    tb.end()


@generator("native_continuation", family="continuations", weight=2, toggle="continuations")
def gen_native_continuation(b: ProgramBuilder):
    """Rhino's legacy `new Continuation()` object: constructing it and calling it."""
    r = b.fresh(T.UNKNOWN, "null")
    tb = b.try_block()
    with tb.body():
        form = b.choice(["typeof new Continuation()", "String(new Continuation())",
                         "(function () { var k = new Continuation(); return typeof k; })()",
                         "(function () { var k = new Continuation(); if (" + str(b.rand_int(0, 1)) + ") k(7); return 1; })()"])
        b.emit(f"{r} = {form};")
    with tb.catch("e"):
        b.emit(f"{r} = \"threw:\" + (e && e.name) + \":\" + String(e).slice(0, 40);")
    tb.end()
