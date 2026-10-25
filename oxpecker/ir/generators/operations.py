"""Operator generators (port of `gen/generators/operations.py`).

No parenthesisation here: `-1 ** 2`, `--1` and friends are the lifter's problem (decision D12).
"""

from __future__ import annotations

from ...gen.types import T
from ..builder import Builder
from ..ops import ARITH, BITWISE, COMPARE, LOGICAL, CallFunction, CallMethod, CompoundAssign, Conditional, GetProperty, Update
from ..registry import generator

UNARY = ["-", "+", "!", "~", "typeof", "void"]
NUMERIC_TYPES = [T.NUMBER, T.INT32, T.STRING, T.BOOLEAN, T.UNDEFINED, T.NULL, T.OBJECT, T.ARRAY]


def operand(b: Builder, prefer: T = T.NUMBER) -> int:
    """Mostly numbers; sometimes something that converts interestingly. Shrinks towards `prefer`."""
    return b.pick_or_make(b.choice(NUMERIC_TYPES) if b.maybe(0.25) else prefer)


@generator("unary", family="operations", weight=3, output=T.UNKNOWN)
def gen_unary(b: Builder):
    """Unary operator applied to an existing value or literal."""
    op = b.choice(UNARY)
    b.unary(op, operand(b, b.choice([T.NUMBER, T.INT32, T.STRING])))


@generator("binary_arith", family="operations", weight=5, output=T.NUMBER)
def gen_binary_arith(b: Builder):
    """Arithmetic on two operands; `+` doubles as concatenation when a string sneaks in."""
    b.binary(b.choice(ARITH), operand(b), operand(b))


@generator("binary_bitwise", family="operations", weight=3, output=T.INT32)
def gen_binary_bitwise(b: Builder):
    """Bitwise/shift operators, which force int32 conversion."""
    b.binary(b.choice(BITWISE), operand(b), operand(b, T.INT32))


@generator("int32_coerce", family="operations", weight=2, output=T.INT32)
def gen_int32_coerce(b: Builder):
    """`x | 0`, `x >>> 0`, `~~x` on numbers near the int32 boundaries."""
    x = b.pick_or_make(T.NUMBER)
    form = b.choice(["|0", ">>>0", "~~", ">>0", "&0xffffffff"])
    if form == "~~":
        b.unary("~", b.unary("~", x))
    else:
        op = {"|0": "|", ">>>0": ">>>", ">>0": ">>", "&0xffffffff": "&"}[form]
        b.binary(op, x, b.const(T.INT32, "0xffffffff" if op == "&" else "0"))


@generator("compare", family="operations", weight=3, output=T.BOOLEAN)
def gen_compare(b: Builder):
    """Relational or (strict) equality comparison, with mixed types some of the time."""
    b.binary(b.choice(COMPARE), operand(b, b.choice([T.NUMBER, T.STRING])),
             operand(b, b.choice([T.NUMBER, T.STRING])))


@generator("logical", family="operations", weight=2, output=T.UNKNOWN)
def gen_logical(b: Builder):
    """Short-circuit operators returning one of their operands."""
    b.binary(b.choice(LOGICAL), b.pick_or_make(T.UNKNOWN), b.pick_or_make(T.UNKNOWN))


@generator("compound_assign", family="operations", weight=4)
def gen_compound_assign(b: Builder):
    """`x op= y` on an existing variable, possibly changing its type."""
    op = b.choice(ARITH + BITWISE)
    target = b.target(T.NUMBER)
    b.emit(CompoundAssign(op), (target, operand(b)))


@generator("update", family="operations", weight=4)
def gen_update(b: Builder):
    """Prefix/postfix ++/-- on an existing variable, using the result some of the time."""
    target = b.target(T.NUMBER)
    b.emit(Update(b.choice(["++", "--"]), b.maybe(0.5), value=b.maybe(0.5)), (target,))


@generator("conditional", family="operations", weight=2, output=T.UNKNOWN)
def gen_conditional(b: Builder):
    """`c ? a : b` with a non-boolean condition some of the time."""
    cond = b.pick_or_make(T.UNKNOWN if b.maybe(0.4) else T.BOOLEAN)
    b.emit(Conditional(), (cond, b.pick_or_make(T.UNKNOWN), b.pick_or_make(T.UNKNOWN)))


@generator("comma", family="operations", weight=1, output=T.UNKNOWN)
def gen_comma(b: Builder):
    """Comma expression; the value is the last operand."""
    b.eval("({0}, {1})", b.pick_or_make(T.UNKNOWN), b.pick_or_make(T.UNKNOWN), out=T.UNKNOWN, pure=True)


@generator("template_literal", family="operations", weight=2, output=T.STRING)
def gen_template_literal(b: Builder):
    """Template literal interpolating existing values (ToString on each)."""
    parts = [b.pick_or_make(T.UNKNOWN) for _ in range(b.int(1, 3))]
    code = "`" + b.choice(["v=", "", "<"]) + "-".join("${{%d}}" % k for k in range(len(parts))) + "`"
    b.eval(code.replace("{{", "{").replace("}}", "}"), *parts, out=T.STRING, pure=True)


@generator("string_concat", family="operations", weight=3, output=T.STRING)
def gen_string_concat(b: Builder):
    """`s + x` where x may be a number, object, or another string (ConsString territory)."""
    b.binary("+", b.pick_or_make(T.STRING), b.pick_or_make(b.choice([T.STRING, T.NUMBER, T.OBJECT, T.ARRAY])))


@generator("number_conversion", family="operations", weight=2, output=T.NUMBER)
def gen_number_conversion(b: Builder):
    """Explicit conversions: Number(x), parseInt, parseFloat, +x, parseInt(x, radix)."""
    x = b.pick_or_make(b.choice([T.STRING, T.NUMBER, T.BOOLEAN, T.OBJECT]))
    form = b.choice(["Number", "parseInt", "parseFloat", "+", "parseInt/radix"])
    if form == "+":
        b.unary("+", x)
    elif form == "parseInt/radix":
        b.emit(CallFunction(), (b.builtin("parseInt", T.FUNCTION), x, b.const(T.INT32, b.choice(["2", "8", "16", "36"]))),
               t=T.NUMBER)
    else:
        b.emit(CallFunction(), (b.builtin(form, T.FUNCTION), x), t=T.NUMBER)


@generator("number_to_string", family="operations", weight=2, output=T.STRING)
def gen_number_to_string(b: Builder):
    """Number formatting: String(x), x.toString(radix), x.toFixed, x.toPrecision, x.toExponential."""
    x = b.pick_or_make(T.NUMBER)
    form = b.choice(["String", "toString", "toFixed", "toPrecision", "toExponential"])

    def body():
        if form == "String":
            b.emit(CallFunction(), (b.builtin("String", T.FUNCTION), x), t=T.STRING)
        elif form == "toExponential":
            b.emit(CallMethod(form), (x,), t=T.STRING)
        else:
            arg = {"toString": lambda: b.choice(["2", "10", "16", "36"]), "toFixed": lambda: str(b.int(0, 5)),
                   "toPrecision": lambda: str(b.int(1, 6))}[form]()
            b.emit(CallMethod(form), (x, b.const(T.INT32, arg)), t=T.STRING)

    def caught(e):
        b.emit(GetProperty("name"), (e,))
        b.probe("conv")

    b.guarded(body, on_error=caught)
