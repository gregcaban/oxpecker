"""Container generators: arrays, objects, typed arrays, Map/Set."""

from __future__ import annotations

from ..builder import ProgramBuilder
from ..registry import generator, value_maker
from ..types import T
from .values import literal

KEYS = ["a", "b", "x", "y", "length", "0", "1", "10", "constructor", "toString", "valueOf", "__proto__", "foo"]


def elements(b: ProgramBuilder, n: int) -> list[str]:
    return [b.expr(b.choice([T.NUMBER, T.INT32, T.STRING, T.BOOLEAN, T.UNDEFINED, T.NULL]), literal) for _ in range(n)]


@value_maker(T.ARRAY)
@generator("array", family="containers", weight=4, output=T.ARRAY)
def gen_array(b: ProgramBuilder):
    """An array literal of existing values and literals."""
    return b.fresh(T.ARRAY, "[" + ", ".join(elements(b, b.rand_int(0, 5))) + "]")


@generator("sparse_array", family="containers", weight=2, output=T.ARRAY)
def gen_sparse_array(b: ProgramBuilder):
    """An array with holes, or a very large length."""
    kind = b.rand_int(0, 2)
    if kind == 0:
        els = elements(b, 3)
        a = b.fresh(T.ARRAY, f"[{els[0]}, , {els[1]}, , {els[2]}]")
    elif kind == 1:
        a = b.fresh(T.ARRAY, "[]")
        b.emit(f"{a}[{b.choice(['5', '100', '4294967294'])}] = {b.expr(T.NUMBER, literal)};")
    else:
        a = b.fresh(T.ARRAY, "[" + ", ".join(elements(b, 2)) + "]")
        b.emit(f"{a}.length = {b.choice(['0', '1', '5', '4294967295'])};")
    return a


@value_maker(T.OBJECT)
@generator("object", family="containers", weight=4, output=T.OBJECT)
def gen_object(b: ProgramBuilder):
    """An object literal with data properties, maybe numeric or computed keys."""
    props = []
    for _ in range(b.rand_int(0, 4)):
        key = b.choice(KEYS[:-1]) if b.maybe(0.8) else f"[{b.expr(T.STRING, literal)}]"
        props.append(f"{key}: {b.expr(b.choice([T.NUMBER, T.STRING, T.BOOLEAN, T.OBJECT]), literal)}")
    return b.fresh(T.OBJECT, "{" + ", ".join(props) + "}")


@generator("object_accessors", family="containers", weight=2, output=T.OBJECT)
def gen_object_accessors(b: ProgramBuilder):
    """An object literal with a getter/setter pair backed by a captured variable."""
    backing = b.pick_or_make(T.NUMBER)
    key = b.choice(["x", "y", "0", "value"])
    o = b.declare(T.OBJECT)
    b.emit(f"var {o} = {{")
    b.indent += 1
    b.emit(f"get {key}() {{ return {backing}; }},")
    b.emit(f"set {key}(v) {{ {backing} = v; }},")
    b.emit(f"{b.choice(['n', 'm'])}: {literal(b, T.NUMBER)},")
    b.indent -= 1
    b.emit("};")
    return o


@value_maker(T.TYPEDARRAY)
@generator("typedarray", family="containers", weight=2, output=T.TYPEDARRAY)
def gen_typedarray(b: ProgramBuilder):
    """A typed array from a length or an array of values (clamping, truncation, NaN)."""
    ctor = b.choice(["Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array",
                     "Int32Array", "Uint32Array", "Float32Array", "Float64Array"])
    if b.maybe(0.5):
        return b.fresh(T.TYPEDARRAY, f"new {ctor}({b.rand_int(0, 8)})")
    return b.fresh(T.TYPEDARRAY, f"new {ctor}([{', '.join(b.expr(T.NUMBER, literal) for _ in range(b.rand_int(1, 4)))}])")


@value_maker(T.MAP)
@generator("map", family="containers", weight=1, output=T.MAP)
def gen_map(b: ProgramBuilder):
    """A Map with mixed-type keys (-0/0 and NaN normalisation)."""
    pairs = [f"[{b.expr(b.choice([T.NUMBER, T.STRING, T.OBJECT]), literal)}, {b.expr(T.UNKNOWN, literal)}]"
             for _ in range(b.rand_int(0, 3))]
    return b.fresh(T.MAP, f"new Map([{', '.join(pairs)}])")


@value_maker(T.SET)
@generator("set", family="containers", weight=1, output=T.SET)
def gen_set(b: ProgramBuilder):
    """A Set with mixed-type values."""
    vals = [b.expr(b.choice([T.NUMBER, T.STRING, T.OBJECT]), literal) for _ in range(b.rand_int(0, 4))]
    return b.fresh(T.SET, f"new Set([{', '.join(vals)}])")


@value_maker(T.FUNCTION)
@generator("function_literal", family="containers", weight=1, output=T.FUNCTION)
def gen_function_literal(b: ProgramBuilder):
    """A small function expression or arrow with a one-line body."""
    return b.fresh(T.FUNCTION, literal(b, T.FUNCTION))
