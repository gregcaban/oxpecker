"""Java interop generators: values crossing the JS/Java boundary through the harness's `J`
object (see JavaProbe.java). Each `J.*` call records the Java runtime class and value it
received, so Integer-vs-Double and conversion differences between the modes show up as findings."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal

TYPED = [("takesInt", T.NUMBER), ("takesLong", T.NUMBER), ("takesShort", T.NUMBER), ("takesByte", T.NUMBER),
         ("takesChar", T.STRING), ("takesFloat", T.NUMBER), ("takesDouble", T.NUMBER), ("takesBoolean", T.BOOLEAN),
         ("takesString", T.STRING), ("takesInteger", T.NUMBER), ("takesNumber", T.NUMBER),
         ("takesBigInteger", T.BIGINT), ("takesCharSequence", T.STRING)]
RETURNING = ["anInt()", "aLong()", "maxLong()", "aFloat()", "aDouble()", "negZero()", "nan()", "aShort()", "aByte()",
             "aChar()", "boxedInt()", "boxedLong()", "bigInteger()", "aString()", "nothing()", "field", "PI_ISH",
             "bump()", "staticCounter"]
EDGE_NUMBERS = ["0", "-0", "1", "1.5", "-1.5", "2147483647", "2147483648", "-2147483649", "4294967295",
                "9007199254740992", "9007199254740993", "NaN", "Infinity", "-Infinity", "0.1", "1e21", "255", "256",
                "65536", "-1", "127", "128"]


def _guarded(b: ProgramBuilder, type: T, expr: str) -> None:
    """A J call that may throw a Java conversion error (recorded, not fatal)."""
    tb = b.try_block()
    with tb.body():
        b.fresh(type, expr)
    with tb.catch("e"):
        b.emit('__probe("jerr", e && e.name, String(e).slice(0, 60));' if b.opts.probes else "// Java conversion failed")
    tb.end()


@generator("java_typed_call", family="java-interop", weight=6, output=T.UNKNOWN)
def gen_java_typed_call(b: ProgramBuilder):
    """Pass a JS value to a Java method with a primitive/boxed parameter type (int, long, float,
    String, BigInteger...): Rhino converts it, and the conversion result is observable."""
    name, t = b.choice(TYPED)
    if t is T.NUMBER and b.maybe(0.5):
        arg = b.choice(EDGE_NUMBERS)
    else:
        arg = b.expr(b.choice([t, T.UNKNOWN]), literal)
    _guarded(b, t if t is not T.BIGINT else T.UNKNOWN, f"J.{name}({arg})")


@generator("java_echo", family="java-interop", weight=6, inputs=(T.UNKNOWN,), output=T.UNKNOWN)
def gen_java_echo(b: ProgramBuilder, v: Var):
    """J.echo(x) / J.typeOf(x): what Java class does the value arrive as (Integer? Double?
    ConsString? NativeArray?), and does it survive the round trip."""
    r = b.rand_int(0, 2)
    if r == 0:
        b.fresh(T.UNKNOWN, f"J.echo({v})")
    elif r == 1:
        b.fresh(T.STRING, f"J.typeOf({v})")
    else:
        b.fresh(T.BOOLEAN, f"J.echo({v}) === {v}")


@generator("java_echo_arith", family="java-interop", weight=5, output=T.UNKNOWN)
def gen_java_echo_arith(b: ProgramBuilder):
    """The result of an arithmetic expression (where the compiler may keep an int) goes to Java."""
    x, y = b.expr(T.NUMBER, literal), b.expr(T.NUMBER, literal)
    v = b.pick_or_make(T.NUMBER)
    expr = b.choice([f"{x} + {y}", f"{x} * {y}", f"{x} - {y}", f"{x} / {y}", f"{x} | 0", f"{x} >>> 0", f"-{x}",
                     f"{v}++", f"{x} % {y}", f"({x}) ** 2", f"Math.floor({x})", f"({x} + {y}) | 0", f"{x} << 1", f"~{x}"])
    b.fresh(T.STRING, f"J.typeOf({expr})")
    if b.maybe(0.5):
        b.fresh(T.UNKNOWN, f"J.echo({expr})")


@generator("java_overload", family="java-interop", weight=5, output=T.STRING)
def gen_java_overload(b: ProgramBuilder):
    """J.over(x) is overloaded on int/long/double/String/boolean/Object: which overload Rhino
    picks depends on the runtime representation of x."""
    arg = b.choice(EDGE_NUMBERS) if b.maybe(0.5) else b.expr(T.UNKNOWN, literal)
    _guarded(b, T.STRING, f"J.over({arg})")


@generator("java_returned_value", family="java-interop", weight=5, output=T.UNKNOWN)
def gen_java_returned_value(b: ProgramBuilder):
    """A Java primitive/boxed value (long beyond 2^53, float 0.1f, byte, char, BigInteger, null)
    comes back into JS and is used in arithmetic, comparison or concatenation."""
    r = b.fresh(T.UNKNOWN, f"J.{b.choice(RETURNING)}")
    use = b.choice([f"{r} + 1", f"{r} * 2", f"{r} === {r}", f"typeof {r}", f"{r} + \"\"", f"{r} | 0", f"{r} == 7",
                    f"Number({r})", f"{r} > 2147483647", f"String({r}).length", f"{r} / 3", f"[{r}].length",
                    f"{r} === null", f"JSON.stringify({r})", f"Object.is({r}, -0)"])
    _guarded(b, T.UNKNOWN, use)


@generator("java_array", family="java-interop", weight=4)
def gen_java_array(b: ProgramBuilder):
    """Java arrays (int[], double[], Object[], String[]) read and written from JS, including
    out-of-range stores, length, and passing a JS array to a Java array parameter."""
    kind = b.rand_int(0, 3)
    n = b.rand_int(0, 5)
    if kind == 0:
        a = b.fresh(T.UNKNOWN, f"J.intArray({n})")
        _guarded(b, T.UNKNOWN, f"({a}[{b.rand_int(0, 2)}] = {b.choice(EDGE_NUMBERS)}, {a}[0])")
        b.fresh(T.UNKNOWN, f"[{a}.length, J.typeOf({a}[0]), J.sum({a})]")
    elif kind == 1:
        a = b.fresh(T.UNKNOWN, f"J.doubleArray({n})")
        _guarded(b, T.UNKNOWN, f"({a}[0] = {b.expr(T.NUMBER, literal)}, {a}[0] + 1)")
    elif kind == 2:
        a = b.fresh(T.UNKNOWN, f"J.objectArray({n})")
        _guarded(b, T.UNKNOWN, f"({a}[0] = {b.expr(T.UNKNOWN, literal)}, J.typeOf({a}[0]))")
    else:
        js = b.expr(T.ARRAY, literal)
        _guarded(b, T.UNKNOWN, f"J.sum({js})")
        b.fresh(T.UNKNOWN, f"J.stringArray().length")


@generator("java_collections", family="java-interop", weight=3)
def gen_java_collections(b: ProgramBuilder):
    """java.util.List / Map seen from JS: indexing, size, get, iteration via for-in."""
    r = b.rand_int(0, 2)
    if r == 0:
        l = b.fresh(T.UNKNOWN, "J.list()")
        b.fresh(T.UNKNOWN, b.choice([f"[{l}.size(), J.typeOf({l}.get(0)), J.typeOf({l}.get(1))]", f"{l}[0] + {l}[1]",
                                     f"J.size({l})", f"{l}.get(2) + 1", f"String({l}.get(0))"]))
    elif r == 1:
        m = b.fresh(T.UNKNOWN, "J.map()")
        b.fresh(T.UNKNOWN, b.choice([f"{m}.get('k') + {m}.get('d')", f"J.get({m}, 'k')", f"J.typeOf({m}.get('k'))",
                                     f"{m}.size()", f"{m}.k", f"J.typeOf(J.get({m}, 'd'))"]))
    else:
        acc = b.fresh(T.ARRAY, "[]")
        b.emit(f"for (var jk in J.list()) {{ {acc}.push(jk); }}")


@generator("java_numeric_pipeline", family="java-interop", weight=5, output=T.FUNCTION)
def gen_java_numeric_pipeline(b: ProgramBuilder):
    """A numeric-typed function (optimizer territory) whose intermediate results are handed to
    Java at several points: the int/double representation the compiler chose is exposed."""
    with b.define_function(2, param_types=[T.NUMBER, T.NUMBER]) as (f, params):
        x = b.declare(T.NUMBER)
        b.emit(f"var {x} = {params[0]} + {params[1]};")
        b.emit(f"var t1 = J.typeOf({x});")
        b.emit(f"{x} = {b.choice([f'{x} * 2', f'{x} / 2', f'{x} | 0', f'{x} + 0.5', f'{x} - {params[0]}', f'{x} ** 2', f'-{x}'])};")
        b.emit(f"var t2 = J.typeOf({x});")
        b.emit(f"{x}++;")
        b.emit(f"return [t1, t2, J.typeOf({x}), J.echo({x}), J.over({x})];")
    b.fresh(T.ARRAY, f"{f}({b.choice(EDGE_NUMBERS)}, {b.choice(EDGE_NUMBERS)})")
    b.fresh(T.ARRAY, f"{f}({b.choice(['1', '2', '3'])}, {b.choice(['1', '2', '3'])})")
    b.fresh(T.ARRAY, f"{f}({b.choice(['0.5', '1.5', 'NaN'])}, {b.choice(['1', '2.5', '\"3\"'])})")
