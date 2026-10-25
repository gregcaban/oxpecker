"""Unit tests for the IR -> JavaScript lifter (oxpecker/ir/lift.py): hand-built programs, no JVM.

    pytest tests/test_ir_lift.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.types import T
from oxpecker.ir.lift import _spans_whole, lift, literal_prec
from oxpecker.ir.ops import (BeginCatch, BeginElse, BeginFinally, BeginFor, BeginFunction, BeginIf, BeginTry, Binary,
                             CallFunction, CallMethod, CompoundAssign, Conditional, Construct, CreateArray,
                             CreateObject, DeclareVar, DeleteProperty, EndFor, EndFunction, EndIf, EndTry, Eval,
                             GetElement, GetProperty, Instr, LoadBuiltin, LoadLiteral, Probe, Reassign, Return,
                             SetElement, SetProperty, Throw, Unary, Update)
from oxpecker.ir.strategies import ir_programs

NODE = shutil.which("node")
RELAXED = settings(deadline=None, database=None, suppress_health_check=list(HealthCheck))


def I(op, inputs=(), outputs=(), inner=(), **kw):
    return Instr(op, tuple(inputs), tuple(outputs), tuple(inner), **kw)


def lit(v, text, t=T.INT32, **kw):
    return I(LoadLiteral(t, text), (), (v,), **kw)


def body(prog, **kw) -> str:
    return lift(prog, probes=False, comments=False, **kw).source


def raw(prog, **kw) -> str:
    """Lifted without dead-code elimination, so unused values show up."""
    return body(prog, dce=False, **kw)


def lines(src: str) -> list[str]:
    return src.rstrip("\n").split("\n")


class P:
    """Program builder that allocates variable ids."""

    def __init__(self):
        self.ins: list[Instr] = []
        self.n = 0

    def _vars(self, k):
        vs = tuple(range(self.n, self.n + k))
        self.n += k
        return vs

    def add(self, op, inputs=(), nout=1, ninner=0, **kw):
        outs, inner = self._vars(nout), self._vars(ninner)
        self.ins.append(I(op, inputs, outs, inner, **kw))
        if ninner:
            return (outs[0] if nout == 1 else outs), inner
        return outs[0] if nout == 1 else outs

    def stmt(self, op, inputs=(), **kw):
        self.ins.append(I(op, inputs, **kw))

    def lit(self, text, t=None, **kw):
        if t is None:
            t = (T.STRING if text[:1] in "\"'" else T.BOOLEAN if text in ("true", "false") else T.BIGINT
                 if text.endswith("n") and text[:-1].isdigit() else T.INT32)
        return self.add(LoadLiteral(t, text), **kw)

    def builtin(self, name, t=T.UNKNOWN, keep=True, hint=None):
        return self.add(LoadBuiltin(name, t), keep=keep, hint=hint)

    def declare(self, value, kind="var", hint="d", **kw):
        return self.add(DeclareVar(kind), (value,), hint=hint, **kw)

    def tree(self, spec):
        """Expression trees: "1" literal, ("id", name), ("bin", op, l, r), ("un", op, x), ("cond", c, a, b),
        ("get", x, name), ("elem", x, k), ("call", f, *args), ("method", x, name, *args), ("new", f, *args),
        ("arr", *elems), ("obj", keys, *values)."""
        if isinstance(spec, str):
            return self.lit(spec)
        kind, *rest = spec
        if kind == "id":
            return self.builtin(rest[0], T.UNKNOWN, keep=None)
        if kind == "bin":
            return self.add(Binary(rest[0]), (self.tree(rest[1]), self.tree(rest[2])))
        if kind == "un":
            return self.add(Unary(rest[0]), (self.tree(rest[1]),))
        if kind == "cond":
            return self.add(Conditional(), tuple(self.tree(s) for s in rest))
        if kind == "get":
            return self.add(GetProperty(rest[1]), (self.tree(rest[0]),))
        if kind == "elem":
            return self.add(GetElement(), (self.tree(rest[0]), self.tree(rest[1])))
        if kind == "call":
            return self.add(CallFunction(), tuple(self.tree(s) for s in rest))
        if kind == "method":
            return self.add(CallMethod(rest[1]), (self.tree(rest[0]),) + tuple(self.tree(s) for s in rest[2:]))
        if kind == "new":
            return self.add(Construct(), tuple(self.tree(s) for s in rest))
        if kind == "arr":
            return self.add(CreateArray(), tuple(self.tree(s) for s in rest))
        if kind == "obj":
            return self.add(CreateObject(tuple(rest[0])), tuple(self.tree(s) for s in rest[1:]))
        raise AssertionError(kind)

    def program(self):
        return tuple(self.ins)


def spell(spec) -> str:
    """The text a tree is spelled as when it is the initialiser of a declared variable."""
    p = P()
    p.declare(p.tree(spec))
    out = lines(body(p.program()))
    last = out[-1]
    assert last.startswith("var d1 = ") and last.endswith(";"), out
    return last[len("var d1 = "):-1]


def opcase(op, nin, **kw):
    """`var r1 = <op over a1, b1, c1>;` as the last line, over kept unknown-typed builtins."""
    p = P()
    xs = [p.builtin(n, hint=h) for n, h in zip("ABC", "abc")][:nin]
    p.add(op, tuple(xs), hint="r", **kw)
    return lines(body(p.program(), dce=False))[-1]


def stmtcase(op, nin, **kw):
    p = P()
    xs = [p.builtin(n, hint=h) for n, h in zip("ABC", "abc")][:nin]
    p.stmt(op, tuple(xs), **kw)
    return lines(body(p.program()))[-1]


# ---------------------------------------------------------------------------- every op kind

@pytest.mark.parametrize("op,nin,expected", [
    (Unary("-"), 1, "var r1 = -a1;"),
    (Unary("+"), 1, "var r1 = +a1;"),
    (Unary("!"), 1, "var r1 = !a1;"),
    (Unary("~"), 1, "var r1 = ~a1;"),
    (Unary("typeof"), 1, "var r1 = typeof a1;"),
    (Unary("void"), 1, "var r1 = void a1;"),
    *[(Binary(o), 2, f"var r1 = a1 {o} b1;") for o in
      ("+", "-", "*", "/", "%", "**", "&", "|", "^", "<<", ">>", ">>>", "<", "<=", ">", ">=", "==", "!=", "===",
       "!==", "&&", "||", "??", "in", "instanceof")],
    (Conditional(), 3, "var r1 = a1 ? b1 : c1;"),
    (GetProperty("p"), 1, "var r1 = a1.p;"),
    (GetProperty("my-p"), 1, 'var r1 = a1["my-p"];'),
    (GetProperty("0"), 1, 'var r1 = a1["0"];'),
    (GetElement(), 2, "var r1 = a1[b1];"),
    (DeleteProperty("p"), 1, "var r1 = delete a1.p;"),
    (DeleteProperty("a b"), 1, 'var r1 = delete a1["a b"];'),
    (CallFunction(), 1, "var r1 = a1();"),
    (CallFunction(), 3, "var r1 = a1(b1, c1);"),
    (CallMethod("m"), 1, "var r1 = a1.m();"),
    (CallMethod("m"), 3, "var r1 = a1.m(b1, c1);"),
    (CallMethod("a-b"), 2, 'var r1 = a1["a-b"](b1);'),
    (Construct(), 1, "var r1 = new a1();"),
    (Construct(), 3, "var r1 = new a1(b1, c1);"),
    (CreateArray(), 0, "var r1 = [];"),
    (CreateArray(), 2, "var r1 = [a1, b1];"),
    (CreateArray(holes=(1,)), 2, "var r1 = [a1, , b1];"),
    (CreateArray(holes=(0,)), 1, "var r1 = [, a1];"),
    (CreateArray(holes=(2,)), 2, "var r1 = [a1, b1, ,];"),
    (CreateObject(), 0, "var r1 = {};"),
    (CreateObject(("k", "a-b")), 2, 'var r1 = {k: a1, "a-b": b1};'),
    (Eval("A.f({0}, {1})", T.UNKNOWN), 2, "var r1 = A.f(a1, b1);"),
])
def test_expression_instruction_forms(op, nin, expected):
    assert opcase(op, nin) == expected


@pytest.mark.parametrize("op,nin,expected", [
    (CallFunction(discard=True), 1, "a1();"),
    (CallFunction(discard=True), 3, "a1(b1, c1);"),
    (CallMethod("m", discard=True), 2, "a1.m(b1);"),
    (Construct(discard=True), 2, "new a1(b1);"),
    (SetProperty("p"), 2, "a1.p = b1;"),
    (SetProperty("a-b"), 2, 'a1["a-b"] = b1;'),
    (SetElement(), 3, "a1[b1] = c1;"),
    (Eval("{0}.x = {1}"), 2, "a1.x = b1;"),
    (Throw(), 1, None),            # handled below: throw is only valid in any block, text is checked here
])
def test_statement_forms(op, nin, expected):
    if isinstance(op, Throw):
        expected = "throw a1;"
    assert stmtcase(op, nin) == expected


def test_return_and_throw_forms():
    p = P()
    f, (a,) = p.add(BeginFunction(1), nout=1, ninner=1)
    p.stmt(Return(), (a,))
    p.stmt(Return())
    p.stmt(Throw(), (a,))
    p.stmt(EndFunction())
    assert lines(body(p.program())) == ["function f1(p1) {", "  return p1;", "  return;", "  throw p1;", "}"]


@pytest.mark.parametrize("op,kind,expected", [
    (Reassign(), None, "d1 = 5;"),
    (CompoundAssign("+"), None, "d1 += 5;"),
    (CompoundAssign(">>>"), None, "d1 >>>= 5;"),
    (CompoundAssign("**"), None, "d1 **= 5;"),
])
def test_assignment_statements(op, kind, expected):
    p = P()
    d = p.declare(p.lit("1"))
    k = p.lit("5")
    p.stmt(op, (d, k))
    assert lines(body(p.program()))[-1] == expected


@pytest.mark.parametrize("op,expected", [
    (Update("++", value=False), "d1++;"),
    (Update("--", value=False), "d1--;"),
    (Update("++", prefix=True, value=False), "++d1;"),
])
def test_update_statements(op, expected):
    p = P()
    d = p.declare(p.lit("1"))
    p.stmt(op, (d,))
    assert lines(body(p.program()))[-1] == expected


@pytest.mark.parametrize("op,expected", [
    (Update("++"), "var r1 = d1++;"),
    (Update("--"), "var r1 = d1--;"),
    (Update("++", prefix=True), "var r1 = ++d1;"),
    (Update("--", prefix=True), "var r1 = --d1;"),
])
def test_update_expressions(op, expected):
    p = P()
    d = p.declare(p.lit("1"))
    p.add(op, (d,), hint="r", keep=True)
    assert lines(body(p.program()))[-1] == expected


def test_declare_kinds():
    p = P()
    for k in ("var", "let", "const"):
        p.declare(p.lit("1"), kind=k, hint=k[0])
    assert lines(body(p.program())) == ["var v1 = 1;", "let l1 = 1;", "const c1 = 1;"]


def test_load_builtin_forms():
    p = P()
    p.builtin("Math", T.OBJECT, keep=True, hint="m")
    p.builtin("Array.prototype", T.OBJECT, keep=True, hint="m")
    assert lines(raw(p.program())) == ["var m1 = Math;", "var m2 = Array.prototype;"]


def test_member_access_on_dotted_builtin_needs_no_parens():
    p = P()
    b = p.builtin("Array.prototype", T.OBJECT, keep=None)
    p.declare(p.add(GetProperty("slice"), (b,)))
    assert body(p.program()) == "var d1 = Array.prototype.slice;\n"


# ---------------------------------------------------------------------------- precedence

L, ID = "lit", lambda n: ("id", n)


@pytest.mark.parametrize("spec,expected", [
    (("bin", "*", ("bin", "+", "1", "2"), "3"), "(1 + 2) * 3"),
    (("bin", "+", "1", ("bin", "*", "2", "3")), "1 + 2 * 3"),
    (("bin", "-", "1", ("bin", "-", "2", "3")), "1 - (2 - 3)"),
    (("bin", "-", ("bin", "-", "1", "2"), "3"), "1 - 2 - 3"),
    (("bin", "/", "1", ("bin", "*", "2", "3")), "1 / (2 * 3)"),
    (("bin", "<", ("bin", "+", "1", "2"), "3"), "1 + 2 < 3"),
    (("bin", "==", ("bin", "<", "1", "2"), "true"), "1 < 2 == true"),
    (("bin", "<", "1", ("bin", "==", "2", "3")), "1 < (2 == 3)"),
    (("bin", "&", ("bin", "==", "1", "2"), "3"), "1 == 2 & 3"),
    (("bin", "==", ("bin", "&", "1", "2"), "3"), "(1 & 2) == 3"),
    (("bin", "|", ("bin", "^", "1", "2"), ("bin", "&", "3", "4")), "1 ^ 2 | 3 & 4"),
    (("bin", "&", ("bin", "|", "1", "2"), "3"), "(1 | 2) & 3"),
    (("bin", "<<", ("bin", "+", "1", "2"), "3"), "1 + 2 << 3"),
    (("bin", "in", '"a"', ("id", "o")), '"a" in o'),
    (("bin", "instanceof", ("id", "o"), ("id", "C")), "o instanceof C"),
    # exponentiation: right-associative, unary left operand parenthesised
    (("bin", "**", "2", ("bin", "**", "3", "4")), "2 ** 3 ** 4"),
    (("bin", "**", ("bin", "**", "2", "3"), "4"), "(2 ** 3) ** 4"),
    (("bin", "**", ("un", "-", "2"), "2"), "(-2) ** 2"),
    (("bin", "**", ("un", "typeof", "2"), "2"), "(typeof 2) ** 2"),
    (("bin", "**", "-1", "2"), "(-1) ** 2"),
    (("bin", "**", "2", ("un", "-", "3")), "2 ** -3"),
    (("bin", "*", ("bin", "**", "2", "3"), "4"), "2 ** 3 * 4"),
    (("bin", "**", ("bin", "*", "2", "3"), "4"), "(2 * 3) ** 4"),
    # logical and nullish
    (("bin", "??", ("bin", "||", ("id", "a"), ("id", "b")), ("id", "c")), "(a || b) ?? c"),
    (("bin", "??", ("id", "a"), ("bin", "&&", ("id", "b"), ("id", "c"))), "a ?? (b && c)"),
    (("bin", "||", ("bin", "??", ("id", "a"), ("id", "b")), ("id", "c")), "(a ?? b) || c"),
    (("bin", "&&", ("id", "a"), ("bin", "??", ("id", "b"), ("id", "c"))), "a && (b ?? c)"),
    (("bin", "??", ("bin", "??", ("id", "a"), ("id", "b")), ("id", "c")), "a ?? b ?? c"),
    (("bin", "??", ("id", "a"), ("bin", "??", ("id", "b"), ("id", "c"))), "a ?? (b ?? c)"),
    (("bin", "||", ("id", "a"), ("bin", "&&", ("id", "b"), ("id", "c"))), "a || b && c"),
    (("bin", "&&", ("bin", "||", ("id", "a"), ("id", "b")), ("id", "c")), "(a || b) && c"),
    (("bin", "||", ("bin", "||", ("id", "a"), ("id", "b")), ("id", "c")), "a || b || c"),
    (("bin", "??", ("bin", "==", ("id", "a"), ("id", "b")), ("id", "c")), "a == b ?? c"),
    # unary
    (("un", "-", "-1"), "-(-1)"),
    (("un", "+", "+1"), "+(+1)"),
    (("un", "-", ("un", "-", ("id", "x"))), "-(-x)"),
    (("un", "+", ("un", "+", ("id", "x"))), "+(+x)"),
    (("un", "-", ("un", "+", ("id", "x"))), "-+x"),
    (("un", "+", ("un", "-", ("id", "x"))), "+-x"),
    (("un", "!", ("un", "!", ("id", "x"))), "!!x"),
    (("un", "~", ("un", "~", ("id", "x"))), "~~x"),
    (("un", "-", ("bin", "+", "1", "2")), "-(1 + 2)"),
    (("un", "!", ("bin", "&&", ("id", "a"), ("id", "b"))), "!(a && b)"),
    (("un", "typeof", ("un", "typeof", ("id", "x"))), "typeof typeof x"),
    (("un", "typeof", ("bin", "+", "1", "2")), "typeof (1 + 2)"),
    (("un", "void", "0"), "void 0"),
    (("un", "-", "5"), "-5"),
    (("un", "!", ("get", ("id", "a"), "b")), "!a.b"),
    (("bin", "-", "1", ("un", "-", "2")), "1 - -2"),
    # conditional
    (("cond", ("cond", ("id", "a"), ("id", "b"), ("id", "c")), ("id", "d"), ("id", "e")), "(a ? b : c) ? d : e"),
    (("cond", ("id", "a"), ("id", "b"), ("cond", ("id", "c"), ("id", "d"), ("id", "e"))), "a ? b : c ? d : e"),
    (("cond", ("id", "a"), ("cond", ("id", "b"), ("id", "c"), ("id", "d")), ("id", "e")), "a ? b ? c : d : e"),
    (("cond", ("bin", "||", ("id", "a"), ("id", "b")), "1", "2"), "a || b ? 1 : 2"),
    (("cond", ("id", "a"), ("bin", "+", "1", "2"), "3"), "a ? 1 + 2 : 3"),
    (("bin", "+", ("cond", ("id", "a"), "1", "2"), "3"), "(a ? 1 : 2) + 3"),
    # member, call, new
    (("get", ("bin", "+", "1", "2"), "x"), "(1 + 2).x"),
    (("method", "1", "toString"), "(1).toString()"),
    (("get", "1.5", "x"), "(1.5).x"),
    (("get", "-1", "x"), "(-1).x"),
    (("get", '"a"', "length"), '"a".length'),
    (("get", "true", "x"), "true.x"),
    (("get", ("new", ("id", "F")), "x"), "new F().x"),
    (("get", ("get", ("id", "a"), "b"), "c"), "a.b.c"),
    (("elem", ("cond", ("id", "a"), ("id", "b"), ("id", "c")), "0"), "(a ? b : c)[0]"),
    (("elem", ("id", "a"), ("bin", "+", "1", "2")), "a[1 + 2]"),
    (("elem", "1", "0"), "(1)[0]"),
    (("call", ("bin", "||", ("id", "a"), ("id", "b"))), "(a || b)()"),
    (("call", ("id", "f"), ("cond", ("id", "a"), ("id", "b"), ("id", "c"))), "f(a ? b : c)"),
    (("call", ("id", "f"), ("bin", "+", "1", "2"), "3"), "f(1 + 2, 3)"),
    (("call", ("call", ("id", "f"))), "f()()"),
    (("method", ("call", ("id", "f")), "m", "1"), "f().m(1)"),
    (("method", ("un", "-", "1"), "m"), "(-1).m()"),
    (("new", ("id", "F")), "new F()"),
    (("new", ("get", ("id", "a"), "b")), "new a.b()"),
    (("new", ("call", ("id", "f"))), "new (f())()"),
    (("new", ("call", ("get", ("id", "a"), "b"))), "new (x1())()"),     # a property read is not folded into a callee
    (("new", ("method", ("id", "a"), "b")), "new (a.b())()"),
    (("arr", ("cond", ("id", "a"), ("id", "b"), ("id", "c")), "1"), "[a ? b : c, 1]"),
    (("arr", ("bin", "||", ("id", "a"), ("id", "b")), ("bin", "+", "1", "2")), "[a || b, 1 + 2]"),
    (("obj", ("k",), ("cond", ("id", "a"), ("id", "b"), ("id", "c"))), "{k: a ? b : c}"),
    (("new", ("elem", ("id", "a"), "0")), "new (a[0])()"),
    (("new", ("new", ("id", "F"))), "new (new F())()"),
    (("new", ("id", "F"), ("bin", "+", "1", "2")), "new F(1 + 2)"),
    (("bin", "+", ("new", ("id", "F")), "1"), "new F() + 1"),
])
def test_precedence_and_parenthesisation(spec, expected):
    assert spell(spec) == expected


def test_eval_operand_is_parenthesised_in_binary_but_not_in_arguments():
    p = P()
    e = p.add(Eval("x = 1", T.NUMBER, pure=True))
    p.declare(p.add(Binary("+"), (e, p.lit("2"))))
    assert body(p.program()) == "var d1 = (x = 1) + 2;\n"
    p = P()
    e = p.add(Eval("x = 1", T.NUMBER, pure=True))
    p.declare(p.add(CreateArray(), (e,)))
    assert body(p.program()) == "var d1 = [x = 1];\n"


@pytest.mark.parametrize("text,prec", [
    ("5", 20), ("0x1F", 20), ("1.5", 20), ("1.5e3", 20), ("10n", 20), ("NaN", 20), ("Infinity", 20),
    ("true", 20), ("null", 20), ("undefined", 20), (" 5 ", 20),
    ("-5", 16), ("+1", 16), ("-0", 16), ("-Infinity", 16), ("-1n", 16),
    ("/a/g", 20), ("/a+/", 20),
    ('"a"', 20), ("'a'", 20), ('"a\\"b"', 20), ("`x`", 20), ('""', 20), ('"("', 20),
    ('"a" + "b"', 2), ('"a" "b"', 2), ("'a' + 'b'", 2),
    ("[1, 2]", 20), ("[]", 20), ('[")"]', 20), ("[[1], [2]]", 20), ("{a: 1}", 20), ("{}", 20),
    ("[1] + [2]", 2), ("[1][0]", 2), ("[1, 2].length", 2), ("{a: 1}.a", 2),
    ('Symbol("s")', 19), ('Symbol.for("k")', 19), ("BigInt(1)", 19),
    ("1/3", 14), ("1 / 3", 14), ("0.5/0.25", 14),
    ("void 0", 2), ("1 + 2", 2), ("a ? b : c", 2),
])
def test_literal_prec(text, prec):
    assert literal_prec(text) == prec


@pytest.mark.parametrize("text,whole", [
    ('"a"', True), ('"a" + "b"', False), ('"a" "b"', False), ('"a\\"b"', True), ('"\\\\"', True),
    ("'a'", True), ("`a${1}`", True), ("[1, 2]", True), ("[1][0]", False), ("[1] + [2]", False),
    ('[")"]', True), ('["]"]', True), ("[[1], [2]]", True), ("{a: {b: 1}}", True), ("{a: 1}.a", False),
    ("(a)(b)", False), ("(a)", True), ("[", False), ('"unterminated', False), ("[1, 2", False),
])
def test_spans_whole(text, whole):
    assert _spans_whole(text) is whole


@pytest.mark.parametrize("text,expected", [
    ("-5", "(-5) ** 2"), ("1/3", "(1/3) ** 2"), ('"a" + "b"', '("a" + "b") ** 2'),
    ("[1, 2]", "[1, 2] ** 2"), ("{a: 1}", "{a: 1} ** 2"),
])
def test_literal_spellings_are_parenthesised_by_class(text, expected):
    p = P()
    p.declare(p.add(Binary("**"), (p.lit(text, T.UNKNOWN), p.lit("2"))))
    assert body(p.program()) == f"var d1 = {expected};\n"


# ---------------------------------------------------------------------------- literals and keep

@pytest.mark.parametrize("keep,inlined", [(None, True), (False, True), (True, False)])
def test_literal_keep_states(keep, inlined):
    p = P()
    k = p.lit("5", keep=keep)
    p.declare(p.add(Binary("+"), (k, k)), keep=None)
    out = body(p.program())
    if inlined:
        assert out == "var d1 = 5 + 5;\n"
    else:
        assert out == "var i1 = 5;\nvar d1 = i1 + i1;\n"


@pytest.mark.parametrize("keep", [None, False, True])
def test_literal_with_no_use_stays_a_variable(keep):
    p = P()
    p.lit("5", keep=keep)
    assert body(p.program(), dce=False) == "var i1 = 5;\n"
    assert lift(p.program(), comments=False).source == "var i1 = 5;\n"          # probes on: nothing is dropped
    assert body(p.program()).strip() == ""                                        # plain: dead pure temporary


@pytest.mark.parametrize("t,text", [(T.REGEXP, "/a/"), (T.SYMBOL, 'Symbol("s")'), (T.ARRAY, "[1]")])
def test_non_primitive_literals_fold_like_pure_expressions_but_are_never_spelled_at_several_uses(t, text):
    p = P()
    k = p.lit(text, t)
    p.stmt(CallMethod("m", discard=True), (k,))
    assert lines(body(p.program())) == [f"{text}.m();"]
    p = P()
    k = p.lit(text, t)
    p.stmt(CallMethod("m", discard=True), (k,))
    p.stmt(CallMethod("n", discard=True), (k,))
    out = lines(body(p.program()))
    assert out[0].startswith("var ") and out[0].endswith(f" = {text};") and len(out) == 3


@pytest.mark.parametrize("make", ["literal", "create"])
def test_statement_never_starts_with_an_object_literal(make):
    p = P()
    k = p.lit("{}", T.OBJECT) if make == "literal" else p.add(CreateObject())
    p.stmt(CallMethod("m", discard=True), (k,))
    assert not lines(body(p.program()))[0].startswith("{")


@pytest.mark.parametrize("t,text,name", [
    (T.NUMBER, "1.5", "n1"), (T.INT32, "1", "i1"), (T.BIGINT, "1n", "b1"), (T.STRING, '"s"', "s1"),
    (T.BOOLEAN, "true", "t1"), (T.UNDEFINED, "undefined", "u1"), (T.NULL, "null", "z1"),
])
def test_primitive_literal_types_are_inlined_and_named_by_type(t, text, name):
    p = P()
    k = p.lit(text, t, keep=True)
    p.stmt(CallMethod("m", discard=True), (k,))
    assert lines(body(p.program())) == [f"var {name} = {text};", f"{name}.m();"]
    p = P()
    k = p.lit(text, t)
    p.stmt(CallMethod("m", discard=True), (k,))
    out = lines(body(p.program()))
    assert out == [f"({text}).m();"] if text[0].isdigit() or text[0] == "-" else [f"{text}.m();"]


def test_inline_false_keeps_every_temporary_as_a_statement():
    p = P()
    a = p.lit("1")
    b = p.lit("2")
    c = p.add(Binary("+"), (a, b))
    p.declare(c)
    assert lines(body(p.program(), inline=False)) == ["var i1 = 1;", "var i2 = 2;", "var n1 = i1 + i2;",
                                                       "var d1 = n1;"]
    assert lines(body(p.program())) == ["var d1 = 1 + 2;"]


def test_declared_variables_are_never_inlined():
    p = P()
    d = p.declare(p.lit("1"))
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None), d))
    assert lines(body(p.program())) == ["var d1 = 1;", "f(d1);"]


# ---------------------------------------------------------------------------- folding

def test_two_uses_are_not_folded():
    p = P()
    f = p.builtin("f", keep=None)
    c = p.add(CallFunction(), (f,))
    p.declare(p.add(Binary("+"), (c, c)))
    assert lines(body(p.program())) == ["var x1 = f();", "var d1 = x1 + x1;"]


def test_keep_true_prevents_folding_and_none_or_false_allow_it():
    for keep, folded in ((None, True), (False, True), (True, False)):
        p = P()
        c = p.add(CallFunction(), (p.builtin("f", keep=None),), keep=keep, hint="c")
        p.declare(c)
        assert lines(body(p.program())) == (["var d1 = f();"] if folded else ["var c1 = f();", "var d1 = c1;"])


def test_unused_impure_expression_stays_a_statement():
    p = P()
    p.add(CallFunction(), (p.builtin("f", keep=None),), hint="c")
    assert body(p.program()) == "var c1 = f();\n"


def test_not_adjacent_use_is_not_folded_even_if_pure():
    p = P()
    a = p.add(Binary("+"), (p.lit("1"), p.lit("2")), hint="a")
    f = p.builtin("f", keep=None)
    p.stmt(CallFunction(discard=True), (f,))
    p.declare(a)
    assert lines(body(p.program())) == ["var a1 = 1 + 2;", "f();", "var d1 = a1;"]


def test_literals_between_expression_and_use_are_skipped():
    p = P()
    c = p.add(CallFunction(), (p.builtin("f", keep=None),))
    k = p.lit("5")
    p.declare(p.add(Binary("+"), (c, k)))
    assert body(p.program()) == "var d1 = f() + 5;\n"


def test_kept_literal_between_blocks_folding():
    p = P()
    c = p.add(CallFunction(), (p.builtin("f", keep=None),), hint="c")
    k = p.lit("5", keep=True, hint="k")
    p.declare(p.add(Binary("+"), (c, k)))
    assert lines(body(p.program())) == ["var c1 = f();", "var k1 = 5;", "var d1 = c1 + k1;"]


def test_dead_code_between_does_not_block_folding_with_dce_only():
    p = P()
    c = p.add(CallFunction(), (p.builtin("f", keep=None),), hint="c")
    p.add(Binary("+"), (p.lit("1"), p.lit("2")), hint="dead")
    p.declare(c)
    assert body(p.program()) == "var d1 = f();\n"
    out = lift(p.program(), probes=True, comments=False).source
    assert "var c1 = f();" in out and "var dead1 = 1 + 2;" in out and "var d1 = c1;" in out


def _decl_use(prefix_stmts, make_use):
    p = P()
    d = p.declare(p.lit("1"))
    f = p.builtin("f", keep=None)
    make_use(p, d, f)
    return lines(body(p.program()))[1:]


def test_impure_call_not_folded_past_declared_variable_read():
    # d + f(): f could assign d, and d was read after the call originally? here the call came first.
    def use(p, d, f):
        c = p.add(CallFunction(), (f,), hint="c")
        p.declare(p.add(Binary("+"), (d, c)), hint="e")
    assert _decl_use((), use) == ["var c1 = f();", "var e1 = d1 + c1;"]


def test_impure_call_folds_when_everything_before_it_is_a_temporary():
    def use(p, d, f):
        t = p.add(Binary("+"), (p.lit("1"), p.lit("2")))
        c = p.add(CallFunction(), (f,), hint="c")
        p.declare(p.add(Binary("+"), (t, c)), hint="e")
    assert _decl_use((), use) == ["var e1 = 1 + 2 + f();"]


def test_pure_expression_folds_past_declared_variable_read():
    def use(p, d, f):
        t = p.add(Binary("*"), (p.lit("2"), p.lit("3")))
        p.declare(p.add(Binary("+"), (d, t)), hint="e")
    assert _decl_use((), use) == ["var e1 = d1 + 2 * 3;"]


def test_compound_assign_reads_its_target_first():
    def use(p, d, f):
        c = p.add(CallFunction(), (f,), hint="c")
        p.stmt(CompoundAssign("+"), (d, c))
    assert _decl_use((), use) == ["var c1 = f();", "d1 += c1;"]

    def pure(p, d, f):
        p.stmt(CompoundAssign("+"), (d, p.add(Binary("*"), (p.lit("2"), p.lit("3")))))
    assert _decl_use((), pure) == ["d1 += 2 * 3;"]


def test_reassign_rhs_does_not_read_the_target():
    def use(p, d, f):
        p.stmt(Reassign(), (d, p.add(CallFunction(), (f,))))
    assert _decl_use((), use) == ["d1 = f();"]


def test_update_target_is_the_only_operand_so_nothing_folds_into_it():
    def use(p, d, f):
        c = p.add(CallFunction(), (f,), hint="c")
        p.stmt(Update("++", value=False), (d,))
        p.stmt(Reassign(), (d, c))
    assert _decl_use((), use) == ["var c1 = f();", "d1++;", "d1 = c1;"]


def test_set_property_receiver_and_value_slots():
    # o.p = f(): the receiver is a declared variable evaluated first -> the call cannot fold.
    p = P()
    o = p.declare(p.builtin("O", T.OBJECT, keep=None), hint="o")
    p.stmt(SetProperty("p"), (o, p.add(CallFunction(), (p.builtin("f", keep=None),), hint="c")))
    assert lines(body(p.program()))[1:] == ["var c1 = f();", "o1.p = c1;"]
    # With a temporary receiver it folds.
    p = P()
    o = p.builtin("O", T.OBJECT, keep=None)
    p.stmt(SetProperty("p"), (o, p.add(CallFunction(), (p.builtin("f", keep=None),))))
    assert lines(body(p.program())) == ["O.p = f();"]


def test_call_folded_past_property_read_only_in_program_order():
    def prog(order):
        p = P()
        o = p.builtin("o", keep=True, hint="o")
        f = p.builtin("f", keep=True, hint="f")
        c = p.add(CallFunction(), (f,), hint="c")
        g = p.add(GetProperty("p"), (o,), hint="g")
        p.declare(p.add(Binary("+"), tuple({"c": c, "g": g}[k] for k in order)), hint="e")
        return lines(body(p.program()))[2:]
    assert prog("cg") == ["var e1 = f1() + o1.p;"]
    assert prog("gc") == ["var c1 = f1();", "var e1 = o1.p + c1;"]


def test_property_read_folds_past_a_later_call_in_order():
    p = P()
    o = p.builtin("o", keep=True, hint="o")
    f = p.builtin("f", keep=True, hint="f")
    g = p.add(GetProperty("p"), (o,))
    c = p.add(CallFunction(), (f,))
    p.declare(p.add(Binary("+"), (g, c)), hint="e")
    assert lines(body(p.program()))[2:] == ["var e1 = o1.p + f1();"]


def test_several_expressions_fold_into_one_use_in_slot_order():
    def prog(order):
        p = P()
        f = p.builtin("f", keep=True, hint="f")
        g = p.builtin("g", keep=True, hint="g")
        c1 = p.add(CallFunction(), (f,))
        c2 = p.add(CallFunction(), (g,))
        p.declare(p.add(CreateArray(), tuple((c1, c2)[k] for k in order)), hint="e")
        return lines(body(p.program()))[2:]
    assert prog((0, 1)) == ["var e1 = [f1(), g1()];"]
    out = prog((1, 0))
    assert out[-1] == "var e1 = [g1(), x1];" and out[0] == "var x1 = f1();"


def test_property_read_never_folds_into_a_callee_but_does_elsewhere():
    for make_get, text in ((lambda p, o: p.add(GetProperty("m"), (o,)), "o1.m"),
                           (lambda p, o: p.add(GetElement(), (o, p.lit("0"))), "o1[0]")):
        p = P()
        o = p.builtin("o", keep=True, hint="o")
        g = make_get(p, o)
        p.declare(p.add(CallFunction(), (g,)), hint="e")
        out = lines(body(p.program()))
        assert out[-1] == "var e1 = x1();" and out[-2] == f"var x1 = {text};", out
        # as an argument it folds, as the receiver of a method call too
        p = P()
        o = p.builtin("o", keep=True, hint="o")
        f = p.builtin("f", keep=None)
        g = make_get(p, o)
        p.stmt(CallFunction(discard=True), (f, g))
        assert lines(body(p.program()))[-1] == f"f({text});"
        p = P()
        o = p.builtin("o", keep=True, hint="o")
        g = make_get(p, o)
        p.stmt(CallMethod("go", discard=True), (g,))
        assert lines(body(p.program()))[-1] == f"{text}.go();"


def test_object_typed_inputs_make_an_expression_impure():
    # `o + 1` may run valueOf; it must not move past the read of d. Primitive operands may.
    for src, folded in ((("O", T.OBJECT), False), (("5", T.INT32), True)):
        p = P()
        d = p.declare(p.lit("1"))
        x = p.builtin("O", src[1], keep=True, hint="o") if src[1] == T.OBJECT else p.lit(src[0], src[1], keep=True,
                                                                                     hint="o")
        t = p.add(Binary("+"), (x, p.lit("1")), hint="t")
        p.stmt(CompoundAssign("+"), (d, t))
        out = lines(body(p.program()))
        assert (out[-1] == "d1 += o1 + 1;") if folded else (out[-2:] == ["var t1 = o1 + 1;", "d1 += t1;"]), out


def test_length_of_string_or_array_is_pure_but_not_of_other_things():
    for t, folded in ((T.STRING, True), (T.ARRAY, True), (T.OBJECT, False), (T.UNKNOWN, False)):
        p = P()
        d = p.declare(p.lit("1"))
        s = p.builtin("S", t, keep=True, hint="s")
        n = p.add(GetProperty("length"), (s,), hint="n")
        p.stmt(CompoundAssign("+"), (d, n))
        out = lines(body(p.program()))
        assert (out[-1] == "d1 += s1.length;") if folded else (out[-2:] == ["var n1 = s1.length;", "d1 += n1;"]), t
    # other properties of a string are not pure
    p = P()
    d = p.declare(p.lit("1"))
    s = p.builtin("S", T.STRING, keep=True, hint="s")
    n = p.add(GetProperty("size"), (s,), hint="n")
    p.stmt(CompoundAssign("+"), (d, n))
    assert lines(body(p.program()))[-2:] == ["var n1 = s1.size;", "d1 += n1;"]


def test_pure_eval_folds_and_impure_eval_does_not():
    for pure, folded in ((True, True), (False, False)):
        p = P()
        d = p.declare(p.lit("1"))
        e = p.add(Eval("Math.abs(1)", T.NUMBER, pure=pure), hint="e")
        p.stmt(CompoundAssign("+"), (d, e))
        out = lines(body(p.program()))
        assert (out[-1] == "d1 += Math.abs(1);") if folded else (out[-2:] == ["var e1 = Math.abs(1);", "d1 += e1;"])


def test_eval_user_has_opaque_order_so_only_pure_expressions_fold_into_it():
    p = P()
    pure = p.add(Binary("+"), (p.lit("1"), p.lit("2")))
    p.stmt(Eval("print({0})"), (pure,))
    assert lines(body(p.program())) == ["print((1 + 2));"]
    p = P()
    c = p.add(CallFunction(), (p.builtin("f", keep=None),), hint="c")
    p.stmt(Eval("print({0})"), (c,))
    assert lines(body(p.program())) == ["var c1 = f();", "print(c1);"]


def test_discarded_call_arguments_fold():
    p = P()
    f = p.builtin("f", keep=None)
    c = p.add(CallMethod("m"), (p.builtin("o", keep=None),))
    p.stmt(CallFunction(discard=True), (f, c, p.lit("1")))
    assert lines(body(p.program())) == ["f(o.m(), 1);"]


def test_chain_of_calls_text():
    p = P()
    f = p.builtin("f", keep=None)
    c1 = p.add(CallFunction(), (f,))
    c2 = p.add(CallMethod("m"), (c1,))
    p.declare(c2)
    assert body(p.program()) == "var d1 = f().m();\n"


def test_probe_blocks_nothing_but_reports_only_visible_not_inlined():
    p = P()
    c = p.add(CallFunction(), (p.builtin("f", keep=None),), hint="c")
    p.declare(c)
    p.stmt(Probe())
    out = lift(p.program(), comments=False).source
    assert lines(out) == ["var d1 = f();", '__probe("p1", d1);']


# ---------------------------------------------------------------------------- dead code

def test_dce_drops_dead_pure_chains_and_their_literals():
    p = P()
    a = p.add(Binary("+"), (p.lit("1"), p.lit("2")), hint="a", keep=True)
    p.add(Binary("*"), (a, p.lit("3")), hint="b")
    assert body(p.program()).strip() == ""
    out = lift(p.program(), probes=True, comments=False).source
    assert lines(out) == ["var a1 = 1 + 2;", "var b1 = a1 * 3;"]


def test_dce_keeps_what_has_effects_or_is_used_or_not_primitive():
    p = P()
    p.add(CallFunction(), (p.builtin("f", keep=None),), hint="c")                       # impure
    p.add(Binary("+"), (p.builtin("O", T.OBJECT, keep=True, hint="o"), p.lit("1")), hint="t")   # may run valueOf
    a = p.add(Binary("+"), (p.lit("1"), p.lit("2")), hint="a")                          # used
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None), a))
    p.add(Unary("-"), (p.lit("1"),), hint="dead")                                       # dropped
    assert lines(body(p.program())) == ["var c1 = f();", "var o1 = O;", "var t1 = o1 + 1;", "f(1 + 2);"]


def test_dce_explicit_flag_overrides_probe_default():
    p = P()
    p.add(Unary("-"), (p.lit("1"),), hint="dead")
    assert lift(p.program(), probes=False, comments=False, dce=False).source == "var dead1 = -1;\n"
    assert lift(p.program(), probes=True, comments=False, dce=True).source.strip() == ""


def test_dead_load_builtin_and_array_are_dropped_without_probes():
    p = P()
    p.builtin("Math", T.OBJECT, keep=False)
    p.add(CreateArray(), (p.lit("1"),))
    p.add(CreateObject(("k",)), (p.lit("1"),))
    assert body(p.program()).strip() == ""


def test_call_whose_only_user_is_dead_code_is_still_emitted():
    # function f1() { sideeffect(); return 1; }  var c = f1();  var dead = c + 2;  (c is INT32, so `dead` is
    # pure and unused). The call is folded into the dead instruction, which emits nothing: the call vanishes.
    p = P()
    f = p.add(BeginFunction(0), nout=1, ninner=0)
    p.stmt(Eval("sideeffect()"))
    p.stmt(Return(), (p.lit("1"),))
    p.stmt(EndFunction())
    c = p.add(CallFunction(), (f,), hint="c")
    p.add(Binary("+"), (c, p.lit("2")), hint="dead")
    assert "f1()" in body(p.program()).split("}\n", 1)[1]


# ---------------------------------------------------------------------------- naming

def test_names_by_type_prefix_with_one_counter_per_prefix():
    p = P()
    for t, text in ((T.INT32, "1"), (T.STRING, '"s"'), (T.INT32, "2"), (T.NUMBER, "1.5"), (T.STRING, '"t"')):
        p.lit(text, t, keep=True)
    assert [x.split(" = ")[0] for x in lines(raw(p.program()))] == ["var i1", "var s1", "var i2", "var n1", "var s2"]


@pytest.mark.parametrize("t,prefix", [
    (T.ARRAY, "a"), (T.OBJECT, "o"), (T.FUNCTION, "f"), (T.REGEXP, "r"), (T.TYPEDARRAY, "ta"), (T.MAP, "m"),
    (T.SET, "e"), (T.UNKNOWN, "x"), (T.SYMBOL, "y"),
])
def test_object_type_prefixes(t, prefix):
    p = P()
    p.builtin("Q", t, keep=True)
    assert raw(p.program()) == f"var {prefix}1 = Q;\n"


def test_hint_overrides_prefix_and_shares_counter_space_with_equal_prefix():
    p = P()
    p.lit("1", keep=True, hint="acc")
    p.lit("2", keep=True, hint="acc")
    p.lit("3", keep=True)
    p.lit("4", keep=True, hint="i")
    assert [x.split(" = ")[0] for x in lines(raw(p.program()))] == ["var acc1", "var acc2", "var i1", "var i2"]


def test_numbering_follows_first_use_in_the_output():
    p = P()
    a = p.lit("1", keep=True)
    b = p.lit("2", keep=True)
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None), b, a))
    assert lines(body(p.program())) == ["var i1 = 1;", "var i2 = 2;", "f(i2, i1);"]


def test_params_function_names_and_counters():
    p = P()
    p.add(BeginFunction(2), nout=1, ninner=2)
    p.stmt(EndFunction())
    p.add(BeginFunction(1), nout=1, ninner=1, hint="ctor")
    p.stmt(EndFunction())
    _, (i,) = p.add(BeginFor(2), nout=0, ninner=1)
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None), i))
    p.stmt(EndFor())
    p.add(BeginFor(3), nout=0, ninner=1)
    p.stmt(EndFor())
    assert lines(body(p.program())) == [
        "function f1(p1, p2) {", "}", "function ctor1(p3) {", "}", "for (var i1 = 0; i1 < 2; i1++) {", "  f(i1);",
        "}", "for (var i2 = 0; i2 < 3; i2++) {", "}"]


def test_catch_variable_names():
    p = P()
    p.stmt(BeginTry())
    p.stmt(Throw(), (p.lit("1"),))
    _, (e,) = p.add(BeginCatch(), nout=0, ninner=1)
    p.stmt(Throw(), (e,))
    p.stmt(BeginFinally())
    p.stmt(EndTry())
    p.stmt(BeginTry())
    _, (e2,) = p.add(BeginCatch(), nout=0, ninner=1)
    p.stmt(EndTry())
    assert lines(body(p.program())) == ["try {", "  throw 1;", "} catch (ex1) {", "  throw ex1;", "} finally {", "}",
                                        "try {", "} catch (ex2) {", "}"]


def test_loop_counters_and_literals_share_the_i_counter():
    p = P()
    p.lit("1", keep=True)
    _, (i,) = p.add(BeginFor(2), nout=0, ninner=1)
    p.stmt(EndFor())
    assert lines(raw(p.program())) == ["var i1 = 1;", "for (var i2 = 0; i2 < 2; i2++) {", "}"]


# ---------------------------------------------------------------------------- blocks

@pytest.mark.parametrize("style,strict,expected", [
    ("decl", False, ["function f1(p1, p2) {", "  return p1;", "}"]),
    ("expr", False, ["var f1 = function(p1, p2) {", "  return p1;", "};"]),
    ("arrow", False, ["var f1 = (p1, p2) => {", "  return p1;", "};"]),
    ("decl", True, ["function f1(p1, p2) {", '  "use strict";', "  return p1;", "}"]),
    ("expr", True, ["var f1 = function(p1, p2) {", '  "use strict";', "  return p1;", "};"]),
    ("arrow", True, ["var f1 = (p1, p2) => {", '  "use strict";', "  return p1;", "};"]),
])
def test_function_styles(style, strict, expected):
    p = P()
    f, (a, b) = p.add(BeginFunction(2, style, strict), nout=1, ninner=2)
    p.stmt(Return(), (a,))
    p.stmt(EndFunction())
    assert lines(body(p.program())) == expected


def test_zero_arity_functions():
    p = P()
    p.add(BeginFunction(0), nout=1, ninner=0)
    p.stmt(EndFunction())
    p.add(BeginFunction(0, "arrow"), nout=1, ninner=0)
    p.stmt(EndFunction())
    p.add(BeginFunction(0, "expr"), nout=1, ninner=0)
    p.stmt(EndFunction())
    assert lines(body(p.program())) == ["function f1() {", "}", "var f2 = () => {", "};", "var f3 = function() {", "};"]


@pytest.mark.parametrize("opener", ["if", "for", "try"])
def test_declaration_not_at_top_or_function_level_is_an_expression(opener):
    p = P()
    if opener == "if":
        p.stmt(BeginIf(), (p.lit("true", keep=True),))
    elif opener == "for":
        p.add(BeginFor(1), nout=0, ninner=1)
    else:
        p.stmt(BeginTry())
    p.add(BeginFunction(0), nout=1, ninner=0)
    p.stmt(EndFunction())
    p.stmt({"if": EndIf, "for": EndFor, "try": EndTry}[opener]()) if opener != "try" else (
        p.stmt(BeginFinally()), p.stmt(EndTry()))
    out = lines(body(p.program()))
    assert any(l == "  var f1 = function() {" for l in out) and "  };" in out, out


def test_declaration_in_else_branch_is_an_expression_and_in_function_in_if_is_a_declaration():
    p = P()
    p.stmt(BeginIf(), (p.lit("true", keep=True),))
    p.stmt(BeginElse())
    p.add(BeginFunction(0), nout=1, ninner=0)
    p.stmt(EndFunction())
    p.stmt(EndIf())
    assert "  var f1 = function() {" in body(p.program())
    p = P()
    p.stmt(BeginIf(), (p.lit("true", keep=True),))
    p.add(BeginFunction(0, "expr"), nout=1, ninner=0)
    p.add(BeginFunction(0), nout=1, ninner=0)
    p.stmt(EndFunction())
    p.stmt(EndFunction())
    p.stmt(EndIf())
    assert lines(body(p.program()))[3:5] == ["    function f2() {", "    }"]


def test_nested_function_keeps_decl_style_and_closes_correctly():
    p = P()
    p.add(BeginFunction(0), nout=1, ninner=0)
    p.add(BeginFunction(0, "arrow"), nout=1, ninner=0)
    p.stmt(EndFunction())
    p.add(BeginFunction(0), nout=1, ninner=0)
    p.stmt(EndFunction())
    p.stmt(EndFunction())
    assert lines(body(p.program())) == ["function f1() {", "  var f2 = () => {", "  };", "  function f3() {", "  }", "}"]


def test_if_else_for_and_try_catch_finally_layout():
    p = P()
    c = p.lit("true", keep=True, hint="c")
    p.stmt(BeginIf(), (c,))
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None),))
    p.stmt(BeginElse())
    _, (i,) = p.add(BeginFor(3), nout=0, ninner=1)
    p.stmt(CallFunction(discard=True), (p.builtin("g", keep=None), i))
    p.stmt(EndFor())
    p.stmt(EndIf())
    p.stmt(BeginTry())
    p.stmt(CallFunction(discard=True), (p.builtin("h", keep=None),))
    _, (e,) = p.add(BeginCatch(), nout=0, ninner=1)
    p.stmt(CallFunction(discard=True), (p.builtin("h", keep=None), e))
    p.stmt(BeginFinally())
    p.stmt(CallFunction(discard=True), (p.builtin("k", keep=None),))
    p.stmt(EndTry())
    assert lines(body(p.program())) == [
        "var c1 = true;", "if (c1) {", "  f();", "} else {", "  for (var i1 = 0; i1 < 3; i1++) {", "    g(i1);",
        "  }", "}", "try {", "  h();", "} catch (ex1) {", "  h(ex1);", "} finally {", "  k();", "}"]


def test_try_without_catch_and_if_without_else():
    p = P()
    p.stmt(BeginTry())
    p.stmt(BeginFinally())
    p.stmt(EndTry())
    p.stmt(BeginIf(), (p.lit("false"),))
    p.stmt(EndIf())
    assert lines(body(p.program())) == ["try {", "} finally {", "}", "if (false) {", "}"]


def test_if_condition_is_not_parenthesised_beyond_its_own_parens():
    p = P()
    cond = p.add(Conditional(), (p.builtin("a", keep=None), p.builtin("b", keep=None), p.builtin("c", keep=None)))
    p.stmt(BeginIf(), (cond,))
    p.stmt(EndIf())
    assert lines(body(p.program())) == ["if (a ? b : c) {", "}"]


def test_expression_folds_into_if_condition_and_return():
    p = P()
    c = p.add(CallFunction(), (p.builtin("f", keep=None),))
    p.stmt(BeginIf(), (c,))
    p.add(BeginFunction(0), nout=1, ninner=0)
    p.stmt(Return(), (p.add(Binary("+"), (p.lit("1"), p.lit("2"))),))
    p.stmt(EndFunction())
    p.stmt(EndIf())
    assert lines(body(p.program())) == ["if (f()) {", "  var f1 = function() {", "    return 1 + 2;", "  };", "}"]


# ---------------------------------------------------------------------------- eval

def test_eval_placeholders_are_replaced_and_other_braces_are_left_alone():
    p = P()
    x = p.declare(p.lit("1"), hint="x")
    y = p.declare(p.lit("2"), hint="y")
    p.stmt(Eval("`${{0}}` + {1} + {{a: {0}}}"), (x, y))
    assert lines(body(p.program()))[-1] == "`${x1}` + y1 + {{a: x1}};"


def test_eval_placeholder_wraps_non_primary_inputs():
    p = P()
    s = p.add(Binary("+"), (p.lit("1"), p.lit("2")))
    p.stmt(Eval("print({0}, {1})"), (s, p.lit("5")))
    assert lines(body(p.program())) == ["print((1 + 2), 5);"]


def test_eval_expression_and_statement_forms():
    p = P()
    d = p.declare(p.lit("1"))
    p.add(Eval("{0} + 1", T.NUMBER), (d,), hint="r", keep=True)
    p.stmt(Eval("{0}.run()"), (d,))
    p.stmt(Eval("noop()"))
    assert lines(body(p.program()))[1:] == ["var r1 = d1 + 1;", "d1.run();", "noop();"]


def test_eval_with_out_none_is_never_folded_or_named():
    p = P()
    p.stmt(Eval("a()"))
    p.stmt(Eval("b()"))
    assert lines(body(p.program())) == ["a();", "b();"]


# ---------------------------------------------------------------------------- probes

def _vars_prog(k):
    p = P()
    for _ in range(k):
        p.declare(p.lit("1"))
    return p


def test_probe_lists_visible_variables_in_definition_order():
    p = _vars_prog(3)
    p.stmt(Probe())
    assert lines(lift(p.program(), comments=False).source)[-1] == '__probe("p1", d1, d2, d3);'


@pytest.mark.parametrize("width,expected", [(1, 'd3'), (2, 'd2, d3'), (3, 'd1, d2, d3'), (8, 'd1, d2, d3')])
def test_probe_width_keeps_the_most_recent_variables(width, expected):
    p = _vars_prog(3)
    p.stmt(Probe())
    assert lines(lift(p.program(), comments=False, probe_width=width).source)[-1] == f'__probe("p1", {expected});'


def test_probe_with_nothing_visible():
    p = P()
    p.stmt(Probe())
    assert lift(p.program(), comments=False).source == '__probe("p1");\n'


def test_consecutive_probes_are_deduplicated_and_numbering_counts_emitted_ones():
    p = _vars_prog(1)
    p.stmt(Probe())
    p.stmt(Probe())
    p.stmt(Probe())
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None),))
    p.stmt(Probe())
    p.stmt(Probe())
    assert lines(lift(p.program(), comments=False).source) == [
        "var d1 = 1;", '__probe("p1", d1);', "f();", '__probe("p2", d1);']


def test_probes_at_different_indents_are_not_deduplicated_against_each_other():
    p = P()
    p.stmt(BeginIf(), (p.lit("true", keep=True, hint="c"),))
    p.stmt(Probe())
    p.stmt(EndIf())
    p.stmt(Probe())
    out = lines(lift(p.program(), comments=False).source)
    assert out == ["var c1 = true;", "if (c1) {", '  __probe("p1", c1);', "}", '__probe("p2", c1);']


def test_inlined_variables_are_excluded_from_probes_but_kept_ones_are_included():
    p = P()
    a = p.lit("1")
    k = p.lit("2", keep=True, hint="k")
    p.declare(p.add(Binary("+"), (a, k)))
    p.stmt(Probe())
    assert lines(lift(p.program(), comments=False).source) == [
        "var k1 = 2;", "var d1 = 1 + k1;", '__probe("p1", k1, d1);']


def test_probe_sees_function_parameters_and_function_names_inside_functions():
    p = P()
    f, (a,) = p.add(BeginFunction(1), nout=1, ninner=1)
    p.stmt(Probe())
    p.stmt(EndFunction())
    p.stmt(Probe())
    assert lines(lift(p.program(), comments=False).source) == [
        "function f1(p1) {", '  __probe("p1", f1, p1);', "}", '__probe("p2", f1);']


def test_variables_out_of_scope_are_not_probed():
    p = P()
    p.stmt(BeginIf(), (p.lit("true", keep=True, hint="c"),))
    p.declare(p.lit("1"), kind="let")
    p.stmt(EndIf())
    p.stmt(Probe())
    assert lines(lift(p.program(), comments=False).source)[-1] == '__probe("p1", c1);'


def test_probes_off_emit_nothing_and_do_not_count():
    p = _vars_prog(2)
    p.stmt(Probe())
    p.stmt(Probe())
    r = lift(p.program(), probes=False, comments=False)
    assert "__probe" not in r.source and r.stats["probes"] == 0
    r = lift(p.program(), probes=True, comments=False)
    assert r.stats["probes"] == 1


def test_probes_off_does_not_leave_blank_lines_or_break_dedupe_counts():
    p = _vars_prog(1)
    p.stmt(Probe())
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None),))
    assert body(p.program()) == "var d1 = 1;\nf();\n"


# ---------------------------------------------------------------------------- comments, spans, stats

def _gen_prog():
    p = P()
    p.declare(p.lit("1"), gen="A", origin=1)                       # line: var d1 = 1;
    p.declare(p.lit("2"), gen="A", origin=1)
    p.stmt(BeginIf(), (p.lit("true", keep=True, hint="c", gen="B"),), gen="B")
    p.stmt(CallFunction(discard=True), (p.builtin("f", keep=None),), gen="C")     # nested: no comment
    p.stmt(EndIf(), gen="B")                                       # "}" line: no comment
    p.stmt(CallFunction(discard=True), (p.builtin("g", keep=None),), gen="C")
    return p.program()


def test_generator_comments_precede_the_first_top_level_line_of_a_new_generator():
    out = lines(lift(_gen_prog(), probes=False).source)
    assert out == ["// [A]", "var d1 = 1;", "var d2 = 2;",
                   "// [B]", "var c1 = true;", "if (c1) {", "  f();", "}",
                   "// [C]", "g();"]


def test_comments_off_and_no_gen_means_no_comments():
    assert "//" not in lift(_gen_prog(), probes=False, comments=False).source
    p = P()
    p.declare(p.lit("1"))
    assert "//" not in lift(p.program(), probes=False).source


def test_comment_not_repeated_for_same_generator_and_repeated_after_interruption():
    p = P()
    for g in ("A", "B", "A"):
        p.declare(p.lit("1"), gen=g)
    assert lines(lift(p.program(), probes=False).source) == [
        "// [A]", "var d1 = 1;", "// [B]", "var d2 = 1;", "// [A]", "var d3 = 1;"]


def test_probes_do_not_interrupt_generator_comments():
    p = P()
    p.declare(p.lit("1"), gen="A")
    p.stmt(Probe(), gen="A")
    p.declare(p.lit("1"), gen="A")
    out = lines(lift(p.program(), probes=True).source)
    assert out.count("// [A]") == 1


def test_header_comes_first_followed_by_a_blank_line():
    p = P()
    p.declare(p.lit("1"))
    out = lift(p.program(), probes=False, comments=False, header=("template: t", "generators: a, b")).source
    assert lines(out) == ["// template: t", "// generators: a, b", "", "var d1 = 1;"]


def test_spans_cover_statement_lines_of_each_generator_run():
    r = lift(_gen_prog(), probes=False)
    # lines are 0-based indices into the output
    assert r.spans == [{"gen": "A", "lines": [1, 2]}, {"gen": "B", "lines": [4, 5]}, {"gen": "C", "lines": [6, 6]},
                       {"gen": "B", "lines": [7, 7]}, {"gen": "C", "lines": [9, 9]}]


def test_spans_exact_for_simple_runs_and_header_offset():
    p = P()
    p.declare(p.lit("1"), gen="A")
    p.declare(p.lit("1"), gen="A")
    p.declare(p.lit("1"), gen="B")
    r = lift(p.program(), probes=False, comments=False, header=("h",))
    assert lines(r.source) == ["// h", "", "var d1 = 1;", "var d2 = 1;", "var d3 = 1;"]
    assert r.spans == [{"gen": "A", "lines": [2, 3]}, {"gen": "B", "lines": [4, 4]}]


def test_spans_with_comments_on_merge_across_comment_lines_of_the_same_gen():
    p = P()
    p.declare(p.lit("1"), gen="A")
    p.declare(p.lit("1"), gen="B")
    r = lift(p.program(), probes=False)
    assert lines(r.source) == ["// [A]", "var d1 = 1;", "// [B]", "var d2 = 1;"]
    assert r.spans == [{"gen": "A", "lines": [1, 1]}, {"gen": "B", "lines": [3, 3]}]


def test_instructions_without_gen_have_no_spans():
    p = P()
    p.declare(p.lit("1"))
    assert lift(p.program(), probes=False).spans == []


def test_stats():
    p = P()
    p.declare(p.lit("1"))
    p.stmt(BeginIf(), (p.lit("true", keep=True, hint="c"),))
    p.declare(p.lit("2"))
    p.stmt(EndIf())
    p.stmt(Probe())
    r = lift(p.program(), comments=False)
    assert r.stats["instructions"] == len(p.program())
    assert r.stats["statements"] == 4              # d1, c1, if, d2 ("}" and probes are not counted)
    assert r.stats["topLevelStatements"] == 3
    assert r.stats["maxDepth"] == 1
    assert r.stats["probes"] == 1
    assert r.stats["vars"] == 3
    assert r.stats["bytes"] == len(r.source)


# ---------------------------------------------------------------------------- generated programs

def _check_node(src: str):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.js"
        f.write_text(src)
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + "\n" + src


def _block_balance(src: str):
    opens = sum(1 for l in lines(src) if l.endswith("{") and not l.lstrip().startswith("//"))
    closes = sum(1 for l in lines(src) if l.lstrip().startswith("}") and not l.lstrip().startswith("//"))
    assert opens == closes, src


@settings(RELAXED, max_examples=30)
@given(ir_programs(GenOptions()))
def test_lifting_generated_programs_never_raises_and_nests_blocks(pm):
    prog, _ = pm
    sources = {}
    for inline in (True, False):
        for probes in (True, False):
            r = lift(prog, probes=probes, inline=inline)
            _block_balance(r.source)
            sources[(inline, probes)] = r
    # inlining only removes statements
    assert sources[(True, False)].stats["statements"] <= sources[(False, False)].stats["statements"]
    assert sources[(True, False)].stats["probes"] == 0


@pytest.mark.skipif(NODE is None, reason="node not installed")
@settings(RELAXED, max_examples=30)
@given(ir_programs(GenOptions()))
def test_lifted_generated_programs_parse_with_node_inline_and_not(pm):
    prog, _ = pm
    for inline in (True, False):
        _check_node(lift(prog, probes=False, inline=inline).source)
