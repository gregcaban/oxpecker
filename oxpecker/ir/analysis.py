"""Scopes, definitions, uses, storage and types of a program, recomputed from scratch on every
call -- nothing is stored on instructions, so a mutation can never leave a stale type behind.

Two classes of variables (ops.py, decision D15):
- temporaries (expression outputs) have one type, computed from their inputs' types at the point
  they are defined;
- declared variables (DeclareVar, parameters, catch variables, function names) are typed
  **flow-sensitively**: an assignment is a strong update, if/else and try/catch join their
  branches, a loop body is iterated to a fixpoint with the loop entry. A declared variable that is
  assigned inside a function other than the one declaring it can change whenever that function is
  called, so it falls back to the join of everything ever assigned to it (`escaping`).
`type_at(i, v)` is the type of v just before instruction i; `types[v]` the join over the program.

Call results depend on function return types, which depend on calls (recursion), and escaping
variables on joins over the whole program, so the walk is iterated to a fixpoint.

Per function, `activation[f]` says whether its body (not nested functions) uses `arguments`,
`eval` or `with` -- what makes Rhino give up typing its locals; per declared variable,
`storage[v]` says where it lives (top, local, activation-local, block, param, ...).

`verify()` checks the invariants every generator and mutator must keep:
  1. every input is defined before its use and visible there; each id is defined once;
  2. blocks are balanced and well-formed (else only in an if, catch/finally only in a try, a try
     has a catch or a finally, return only inside a function);
  3. only declared, non-const variables are assigned, and never a loop counter;
  4. input-slot requirements hold under the types at the point of use: a callee or constructor
     may be a function (FUNCTION or UNKNOWN); no property access on a definitely null/undefined
     value. Everything merely likely to throw is allowed: both modes must throw alike.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..gen.types import OBJECTS, T
from .ops import (ARITH, BITWISE, COMPARE, LOGICAL, LOOPS, RELATION, BeginBlock, BeginCatch, BeginElse, BeginFinally,
                  BeginFor, BeginForEach, BeginFunction, BeginIf, BeginSwitch, BeginTry, BeginWhile, BeginWith, Binary,
                  Break, CallFunction, CallMethod, Case, CompoundAssign, Conditional, Construct, Continue, CreateArray,
                  CreateObject, DeclareVar, DeleteProperty, EndBlock, EndFor, EndForEach, EndFunction, EndIf,
                  EndSwitch, EndTry, EndWhile, EndWith, Eval, GetElement, GetProperty, Instr, LoadBuiltin, LoadLiteral,
                  Program, Reassign, Return, SetElement, SetProperty, Throw, Unary, Update, Yield)

_LOOP_FRAMES = ("for", "while", "foreach")
_BLOCK_FRAMES = {BeginWhile: "while", BeginForEach: "foreach", BeginWith: "with", BeginBlock: "block"}
_END_WANTS = {EndFunction: ("function",), EndIf: ("then", "else"), EndFor: ("for",), EndWhile: ("while",),
              EndForEach: ("foreach",), EndWith: ("with",), EndBlock: ("block",), EndTry: ("catch", "finally")}


class InvalidProgram(Exception):
    pass


def join(a: T | None, b: T | None) -> T | None:
    """None is bottom (no information yet), UNKNOWN is top."""
    if a is None:
        return b
    if b is None or a == b:
        return a
    if {a, b} == {T.NUMBER, T.INT32}:
        return T.NUMBER
    if a in OBJECTS and b in OBJECTS:
        return T.OBJECT
    return T.UNKNOWN


def join_env(a: dict[int, T], b: dict[int, T]) -> dict[int, T]:
    return {v: join(a.get(v), b.get(v)) or T.UNKNOWN for v in a.keys() | b.keys()}


_NUMERIC_ISH = {T.NUMBER, T.INT32, T.BOOLEAN, T.NULL, T.UNDEFINED}

_CONSTRUCTED = {"Map": T.MAP, "Set": T.SET, "WeakMap": T.OBJECT, "Array": T.ARRAY, "RegExp": T.REGEXP,
                "Int8Array": T.TYPEDARRAY, "Uint8Array": T.TYPEDARRAY, "Uint8ClampedArray": T.TYPEDARRAY,
                "Int16Array": T.TYPEDARRAY, "Uint16Array": T.TYPEDARRAY, "Int32Array": T.TYPEDARRAY,
                "Uint32Array": T.TYPEDARRAY, "Float32Array": T.TYPEDARRAY, "Float64Array": T.TYPEDARRAY}

_METHOD_RESULT = {
    **dict.fromkeys(["push", "unshift", "indexOf", "lastIndexOf", "findIndex", "charCodeAt", "codePointAt",
                     "localeCompare", "search", "getTime", "imul", "max", "min", "abs", "floor", "ceil", "round",
                     "sqrt", "pow", "trunc", "sign", "random"], T.NUMBER),
    **dict.fromkeys(["toString", "join", "toFixed", "toPrecision", "toExponential", "charAt", "toUpperCase",
                     "toLowerCase", "trim", "padStart", "padEnd", "repeat", "substring", "substr",
                     "toLocaleString", "normalize"], T.STRING),
    **dict.fromkeys(["includes", "hasOwnProperty", "test", "every", "some", "has", "isPrototypeOf",
                     "propertyIsEnumerable", "startsWith", "endsWith", "delete", "isArray", "isInteger",
                     "isFinite", "isNaN"], T.BOOLEAN),
}
_ARRAY_RESULT = {"map", "filter", "slice", "concat", "splice", "reverse", "sort", "flat", "fill", "copyWithin"}
_ACTIVATION = re.compile(r"\barguments\b|\beval\b|\bwith\s*\(")


def binary_type(op: str, a: T | None, b: T | None) -> T:
    if op in COMPARE or op in RELATION:
        return T.BOOLEAN
    if op in LOGICAL:
        return join(a, b) or T.UNKNOWN
    if a == T.BIGINT and b == T.BIGINT:
        return T.BIGINT
    if op in BITWISE:
        return T.UNKNOWN if T.UNKNOWN in (a, b) or T.BIGINT in (a, b) else T.INT32
    if op == "+":
        if T.STRING in (a, b):
            return T.STRING
        if a in _NUMERIC_ISH and b in _NUMERIC_ISH:
            return T.NUMBER
        return T.UNKNOWN
    if op in ARITH:
        return T.UNKNOWN if T.UNKNOWN in (a, b) or T.BIGINT in (a, b) else T.NUMBER
    raise AssertionError(op)


def unary_type(op: str, a: T | None) -> T:
    if op == "!":
        return T.BOOLEAN
    if op == "typeof":
        return T.STRING
    if op == "void":
        return T.UNDEFINED
    if op == "+":
        return T.NUMBER
    if a in (T.BIGINT, T.UNKNOWN):
        return a
    return T.INT32 if op == "~" else T.NUMBER


def update_type(a: T | None) -> T:
    return a if a in (T.BIGINT, T.UNKNOWN) else T.NUMBER


@dataclass
class Frame:
    kind: str                       # top | function | then | else | for | while | foreach | switch | case
                                    # | with | block | try | catch | finally
    begin: int                      # index of the instruction that opened the block (-1: top)
    vars: list[int] = field(default_factory=list)
    fn: int | None = None           # innermost enclosing function variable
    label: str | None = None        # loops and blocks: a break/continue target name
    generator: bool = False         # function frames: a generator function (may yield)


@dataclass
class Analysis:
    def_index: dict[int, int]                   # variable -> defining instruction
    visible_at: list[list[int]]                 # variables visible before instruction i, definition order
    depth_at: list[int]                         # block nesting depth before instruction i
    frame_at: list[str]                         # kind of the innermost block before instruction i
    fn_at: list[int | None]                     # innermost enclosing function variable before instruction i
    block_end: dict[int, int] = field(default_factory=dict)     # Begin index -> End index
    middle: dict[int, list[int]] = field(default_factory=dict)  # Begin index -> else/catch/finally indices
    declared: dict[int, str] = field(default_factory=dict)      # assignable variable -> kind (var/let/const/param/catch/function)
    loop_counters: set[int] = field(default_factory=set)
    reassigned: set[int] = field(default_factory=set)           # targets of Reassign/CompoundAssign/Update
    escaping: set[int] = field(default_factory=set)             # assigned inside a nested function
    captured: set[int] = field(default_factory=set)             # used inside a nested function
    use_count: dict[int, int] = field(default_factory=dict)
    users: dict[int, list[int]] = field(default_factory=dict)
    activation: dict[int, bool] = field(default_factory=dict)   # function -> body uses arguments/eval/with
    storage: dict[int, str] = field(default_factory=dict)       # variable -> where it lives
    types: dict[int, T] = field(default_factory=dict)           # join over the whole program
    env_at: list[dict[int, T]] = field(default_factory=list)    # declared variables' types before instruction i
    returns: dict[int, T] = field(default_factory=dict)         # function variable -> return type

    def visible_recent(self, i: int) -> list[int]:
        """Most recently defined first: the order generators and mutators index into (§3.8 rule 5)."""
        return self.visible_at[i][::-1]

    def type_at(self, i: int, v: int) -> T:
        if v in self.escaping:                    # any call may have changed it: the join over the program
            return self.types.get(v, T.UNKNOWN)
        if v in self.declared and i < len(self.env_at) and v in self.env_at[i]:
            return self.env_at[i][v]
        return self.types.get(v, T.UNKNOWN)

    def assignable(self, v: int) -> bool:
        return self.declared.get(v) not in (None, "const") and v not in self.loop_counters


def analyse(prog: Program) -> Analysis:
    an = _structure(prog)
    state = _Typer(prog, an)
    for _ in range(12):
        changed = state.walk_all()
        if not changed:
            break
    an.types = {v: (t or T.UNKNOWN) for v, t in state.types.items()}
    an.returns = {f: (t or T.UNKNOWN) for f, t in state.returns.items()}
    an.env_at = [state.env_at.get(i, {}) for i in range(len(prog) + 1)]
    return an


# ---------------------------------------------------------------------------- structure

def _structure(prog: Program) -> Analysis:
    """Scopes, blocks, definitions, uses, storage; raises InvalidProgram on structural violations."""
    frames = [Frame("top", -1)]
    an = Analysis({}, [], [], [], [])
    fn_frames: list[int] = []                   # function variables of the open functions

    def define(v: int, i: int) -> None:
        if v in an.def_index:
            raise InvalidProgram(f"v{v} defined twice")
        an.def_index[v] = i

    def block_kind() -> str:
        return "block" if frames[-1].kind not in ("top", "function") else frames[-1].kind

    def enclosing(stop_at_function: bool = True):
        """Frames from the innermost outwards, up to (not including) the enclosing function."""
        for f in reversed(frames):
            if stop_at_function and f.kind == "function":
                return
            yield f

    def check_label(i: int, label: str | None) -> None:
        if label is not None and any(f.label == label for f in enclosing()):
            raise InvalidProgram(f"label {label!r} at {i} shadows an enclosing label")

    for i, ins in enumerate(prog):
        visible = [v for f in frames for v in f.vars]
        an.visible_at.append(visible)
        an.depth_at.append(len(frames) - 1)
        an.frame_at.append(frames[-1].kind)
        an.fn_at.append(frames[-1].fn)
        vis = set(visible)
        fn = frames[-1].fn
        for v in ins.inputs:
            if v not in vis:
                raise InvalidProgram(f"instruction {i} ({type(ins.op).__name__}) uses v{v}, not visible there")
            an.use_count[v] = an.use_count.get(v, 0) + 1
            an.users.setdefault(v, []).append(i)
            if fn is not None and _owner(an, v) != fn:
                an.captured.add(v)
        op = ins.op
        if isinstance(op, (Reassign, CompoundAssign, Update)) and ins.inputs:
            t = ins.inputs[0]
            an.reassigned.add(t)
            if fn is not None and _owner(an, t) != fn:
                an.escaping.add(t)
        if fn is not None and (isinstance(op, LoadBuiltin) and op.name in ("arguments", "eval")
                               or isinstance(op, Eval) and _ACTIVATION.search(op.code)):
            an.activation[fn] = True
        for v in ins.outputs:
            define(v, i)
        for v in ins.inner:
            define(v, i)
        if frames[-1].kind == "switch" and not isinstance(op, (Case, EndSwitch)):
            raise InvalidProgram(f"{type(op).__name__} at {i} directly in a switch, outside any case")

        if isinstance(op, BeginFunction):
            if len(ins.outputs) != 1 or len(ins.inner) != op.arity:
                raise InvalidProgram(f"function at {i}: needs one output and {op.arity} parameters")
            f = ins.outputs[0]
            an.declared[f] = "function"
            an.storage[f] = "function"
            an.activation.setdefault(f, False)
            for p in ins.inner:
                an.declared[p] = "param"
            frames[-1].vars.append(f)                       # visible inside (recursion) and after
            frames.append(Frame("function", i, list(ins.inner), fn=f, generator=op.style == "generator"))
        elif isinstance(op, BeginIf):
            frames.append(Frame("then", i, fn=fn))
        elif isinstance(op, BeginElse):
            if frames[-1].kind != "then":
                raise InvalidProgram(f"else at {i} outside an if")
            begin = frames.pop().begin
            an.middle.setdefault(begin, []).append(i)
            frames.append(Frame("else", begin, fn=fn))
        elif isinstance(op, BeginFor):
            if len(ins.inner) != 1:
                raise InvalidProgram(f"for at {i} needs a counter")
            check_label(i, op.label)
            an.loop_counters.add(ins.inner[0])
            an.storage[ins.inner[0]] = "counter"
            frames.append(Frame("for", i, list(ins.inner), fn=fn, label=op.label))
        elif isinstance(op, (BeginWhile, BeginForEach, BeginWith, BeginBlock)):
            label = getattr(op, "label", None)
            check_label(i, label)
            if isinstance(op, BeginWhile):
                if len(ins.inner) != 1:
                    raise InvalidProgram(f"while at {i} needs a counter")
                an.loop_counters.add(ins.inner[0])
                an.storage[ins.inner[0]] = "counter"
            elif isinstance(op, BeginForEach):
                if len(ins.inner) != 1 or len(ins.inputs) != 1 or op.kind not in ("of", "in"):
                    raise InvalidProgram(f"for-{op.kind} at {i} needs an iterable and one loop variable")
                an.declared[ins.inner[0]] = "loopvar"
                an.storage[ins.inner[0]] = "loopvar"
            elif isinstance(op, BeginWith) and len(ins.inputs) != 1:
                raise InvalidProgram(f"with at {i} needs an object")
            frames.append(Frame(_BLOCK_FRAMES[type(op)], i, list(ins.inner), fn=fn, label=label))
        elif isinstance(op, BeginSwitch):
            if len(ins.inputs) != 1:
                raise InvalidProgram(f"switch at {i} needs a discriminant")
            frames.append(Frame("switch", i, fn=fn))
        elif isinstance(op, Case):
            if frames[-1].kind == "case":
                frames.pop()
            if frames[-1].kind != "switch":
                raise InvalidProgram(f"case at {i} outside a switch")
            begin = frames[-1].begin
            outside = set(an.visible_at[begin])
            if any(v not in outside for v in ins.inputs):
                raise InvalidProgram(f"case value at {i} must be defined before the switch")
            cases = an.middle.setdefault(begin, [])
            if not ins.inputs and any(not prog[c].inputs for c in cases):
                raise InvalidProgram(f"second default at {i}")
            if len(ins.inputs) > 1:
                raise InvalidProgram(f"case at {i} has more than one value")
            cases.append(i)
            frames.append(Frame("case", begin, fn=fn))
        elif isinstance(op, EndSwitch):
            if frames[-1].kind == "case":
                frames.pop()
            if frames[-1].kind != "switch":
                raise InvalidProgram(f"EndSwitch at {i} closes a {frames[-1].kind} block")
            an.block_end[frames.pop().begin] = i
        elif isinstance(op, Break):
            ok = any((f.label == op.label) if op.label else (f.kind in _LOOP_FRAMES or f.kind == "case")
                     for f in enclosing())
            if not ok:
                raise InvalidProgram(f"break{' ' + op.label if op.label else ''} at {i} has no target")
        elif isinstance(op, Continue):
            ok = any(f.kind in _LOOP_FRAMES and (op.label is None or f.label == op.label) for f in enclosing())
            if not ok:
                raise InvalidProgram(f"continue{' ' + op.label if op.label else ''} at {i} has no loop target")
        elif isinstance(op, Yield):
            fn_frame = next((f for f in reversed(frames) if f.kind == "function"), None)
            if fn_frame is None or not fn_frame.generator:
                raise InvalidProgram(f"yield at {i} outside a generator function")
            if (ins.outputs and not op.value) or len(ins.inputs) > 1 or (op.delegate and not ins.inputs):
                raise InvalidProgram(f"malformed yield at {i}")
            if ins.outputs:
                an.storage[ins.outputs[0]] = "temp-local"
                frames[-1].vars.extend(ins.outputs)
        elif isinstance(op, BeginTry):
            frames.append(Frame("try", i, fn=fn))
        elif isinstance(op, (BeginCatch, BeginFinally)):
            if frames[-1].kind not in (("try",) if isinstance(op, BeginCatch) else ("try", "catch")):
                raise InvalidProgram(f"{type(op).__name__} at {i} in the wrong place")
            begin = frames.pop().begin
            an.middle.setdefault(begin, []).append(i)
            for e in ins.inner:
                an.declared[e] = "catch"
                an.storage[e] = "catch"
            frames.append(Frame("catch" if isinstance(op, BeginCatch) else "finally", begin, list(ins.inner), fn=fn))
        elif type(op) in _END_WANTS:
            want = _END_WANTS[type(op)]
            if frames[-1].kind not in want:
                raise InvalidProgram(f"{type(op).__name__} at {i} closes a {frames[-1].kind} block")
            an.block_end[frames.pop().begin] = i
        elif isinstance(op, Return):
            if fn is None:
                raise InvalidProgram(f"return at {i} outside a function")
        elif isinstance(op, DeclareVar):
            (v,) = ins.outputs
            an.declared[v] = op.kind
            where = block_kind()
            if where == "top":
                an.storage[v] = f"top-{op.kind}"
            elif where == "function":
                an.storage[v] = f"local-{op.kind}"
            else:
                an.storage[v] = f"block-{op.kind}" if op.kind != "var" else "local-var"
            frames[-1].vars.append(v)
        elif ins.outputs:
            for v in ins.outputs:
                an.storage[v] = "temp-top" if fn is None else "temp-local"
            frames[-1].vars.extend(ins.outputs)

    if len(frames) != 1:
        raise InvalidProgram(f"unclosed {frames[-1].kind} block")
    an.visible_at.append([v for f in frames for v in f.vars])
    an.depth_at.append(0)
    an.frame_at.append("top")
    an.fn_at.append(None)
    # Locals of a function with an activation are not optimizer-typed: say so.
    for v, s in list(an.storage.items()):
        if s.startswith(("local-", "temp-local")) and an.activation.get(_owner(an, v), False):
            an.storage[v] = "activation-" + s
    for f, ins_i in ((ins.outputs[0], i) for i, ins in enumerate(prog) if isinstance(ins.op, BeginFunction)):
        for p in prog[ins_i].inner:
            an.storage[p] = "activation-param" if an.activation.get(f) else "param"
    return an


def _owner(an: Analysis, v: int) -> int | None:
    """The function a variable belongs to (None: top level). Parameters belong to their function."""
    i = an.def_index[v]
    if an.declared.get(v) == "param":
        return an.fn_at[i + 1] if i + 1 < len(an.fn_at) else None
    return an.fn_at[i]


# ---------------------------------------------------------------------------- types

class _Typer:
    def __init__(self, prog: Program, an: Analysis):
        self.prog, self.an = prog, an
        self.types: dict[int, T | None] = {}        # temporaries; declared: join over the program
        self.returns: dict[int, T | None] = {}
        self.env_at: dict[int, dict[int, T]] = {}
        self.escaping_t: dict[int, T | None] = {}   # escaping variables: join of everything assigned
        self.targets: list[dict] = []               # open loops/switches/labelled blocks (break/continue)

    def walk_all(self) -> bool:
        """One pass from scratch, reading return types and escaping variables' types from the
        previous pass (bottom on the first). Iterated by `analyse` until those summaries settle."""
        before = (dict(self.types), dict(self.returns), dict(self.escaping_t))
        self.types = {}
        self.new_returns: dict[int, T | None] = {}
        self.new_escaping: dict[int, T | None] = {}
        end_env = self.walk(0, len(self.prog), {})
        self.env_at[len(self.prog)] = dict(end_env)
        for i, ins in enumerate(self.prog):
            if isinstance(ins.op, BeginFunction):
                # Unless the body's last top-level statement returns or throws, the function can fall
                # off the end and return undefined (conservative: a return in every branch of an
                # if/else still counts as "can fall off").
                end = self.an.block_end[i]
                last = self.prog[end - 1].op if end - 1 > i else None
                if not isinstance(last, (Return, Throw)):
                    f = ins.outputs[0]
                    self.new_returns[f] = join(self.new_returns.get(f), T.UNDEFINED)
        self.returns = self.new_returns
        self.escaping_t = self.new_escaping
        return (self.types, self.returns, self.escaping_t) != before

    # -- reading and writing types
    def read(self, v: int, env: dict[int, T]) -> T | None:
        if v in self.an.escaping:
            return join(self.escaping_t.get(v), env.get(v))
        if v in self.an.declared:
            return env.get(v, self.types.get(v))
        return self.types.get(v)

    def assign(self, v: int, t: T | None, env: dict[int, T]) -> None:
        t = t or T.UNKNOWN
        env[v] = t
        self.types[v] = join(self.types.get(v), t)
        if v in self.an.escaping:
            self.new_escaping[v] = join(self.new_escaping.get(v), t)

    # -- the structured walk
    def walk(self, lo: int, hi: int, env: dict[int, T]) -> dict[int, T]:
        prog, an = self.prog, self.an
        i = lo
        while i < hi:
            ins = prog[i]
            op = ins.op
            self.env_at[i] = dict(env)
            if isinstance(op, BeginFunction):
                end = an.block_end[i]
                self.assign(ins.outputs[0], T.FUNCTION, env)
                inner = dict(env)
                for p in ins.inner:
                    self.assign(p, T.UNKNOWN, inner)
                self.walk(i + 1, end, inner)
                self.env_at[end] = dict(env)
                i = end + 1
                continue
            if isinstance(op, BeginIf):
                end = an.block_end[i]
                mids = an.middle.get(i, [])
                then_end = mids[0] if mids else end
                env_then = self.walk(i + 1, then_end, dict(env))
                if mids:
                    self.env_at[then_end] = dict(env_then)
                    env_else = self.walk(then_end + 1, end, dict(env))
                else:
                    env_else = env
                self.env_at[end] = dict(env_else)
                env = join_env(env_then, env_else)
                i = end + 1
                continue
            if isinstance(op, LOOPS):
                end = an.block_end[i]
                env = self.loop(i, end, ins, env)
                i = end + 1
                continue
            if isinstance(op, BeginSwitch):
                end = an.block_end[i]
                env = self.switch(i, end, env)
                i = end + 1
                continue
            if isinstance(op, (BeginWith, BeginBlock)):
                end = an.block_end[i]
                target = {"label": getattr(op, "label", None), "kind": "block", "breaks": [], "continues": []}
                self.targets.append(target)
                out = self.walk(i + 1, end, dict(env))
                self.targets.pop()
                self.env_at[end] = dict(out)
                env = out
                for e in target["breaks"]:
                    env = join_env(env, e)
                i = end + 1
                continue
            if isinstance(op, (Break, Continue)):
                t = self._target(op)
                if t is not None:
                    t["breaks" if isinstance(op, Break) else "continues"].append(dict(env))
                i += 1
                continue
            if isinstance(op, BeginTry):
                end = an.block_end[i]
                mids = an.middle.get(i, [])
                parts = [i] + mids + [end]
                env_try = self.walk(i + 1, parts[1], dict(env))
                after = env_try
                k = 1
                if isinstance(prog[parts[k]].op, BeginCatch):
                    self.env_at[parts[k]] = dict(env_try)
                    c_in = join_env(env, env_try)           # the throw can come from anywhere in the try
                    for e in prog[parts[k]].inner:
                        self.assign(e, T.UNKNOWN, c_in)
                    env_catch = self.walk(parts[k] + 1, parts[k + 1], c_in)
                    after = join_env(env_try, env_catch)
                    k += 1
                if k < len(parts) - 1 and isinstance(prog[parts[k]].op, BeginFinally):
                    self.env_at[parts[k]] = dict(after)
                    after = self.walk(parts[k] + 1, end, join_env(env, after))
                self.env_at[end] = dict(after)
                env = after
                i = end + 1
                continue
            self.step(i, ins, env)
            i += 1
        return env

    def _target(self, op) -> dict | None:
        """The loop/switch/labelled statement a break or continue leaves (verify checked it exists)."""
        for t in reversed(self.targets):
            if op.label is not None:
                if t["label"] == op.label:
                    return t
            elif t["kind"] == "loop" or (isinstance(op, Break) and t["kind"] == "switch"):
                return t
        return None

    def loop(self, i: int, end: int, ins: Instr, env: dict[int, T]) -> dict[int, T]:
        """for / while / for-each: the body runs 0..n times (do-while at least once, but joining
        with the entry is a sound over-approximation). A `continue` flows back to the head, a
        `break` to the exit."""
        op = ins.op
        if isinstance(op, (BeginFor, BeginWhile)):
            self.types[ins.inner[0]] = T.INT32
        target = {"label": op.label, "kind": "loop", "breaks": [], "continues": []}
        self.targets.append(target)
        head = dict(env)
        for _ in range(5):
            target["breaks"].clear()
            target["continues"].clear()
            body = dict(head)
            if isinstance(op, BeginForEach):
                self.assign(ins.inner[0], T.STRING if op.kind == "in" else T.UNKNOWN, body)
            out = self.walk(i + 1, end, body)
            nxt = join_env(env, out)
            for c in target["continues"]:
                nxt = join_env(nxt, c)
            if nxt == head:
                break
            head = nxt
        self.targets.pop()
        self.env_at[end] = dict(out)
        exit_env = head
        for b in target["breaks"]:
            exit_env = join_env(exit_env, b)
        return exit_env

    def switch(self, i: int, end: int, env: dict[int, T]) -> dict[int, T]:
        """Each case is entered from the discriminant test or by falling through from the previous
        case; the switch is left by a break, by falling off the last case, or (no default) by no
        case matching."""
        cases = self.an.middle.get(i, [])
        target = {"label": None, "kind": "switch", "breaks": [], "continues": []}
        self.targets.append(target)
        fall: dict[int, T] | None = None
        for k, c in enumerate(cases):
            stop = cases[k + 1] if k + 1 < len(cases) else end
            entry = join_env(env, fall) if fall is not None else dict(env)
            self.env_at[c] = dict(entry)
            fall = self.walk(c + 1, stop, entry)
        self.targets.pop()
        out = fall if fall is not None else dict(env)
        if not any(not self.prog[c].inputs for c in cases):      # no default: maybe nothing matched
            out = join_env(out, env)
        for b in target["breaks"]:
            out = join_env(out, b)
        self.env_at[end] = dict(out)
        return out

    def step(self, i: int, ins: Instr, env: dict[int, T]) -> None:
        op, an = ins.op, self.an
        ts = [self.read(v, env) for v in ins.inputs]
        out: T | None = None
        if isinstance(op, LoadLiteral):
            out = op.type
        elif isinstance(op, DeclareVar):
            self.assign(ins.outputs[0], ts[0], env)
            return
        elif isinstance(op, LoadBuiltin):
            out = op.type
        elif isinstance(op, CreateArray):
            out = T.ARRAY
        elif isinstance(op, CreateObject):
            out = T.OBJECT
        elif isinstance(op, Unary):
            out = unary_type(op.op, ts[0])
        elif isinstance(op, Binary):
            out = binary_type(op.op, ts[0], ts[1])
        elif isinstance(op, Conditional):
            out = join(ts[1], ts[2]) or T.UNKNOWN
        elif isinstance(op, GetProperty):
            out = T.INT32 if op.name == "length" and ts[0] in (T.ARRAY, T.STRING, T.FUNCTION, T.TYPEDARRAY) \
                else T.UNKNOWN
        elif isinstance(op, GetElement):
            out = T.UNKNOWN
        elif isinstance(op, DeleteProperty):
            out = T.BOOLEAN
        elif isinstance(op, CallFunction):
            callee = ins.inputs[0]
            d = self.prog[an.def_index[callee]].op
            known = isinstance(d, BeginFunction) and callee not in an.reassigned
            if known and d.style == "generator":
                out = T.OBJECT                                         # calling a generator makes an iterator
            else:
                out = self.returns.get(callee) if known else T.UNKNOWN  # None (bottom) until known
        elif isinstance(op, CallMethod):
            out = _METHOD_RESULT.get(op.name) or (T.ARRAY if op.name in _ARRAY_RESULT and ts[0] == T.ARRAY
                                                  else T.UNKNOWN)
        elif isinstance(op, Construct):
            d = self.prog[an.def_index[ins.inputs[0]]].op
            out = _CONSTRUCTED.get(d.name, T.OBJECT) if isinstance(d, LoadBuiltin) else T.OBJECT
        elif isinstance(op, Eval):
            out = op.out
        elif isinstance(op, Yield):
            out = T.UNKNOWN if op.value else None                      # whatever the caller sends in
        elif isinstance(op, Reassign):
            self.assign(ins.inputs[0], ts[1], env)
        elif isinstance(op, CompoundAssign):
            self.assign(ins.inputs[0], binary_type(op.op, ts[0], ts[1]), env)
        elif isinstance(op, Update):
            self.assign(ins.inputs[0], update_type(ts[0]), env)
            out = update_type(ts[0]) if op.value else None
        elif isinstance(op, Return):
            f = an.fn_at[i]
            self.new_returns[f] = join(self.new_returns.get(f), ts[0] if ts else T.UNDEFINED)
        if out is not None:
            for v in ins.outputs:
                self.types[v] = out


# ---------------------------------------------------------------------------- verification

def _requirement(an: Analysis, i: int, ins: Instr, k: int) -> str | None:
    """Why input slot k of instruction i is violated, or None."""
    op, v = ins.op, ins.inputs[k]
    if isinstance(op, (Reassign, CompoundAssign, Update)) and k == 0:
        if v in an.loop_counters:
            return f"assignment to loop counter v{v}"
        if v not in an.declared:
            return f"assignment to temporary v{v}"
        if an.declared[v] == "const":
            return f"assignment to const v{v}"
        return None
    t = an.type_at(i, v)
    if isinstance(op, (CallFunction, Construct)) and k == 0 and t not in (T.FUNCTION, T.UNKNOWN):
        return f"callee v{v} is {t.value}"
    if (isinstance(op, (GetProperty, SetProperty, GetElement, SetElement, DeleteProperty, CallMethod))
            and k == 0 and t in (T.NULL, T.UNDEFINED)):
        return f"property access on v{v}, which is {t.value}"
    return None


def requirement_ok(an: Analysis, i: int, ins: Instr, k: int) -> bool:
    return _requirement(an, i, ins, k) is None


def verify(prog: Program) -> Analysis:
    an = analyse(prog)
    for i, ins in enumerate(prog):
        for k in range(len(ins.inputs)):
            why = _requirement(an, i, ins, k)
            if why:
                raise InvalidProgram(f"instruction {i} ({type(ins.op).__name__}): {why}")
    return an
