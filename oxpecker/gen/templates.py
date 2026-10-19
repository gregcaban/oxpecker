"""Program templates: the overall shape of a generated program. A template drives the
top-level statement list (one list draw, so the shrinker can delete statements) and
places probes."""

from __future__ import annotations

from typing import Any

from .builder import ProgramBuilder, Var
from .generators.compiler import PERTURB, numeric_body
from .generators.values import INT32S, NUMBERS
from .registry import template
from .types import T


def statements(b: ProgramBuilder, lo: int | None = None, hi: int | None = None) -> int:
    """Draws the top-level statement list and runs it, probing as configured."""
    lo = b.opts.min_statements if lo is None else lo
    hi = b.opts.max_statements if hi is None else hi
    gens = b.rand.sample_list(b.pool, lo, hi)
    for n, g in enumerate(gens, 1):
        b.run(g)
        if b.opts.probe_every and n % b.opts.probe_every == 0:
            b.probe()
    return len(gens)


@template("plain", weight=6)
def tpl_plain(b: ProgramBuilder):
    """A flat list of generated statements with a probe after each."""
    statements(b)
    b.probe("end")


@template("warmup_then_type_change", weight=4)
def tpl_warmup_then_type_change(b: ProgramBuilder):
    """Define a function over numeric parameters, call it in a loop with one type, then once
    with another type (string/object/undefined/BigInt), probing every result."""
    statements(b, 0, max(0, b.opts.max_statements // 3))
    with b.define_function(2, param_types=[T.NUMBER, T.NUMBER]) as (f, params):
        locals_ = numeric_body(b, params, b.rand_int(1, 3))
        b.run_some(2)
        b.emit(f"return {b.choice(locals_)};")
    acc = b.fresh(T.ARRAY, "[]")
    first = b.choice(INT32S + ["1.5", "0.1"])
    with b.for_loop(b.rand_int(2, 8)) as i:
        b.emit(f"{acc}.push({f}({first}, {i}));")
    b.probe("warm")
    for _ in range(b.rand_int(1, 3)):
        b.emit(f"{acc}.push({f}({b.choice(PERTURB + NUMBERS)}, {b.choice(PERTURB + INT32S)}));")
        b.probe("changed")
    statements(b, 0, max(0, b.opts.max_statements // 3))
    b.probe("end")


@template("mutate_between_calls", weight=3)
def tpl_mutate_between_calls(b: ProgramBuilder):
    """Call a function on an object repeatedly while generated statements mutate the object's
    properties and prototype between calls."""
    statements(b, 1, 3)
    o = b.pick_or_make(T.OBJECT)
    with b.define_function(1, param_types=[T.OBJECT]) as (f, params):
        p = params[0]
        b.emit(f"return [{p}.x, {p}.a, typeof {p}.length, {p}[0], \"x\" in {p}];")
    acc = b.fresh(T.ARRAY, "[]")
    for _ in range(b.rand_int(2, 4)):
        b.emit(f"{acc}.push({f}({o}));")
        b.run_some(2)
        b.probe()
    b.emit(f"{acc}.push({f}({o}));")
    b.probe("end")


@template("deep_closures", weight=2)
def tpl_deep_closures(b: ProgramBuilder):
    """Nested functions several levels deep, each capturing and mutating outer variables."""
    statements(b, 1, 4)
    captured = [b.pick_or_make(T.NUMBER) for _ in range(2)]
    depth = b.rand_int(2, min(4, b.opts.max_depth + 1))
    fvars: list[Var] = []
    for level in range(depth):
        b.scopes.append(type(b.scopes[-1])("function"))
        f = b.declare(T.FUNCTION)
        fvars.append(f)
        b.emit(f"function {f}(a{level}) {{")
        b.indent += 1
        b.scopes[-1].vars.append(Var(f"a{level}", T.NUMBER))
        b.emit(f"{captured[level % 2]} += a{level};")
        b.run_some(1)
    b.emit(f"return {captured[0]} + {captured[1]};")
    for level in reversed(range(depth)):
        b.indent -= 1
        if level > 0:
            b.emit("}")
            b.emit(f"return {fvars[level]}({b.choice(INT32S)});")
        else:
            b.emit("}")
        b.scopes.pop()
    b.fresh(T.NUMBER, f"{fvars[0]}({b.choice(INT32S)})")
    b.probe("end")


@template("strict_vs_sloppy_body", weight=2)
def tpl_strict_vs_sloppy_body(b: ProgramBuilder):
    """The same generated statements inside a strict and a sloppy function, results compared."""
    # `with` / direct eval are SyntaxErrors inside the strict copy: drop sloppy-only generators.
    saved_pool = b.pool
    b.pool = [g for g in b.pool if not g.sloppy_only] or b.pool
    with b.capture() as body:
        b.indent += 1
        b.scopes.append(type(b.scopes[-1])("function"))
        statements(b, 1, max(1, b.opts.max_statements // 2))
        b.scopes.pop()
        b.indent -= 1
    b.pool = saved_pool
    inner_vars = sorted({ln.text.split()[1] for ln in body if ln.text.startswith("var ") and ln.indent == 1})
    ret = "return [" + ", ".join(v for v in inner_vars if v) + "];"
    fs = b.declare(T.FUNCTION)
    fl = b.declare(T.FUNCTION)
    b.emit(f"function {fs}() {{")
    b.indent += 1
    b.emit('"use strict";')
    b.indent -= 1
    b.emit_lines(body)
    b.indent += 1
    b.emit(ret)
    b.indent -= 1
    b.emit("}")
    b.emit(f"function {fl}() {{")
    b.emit_lines(body)
    b.indent += 1
    b.emit(ret)
    b.indent -= 1
    b.emit("}")
    res = b.fresh(T.ARRAY, "[]")
    for f in (fs, fl):
        tb = b.try_block()
        with tb.body():
            b.emit(f"{res}.push({f}());")
        with tb.catch("e"):
            b.emit(f"{res}.push(\"threw:\" + (e && e.name));")
        tb.end()
    b.probe("end")


def run_template(tpl: Any, b: ProgramBuilder) -> None:
    saved = b.current_gen
    b.current_gen = tpl.name
    try:
        tpl.fn(b)
    finally:
        b.current_gen = saved
