"""Operator generators: unary, binary, comparison, logical, compound assignment, update."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal

ARITH = ["+", "-", "*", "/", "%", "**"]
BITWISE = ["&", "|", "^", "<<", ">>", ">>>"]
COMPARE = ["<", "<=", ">", ">=", "==", "!=", "===", "!=="]
LOGICAL = ["&&", "||", "??"]
UNARY = ["-", "+", "!", "~", "typeof ", "void "]
NUMERIC_TYPES = [T.NUMBER, T.INT32, T.STRING, T.BOOLEAN, T.UNDEFINED, T.NULL, T.OBJECT, T.ARRAY]


def operand(b: ProgramBuilder, prefer: T = T.NUMBER) -> str:
    """Mostly numbers; sometimes something that converts interestingly."""
    t = prefer if b.maybe(0.75) else b.choice(NUMERIC_TYPES)
    return b.expr(t, literal)


@generator("unary", family="operations", weight=3, output=T.UNKNOWN)
def gen_unary(b: ProgramBuilder):
    """Unary operator applied to an existing value or literal."""
    op = b.choice(UNARY)
    out_t = T.STRING if op == "typeof " else T.BOOLEAN if op == "!" else T.UNDEFINED if op == "void " else T.NUMBER
    b.fresh(out_t, f"{op}{operand(b, b.choice([T.NUMBER, T.INT32, T.STRING]))}")


@generator("binary_arith", family="operations", weight=5, output=T.NUMBER)
def gen_binary_arith(b: ProgramBuilder):
    """Arithmetic on two operands; `+` doubles as concatenation when a string sneaks in."""
    op = b.choice(ARITH)
    left = operand(b)
    if op == "**":
        left = f"({left})"      # `-1 ** 2` is a SyntaxError; a unary operand must be parenthesised
    b.fresh(T.NUMBER, f"{left} {op} {operand(b)}")


@generator("binary_bitwise", family="operations", weight=3, output=T.INT32)
def gen_binary_bitwise(b: ProgramBuilder):
    """Bitwise/shift operators, which force int32 conversion."""
    b.fresh(T.INT32, f"{operand(b)} {b.choice(BITWISE)} {operand(b, T.INT32)}")


@generator("int32_coerce", family="operations", weight=2, output=T.INT32)
def gen_int32_coerce(b: ProgramBuilder):
    """`x | 0`, `x >>> 0`, `~~x` on numbers near the int32 boundaries."""
    x = b.expr(T.NUMBER, literal)
    b.fresh(T.INT32, b.choice([f"{x} | 0", f"{x} >>> 0", f"~~{x}", f"{x} >> 0", f"{x} & 0xffffffff"]))


@generator("compare", family="operations", weight=3, output=T.BOOLEAN)
def gen_compare(b: ProgramBuilder):
    """Relational or (strict) equality comparison, with mixed types some of the time."""
    b.fresh(T.BOOLEAN, f"{operand(b, b.choice([T.NUMBER, T.STRING]))} {b.choice(COMPARE)} {operand(b, b.choice([T.NUMBER, T.STRING]))}")


@generator("logical", family="operations", weight=2, output=T.UNKNOWN)
def gen_logical(b: ProgramBuilder):
    """Short-circuit operators returning one of their operands."""
    b.fresh(T.UNKNOWN, f"{b.expr(T.UNKNOWN, literal)} {b.choice(LOGICAL)} {b.expr(T.UNKNOWN, literal)}")


@generator("compound_assign", family="operations", weight=4, inputs=(T.NUMBER,))
def gen_compound_assign(b: ProgramBuilder, target: Var):
    """`x op= y` on an existing variable, possibly changing its type."""
    op = b.choice(ARITH + BITWISE)
    b.emit(f"{target} {op}= {operand(b)};")


@generator("update", family="operations", weight=4, inputs=(T.NUMBER,))
def gen_update(b: ProgramBuilder, target: Var):
    """Prefix/postfix ++/-- on an existing variable, using the result."""
    form = b.choice([f"{target}++", f"++{target}", f"{target}--", f"--{target}"])
    if b.maybe(0.5):
        b.fresh(T.NUMBER, form)
    else:
        b.emit(f"{form};")


@generator("conditional", family="operations", weight=2, output=T.UNKNOWN)
def gen_conditional(b: ProgramBuilder):
    """`c ? a : b` with a non-boolean condition some of the time."""
    cond = b.expr(T.BOOLEAN if b.maybe(0.6) else T.UNKNOWN, literal)
    b.fresh(T.UNKNOWN, f"{cond} ? {b.expr(T.UNKNOWN, literal)} : {b.expr(T.UNKNOWN, literal)}")


@generator("comma", family="operations", weight=1, output=T.UNKNOWN)
def gen_comma(b: ProgramBuilder):
    """Comma expression; the value is the last operand."""
    b.fresh(T.UNKNOWN, f"({b.expr(T.UNKNOWN, literal)}, {b.expr(T.UNKNOWN, literal)})")


@generator("template_literal", family="operations", weight=2, output=T.STRING)
def gen_template_literal(b: ProgramBuilder):
    """Template literal interpolating existing values (ToString on each)."""
    parts = [f"${{{b.expr(T.UNKNOWN, literal)}}}" for _ in range(b.rand_int(1, 3))]
    b.fresh(T.STRING, "`" + b.choice(["v=", "", "<"]) + "-".join(parts) + "`")


@generator("string_concat", family="operations", weight=3, output=T.STRING)
def gen_string_concat(b: ProgramBuilder):
    """`s + x` where x may be a number, object, or another string (ConsString territory)."""
    b.fresh(T.STRING, f"{b.expr(T.STRING, literal)} + {b.expr(b.choice([T.STRING, T.NUMBER, T.OBJECT, T.ARRAY]), literal)}")


@generator("number_conversion", family="operations", weight=2, output=T.NUMBER)
def gen_number_conversion(b: ProgramBuilder):
    """Explicit conversions: Number(x), parseInt, parseFloat, +x, x.toString(radix)."""
    x = b.expr(b.choice([T.STRING, T.NUMBER, T.BOOLEAN, T.OBJECT]), literal)
    form = b.choice([f"Number({x})", f"parseInt({x})", f"parseFloat({x})", f"+{x}", f"parseInt({x}, {b.choice(['2', '8', '16', '36'])})"])
    b.fresh(T.NUMBER, form)


@generator("number_to_string", family="operations", weight=2, output=T.STRING)
def gen_number_to_string(b: ProgramBuilder):
    """Number formatting: String(x), x.toFixed, x.toString(radix), x.toPrecision."""
    x = b.pick_or_make(T.NUMBER)
    form = b.choice([f"String({x})", f"({x}).toString({b.choice(['2', '10', '16', '36'])})",
                     f"({x}).toFixed({b.rand_int(0, 5)})", f"({x}).toPrecision({b.rand_int(1, 6)})",
                     f"({x}).toExponential()"])
    tb = b.try_block()
    with tb.body():
        b.fresh(T.STRING, form)
    with tb.catch("e"):
        b.emit("__probe(\"conv\", e.name);") if b.opts.probes else b.emit("// conversion threw")
    tb.end()
