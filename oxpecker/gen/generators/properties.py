"""Property access generators: get/set/delete, defineProperty, in, for-in, prototype mutation."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .containers import KEYS
from .values import literal


def key_expr(b: ProgramBuilder) -> str:
    """A property key: identifier, string, number or computed."""
    r = b.rand_int(0, 3)
    if r == 0:
        return "." + b.choice([k for k in KEYS if not k.isdigit() and k != "__proto__"])
    if r == 1:
        return f"[{b.choice(['0', '1', '2', '-1', '1.5', '4294967295', '\"0\"', '\"a\"', '\"length\"'])}]"
    if r == 2:
        return f"[{b.expr(T.STRING, literal)}]"
    return f"[{b.expr(b.choice([T.NUMBER, T.INT32, T.SYMBOL]), literal)}]"


@generator("prop_get", family="properties", weight=5, inputs=(T.OBJECT,), output=T.UNKNOWN)
def gen_prop_get(b: ProgramBuilder, obj: Var):
    """Read a property by name, index or computed key."""
    b.fresh(T.UNKNOWN, f"{obj}{key_expr(b)}")


@generator("prop_set", family="properties", weight=5, inputs=(T.OBJECT,))
def gen_prop_set(b: ProgramBuilder, obj: Var):
    """Write a property (adds or changes shape)."""
    b.emit(f"{obj}{key_expr(b)} = {b.expr(T.UNKNOWN, literal)};")


@generator("prop_delete", family="properties", weight=2, inputs=(T.OBJECT,))
def gen_prop_delete(b: ProgramBuilder, obj: Var):
    """Delete a property, keeping the boolean result."""
    b.fresh(T.BOOLEAN, f"delete {obj}{key_expr(b)}")


@generator("prop_in", family="properties", weight=2, inputs=(T.OBJECT,), output=T.BOOLEAN)
def gen_prop_in(b: ProgramBuilder, obj: Var):
    """`key in obj`."""
    b.fresh(T.BOOLEAN, f"{b.expr(b.choice([T.STRING, T.INT32, T.SYMBOL]), literal)} in {obj}")


@generator("prop_compound", family="properties", weight=3, inputs=(T.OBJECT,))
def gen_prop_compound(b: ProgramBuilder, obj: Var):
    """Compound assignment or ++ through a property reference."""
    key = key_expr(b)
    form = b.choice([f"{obj}{key} += {b.expr(T.NUMBER, literal)}", f"{obj}{key}++", f"++{obj}{key}",
                     f"{obj}{key} -= 1", f"{obj}{key} |= {b.expr(T.INT32, literal)}", f"{obj}{key}--"])
    b.fresh(T.UNKNOWN, form)


@generator("define_property", family="properties", weight=2, inputs=(T.OBJECT,))
def gen_define_property(b: ProgramBuilder, obj: Var):
    """Object.defineProperty with non-default attributes, or an accessor."""
    key = b.choice(['"a"', '"x"', '"0"', '"length"', '"y"'])
    if b.maybe(0.6):
        desc = f"{{value: {b.expr(T.UNKNOWN, literal)}, writable: {b.choice(['true', 'false'])}, enumerable: {b.choice(['true', 'false'])}, configurable: {b.choice(['true', 'false'])}}}"
    else:
        desc = f"{{get() {{ return {b.expr(T.NUMBER, literal)}; }}, configurable: true}}"
    tb = b.try_block()
    with tb.body():
        b.emit(f"Object.defineProperty({obj}, {key}, {desc});")
    with tb.catch("e"):
        b.emit("// defineProperty rejected (frozen / non-configurable)")
    tb.end()


@generator("freeze_seal", family="properties", weight=1, inputs=(T.OBJECT,))
def gen_freeze_seal(b: ProgramBuilder, obj: Var):
    """Object.freeze / seal / preventExtensions, then a write that silently fails in sloppy mode."""
    b.emit(f"Object.{b.choice(['freeze', 'seal', 'preventExtensions'])}({obj});")
    tb = b.try_block()
    with tb.body():
        b.emit(f"{obj}{key_expr(b)} = {b.expr(T.UNKNOWN, literal)};")
    with tb.catch("e"):
        b.emit("// strict-mode write to a frozen object throws")
    tb.end()


@generator("for_in", family="properties", weight=2, inputs=(T.OBJECT,))
def gen_for_in(b: ProgramBuilder, obj: Var):
    """for-in enumeration collecting keys into an array."""
    acc = b.fresh(T.ARRAY, "[]")
    k = b.new_name(T.STRING)
    with b.block(f"for (var {k} in {obj}) {{"):
        b.scopes[-1].vars.append(Var(k, T.STRING))
        b.emit(f"{acc}.push({k});")
        if b.maybe(0.3):
            b.emit(f"{acc}.push({obj}[{k}]);")


@generator("proto_mutation", family="properties", weight=2, inputs=(T.OBJECT, T.OBJECT))
def gen_proto_mutation(b: ProgramBuilder, obj: Var, proto: Var):
    """Change an object's prototype (setPrototypeOf / __proto__) between property accesses."""
    before = b.fresh(T.UNKNOWN, f"{obj}.{b.choice(['x', 'a', 'toString'])}")
    if b.maybe(0.5):
        b.emit(f"Object.setPrototypeOf({obj}, {proto});")
    else:
        b.emit(f"{obj}.__proto__ = {proto};")
    b.fresh(T.UNKNOWN, f"{obj}.{b.choice(['x', 'a', 'toString'])}")
    b.fresh(T.BOOLEAN, f"{before} === {obj}.x")


@generator("object_static", family="properties", weight=2, inputs=(T.OBJECT,), output=T.UNKNOWN)
def gen_object_static(b: ProgramBuilder, obj: Var):
    """Object.keys/values/entries/getOwnPropertyNames/assign and JSON round-trips."""
    form = b.choice([f"Object.keys({obj})", f"Object.values({obj})", f"Object.entries({obj})",
                     f"Object.getOwnPropertyNames({obj})", f"Object.assign({{}}, {obj})",
                     f"JSON.stringify({obj})", f"Object.getPrototypeOf({obj}) === Object.prototype"])
    tb = b.try_block()
    with tb.body():
        b.fresh(T.UNKNOWN, form)
    with tb.catch("e"):
        b.emit("// e.g. JSON.stringify on a cycle or BigInt")
    tb.end()
