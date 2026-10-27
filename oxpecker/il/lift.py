# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/Lifting/JavaScriptLifter.swift (the per-operation rules, 341-2171, and the
# JavaScriptWriter expression-inlining helper, 2582-3062), Expression.swift, JSExpressions.swift
# and ScriptWriter.swift at commit be2811d00282320381fc8007d64873745772011d, with the
# oxpecker changes of docs/design-generation-layer.md §8 (per-instruction `lift` choice, declared
# variables never inlined, Rhino spellings, probes, provenance comments, escaped code strings).
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""The lifter: the only place JavaScript text is produced. `lift(code, options)` is a pure
function of the instructions (including their `lift` choices) and the options."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from . import ops as O
from .analysis import Analysis, Storage, analyse
from .ops import Lift
from .program import Instruction, Var, block_end
from .types import Key, KeyKind, Kind, Type

# ------------------------------------------------------------------------------- expressions
class Assoc(Enum):
    NONE = 0
    LEFT = 1
    RIGHT = 2


class Char(Enum):
    PURE = 0        # may be inlined at every use, across blocks
    EFFECTFUL = 1   # single use, same block, nothing effectful in between


@dataclass(frozen=True)
class ExprType:
    precedence: int
    assoc: Assoc
    char: Char


IDENTIFIER = ExprType(20, Assoc.NONE, Char.PURE)
LITERAL = ExprType(20, Assoc.NONE, Char.PURE)
KEYWORD = ExprType(20, Assoc.NONE, Char.PURE)
REGEXP_LITERAL = ExprType(20, Assoc.NONE, Char.EFFECTFUL)      # an object: never duplicated
CALL = ExprType(19, Assoc.LEFT, Char.EFFECTFUL)
MEMBER = ExprType(19, Assoc.LEFT, Char.EFFECTFUL)
NEW = ExprType(19, Assoc.NONE, Char.EFFECTFUL)
NUMBER_LITERAL = ExprType(17, Assoc.NONE, Char.PURE)
STRING_LITERAL = ExprType(17, Assoc.NONE, Char.PURE)
TEMPLATE_LITERAL = ExprType(17, Assoc.NONE, Char.EFFECTFUL)
OBJECT_LITERAL = ExprType(17, Assoc.NONE, Char.EFFECTFUL)
ARRAY_LITERAL = ExprType(17, Assoc.NONE, Char.EFFECTFUL)
POSTFIX = ExprType(16, Assoc.NONE, Char.EFFECTFUL)
UNARY = ExprType(15, Assoc.RIGHT, Char.EFFECTFUL)
BINARY = ExprType(14, Assoc.NONE, Char.EFFECTFUL)
TERNARY = ExprType(4, Assoc.NONE, Char.EFFECTFUL)
ASSIGNMENT = ExprType(3, Assoc.RIGHT, Char.EFFECTFUL)
YIELD = ExprType(2, Assoc.RIGHT, Char.EFFECTFUL)
SPREAD = ExprType(2, Assoc.NONE, Char.EFFECTFUL)
COMMA = ExprType(1, Assoc.LEFT, Char.EFFECTFUL)


@dataclass(frozen=True)
class Expr:
    type: ExprType
    text: str
    parts: int = 0

    @property
    def effectful(self) -> bool:
        return self.type.char is Char.EFFECTFUL

    def needs_brackets(self, outer: Expr, is_lhs: bool) -> bool:        # Expression.swift:69-80
        if self.type.precedence == outer.type.precedence:
            if self.type.assoc != outer.type.assoc:
                return True
            if self.type.assoc is Assoc.NONE:
                return True
            if self.type.assoc is Assoc.LEFT:
                return not is_lhs
            return is_lhs
        return self.type.precedence < outer.type.precedence

    def __add__(self, other: Expr | str) -> Expr:
        if isinstance(other, str):
            return Expr(self.type, self.text + other, self.parts)
        if other.needs_brackets(self, is_lhs=self.parts == 0):
            return Expr(self.type, self.text + "(" + other.text + ")", self.parts + 1)
        return Expr(self.type, self.text + other.text, self.parts + 1)

    def __str__(self) -> str:
        return self.text


def ident(name: str) -> Expr:
    return Expr(IDENTIFIER, name)


# ------------------------------------------------------------------------------- spellings
UNARY_TOKENS = {
    O.UnaryOperator.PRE_INC: "++", O.UnaryOperator.PRE_DEC: "--", O.UnaryOperator.POST_INC: "++",
    O.UnaryOperator.POST_DEC: "--", O.UnaryOperator.LOGICAL_NOT: "!", O.UnaryOperator.BITWISE_NOT: "~",
    O.UnaryOperator.PLUS: "+", O.UnaryOperator.MINUS: "-",
}
BINARY_TOKENS = {
    O.BinaryOperator.ADD: "+", O.BinaryOperator.SUB: "-", O.BinaryOperator.MUL: "*", O.BinaryOperator.DIV: "/",
    O.BinaryOperator.MOD: "%", O.BinaryOperator.BIT_AND: "&", O.BinaryOperator.BIT_OR: "|",
    O.BinaryOperator.LOGIC_AND: "&&", O.BinaryOperator.LOGIC_OR: "||", O.BinaryOperator.XOR: "^",
    O.BinaryOperator.LSHIFT: "<<", O.BinaryOperator.RSHIFT: ">>", O.BinaryOperator.EXP: "**",
    O.BinaryOperator.U_RSHIFT: ">>>", O.BinaryOperator.NULL_COALESCE: "??",
}
COMPARATOR_TOKENS = {
    O.Comparator.EQUAL: "==", O.Comparator.STRICT_EQUAL: "===", O.Comparator.NOT_EQUAL: "!=",
    O.Comparator.STRICT_NOT_EQUAL: "!==", O.Comparator.LESS: "<", O.Comparator.LESS_EQUAL: "<=",
    O.Comparator.GREATER: ">", O.Comparator.GREATER_EQUAL: ">=",
}
REGEXP_FLAG_CHARS = (                     # JsOperations.swift:352-361: fixed order d g i m s u y
    (O.RegExpFlag.GLOBAL, "g"), (O.RegExpFlag.IGNORE_CASE, "i"),
    (O.RegExpFlag.MULTILINE, "m"), (O.RegExpFlag.DOT_ALL, "s"), (O.RegExpFlag.UNICODE, "u"), (O.RegExpFlag.STICKY, "y"),
)
DECLARATION_TOKENS = {O.DeclarationKind.VAR: "var", O.DeclarationKind.LET: "let", O.DeclarationKind.CONST: "const"}
LIFT_TOKENS = {Lift.VAR: "var", Lift.LET: "let", Lift.CONST: "const"}


def spell_integer(value: int, radix: O.Radix) -> str:
    if radix is O.Radix.HEX:
        return hex(value)
    if radix is O.Radix.OCT:
        return "0o" + oct(value)[2:]
    if radix is O.Radix.BIN:
        return bin(value)
    return str(value)


def spell_float(value: float, style: O.FloatStyle = O.FloatStyle.PLAIN) -> str:
    if style is O.FloatStyle.EXPONENT:
        mantissa, _, exp = f"{value:.17e}".partition("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return f"{mantissa}e{int(exp)}"
    text = repr(value)                     # shortest round-trip; '1e+21', '5e-324', '1.0' are all JavaScript
    return text


def spell_string(value: str) -> str:
    """A double-quoted JavaScript string literal with every character the parser could choke on
    escaped (FuzzIL escapes only the quote, JavaScriptLifter.swift:366)."""
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or 0xD800 <= code <= 0xDFFF or code in (0x2028, 0x2029):
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def spell_template_part(value: str) -> str:
    out = []
    for i, ch in enumerate(value):
        if ch == "`" or ch == "\\":
            out.append("\\" + ch)
        elif ch == "$" and value[i + 1: i + 2] == "{":
            out.append("\\$")
        elif 0xD800 <= ord(ch) <= 0xDFFF:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def spell_key(key: Key) -> str:
    """A property-definition key: bare identifier, bare index, or a quoted string."""
    if key.is_identifier or key.kind is KeyKind.INDEX:
        return key.name
    return spell_string(key.name)


def member(obj: Expr, key: Key, optional: bool) -> Expr:            # liftMemberAccess, JavaScriptLifter.swift:2264-2270
    if key.is_identifier:
        return Expr(MEMBER, "") + obj + ("?." if optional else ".") + key.name
    inner = key.name if key.kind is KeyKind.INDEX else spell_string(key.name)
    return Expr(MEMBER, "") + obj + ("?.[" if optional else "[") + inner + "]"


def element(obj: Expr, index: int, optional: bool) -> Expr:
    idx = str(index) if index >= 0 else f"-{-index}"
    return Expr(MEMBER, "") + obj + ("?.[" if optional else "[") + idx + "]"


def computed(obj: Expr, key: Expr, optional: bool) -> Expr:
    return Expr(MEMBER, "") + obj + ("?.[" if optional else "[") + key.text + "]"


def regexp_flags(flags: O.RegExpFlag) -> str:
    return "".join(ch for flag, ch in REGEXP_FLAG_CHARS if flag in flags)


# ------------------------------------------------------------------------------- writer
class ScriptWriter:                                                  # ScriptWriter.swift
    def __init__(self, indent: int = 0):
        self.lines: list[str] = []
        self.level = indent

    def emit(self, line: str) -> None:
        self.lines.append("    " * self.level + line)

    def emit_lines(self, lines: Sequence[str]) -> None:
        for ln in lines:
            self.emit(ln)

    def indent(self) -> None:
        self.level += 1

    def dedent(self) -> None:
        self.level -= 1

    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")


class Writer:
    """JavaScriptWriter (JavaScriptLifter.swift:2582-3062): expression inlining with pending
    effectful expressions flushed at every statement and block boundary."""

    def __init__(self, an: Analysis, comments: bool):
        self.an = an
        self.comments = comments
        self.exprs: dict[Var, Expr] = {}
        self.pending: list[Var] = []
        self.names: dict[Var, str] = {}
        self.declared_js: set[Var] = set()             # variables that exist as JavaScript bindings
        self.out = ScriptWriter()
        self.buffers: list[tuple[ScriptWriter, list[Var]]] = []
        self.gen = ""
        self._last_gen = ""
        self.last_was_probe = False

    # -- output
    @property
    def w(self) -> ScriptWriter:
        return self.buffers[-1][0] if self.buffers else self.out

    def emit(self, line: str) -> None:
        self.flush()
        self._comment_for_statement()
        self.w.emit(line)
        self.last_was_probe = False

    def emit_raw(self, line: str) -> None:
        """A line that is not a statement of the program (a brace, a case label): no provenance comment."""
        self.flush()
        self.w.emit(line)
        self.last_was_probe = False

    def _comment_for_statement(self) -> None:
        if self.comments and not self.buffers and self.w.level == 0 and self.gen and self.gen != self._last_gen:
            self.w.emit(f"// [{self.gen}]")
            self._last_gen = self.gen

    def enter_block(self) -> None:
        self.flush()
        self.w.indent()

    def leave_block(self) -> None:
        self.flush()
        self.w.dedent()

    def push_buffer(self, indent: int) -> None:
        self.flush()
        self.buffers.append((ScriptWriter(indent), self.pending))
        self.pending = []

    def pop_buffer(self) -> list[str]:
        self.flush()
        buf, pending = self.buffers.pop()
        assert not self.pending
        self.pending = pending
        return buf.lines

    # -- names
    def name_of(self, v: Var) -> str:
        return self.names[v]

    def declare(self, v: Var, name: str | None = None) -> str:
        if v not in self.names:
            self.names[v] = name or self._default_name(v)
        self.declared_js.add(v)
        self.exprs[v] = ident(self.names[v])
        return self.names[v]

    def _default_name(self, v: Var) -> str:
        s = self.an.storage.get(v, Storage.TEMP)
        if s is Storage.PARAM:
            return f"p{v}"
        if s is Storage.CATCH:
            return f"e{v}"
        if s in (Storage.LOOPVAR, Storage.COUNTER, Storage.FOREACH):
            return f"i{v}"
        if s is Storage.FUNCTION:
            return f"f{v}"
        if s is Storage.LABEL:
            return f"L{v}"
        t = self.an.types.get(v, None)
        prefix = "v"
        if t is not None and not t.is_nothing:
            k = t.possible
            if k == Kind.FUNCTION:
                prefix = "f"
            elif k == Kind.STRING:
                prefix = "s"
            elif k == Kind.BOOLEAN:
                prefix = "b"
            elif k == Kind.BIGINT:
                prefix = "big"
            elif not (k & ~(Kind.INTEGER | Kind.FLOAT)):
                prefix = "n"
            elif k == Kind.OBJECT:
                prefix = "arr" if t.group == "Array" else "o"
        return f"{prefix}{v}"

    # -- expressions
    def should_inline(self, e: Expr, v: Var) -> bool:                 # shouldTryInlining, 3039-3061
        if self.an.num_assignments(v) > 1:
            return False
        if not e.effectful:
            return True
        return self.an.num_uses(v) <= 1

    def assign(self, e: Expr, v: Var, lift: Lift | None, *, allow_inline: bool = True) -> None:
        """Bind the value of a value-producing instruction (2649-2676, plus the `lift` choice)."""
        lift = lift or Lift.INLINE
        if v in self.exprs and self.exprs[v].type is IDENTIFIER and v in self.declared_js:
            self.emit(f"{self.exprs[v]} = {e};")
            return
        if allow_inline and lift is Lift.INLINE and self.should_inline(e, v):
            self.exprs[v] = e
            if e.effectful:
                self.pending.append(v)
            return
        if lift is Lift.INLINE and self.an.num_uses(v) == 0 and e.type is not OBJECT_LITERAL:
            self.emit(f"{e};")
            return
        keyword = LIFT_TOKENS.get(lift, "var")
        name = self.declare(v)
        self.emit(f"{keyword} {name} = {e};")

    def declare_var(self, v: Var, e: Expr, keyword: str) -> None:
        """A declared variable (Declare, named variables): always a statement, never inlined."""
        name = self.declare(v)
        self.emit(f"{keyword} {name} = {e};")

    def retrieve(self, vs: Sequence[Var]) -> list[Expr]:             # 2719-2794
        queried = [v for v in dict.fromkeys(vs) if v in self.pending]
        match = 0
        while match < len(queried) and match < len(self.pending) and self.pending[-1 - match] == queried[-1 - match]:
            match += 1
        if match == len(queried):
            # the queried pending expressions are exactly the newest ones, in order: inline them,
            # older pending expressions stay pending
            self.pending = self.pending[: len(self.pending) - match]
        else:
            # out of order: everything pending becomes a statement first
            for v in self.pending:
                self._emit_pending(v)
            self.pending = []
        out = []
        for v in vs:
            e = self.exprs.get(v)
            if e is None:
                raise LiftError(f"no expression for v{v}")
            out.append(e)
            if e.effectful and e.type is not IDENTIFIER:
                del self.exprs[v]          # an effectful expression is used exactly once
        return out

    def flush(self) -> None:                                           # emitPendingExpressions, 2933
        pending, self.pending = self.pending, []
        for v in pending:
            self._emit_pending(v)

    def _emit_pending(self, v: Var) -> None:                          # emitPendingExpression, 2989-3018
        e = self.exprs.pop(v)
        self._comment_for_statement()
        if self.an.num_uses(v) > 0:
            name = self.declare(v)
            self.w.emit(f"var {name} = {e};")
        elif e.type is OBJECT_LITERAL:
            name = self.declare(v)
            self.w.emit(f"var {name} = {e};")
        else:
            self.w.emit(f"{e};")
        self.last_was_probe = False

    def as_identifier(self, e: Expr, v: Var) -> Expr:                 # ensureIsIdentifier, 2799-2819
        if e.type is IDENTIFIER or e.type is KEYWORD:
            return e
        name = self.declare(v, f"t{v}")
        self.emit(f"var {name} = {e};")
        return ident(name)


class LiftError(Exception):
    pass


@dataclass(frozen=True)
class LiftOptions:
    probes: bool = True
    comments: bool = True
    strict: bool = False
    probe_width: int = 6


# ------------------------------------------------------------------------------- the lifter
def lift(code: Sequence[Instruction], options: LiftOptions = LiftOptions()) -> str:
    code = _code(code)
    an = analyse(code, strict=options.strict)
    w = Writer(an, options.comments)
    if options.strict:
        w.out.emit('"use strict";')
    _lift_range(code, 0, len(code), an, w, options, depth0=True)
    w.flush()
    return w.out.text()


def js(code, *, probes: bool = False, comments: bool = False, strict: bool = False, probe_width: int = 6) -> str:
    """`lift` with keyword options, for the shell: `print(js(b))`, `print(js(gen.code, probes=True))`.
    Accepts instructions, a Program, a Builder, or anything with a `.code`."""
    return lift(_code(code), LiftOptions(probes=probes, comments=comments, strict=strict, probe_width=probe_width))


def _code(x) -> tuple[Instruction, ...]:
    code = getattr(x, "code", x)
    return tuple(code)


def _label_used(an: Analysis, label: Var) -> bool:
    return an.num_uses(label) > 0


def _params_text(w: Writer, op, inner: Sequence[Var], defaults: Sequence[Expr]) -> str:
    """Parameter list: names, destructuring patterns, defaults and the rest parameter."""
    params: O.Parameters = op.parameters
    pieces = []
    inner = list(inner)
    patterns = dict(params.destructuring)
    defaults = list(defaults)
    for k in range(params.count):
        if k in patterns:
            text, inner = _pattern_text(w, patterns[k], inner, [], declare=True)
        else:
            v = inner.pop(0)
            text = w.declare(v)
        if k in params.default_indices:
            text += " = " + defaults.pop(0).text
        if params.has_rest and k == params.count - 1:
            text = "..." + text
        pieces.append(text)
    return ", ".join(pieces)


def _pattern_text(w: Writer, pattern: O.Pattern, bindings: list[Var], extras: list[Expr], *, declare: bool) -> tuple[str, list[Var]]:
    """Destructuring pattern text; consumes bindings (fresh declarations or existing targets) and
    extra inputs (computed keys, defaults, member-target objects) in DFS order."""

    def target(t) -> str:
        if isinstance(t, O.Binding):
            v = bindings.pop(0)
            return w.declare(v) if declare else w.name_of(v)
        if isinstance(t, O.PropertyTarget):
            return member(extras.pop(0), t.key, False).text
        if isinstance(t, O.ElementTarget):
            return element(extras.pop(0), t.index, False).text
        if isinstance(t, O.ComputedTarget):
            obj = extras.pop(0)
            key = extras.pop(0)
            return computed(obj, key, False).text
        text, _ = _pattern_text(w, t, bindings, extras, declare=declare)
        return text

    if isinstance(pattern, O.ObjectPattern):
        props = []
        for p in pattern.properties:
            key = spell_key(p.key) if p.key is not None else "[" + extras.pop(0).text + "]"
            tgt = target(p.target)
            if p.has_default:
                tgt += " = " + extras.pop(0).text
            props.append(f"{key}: {tgt}")
        if pattern.has_rest:
            props.append("..." + target(O.Binding()))
        return "{" + ", ".join(props) + "}", bindings
    elems = []
    for e in pattern.elements:
        if e.target is None:
            elems.append("")
            continue
        tgt = target(e.target)
        if e.has_default:
            tgt += " = " + extras.pop(0).text
        elems.append(tgt)
    if pattern.rest is not None:
        elems.append("..." + target(pattern.rest))
    text = "[" + ", ".join(elems) + ("," if elems and elems[-1] == "" else "") + "]"
    return text, bindings


def _single_expression(w: Writer, result: Expr | None) -> str:
    """Close a temporary buffer that should have produced one expression (loop headers,
    JavaScriptLifter.swift:2206-2252): comma-join the pending expressions, or wrap statements in
    an immediately invoked arrow function."""
    pending, w.pending = w.pending, []
    exprs = [w.exprs.pop(v) for v in pending if v in w.exprs]
    lines = w.pop_buffer()
    if not lines:
        parts = [e.text for e in exprs] + ([result.text] if result is not None else [])
        if len(parts) <= 1:
            return parts[0] if parts else ""
        return ", ".join(parts)
    body = list(lines)
    for e in exprs:
        body.append("    " + e.text + ";")
    if result is not None:
        body.append("    return " + result.text + ";")
    return "(() => {\n" + "\n".join(body) + "\n})()"


def _lift_range(code: tuple[Instruction, ...], start: int, end: int, an: Analysis, w: Writer,
                options: LiftOptions, *, depth0: bool) -> None:
    """Lift code[start:end] into the writer. Called for the program and, recursively, for the
    body of a code string."""
    frames: list[int] = [0]               # block depth within the current function frame
    function_forms: list[bool] = []       # per open function: True = declaration statement, False = `var f = function`
    object_literals: list[_LiteralState] = []
    for_headers: list[_ForHeader] = []
    i = start
    while i < end:
        instr = code[i]
        op = instr.op
        w.gen = instr.gen

        # code strings: lift the nested program separately and emit it as a string literal
        if isinstance(op, O.BeginCodeString):
            close = block_end(code, i)
            assert isinstance(code[close].op, O.EndCodeString)
            w.flush()
            name = w.declare(instr.output)         # visible inside the string too (it is the string)
            inner = Writer(an, False)
            inner.names = w.names
            inner.declared_js = set(w.declared_js)
            inner.exprs = dict(w.exprs)            # after the flush: identifiers and pure expressions only
            _lift_range(code, i + 1, close, an, inner, options, depth0=False)
            inner.flush()
            w.emit(f"{LIFT_TOKENS.get(instr.lift, 'var')} {name} = {spell_string(inner.out.text())};")
            i = close + 1
            continue

        ins = w.retrieve(instr.inputs)
        fetched = [0]

        def arg(k: int) -> Expr:
            assert k == fetched[0], "inputs must be fetched in order"
            fetched[0] += 1
            return ins[k]

        def arg_ident(k: int) -> Expr:
            return w.as_identifier(arg(k), instr.inputs[k])

        def out(e: Expr, *, allow_inline: bool = True) -> None:
            w.assign(e, instr.output, instr.lift, allow_inline=allow_inline)

        def label_prefix(label: Var) -> str:
            if _label_used(an, label):
                return w.declare(label) + ": "
            return ""

        # ---------------------------------------------------------------- literals
        if isinstance(op, O.LoadInteger):
            out(Expr(NUMBER_LITERAL, spell_integer(op.value, op.radix)))
        elif isinstance(op, O.LoadBigInt):
            out(Expr(NUMBER_LITERAL, f"{op.value}n"))
        elif isinstance(op, O.LoadFloat):
            out(Expr(NUMBER_LITERAL, spell_float(op.value, op.style)))
        elif isinstance(op, O.LoadString):
            out(Expr(STRING_LITERAL, spell_string(op.value)))
        elif isinstance(op, O.LoadBoolean):
            out(Expr(LITERAL, "true" if op.value else "false"))
        elif isinstance(op, O.LoadUndefined):
            out(ident("undefined"))
        elif isinstance(op, O.LoadNull):
            out(Expr(LITERAL, "null"))
        elif isinstance(op, O.LoadThis):
            out(Expr(KEYWORD, "this"))
        elif isinstance(op, O.LoadArguments):
            out(ident("arguments"))
        elif isinstance(op, O.LoadNewTarget):
            out(Expr(KEYWORD, "new.target"))
        elif isinstance(op, O.LoadRegExp):
            out(Expr(REGEXP_LITERAL, f"/{op.pattern}/{regexp_flags(op.flags)}"))
        elif isinstance(op, O.LoadBuiltin):
            out(ident(op.builtin.name))
        elif isinstance(op, O.CreateNamedVariable):
            value = arg(0)
            name = op.identifier.name
            w.names[instr.output] = name
            if op.mode is O.NamedVariableMode.GLOBAL:
                w.emit(f"{name} = {value};")
            else:
                w.emit(f"{op.mode.value} {name} = {value};")
            w.declare(instr.output, name)
        elif isinstance(op, O.Declare):
            w.declare_var(instr.output, arg(0), DECLARATION_TOKENS[op.kind])

        # ---------------------------------------------------------------- object literals
        elif isinstance(op, O.BeginObjectLiteral):
            w.flush()
            object_literals.append(_LiteralState())
        elif isinstance(op, O.ObjectLiteralAddProperty):
            object_literals[-1].fields.append(f"{spell_key(op.key)}: {arg(0)}")
        elif isinstance(op, O.ObjectLiteralAddElement):
            idx = str(op.index) if op.index >= 0 else spell_string(str(op.index))
            object_literals[-1].fields.append(f"{idx}: {arg(0)}")
        elif isinstance(op, O.ObjectLiteralAddComputedProperty):
            key = arg(0)
            object_literals[-1].fields.append(f"[{key}]: {arg(1)}")
        elif isinstance(op, O.ObjectLiteralCopyProperties):
            object_literals[-1].fields.append(f"...{arg(0)}")
        elif isinstance(op, O.ObjectLiteralSetPrototype):
            object_literals[-1].fields.append(f"__proto__: {arg(0)}")
        elif isinstance(op, (O.BeginObjectLiteralMethod, O.BeginObjectLiteralComputedMethod, O.BeginObjectLiteralGetter,
                             O.BeginObjectLiteralComputedGetter, O.BeginObjectLiteralSetter, O.BeginObjectLiteralComputedSetter)):
            inner = list(instr.inner_outputs)
            this = inner.pop(0)
            w.exprs[this] = Expr(KEYWORD, "this")
            w.names[this] = "this"
            if isinstance(op, (O.BeginObjectLiteralComputedMethod, O.BeginObjectLiteralComputedGetter, O.BeginObjectLiteralComputedSetter)):
                key_text = "[" + arg(0).text + "]"
                defaults = [arg(k) for k in range(1, len(ins))]
            else:
                key_text = spell_key(op.key)
                defaults = [arg(k) for k in range(len(ins))]
            if isinstance(op, (O.BeginObjectLiteralMethod, O.BeginObjectLiteralComputedMethod)):
                head = ("*" if op.generator else "") + key_text + "(" + _params_text(w, op, inner, defaults) + ") {"
            elif isinstance(op, (O.BeginObjectLiteralGetter, O.BeginObjectLiteralComputedGetter)):
                head = "get " + key_text + "() {"
            else:
                head = "set " + key_text + "(" + w.declare(inner[0]) + ") {"
            object_literals[-1].open_method(head)
            w.push_buffer(1)
            frames.append(0)
        elif isinstance(op, (O.EndObjectLiteralMethod, O.EndObjectLiteralComputedMethod, O.EndObjectLiteralGetter,
                             O.EndObjectLiteralComputedGetter, O.EndObjectLiteralSetter, O.EndObjectLiteralComputedSetter)):
            frames.pop()
            body = w.pop_buffer()
            object_literals[-1].close_method(body)
        elif isinstance(op, O.EndObjectLiteral):
            state = object_literals.pop()
            if not state.fields and not state.methods:
                out(Expr(OBJECT_LITERAL, "{}"))
            elif not state.methods and len(state.fields) <= 4:
                out(Expr(OBJECT_LITERAL, "{ " + ", ".join(state.fields) + " }"))
            else:
                name = w.declare(instr.output)
                keyword = LIFT_TOKENS.get(instr.lift, "var")
                w.emit(f"{keyword} {name} = {{")
                w.w.indent()
                for f in state.fields:
                    w.w.emit(f + ",")
                for head, body in state.methods:
                    w.w.emit(head)
                    w.w.emit_lines(body)
                    w.w.emit("},")
                w.w.dedent()
                w.w.emit("};")

        # ---------------------------------------------------------------- arrays, strings
        elif isinstance(op, O.CreateArray):
            out(Expr(ARRAY_LITERAL, "[" + ", ".join(arg(k).text for k in range(len(ins))) + "]"))
        elif isinstance(op, O.CreateIntArray):
            out(Expr(ARRAY_LITERAL, "[" + ", ".join(str(v) for v in op.values) + "]"))
        elif isinstance(op, O.CreateFloatArray):
            out(Expr(ARRAY_LITERAL, "[" + ", ".join(_signed_float(v) for v in op.values) + "]"))
        elif isinstance(op, O.CreateArrayWithSpread):
            items = [("..." if spread else "") + arg(k).text for k, spread in enumerate(op.spreads)]
            out(Expr(ARRAY_LITERAL, "[" + ", ".join(items) + "]"))
        elif isinstance(op, O.CreateTemplateString):
            text = "`" + spell_template_part(op.parts[0])
            for k, part in enumerate(op.parts[1:]):
                text += "${" + arg(k).text + "}" + spell_template_part(part)
            out(Expr(TEMPLATE_LITERAL, text + "`"))

        # ---------------------------------------------------------------- properties
        elif isinstance(op, O.GetProperty):
            out(member(arg(0), op.key, op.receiver_optional))
        elif isinstance(op, O.SetProperty):
            obj = arg_ident(0)
            w.emit(f"{member(obj, op.key, False)} = {arg(1)};")
        elif isinstance(op, O.UpdateProperty):
            obj = arg_ident(0)
            w.emit(f"{member(obj, op.key, False)} {BINARY_TOKENS[op.op]}= {arg(1)};")
        elif isinstance(op, O.DeleteProperty):
            out(Expr(UNARY, "delete ") + member(arg_ident(0), op.key, op.receiver_optional))
        elif isinstance(op, O.ConfigureProperty):
            obj = arg_ident(0)
            values = [arg(k) for k in range(1, len(ins))]
            w.emit(f"Object.defineProperty({obj}, {spell_string(op.key.name)}, {_descriptor(op.flags, op.type, values)});")
        elif isinstance(op, O.GetElement):
            out(element(arg(0), op.index, op.receiver_optional))
        elif isinstance(op, O.SetElement):
            obj = arg_ident(0)
            w.emit(f"{element(obj, op.index, False)} = {arg(1)};")
        elif isinstance(op, O.UpdateElement):
            obj = arg_ident(0)
            w.emit(f"{element(obj, op.index, False)} {BINARY_TOKENS[op.op]}= {arg(1)};")
        elif isinstance(op, O.DeleteElement):
            out(Expr(UNARY, "delete ") + element(arg_ident(0), op.index, op.receiver_optional))
        elif isinstance(op, O.ConfigureElement):
            obj = arg_ident(0)
            values = [arg(k) for k in range(1, len(ins))]
            w.emit(f"Object.defineProperty({obj}, {op.index}, {_descriptor(op.flags, op.type, values)});")
        elif isinstance(op, O.GetComputedProperty):
            obj = arg(0)
            out(computed(obj, arg(1), op.receiver_optional))
        elif isinstance(op, O.SetComputedProperty):
            obj = arg_ident(0)
            key = arg(1)
            w.emit(f"{computed(obj, key, False)} = {arg(2)};")
        elif isinstance(op, O.UpdateComputedProperty):
            obj = arg_ident(0)
            key = arg(1)
            w.emit(f"{computed(obj, key, False)} {BINARY_TOKENS[op.op]}= {arg(2)};")
        elif isinstance(op, O.DeleteComputedProperty):
            obj = arg_ident(0)
            out(Expr(UNARY, "delete ") + computed(obj, arg(1), op.receiver_optional))
        elif isinstance(op, O.ConfigureComputedProperty):
            obj = arg_ident(0)
            key = arg(1)
            values = [arg(k) for k in range(2, len(ins))]
            w.emit(f"Object.defineProperty({obj}, {key}, {_descriptor(op.flags, op.type, values)});")
        elif isinstance(op, O.TypeOf):
            out(Expr(UNARY, "typeof ") + arg(0))
        elif isinstance(op, O.Void):
            out(Expr(UNARY, "void ") + arg(0))
        elif isinstance(op, O.TestInstanceOf):
            lhs = arg(0)
            out(Expr(BINARY, "") + lhs + " instanceof " + arg(1))
        elif isinstance(op, O.TestIn):
            lhs = arg(0)
            out(Expr(BINARY, "") + lhs + " in " + arg(1))

        # ---------------------------------------------------------------- functions
        elif isinstance(op, O.FUNCTION_BEGINS):
            defaults = [arg(k) for k in range(len(ins))]
            fname = w.declare(instr.output)
            params = _params_text(w, op, instr.inner_outputs, defaults)
            statement = getattr(op, "named", False) and frames[-1] == 0 and not w.buffers
            if isinstance(op, O.BeginArrowFunction):
                w.emit(f"var {fname} = ({params}) => {{")
            elif isinstance(op, O.BeginGeneratorFunction):
                w.emit(f"function* {fname}({params}) {{" if statement else f"var {fname} = function* ({params}) {{")
            else:
                w.emit(f"function {fname}({params}) {{" if statement else f"var {fname} = function ({params}) {{")
            w.enter_block()
            if getattr(op, "strict", False):
                w.emit('"use strict";')
            frames.append(0)
            function_forms.append(statement)
        elif isinstance(op, (O.EndPlainFunction, O.EndArrowFunction, O.EndGeneratorFunction)):
            frames.pop()
            w.leave_block()
            w.emit_raw("}" if function_forms.pop() else "};")
        elif isinstance(op, O.Directive):
            w.emit('"use strict";')
        elif isinstance(op, O.Return):
            w.emit(f"return {arg(0)};" if ins else "return;")
        elif isinstance(op, O.Yield):
            e = Expr(YIELD, "yield " + arg(0).text) if ins else Expr(YIELD, "yield")
            if an.num_uses(instr.output) == 0:
                w.emit(f"{e};")
            else:
                out(e)
        elif isinstance(op, O.YieldEach):
            w.emit(f"yield* {arg(0)};")

        # ---------------------------------------------------------------- calls
        elif isinstance(op, (O.CallFunction, O.CallFunctionWithSpread)):
            f = arg_ident(0)
            spreads = op.spreads if isinstance(op, O.CallFunctionWithSpread) else ()
            args = _call_args([arg(k) for k in range(1, len(ins))], spreads)
            out(Expr(CALL, "") + f + ("?.(" if op.call_optional else "(") + args + ")")
        elif isinstance(op, (O.Construct, O.ConstructWithSpread)):
            c = arg_ident(0)
            spreads = op.spreads if isinstance(op, O.ConstructWithSpread) else ()
            args = _call_args([arg(k) for k in range(1, len(ins))], spreads)
            out(Expr(NEW, "new ") + c + "(" + args + ")", allow_inline=False)
        elif isinstance(op, (O.CallMethod, O.CallMethodWithSpread)):
            obj = arg(0)
            spreads = op.spreads if isinstance(op, O.CallMethodWithSpread) else ()
            args = _call_args([arg(k) for k in range(1, len(ins))], spreads)
            callee = member(obj, Key(op.method.name), op.receiver_optional)
            out(Expr(CALL, "") + callee + ("?.(" if op.call_optional else "(") + args + ")")
        elif isinstance(op, (O.CallComputedMethod, O.CallComputedMethodWithSpread)):
            obj = arg(0)
            key = arg(1)
            spreads = op.spreads if isinstance(op, O.CallComputedMethodWithSpread) else ()
            args = _call_args([arg(k) for k in range(2, len(ins))], spreads)
            out(Expr(CALL, "") + computed(obj, key, op.receiver_optional) + ("?.(" if op.call_optional else "(") + args + ")")
        elif isinstance(op, O.CallSuperMethod):
            args = _call_args([arg(k) for k in range(len(ins))], ())
            out(Expr(CALL, f"super.{op.key.name}(" + args + ")") if op.key.is_identifier
                else Expr(CALL, f"super[{spell_string(op.key.name)}](" + args + ")"))
        elif isinstance(op, O.GetSuperProperty):
            out(Expr(MEMBER, "super") + ("." + op.key.name if op.key.is_identifier else f"[{spell_string(op.key.name)}]"))
        elif isinstance(op, O.SetSuperProperty):
            w.emit(f"{member(Expr(KEYWORD, 'super'), op.key, False)} = {arg(0)};")
        elif isinstance(op, O.UpdateSuperProperty):
            w.emit(f"{member(Expr(KEYWORD, 'super'), op.key, False)} {BINARY_TOKENS[op.op]}= {arg(0)};")
        elif isinstance(op, O.GetComputedSuperProperty):
            out(Expr(MEMBER, "super[" + arg(0).text + "]"))
        elif isinstance(op, O.SetComputedSuperProperty):
            key = arg(0)
            w.emit(f"super[{key}] = {arg(1)};")

        # ---------------------------------------------------------------- operators
        elif isinstance(op, O.UnaryOperation):
            tok = UNARY_TOKENS[op.op]
            if op.op.is_postfix:
                out(Expr(POSTFIX, "") + arg(0) + tok)
            else:
                out(Expr(UNARY, tok) + arg(0))
        elif isinstance(op, O.BinaryOperation):
            lhs = arg(0)
            rhs = arg(1)
            if op.op is O.BinaryOperator.EXP and lhs.type is UNARY:
                lhs = Expr(LITERAL, "(" + lhs.text + ")")
            out(Expr(BINARY, "") + lhs + f" {BINARY_TOKENS[op.op]} " + rhs)
        elif isinstance(op, O.Compare):
            lhs = arg(0)
            out(Expr(BINARY, "") + lhs + f" {COMPARATOR_TOKENS[op.op]} " + arg(1))
        elif isinstance(op, O.TernaryOperation):
            c = arg(0)
            a = arg(1)
            out(Expr(TERNARY, "") + c + " ? " + a + " : " + arg(2))
        elif isinstance(op, O.Reassign):
            target = arg(0)
            w.emit(f"{target} = {arg(1)};")
        elif isinstance(op, O.Update):
            target = arg(0)
            w.emit(f"{target} {BINARY_TOKENS[op.op]}= {arg(1)};")

        # ---------------------------------------------------------------- destructuring
        elif isinstance(op, O.Destruct):
            source = arg(0)
            extras = [arg(k) for k in range(1, len(ins))]
            text, _ = _pattern_text(w, op.pattern, list(instr.outputs), extras, declare=True)
            w.emit(f"{DECLARATION_TOKENS[op.kind]} {text} = {source};")
        elif isinstance(op, O.DestructAndReassign):
            source = arg(0)
            nb = O.num_bindings(op.pattern)
            targets = [arg(k) for k in range(1, 1 + nb)]
            for v, e in zip(instr.inputs[1:1 + nb], targets):
                if e.type is not IDENTIFIER:
                    raise LiftError("destructuring assignment targets must be identifiers")
            extras = [arg(k) for k in range(1 + nb, len(ins))]
            text, _ = _pattern_text(w, op.pattern, list(instr.inputs[1:1 + nb]), extras, declare=False)
            if isinstance(op.pattern, O.ObjectPattern):
                w.emit(f"({text} = {source});")
            else:
                w.emit(f"{text} = {source};")

        # ---------------------------------------------------------------- control flow
        elif isinstance(op, O.BeginIf):
            cond = arg(0)
            if op.inverted:
                cond = Expr(UNARY, "!") + cond
            w.emit(f"{label_prefix(instr.inner_output())}if ({cond}) {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.BeginElse):
            w.leave_block()
            w.emit_raw("} else {")
            w.enter_block()
        elif isinstance(op, O.EndIf):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, O.BeginWhileLoopHeader):
            w.flush()
            w.push_buffer(w.w.level + 1)
        elif isinstance(op, O.BeginWhileLoopBody):
            cond = _single_expression(w, arg(0))
            w.emit(f"{label_prefix(instr.inner_output())}while ({cond}) {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.EndWhileLoop):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, O.BeginDoWhileLoopBody):
            w.emit(f"{label_prefix(instr.inner_output())}do {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.BeginDoWhileLoopHeader):
            w.leave_block()
            w.push_buffer(w.w.level + 1)
        elif isinstance(op, O.EndDoWhileLoop):
            cond = _single_expression(w, arg(0))
            w.emit_raw(f"}} while ({cond});")
            frames[-1] -= 1
        elif isinstance(op, O.BeginForLoopInitializer):
            w.flush()
            w.push_buffer(w.w.level + 1)
        elif isinstance(op, O.BeginForLoopCondition):
            names = [w.declare(v) for v in instr.inner_outputs]
            initial = [arg(k).text for k in range(len(ins))]
            lines = w.pop_buffer()
            if op.num_loop_variables == 0:
                init = ""
            elif not lines:
                init = DECLARATION_TOKENS[op.declaration] + " " + ", ".join(f"{n} = {e}" for n, e in zip(names, initial))
            else:
                body = lines + [f"    return [{', '.join(initial)}];"]
                init = DECLARATION_TOKENS[op.declaration] + " [" + ", ".join(names) + "] = (() => {\n" + "\n".join(body) + "\n})()"
            for_headers.append(_ForHeader(init, names))
            w.push_buffer(w.w.level + 1)
        elif isinstance(op, O.BeginForLoopAfterthought):
            cond = _single_expression(w, arg(0))
            for_headers[-1].condition = "" if cond == "true" else cond
            for v, name in zip(instr.inner_outputs, for_headers[-1].names):
                w.names[v] = name
                w.exprs[v] = ident(name)
                w.declared_js.add(v)
            w.push_buffer(w.w.level + 1)
        elif isinstance(op, O.BeginForLoopBody):
            after = _single_expression(w, None)
            header = for_headers.pop()
            inner = list(instr.inner_outputs)
            label = inner.pop()
            for v, name in zip(inner, header.names):
                w.names[v] = name
                w.exprs[v] = ident(name)
                w.declared_js.add(v)
            cond = (" " + header.condition) if header.condition else ""
            after = (" " + after) if after else ""
            w.emit(f"{label_prefix(label)}for ({header.init};{cond};{after}) {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.EndForLoop):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, O.BeginForEachLoop):
            iterable = arg(0)
            extras = [arg(k) for k in range(1, len(ins))]
            inner = list(instr.inner_outputs)
            label = inner.pop()
            if op.pattern is None:
                target = w.declare(inner[0])
            else:
                target, _ = _pattern_text(w, op.pattern, inner, extras, declare=True)
            w.emit(f"{label_prefix(label)}for ({DECLARATION_TOKENS[op.declaration]} {target} {op.kind.value} {iterable}) {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.EndForEachLoop):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, O.BeginRepeatLoop):
            inner = list(instr.inner_outputs)
            label = inner.pop()
            counter = w.declare(inner[0]) if op.exposes_counter else f"i{instr.index}"
            kw = DECLARATION_TOKENS[op.declaration]
            w.emit(f"{label_prefix(label)}for ({kw} {counter} = 0; {counter} < {op.iterations}; {counter}++) {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.EndRepeatLoop):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, (O.LoopBreak, O.BlockBreak)):
            w.emit(f"break {w.name_of(instr.input(0))};" if ins else "break;")
        elif isinstance(op, O.LoopContinue):
            w.emit(f"continue {w.name_of(instr.input(0))};" if ins else "continue;")
        elif isinstance(op, O.SwitchBreak):
            w.emit("break;")
        elif isinstance(op, O.BeginTry):
            w.emit("try {")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.BeginCatch):
            w.leave_block()
            w.emit_raw(f"}} catch ({w.declare(instr.inner_output())}) {{")
            w.enter_block()
        elif isinstance(op, O.BeginFinally):
            w.leave_block()
            w.emit_raw("} finally {")
            w.enter_block()
        elif isinstance(op, O.EndTryCatchFinally):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, O.ThrowException):
            w.emit(f"throw {arg(0)};")
        elif isinstance(op, O.BeginBlockStatement):
            w.emit(f"{label_prefix(instr.inner_output())}{{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.EndBlockStatement):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, O.BeginSwitch):
            w.emit(f"{label_prefix(instr.inner_output())}switch ({arg(0)}) {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.BeginSwitchCase):
            w.emit_raw(f"case {arg(0)}:")
            w.enter_block()
        elif isinstance(op, O.BeginSwitchDefaultCase):
            w.emit_raw("default:")
            w.enter_block()
        elif isinstance(op, O.EndSwitchCase):
            if not op.falls_through:
                w.emit("break;")
            w.leave_block()
        elif isinstance(op, O.EndSwitch):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1
        elif isinstance(op, O.BeginWith):
            w.emit(f"with ({arg(0)}) {{")
            w.enter_block()
            frames[-1] += 1
        elif isinstance(op, O.EndWith):
            w.leave_block()
            w.emit_raw("}")
            frames[-1] -= 1

        # ---------------------------------------------------------------- oxpecker
        elif isinstance(op, O.Probe):
            if options.probes and not w.last_was_probe:
                observed = [w.name_of(v) for v in an.visible_at[i] if v in w.declared_js and v in w.names
                            and an.storage.get(v) not in (Storage.LABEL, Storage.THIS)][: options.probe_width]
                label = op.label.value if op.number == 0 else f"{op.label.value}{op.number}"
                w.emit(f"__probe({spell_string(label)}" + "".join(", " + n for n in observed) + ");")
                w.last_was_probe = True
        else:
            raise LiftError(f"no lifting rule for {op.name}")
        i += 1


@dataclass
class _LiteralState:
    fields: list[str] = field(default_factory=list)
    methods: list[tuple[str, list[str]]] = field(default_factory=list)
    _open: list[str] = field(default_factory=list)

    def open_method(self, head: str) -> None:
        self._open.append(head)

    def close_method(self, body: list[str]) -> None:
        self.methods.append((self._open.pop(), body))


@dataclass
class _ForHeader:
    init: str
    names: list[str]
    condition: str = ""


def _call_args(args: list[Expr], spreads: Sequence[bool]) -> str:
    if spreads:
        return ", ".join(("..." if s else "") + a.text for a, s in zip(args, spreads))
    return ", ".join(a.text for a in args)


def _descriptor(flags: O.PropertyFlag, kind: O.PropertyType, values: list[Expr]) -> str:
    parts = []
    if O.PropertyFlag.WRITABLE in flags and kind is O.PropertyType.VALUE:
        parts.append("writable: true")
    if O.PropertyFlag.CONFIGURABLE in flags:
        parts.append("configurable: true")
    if O.PropertyFlag.ENUMERABLE in flags:
        parts.append("enumerable: true")
    if kind is O.PropertyType.VALUE:
        parts.append(f"value: {values[0]}")
    elif kind is O.PropertyType.GETTER:
        parts.append(f"get: {values[0]}")
    elif kind is O.PropertyType.SETTER:
        parts.append(f"set: {values[0]}")
    else:
        parts.append(f"get: {values[0]}")
        parts.append(f"set: {values[1]}")
    return "{ " + ", ".join(parts) + " }"


def _signed_float(v: float) -> str:
    if v != v:
        return "NaN"
    if v == float("inf"):
        return "Infinity"
    if v == float("-inf"):
        return "-Infinity"
    if v < 0 or (v == 0 and str(v).startswith("-")):
        return "-" + spell_float(-v)
    return spell_float(v)
