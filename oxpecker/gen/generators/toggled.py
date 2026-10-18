"""Generators behind toggles, for features Rhino supports only partially:
let/const, generators, for-of, destructuring, spread. Enable with --toggle <name>."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal


@generator("let_const_block", family="toggled", weight=3, toggle="let")
def gen_let_const_block(b: ProgramBuilder):
    """let/const bindings in a block and a loop (per-iteration bindings captured by closures)."""
    fns = b.fresh(T.ARRAY, "[]")
    with b.block("{"):
        c = b.fresh(T.NUMBER, literal(b, T.NUMBER), kw="const")
        v = b.fresh(T.NUMBER, f"{c} + 1", kw="let")
        b.emit(f"{v} *= 2;")
        b.emit(f"{fns}.push(function () {{ return {v}; }});")
    b.emit(f"for (let i = 0; i < 3; i++) {{ {fns}.push(() => i); }}")
    b.fresh(T.ARRAY, f"{fns}.map(function (f) {{ return f(); }})")


@generator("tdz_access", family="toggled", weight=1, toggle="let")
def gen_tdz_access(b: ProgramBuilder):
    """Reading a let binding before initialisation (temporal dead zone)."""
    res = b.fresh(T.UNKNOWN, "null")
    tb = b.try_block()
    with tb.body():
        b.emit(f"{res} = typeof tdz;")
        b.emit(f"let tdz = {literal(b, T.NUMBER)};")
    with tb.catch("e"):
        b.emit(f"{res} = e.name;")
    tb.end()


@generator("generator_function", family="toggled", weight=3, toggle="generators", output=T.FUNCTION)
def gen_generator_function(b: ProgramBuilder):
    """A generator with yield inside loops/try-finally, driven by next()/return()/throw()."""
    g = b.declare(T.FUNCTION)
    log = b.fresh(T.ARRAY, "[]")
    with b.block(f"function* {g}(n) {{", kind="function"):
        b.scopes[-1].vars.append(Var("n", T.NUMBER))
        shape = b.rand_int(0, 2)
        if shape == 0:
            b.emit("for (var i = 0; i < n; i++) { yield i * 2; }")
            b.emit("return 'done';")
        elif shape == 1:
            b.emit(f"try {{ yield 1; yield 2; }} finally {{ {log}.push('finally'); }}")
        else:
            b.emit("var r = yield n; yield r + 1;")
    it = b.fresh(T.OBJECT, f"{g}({b.rand_int(0, 4)})")
    for _ in range(b.rand_int(1, 3)):
        step = b.choice([f"{it}.next()", f"{it}.next({literal(b, T.NUMBER)})", f"{it}.return('r')", f"{it}.throw(new Error('t'))"])
        tb = b.try_block()
        with tb.body():
            b.emit(f"{log}.push(JSON.stringify({step}));")
        with tb.catch("e"):
            b.emit(f"{log}.push('threw:' + e.message);")
        tb.end()


@generator("for_of", family="toggled", weight=3, toggle="for-of")
def gen_for_of(b: ProgramBuilder):
    """for-of over an array, string, Map, Set or a custom iterable."""
    src = b.choice([b.expr(T.ARRAY, literal), b.expr(T.STRING, literal), b.expr(T.MAP, literal), b.expr(T.SET, literal),
                    "{ [Symbol.iterator]() { var i = 0; return { next() { return { value: i, done: i++ > 2 }; } }; } }"])
    acc = b.fresh(T.ARRAY, "[]")
    with b.block(f"for (var it of {src}) {{"):
        b.emit(f"{acc}.push(it);")
        if b.maybe(0.3):
            b.emit("if (acc.length > 2) break;".replace("acc", str(acc)))


@generator("destructuring", family="toggled", weight=3, toggle="destructuring")
def gen_destructuring(b: ProgramBuilder):
    """Array/object destructuring with defaults, holes and rest."""
    form = b.choice([
        f"var [d1, , d2 = {literal(b, T.NUMBER)}, ...drest] = {b.expr(T.ARRAY, literal)};",
        f"var {{a: d1 = 1, b: d2, ...drest}} = {b.expr(T.OBJECT, literal)};",
        f"var [d1 = 1, [d2] = [2]] = [];",
        f"var {{length: d1}} = {b.expr(T.STRING, literal)};",
    ])
    tb = b.try_block()
    with tb.body():
        b.emit(form)
        b.emit(f"__probe(\"destructure\", d1, d2, typeof drest);" if b.opts.probes else "void [d1, d2];")
    with tb.catch("e"):
        b.emit("// destructuring null/undefined throws")
    tb.end()


@generator("spread", family="toggled", weight=3, toggle="spread")
def gen_spread(b: ProgramBuilder):
    """Spread in array and object literals (call spread is excluded: it crashes Rhino)."""
    a = b.pick_or_make(T.ARRAY)
    # Spread in a *call* (`f(...a)`) crashes Rhino b95f75d in both modes with a raw RuntimeException:
    # see tests/regressions/spread-in-call-crashes-both-modes.js. Only array/object spread here.
    form = b.choice([f"[0, ...{a}, 1]", f"[...{b.expr(T.STRING, literal)}]",
                     f"{{...{b.expr(T.OBJECT, literal)}, z: 1}}", f"[...{a}, ...{a}].length", f"[...{a}].indexOf(1)"])
    tb = b.try_block()
    with tb.body():
        b.fresh(T.UNKNOWN, form)
    with tb.catch("e"):
        b.emit("// not iterable")
    tb.end()
