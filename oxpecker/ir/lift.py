"""IR -> JavaScript.

Statement per instruction, with an inlining policy that decides the *shape* of the JS:

- a primitive literal temporary is spelled out at every use unless its instruction is marked
  `keep` -- so the same program can reach Rhino's optimizer with a local (`var n1 = 5; ... n1 * 2`)
  or with constants (`5 * 2`), and a mutation can flip it. Declared variables (`DeclareVar`,
  parameters, ...) are never inlined: they are the storage the optimizer specialises;
- an expression is folded into its single use when that use is the very next statement and either
  (a) it is the first operand that statement evaluates -- then nothing is reordered at all -- or
  (b) it is a unary/binary/conditional/builtin reference/pure Eval over primitive-typed inputs only,
  so no user code (valueOf/toString/getters) can run while it is evaluated. A property read is never
  folded into a callee position (`var t = o.f; t()` and `o.f()` differ in `this`).

`keep` on any instruction disables inlining/folding it (decision D16); it is drawn by the builder and
shrinks towards False, so shrunk reproducers are the most-inlined, most readable form unless the
bug needs the statement. With probes off (the plain reproducer), pure temporaries nothing uses are
dropped.

Parenthesisation comes from a precedence table, once, instead of per-generator string patches.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..gen.types import PREFIX, T
from .analysis import Analysis, analyse
from .ops import (BeginCatch, BeginElse, BeginFinally, BeginFor, BeginFunction, BeginIf, BeginTry, Binary,
                  CallFunction, CallMethod, CompoundAssign, Conditional, Construct, CreateArray, CreateObject,
                  DeclareVar, DeleteProperty, EndFor, EndFunction, EndIf, EndTry, Eval, GetElement, GetProperty, LoadBuiltin,
                  LoadLiteral, PRIMITIVE_TYPES, Probe, Program, Reassign, Return, SetElement, SetProperty, Throw,
                  Unary, Update)

# Higher binds tighter. 20: primary; 19: member/call/new; 16: unary; 15: **; ... 3: conditional.
_BINARY_PREC = {
    "**": 15, "*": 14, "/": 14, "%": 14, "+": 13, "-": 13, "<<": 12, ">>": 12, ">>>": 12,
    "<": 11, "<=": 11, ">": 11, ">=": 11, "in": 11, "instanceof": 11,
    "==": 10, "!=": 10, "===": 10, "!==": 10, "&": 9, "^": 8, "|": 7, "&&": 6, "||": 5, "??": 4,
}
PRIMARY, MEMBER, UNARY, CONDITIONAL, ASSIGNMENT = 20, 19, 16, 3, 2
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_NUMBER_START = re.compile(r"^\d")


_FOLDABLE = (LoadLiteral, Unary, Binary, Conditional, LoadBuiltin, Eval, GetProperty, GetElement, CallFunction,
             CallMethod, Construct, Update, CreateArray, CreateObject, DeleteProperty)
_PURE = (LoadLiteral, LoadBuiltin, Unary, Binary, Conditional, CreateArray, CreateObject)   # given primitive inputs


def _first_evaluated(ins) -> int | None:
    """The input slot a statement evaluates first, if a folded expression there cannot be
    reordered against anything: the right-hand side of `t = v` (resolving `t` has no effects),
    otherwise slot 0 -- except compound assignment and update, which read their target first,
    and Eval, whose order is opaque."""
    op = ins.op
    if isinstance(op, Reassign):
        return 1
    if isinstance(op, (CompoundAssign, Update, Eval, Probe)) or not ins.inputs:
        return None
    return 0


@dataclass
class Expr:
    text: str
    prec: int
    op: str | None = None        # the binary operator at the top, for ?? / && mixing rules


def _spans_whole(t: str) -> bool:
    """Does the bracket or quote opened at t[0] close at t[-1]? (`"a" + "b"` starts and ends with a
    quote but is not one string literal.)"""
    close = {"[": "]", "{": "}", "(": ")", '"': '"', "'": "'", "`": "`"}
    depth, quote, k = 0, None, 0
    while k < len(t):
        c = t[k]
        if quote:
            if c == "\\":
                k += 1
            elif c == quote:
                quote = None
                if depth == 0:
                    return k == len(t) - 1
        elif c in "\"'`":
            quote = c
        elif c in "[{(":
            depth += 1
        elif c in "]})":
            depth -= 1
            if depth == 0:
                return k == len(t) - 1
        k += 1
    return False


def literal_prec(text: str) -> int:
    t = text.strip()
    if t[:1] in "-+":
        return UNARY
    if t.startswith("/"):                       # a regexp literal
        return PRIMARY
    if t[:1] in "{[\"'`" and _spans_whole(t):
        return PRIMARY
    if re.fullmatch(r"[\w$.]+", t):            # numbers, identifiers, true/null/undefined, 10n, 0x..
        return PRIMARY
    if re.fullmatch(r"[\w$.]+\(.*\)", t):       # Symbol("s"), Symbol.for("k")
        return MEMBER
    if re.fullmatch(r"[\d.]+\s*/\s*[\d.]+", t):  # 1/3
        return 14
    return CONDITIONAL - 1                      # unknown shape: always parenthesise


def _wrap(e: Expr, need: int) -> str:
    return e.text if e.prec >= need else f"({e.text})"


def _member_obj(e: Expr) -> str:
    """Receiver of `.name` / `[k]` / a call: `1.toString()` is a syntax error, `(1).toString()` is not."""
    if e.prec < MEMBER or _NUMBER_START.match(e.text):
        return f"({e.text})"
    return e.text


def _new_callee(e: Expr) -> str:
    """`new f()()` is `(new f())()`: a callee containing a call must be parenthesised."""
    return e.text if e.prec == PRIMARY or re.fullmatch(r"[\w$.]+", e.text) else f"({e.text})"


def _prop(obj: str, name: str) -> str:
    return f"{obj}.{name}" if _IDENT.match(name) else f"{obj}[{json.dumps(name)}]"


def _key(name: str) -> str:
    return name if _IDENT.match(name) else json.dumps(name)


@dataclass
class Lifted:
    source: str
    spans: list[dict] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


class _Lifter:
    def __init__(self, prog: Program, an: Analysis, *, probes: bool, comments: bool, probe_width: int,
                 header: tuple[str, ...], inline: bool, dce: bool):
        self.prog, self.an = prog, an
        self.probes, self.comments, self.probe_width, self.inline = probes, comments, probe_width, inline
        self.dead = self._dead() if dce else set()
        self.header = header
        self.names: dict[int, str] = {}
        self.counters: dict[str, int] = {}
        self.inlined: dict[int, Expr] = {}       # variable -> expression text it is replaced by
        self.lines: list[tuple[int, str, str | None, bool]] = []   # (indent, text, gen, is_probe)
        self.probe_count = 0
        self.fn_styles: list[str] = []           # open functions' lifted style, to close them right

    # ------------------------------------------------------------ naming / inlining
    def _dead(self) -> set[int]:
        """Instructions computing pure temporaries that nothing (live) uses -- only meaningful
        without probes, which observe every visible variable."""
        prog, an = self.prog, self.an
        uses = dict(an.use_count)
        dead: set[int] = set()
        for i in range(len(prog) - 1, -1, -1):
            ins = prog[i]
            if (len(ins.outputs) == 1 and isinstance(ins.op, _PURE) and uses.get(ins.outputs[0], 0) == 0
                    and all(an.types.get(x, T.UNKNOWN) in PRIMITIVE_TYPES for x in ins.inputs)):
                dead.add(i)
                for x in ins.inputs:
                    uses[x] -= 1
        return dead

    def name(self, v: int) -> str:
        if v not in self.names:
            ins = self.prog[self.an.def_index[v]]
            if ins.hint and v in ins.outputs:
                prefix = ins.hint
            elif isinstance(ins.op, BeginFunction) and v in ins.inner:
                prefix = "p"
            elif isinstance(ins.op, BeginFor):
                prefix = "i"
            elif isinstance(ins.op, BeginCatch):
                prefix = "ex"
            else:
                prefix = PREFIX.get(self.an.types.get(v, T.UNKNOWN), "x")
            self.counters[prefix] = self.counters.get(prefix, 0) + 1
            self.names[v] = f"{prefix}{self.counters[prefix]}"
        return self.names[v]

    def expr(self, v: int) -> Expr:
        return self.inlined.get(v) or Expr(self.name(v), PRIMARY)

    def _inline_literal(self, i: int) -> bool:
        ins = self.prog[i]
        op = ins.op
        # A literal nothing uses stays a variable: it was generated to be observed (probes) and used.
        return (self.inline and isinstance(op, LoadLiteral) and not ins.keep and op.type in PRIMITIVE_TYPES
                and ins.outputs[0] not in self.an.declared and self.an.use_count.get(ins.outputs[0], 0) > 0)

    def _inline_expr(self, i: int) -> bool:
        """Fold an expression into its single use if that use is the next emitted statement and
        moving it cannot reorder anything observable (module docstring, decision D11)."""
        if not self.inline:
            return False
        ins = self.prog[i]
        op = ins.op
        if ins.keep or not isinstance(op, _FOLDABLE) or (isinstance(op, Eval) and op.out is None) or not ins.outputs:
            return False
        (v,) = ins.outputs
        if v in self.an.reassigned or self.an.use_count.get(v, 0) != 1:
            return False
        j = i + 1
        while j < len(self.prog) and (self._inline_literal(j) or j in self.dead):
            j += 1
        if j >= len(self.prog) or self.an.users[v] != [j]:
            return False
        user = self.prog[j]
        slot = user.inputs.index(v)
        if isinstance(op, (GetProperty, GetElement)) and isinstance(user.op, CallFunction) and slot == 0:
            return False
        if slot == _first_evaluated(user):
            return True
        pure = isinstance(op, (Unary, Binary, Conditional, LoadBuiltin)) or (isinstance(op, Eval) and op.pure)
        return pure and all(self.an.type_at(i, x) in PRIMITIVE_TYPES for x in ins.inputs)

    # ------------------------------------------------------------ expressions
    def _value(self, ins) -> Expr:
        """The expression an output-producing instruction computes."""
        op, xs = ins.op, [self.expr(v) for v in ins.inputs]
        if isinstance(op, LoadLiteral):
            return Expr(op.text, literal_prec(op.text))
        if isinstance(op, LoadBuiltin):
            return Expr(op.name, PRIMARY if _IDENT.match(op.name) else MEMBER)
        if isinstance(op, CreateArray):
            elems, it = [], iter(xs)
            for k in range(len(xs) + len(op.holes)):
                elems.append("" if k in op.holes else _wrap(next(it), ASSIGNMENT))
            return Expr("[" + ", ".join(elems) + ("," if elems and elems[-1] == "" else "") + "]", PRIMARY)
        if isinstance(op, CreateObject):
            body = ", ".join(f"{_key(k)}: {_wrap(x, ASSIGNMENT)}" for k, x in zip(op.keys, xs))
            return Expr("{" + body + "}", PRIMARY)
        if isinstance(op, Unary):
            x = _wrap(xs[0], UNARY)
            if op.op in ("typeof", "void"):
                return Expr(f"{op.op} {x}", UNARY)
            if op.op in "-+" and x[:1] == op.op:   # `--1` is a decrement; `~~x`, `!!x` are fine
                x = f"({x})"
            return Expr(f"{op.op}{x}", UNARY)
        if isinstance(op, Binary):
            p = _BINARY_PREC[op.op]
            a, b = xs
            mix = {"??"} if op.op in ("&&", "||") else {"&&", "||"} if op.op == "??" else set()
            la = a.prec >= (p + 1 if op.op == "**" else p) and a.op not in mix and not (op.op == "**" and a.prec == UNARY)
            rb = b.prec >= (p if op.op == "**" else p + 1) and b.op not in mix
            left = a.text if la else f"({a.text})"
            right = b.text if rb else f"({b.text})"
            return Expr(f"{left} {op.op} {right}", p, op.op)
        if isinstance(op, Conditional):
            c, a, b = xs
            return Expr(f"{_wrap(c, CONDITIONAL + 1)} ? {_wrap(a, ASSIGNMENT)} : {_wrap(b, ASSIGNMENT)}",
                        CONDITIONAL)
        if isinstance(op, GetProperty):
            return Expr(_prop(_member_obj(xs[0]), op.name), MEMBER)
        if isinstance(op, GetElement):
            return Expr(f"{_member_obj(xs[0])}[{_wrap(xs[1], ASSIGNMENT)}]", MEMBER)
        if isinstance(op, DeleteProperty):
            return Expr(f"delete {_prop(_member_obj(xs[0]), op.name)}", UNARY)
        if isinstance(op, CallFunction):
            return Expr(f"{_member_obj(xs[0])}({self._args(xs[1:])})", MEMBER)
        if isinstance(op, CallMethod):
            return Expr(f"{_prop(_member_obj(xs[0]), op.name)}({self._args(xs[1:])})", MEMBER)
        if isinstance(op, Construct):
            return Expr(f"new {_new_callee(xs[0])}({self._args(xs[1:])})", MEMBER)
        if isinstance(op, Update):
            t = self.name(ins.inputs[0])
            return Expr(f"{op.op}{t}" if op.prefix else f"{t}{op.op}", UNARY)
        if isinstance(op, Eval):
            return Expr(self._fill(op.code, xs), CONDITIONAL - 1)
        raise AssertionError(op)

    @staticmethod
    def _args(xs: list[Expr]) -> str:
        return ", ".join(_wrap(x, ASSIGNMENT) for x in xs)

    @staticmethod
    def _fill(code: str, xs: list[Expr]) -> str:
        return re.sub(r"\{(\d+)\}", lambda m: _wrap(xs[int(m.group(1))], PRIMARY), code)

    # ------------------------------------------------------------ statements
    def emit(self, indent: int, text: str, gen: str | None, probe: bool = False) -> None:
        self.lines.append((indent, text, gen, probe))

    def run(self) -> None:
        prog, an = self.prog, self.an
        indent = 0
        for i, ins in enumerate(prog):
            op, gen = ins.op, ins.gen
            if i in self.dead:
                continue
            if ins.outputs and isinstance(op, _FOLDABLE):
                if self._inline_literal(i) or self._inline_expr(i):
                    self.inlined[ins.outputs[0]] = self._value(ins)
                    continue
            xs = [self.expr(v) for v in ins.inputs]
            if isinstance(op, BeginFunction):
                f = self.name(ins.outputs[0])
                ps = ", ".join(self.name(p) for p in ins.inner)
                style = op.style if op.style != "decl" or an.frame_at[i] in ("top", "function") else "expr"
                head = {"decl": f"function {f}({ps}) {{", "expr": f"var {f} = function({ps}) {{",
                        "arrow": f"var {f} = ({ps}) => {{"}[style]
                self.emit(indent, head, gen)
                indent += 1
                if op.strict:
                    self.emit(indent, '"use strict";', gen)
                self.fn_styles.append(style)
            elif isinstance(op, EndFunction):
                indent -= 1
                self.emit(indent, "}" if self.fn_styles.pop() == "decl" else "};", gen)
            elif isinstance(op, BeginIf):
                self.emit(indent, f"if ({xs[0].text}) {{", gen)
                indent += 1
            elif isinstance(op, BeginElse):
                self.emit(indent - 1, "} else {", gen)
            elif isinstance(op, BeginFor):
                c = self.name(ins.inner[0])
                self.emit(indent, f"for (var {c} = 0; {c} < {op.count}; {c}++) {{", gen)
                indent += 1
            elif isinstance(op, BeginTry):
                self.emit(indent, "try {", gen)
                indent += 1
            elif isinstance(op, BeginCatch):
                self.emit(indent - 1, f"}} catch ({self.name(ins.inner[0])}) {{", gen)
            elif isinstance(op, BeginFinally):
                self.emit(indent - 1, "} finally {", gen)
            elif isinstance(op, (EndIf, EndFor, EndTry)):
                indent -= 1
                self.emit(indent, "}", gen)
            elif isinstance(op, Return):
                self.emit(indent, f"return {xs[0].text};" if xs else "return;", gen)
            elif isinstance(op, Throw):
                self.emit(indent, f"throw {xs[0].text};", gen)
            elif isinstance(op, SetProperty):
                self.emit(indent, f"{_prop(_member_obj(xs[0]), op.name)} = {_wrap(xs[1], ASSIGNMENT)};", gen)
            elif isinstance(op, SetElement):
                self.emit(indent, f"{_member_obj(xs[0])}[{_wrap(xs[1], ASSIGNMENT)}] = {_wrap(xs[2], ASSIGNMENT)};", gen)
            elif isinstance(op, DeclareVar):
                self.emit(indent, f"{op.kind} {self.name(ins.outputs[0])} = {_wrap(xs[0], ASSIGNMENT)};", gen)
            elif isinstance(op, Reassign):
                self.emit(indent, f"{self.name(ins.inputs[0])} = {_wrap(xs[1], ASSIGNMENT)};", gen)
            elif isinstance(op, CompoundAssign):
                self.emit(indent, f"{self.name(ins.inputs[0])} {op.op}= {_wrap(xs[1], ASSIGNMENT)};", gen)
            elif isinstance(op, Probe):
                self._probe(i, indent, gen)
            elif isinstance(op, Eval) and op.out is None:
                self.emit(indent, self._fill(op.code, xs) + ";", gen)
            elif ins.outputs:
                self.emit(indent, f"var {self.name(ins.outputs[0])} = {self._value(ins).text};", gen)
            else:
                self.emit(indent, self._value(ins).text + ";", gen)

    def _probe(self, i: int, indent: int, gen: str | None) -> None:
        if not self.probes:
            return
        if self.lines and self.lines[-1][3] and self.lines[-1][0] == indent:
            return                                    # no two probes in a row
        vis = [v for v in self.an.visible_at[i] if v not in self.inlined][-self.probe_width:]
        self.probe_count += 1
        args = ", ".join([f'"p{self.probe_count}"'] + [self.name(v) for v in vis])
        self.emit(indent, f"__probe({args});", gen, probe=True)

    # ------------------------------------------------------------ output
    def render(self) -> Lifted:
        out = [f"// {h}" for h in self.header]
        if self.header:
            out.append("")
        spans: list[dict] = []
        last_gen = None
        for indent, text, gen, probe in self.lines:
            starts = (gen is not None and gen != last_gen and indent == 0 and not probe
                      and not text.startswith("}"))
            if self.comments and starts:
                out.append(f"// [{gen}]")
            if not probe:
                last_gen = gen
                lineno = len(out)
                if gen is not None:
                    if spans and spans[-1]["gen"] == gen and spans[-1]["lines"][1] >= lineno - 2:
                        spans[-1]["lines"][1] = lineno
                    else:
                        spans.append({"gen": gen, "lines": [lineno, lineno]})
            out.append("  " * indent + text)
        source = "\n".join(out) + "\n"
        body = [(ind, t) for ind, t, _, p in self.lines if not p]
        stats = {
            "statements": sum(1 for _, t in body if not t.startswith("}")),
            "topLevelStatements": sum(1 for ind, t in body if ind == 0 and not t.startswith("}")),
            "vars": len(self.names),
            "maxDepth": max((ind for ind, _ in body), default=0),
            "probes": self.probe_count,
            "instructions": len(self.prog),
            "bytes": len(source),
        }
        return Lifted(source, spans, stats)


def lift(prog: Program, *, probes: bool = True, comments: bool = True, probe_width: int = 8,
         header: tuple[str, ...] = (), inline: bool = True, dce: bool | None = None,
         an: Analysis | None = None) -> Lifted:
    """`dce` defaults to "no probes": dead pure temporaries are dropped from plain reproducers."""
    lifter = _Lifter(prog, an or analyse(prog), probes=probes, comments=comments, probe_width=probe_width,
                     header=header, inline=inline, dce=(not probes) if dce is None else dce)
    lifter.run()
    return lifter.render()
