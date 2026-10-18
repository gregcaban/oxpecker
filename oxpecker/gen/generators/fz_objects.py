"""Generators ported from Fuzzilli's CodeGenerators.swift: builtin objects/references, `this`,
named (undeclared) variables, builtin overwrites, and object-shape/prototype-chain generators.

Rhino-only: no class syntax, no destructuring/spread, var-scoped. Kept close to Fuzzilli's
originals but rewritten against oxpecker's ProgramBuilder so the output reads as plain JS.
"""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import INT32S, literal

FLOATS = ["0.5", "1.5", "2.5", "-0.5", "3.14159", "0.1", "0.30000000000000004", "NaN", "Infinity",
          "-Infinity", "-0", "1e21", "5e-324", "1.7976931348623157e308"]

# Builtins Rhino actually implements at the global scope.
BUILTINS = [
    "Object", "Array", "Math", "JSON", "Reflect", "Symbol", "Map", "Set", "WeakMap", "WeakSet",
    "Promise", "Proxy", "Number", "String", "Boolean", "Date", "RegExp",
    "Error", "TypeError", "RangeError", "SyntaxError", "EvalError", "ReferenceError", "URIError",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array",
    "Int32Array", "Uint32Array", "Float32Array", "Float64Array",
    "BigInt", "globalThis", "undefined", "NaN", "Infinity", "encodeURIComponent", "decodeURIComponent",
]

# Undeclared identifiers used by named_variable: deliberately reused across calls so that an
# earlier implicit-global assignment is visible to a later read.
NAMED_VARS = ["zzNamed1", "zzNamed2", "zzNamed3"]

# Builtin prototype methods, keyed by the constructor whose .prototype they live on, with the
# type of a representative argument (None means no argument).
PROTO_METHODS: dict[str, list[tuple[str, T | None]]] = {
    "Array": [("join", T.STRING), ("slice", T.INT32), ("indexOf", T.UNKNOWN), ("includes", T.UNKNOWN),
              ("toString", None), ("concat", T.ARRAY), ("reverse", None), ("pop", None),
              ("forEach", T.FUNCTION), ("map", T.FUNCTION)],
    "String": [("charAt", T.INT32), ("indexOf", T.STRING), ("slice", T.INT32), ("toUpperCase", None),
               ("trim", None), ("split", T.STRING), ("repeat", T.INT32), ("valueOf", None)],
    "Object": [("hasOwnProperty", T.STRING), ("toString", None), ("valueOf", None),
               ("isPrototypeOf", T.OBJECT), ("propertyIsEnumerable", T.STRING)],
}
# Receivers deliberately of the "wrong" type for the method being called.
RECEIVERS = [T.NUMBER, T.STRING, T.BOOLEAN, T.OBJECT, T.ARRAY, T.UNDEFINED, T.NULL]


@generator("int_array", family="objects", weight=3, output=T.ARRAY)
def gen_int_array(b: ProgramBuilder):
    """An array literal of only small integers (packed SMI/int elements kind)."""
    vals = [b.choice(INT32S) for _ in range(b.rand_int(1, 8))]
    return b.fresh(T.ARRAY, "[" + ", ".join(vals) + "]")


@generator("float_array", family="objects", weight=3, output=T.ARRAY)
def gen_float_array(b: ProgramBuilder):
    """An array literal of only doubles (forces a packed double elements kind, never ints)."""
    vals = [b.choice(FLOATS) for _ in range(b.rand_int(1, 8))]
    return b.fresh(T.ARRAY, "[" + ", ".join(vals) + "]")


@generator("holey_array", family="objects", weight=3, output=T.ARRAY)
def gen_holey_array(b: ProgramBuilder):
    """An array literal with at least one elision (hole) surrounded by ints, doubles or existing
    values, occasionally followed by an index read (exercises the holey elements kind)."""
    size = b.rand_int(2, 7)
    hole_idx = b.rand_int(0, size - 2)  # never the last slot: a trailing hole there is dropped
    kind = b.rand_int(0, 2)

    def one() -> str:
        if kind == 0:
            return b.choice(INT32S)
        if kind == 1:
            return b.choice(FLOATS)
        return b.expr(T.UNKNOWN, literal)

    items = ["" if (i == hole_idx or b.maybe(0.15)) else one() for i in range(size)]
    a = b.fresh(T.ARRAY, "[" + ", ".join(items) + "]")
    if b.maybe(0.5):
        b.fresh(T.UNKNOWN, f"{a}[{b.rand_int(0, size - 1)}]")
    return a


@generator("builtin_instance", family="objects", weight=3, output=T.OBJECT)
def gen_builtin_instance(b: ProgramBuilder):
    """`new` of a builtin container type: a sized Array, or an empty Map/WeakMap/Set/WeakSet/Date."""
    kind = b.choice(["Array", "Map", "WeakMap", "Set", "WeakSet", "Date"])
    if kind == "Array":
        return b.fresh(T.ARRAY, f"new Array({b.rand_int(0, 4096)})")
    return b.fresh(T.OBJECT, f"new {kind}()")


@generator("builtin_prototype_call", family="objects", weight=3, output=T.UNKNOWN)
def gen_builtin_prototype_call(b: ProgramBuilder):
    """A builtin prototype method invoked via .call/.apply on a receiver of a different type than
    it normally expects (Array.prototype.join on a number, Object.prototype.hasOwnProperty on a
    string, ...): exercises generic-method / receiver-brand-check divergence."""
    builtin, methods = b.choice(list(PROTO_METHODS.items()))
    name, arg_t = b.choice(methods)
    args = "" if arg_t is None else b.expr(arg_t, literal)
    recv = b.expr(b.choice(RECEIVERS), literal)
    tb = b.try_block()
    with tb.body():
        if b.maybe(0.5):
            rest = f", {args}" if args else ""
            b.fresh(T.UNKNOWN, f"{builtin}.prototype.{name}.call({recv}{rest})")
        else:
            arglist = f"[{args}]" if args else "[]"
            b.fresh(T.UNKNOWN, f"{builtin}.prototype.{name}.apply({recv}, {arglist})")
    with tb.catch("e"):
        b.emit("// receiver doesn't satisfy the method's internal slot / brand check")
    tb.end()


@generator("object_builder_function", family="objects", weight=3, output=T.FUNCTION)
def gen_object_builder_function(b: ProgramBuilder):
    """A function that builds and returns an object literal from its argument, called several
    times at the same call site (warms one object-literal allocation site)."""
    with b.define_function(1, param_types=[T.NUMBER]) as (f, params):
        keys = list(dict.fromkeys(b.choice(["a", "b", "x", "y", "value"]) for _ in range(b.rand_int(1, 4))))
        props = ", ".join(f"{k}: {b.choice([params[0].name, b.expr(T.UNKNOWN, literal)])}" for k in keys)
        b.emit(f"return {{{props}}};")
    for _ in range(b.rand_int(3, 6)):
        b.fresh(T.OBJECT, f"{f}({b.expr(T.NUMBER, literal)})")
    return f


@generator("object_constructor", family="objects", weight=3, output=T.FUNCTION)
def gen_object_constructor(b: ProgramBuilder):
    """A constructor function that assigns a few properties via `this.x = ...`, instantiated with
    `new` several times."""
    nparams = b.rand_int(0, 2)
    with b.define_function(nparams, kind="function") as (f, params):
        keys = ["a", "b", "x", "y"]
        for i, key in enumerate(b.choice(keys) for _ in range(b.rand_int(1, 3))):
            val = params[i].name if i < len(params) else b.expr(T.UNKNOWN, literal)
            b.emit(f"this.{key} = {val};")
    for _ in range(b.rand_int(1, 3)):
        args = ", ".join(b.expr(T.UNKNOWN, literal) for _ in range(b.rand_int(0, 2)))
        b.fresh(T.OBJECT, f"new {f}({args})")
    return f


@generator("this_value", family="objects", weight=2, output=T.UNKNOWN)
def gen_this_value(b: ProgramBuilder):
    """`this`: at top level, inside a sloppy function (boxed global), inside a strict function
    (undefined when called plainly), and as a method's receiver via .call()."""
    kind = b.rand_int(0, 3)
    if kind == 0:
        b.fresh(T.UNKNOWN, "this")
    elif kind == 1:
        f = b.declare(T.FUNCTION)
        b.emit(f"function {f}() {{ return this; }}")
        b.fresh(T.UNKNOWN, f"{f}()    // sloppy: boxed global object")
    elif kind == 2:
        f = b.declare(T.FUNCTION)
        b.emit(f'function {f}() {{ "use strict"; return this; }}')
        b.fresh(T.UNKNOWN, f"{f}()    // strict: undefined when called plainly")
    else:
        recv = b.pick_or_make(T.OBJECT)
        b.fresh(T.UNKNOWN, f"(function () {{ return this; }}).call({recv})")


@generator("builtin_ref", family="objects", weight=3, output=T.UNKNOWN)
def gen_builtin_ref(b: ProgramBuilder):
    """A reference to a global builtin by name: a constructor, namespace object, global function
    or well-known value, used directly or probed with typeof."""
    name = b.choice(BUILTINS)
    if b.maybe(0.5):
        b.fresh(T.UNKNOWN, name)
    else:
        b.fresh(T.STRING, f"typeof {name}")


@generator("named_variable", family="objects", weight=2)
def gen_named_variable(b: ProgramBuilder):
    """An undeclared identifier: typeof (never throws), assignment (implicit global in sloppy
    mode, ReferenceError in strict mode), and a bare read (ReferenceError before any assignment)."""
    name = b.choice(NAMED_VARS)
    kind = b.rand_int(0, 2)
    if kind == 0:
        b.fresh(T.STRING, f"typeof {name}")
    elif kind == 1:
        if b.opts.strict:
            tb = b.try_block()
            with tb.body():
                b.emit(f"{name} = {b.expr(T.UNKNOWN, literal)};")
            with tb.catch("e"):
                b.emit("// strict mode: assigning an undeclared identifier throws")
            tb.end()
        else:
            b.emit(f"{name} = {b.expr(T.UNKNOWN, literal)};    // sloppy mode: implicit global")
    else:
        tb = b.try_block()
        with tb.body():
            b.fresh(T.UNKNOWN, name)
        with tb.catch("e"):
            b.emit("// ReferenceError: read before any assignment created the global")
        tb.end()


# Builtin (target, property, replacement, use) tuples. `use` is built against the saved original
# so the replacement is always restored, keeping the rest of the program well-behaved.
def _builtin_overwrite_case(b: ProgramBuilder, kind: int) -> tuple[str, str, str, str]:
    if kind == 0:
        arr = b.pick_or_make(T.ARRAY)
        return ("Array.prototype", "push", "function () { return -1; }",
                f"{arr}.push({b.expr(T.UNKNOWN, literal)})")
    if kind == 1:
        return ("Math", "max", "function () { return NaN; }",
                f"Math.max({b.expr(T.NUMBER, literal)}, {b.expr(T.NUMBER, literal)})")
    if kind == 2:
        s = b.pick_or_make(T.STRING)
        return ("String.prototype", "trim", 'function () { return this + "!"; }', f"{s}.trim()")
    if kind == 3:
        o = b.pick_or_make(T.OBJECT)
        return ("Object.prototype", "toString", 'function () { return "[custom]"; }', f"String({o})")
    return ("JSON", "stringify", "function (v) { return String(v); }",
            f"JSON.stringify({b.expr(T.UNKNOWN, literal)})")


@generator("builtin_overwrite", family="objects", weight=2)
def gen_builtin_overwrite(b: ProgramBuilder):
    """A builtin method reassigned to a custom implementation (Array.prototype.push, Math.max,
    String.prototype.trim, Object.prototype.toString, JSON.stringify), exercised, then restored."""
    target, prop, impl, use = _builtin_overwrite_case(b, b.rand_int(0, 4))
    orig = b.declare(T.FUNCTION)
    b.emit(f"var {orig} = {target}.{prop};")
    b.emit(f"{target}.{prop} = {impl};")
    b.fresh(T.UNKNOWN, use)
    b.emit(f"{target}.{prop} = {orig};")


@generator("homomorphic_objects", family="objects", weight=2, output=T.ARRAY)
def gen_homomorphic_objects(b: ProgramBuilder):
    """Several objects with an identical final property shape, built via different paths (object
    literal, plain assignment, Object.create, Object.assign), then read through one shared
    function (stresses hidden-class/shape unification)."""
    keys = b.choice([["a", "b"], ["x", "y", "z"], ["id", "value"]])
    objs: list[Var] = []
    for _ in range(b.rand_int(3, 5)):
        vals = [b.expr(b.choice([T.NUMBER, T.STRING, T.BOOLEAN]), literal) for _ in keys]
        pairs = ", ".join(f"{k}: {v}" for k, v in zip(keys, vals))
        kind = b.rand_int(0, 3)
        if kind == 0:
            o = b.fresh(T.OBJECT, "{" + pairs + "}")
        elif kind == 1:
            o = b.fresh(T.OBJECT, "{}")
            for k, v in zip(keys, vals):
                b.emit(f"{o}.{k} = {v};")
        elif kind == 2:
            o = b.fresh(T.OBJECT, "Object.create(Object.prototype)")
            for k, v in zip(keys, vals):
                b.emit(f"{o}.{k} = {v};")
        else:
            o = b.fresh(T.OBJECT, "Object.assign({}, {" + pairs + "})")
        objs.append(o)
    f = b.declare(T.FUNCTION)
    with b.block(f"function {f}(o) {{", kind="function"):
        b.scopes[-1].vars.append(Var("o", T.OBJECT))
        b.emit(f"return {' + '.join(f'o.{k}' for k in keys)};")
    acc = b.fresh(T.ARRAY, "[]")
    for o in objs:
        b.emit(f"{acc}.push({f}({o}));")
    return acc


@generator("object_hierarchy", family="objects", weight=3, output=T.OBJECT)
def gen_object_hierarchy(b: ProgramBuilder):
    """A prototype chain 2-4 deep built with Object.create / __proto__ / setPrototypeOf, with a
    property shadowed partway down, read through the whole chain."""
    depth = b.rand_int(2, 4)
    keys = ["a", "b", "c", "d"][:depth]
    root = b.fresh(T.OBJECT, f"{{{keys[0]}: {b.expr(T.NUMBER, literal)}}}")
    levels = [root]
    for i in range(1, depth):
        parent = levels[-1]
        own_key = b.choice(keys[:i]) if b.maybe(0.4) else keys[i]  # maybe shadow an ancestor's key
        val = b.expr(b.choice([T.NUMBER, T.STRING]), literal)
        kind = b.rand_int(0, 2)
        if kind == 0:
            child = b.fresh(T.OBJECT, f"Object.create({parent}, {{{own_key}: {{value: {val}, enumerable: true}}}})")
        elif kind == 1:
            child = b.fresh(T.OBJECT, "{" + f"{own_key}: {val}" + "}")
            b.emit(f"{child}.__proto__ = {parent};")
        else:
            child = b.fresh(T.OBJECT, "{" + f"{own_key}: {val}" + "}")
            b.emit(f"Object.setPrototypeOf({child}, {parent});")
        levels.append(child)
    leaf = levels[-1]
    b.fresh(T.ARRAY, f"[{', '.join(f'{leaf}.{k}' for k in keys)}]")
    return leaf
