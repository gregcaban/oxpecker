# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/FuzzIL/JSTyper.swift (the branch-state typer, 3022-3371 and the per-operation
# rules) and Analyzer.swift (DefUseAnalyzer, VariableAnalyzer) at commit
# be2811d00282320381fc8007d64873745772011d, with the changes of docs/design-generation-layer.md
# §5.2 (two-pass loops, joining try/catch/finally) and the Rhino model of §5.3, which is derived
# from Rhino b95f75d: Parser.java, IRFactory.java, optimizer/Optimizer.java, optimizer/Block.java,
# optimizer/OptTransformer.java, optimizer/Codegen.java.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Program analysis: scopes, storage kinds, def-use, flow-sensitive types, the Rhino model, and
`verify`, which rejects what a mutation could have broken. Nothing here is stored on
instructions; every call recomputes from the code. The typer is a steppable class so the builder
can run it per emitted instruction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Mapping, Sequence

from . import ops as O
from .environment import CALLS, INSTANCE_GROUP, INSTANCE_TYPE, methods_of
from .ops import Context, Operation
from .program import InvalidProgram, Instruction, Var, block_end, check, contexts
from .types import (
    ANY,
    ARRAY,
    BIGINT,
    BOOLEAN,
    FLOAT,
    FUNCTION,
    INTEGER,
    LENGTH_KEY,
    NOTHING,
    NULL,
    NULLISH,
    NUMBER,
    OBJECT,
    PRIMITIVE,
    STRING,
    UNDEFINED,
    Key,
    KeyKind,
    Param,
    Signature,
    Type,
)

RHINO_MODEL_SOURCE = "b95f75d"     # the Rhino commit the predicates of §5.3 were read from

LABEL = Type.object("Label")
ARGUMENTS = Type.object("Arguments", props=(LENGTH_KEY,))
GENERATOR_OBJECT = Type.object("Generator")
REGEXP = Type.object("RegExp")


class Storage(Enum):
    TEMP = auto()
    TOP_VAR = auto()
    TOP_LET = auto()
    TOP_CONST = auto()
    LOCAL_VAR = auto()
    LOCAL_LET = auto()
    LOCAL_CONST = auto()
    BLOCK_LET = auto()
    BLOCK_CONST = auto()
    PARAM = auto()
    CATCH = auto()
    LOOPVAR = auto()        # a four-part for header variable
    FOREACH = auto()        # a for-in / for-of variable or binding
    FUNCTION = auto()       # a function's own name
    NAMED = auto()          # CreateNamedVariable
    LABEL = auto()
    THIS = auto()
    COUNTER = auto()        # a repeat loop's counter: never assigned
    CODE = auto()           # the string of a code-string block


CONST_STORAGE = frozenset({Storage.TOP_CONST, Storage.LOCAL_CONST, Storage.BLOCK_CONST})
NOT_ASSIGNABLE = CONST_STORAGE | {Storage.TEMP, Storage.LABEL, Storage.THIS, Storage.COUNTER, Storage.CODE}
NOT_A_VALUE = frozenset({Storage.LABEL})
DECLARED = frozenset(Storage) - {Storage.TEMP, Storage.LABEL, Storage.CODE}


class ActivationReason(Enum):          # Rhino b95f75d, see docs §5.3
    NESTED_FUNCTION = "nested function (IRFactory.java:1586)"
    CATCH = "catch clause (IRFactory.java:2035)"
    WITH = "with statement (IRFactory.java:2077)"
    EVAL = "direct eval (Parser.java:4852)"
    ARGUMENTS = "arguments (Parser.java:4836)"
    YIELD = "yield (Parser.java:2432)"
    SUPER = "super (IRFactory.java:205)"


class RhinoType(Enum):                 # Optimizer.java:15-17
    NUMBER = "number"
    ANY = "any"


@dataclass
class RhinoModel:
    """What Rhino's compiled mode is predicted to do (§5.3)."""
    activation: dict[int, tuple[ActivationReason, ...]] = field(default_factory=dict)   # function begin index -> reasons
    number_var: frozenset[Var] = frozenset()
    direct_call_target: frozenset[Var] = frozenset()
    direct_call_site: frozenset[int] = frozenset()
    expr_type: dict[int, RhinoType] = field(default_factory=dict)                      # value-producing instruction -> Rhino type

    def optimizer_runs(self, fn_index: int) -> bool:
        return not self.activation.get(fn_index, ())


@dataclass
class Analysis:
    code: tuple[Instruction, ...]
    visible_at: list[tuple[Var, ...]]              # in-scope variables before each instruction, most recent first
    context_at: list[Context]
    storage: dict[Var, Storage]
    defs: dict[Var, int]
    uses: dict[Var, list[int]]
    assignments: dict[Var, list[int]]              # definition first, then every reassignment
    function_of: list[int | None]                  # enclosing subroutine begin index per instruction
    strict_at: list[bool]
    labels: dict[Var, int]                          # label variable -> the block start that defines it
    types: dict[Var, Type]                          # join over the whole program
    returns: dict[Var, Type]                        # function variable -> return type
    rhino: RhinoModel
    _before: list[dict[Var, Type]]
    _after: list[dict[Var, Type]]
    _alias: dict[Var, Var]                          # for-loop header copies -> canonical loop variable

    @property
    def assignable(self) -> frozenset[Var]:
        return frozenset(v for v, s in self.storage.items() if s not in NOT_ASSIGNABLE)

    def __repr__(self) -> str:
        return f"<Analysis of {len(self.code)} instructions, {len(self.types)} variables>"

    def _repr_pretty_(self, p, cycle) -> None:    # Hypothesis prints dataclasses field by field unless told otherwise
        p.text(repr(self))

    @property
    def final_types(self) -> dict[Var, Type]:
        """Types at the end of the program (a function's full signature lives here)."""
        return self._after[-1] if self._after else {}

    def type_at(self, i: int, v: Var) -> Type:
        """The type of `v` as seen by instruction `i`: before it for inputs, after it for its outputs."""
        v = self._alias.get(v, v)
        if v in self._before[i]:
            return self._before[i][v]
        return self._after[i].get(v, ANY)

    def type_after(self, i: int, v: Var) -> Type:
        v = self._alias.get(v, v)
        return self._after[i].get(v, ANY)

    def num_uses(self, v: Var) -> int:
        return len(self.uses.get(v, ()))

    def num_assignments(self, v: Var) -> int:
        return len(self.assignments.get(v, ()))

    def is_declared(self, v: Var) -> bool:
        return self.storage.get(v, Storage.TEMP) in DECLARED

    def declared_visible_at(self, i: int) -> tuple[Var, ...]:
        return tuple(v for v in self.visible_at[i] if self.is_declared(v))

    def explain(self) -> str:
        """The IL with, per instruction, the types of its outputs, their storage, and the Rhino
        model's predictions (activation reasons on functions, number variables, direct calls)."""
        r = self.rhino
        lines = []
        depth = 0
        for i, instr in enumerate(self.code):
            if instr.op.is_block_end:
                depth = max(0, depth - 1)
            notes = []
            for v in instr.all_outputs:
                s = self.storage.get(v, Storage.TEMP)
                tag = f"v{v}: {self.type_after(i, v)}"
                if s is not Storage.TEMP:
                    tag += f" [{s.name.lower()}]"
                if v in r.number_var:
                    tag += " number-var"
                if v in r.direct_call_target:
                    tag += " direct-call-target"
                notes.append(tag)
            if i in r.activation:
                reasons = r.activation[i]
                notes.append("activation: " + (", ".join(x.name.lower() for x in reasons) if reasons else "none (optimizer runs)"))
            if i in r.direct_call_site:
                notes.append("direct call")
            if instr.lift is not None:
                notes.append(f"lift={instr.lift.value}")
            line = "    " * depth + f"{i:3}  {instr}"
            lines.append(line + (("    ; " + "; ".join(notes)) if notes else ""))
            if instr.op.is_block_start:
                depth += 1
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.explain()


# =============================================================================== structure
def declared_storage(kind: O.DeclarationKind, in_function: bool, in_block: bool) -> Storage:
    if kind is O.DeclarationKind.VAR:
        return Storage.LOCAL_VAR if in_function else Storage.TOP_VAR
    if kind is O.DeclarationKind.LET:
        return Storage.BLOCK_LET if in_block else (Storage.LOCAL_LET if in_function else Storage.TOP_LET)
    return Storage.BLOCK_CONST if in_block else (Storage.LOCAL_CONST if in_function else Storage.TOP_CONST)


def output_storage(op: Operation, in_function: bool, in_block: bool) -> Storage:
    if isinstance(op, (O.Declare, O.Destruct)):
        return declared_storage(op.kind, in_function, in_block)
    if isinstance(op, O.CreateNamedVariable):
        return Storage.NAMED
    if isinstance(op, O.FUNCTION_BEGINS):
        return Storage.FUNCTION
    if isinstance(op, O.BeginCodeString):
        return Storage.CODE
    return Storage.TEMP


def inner_storage(op: Operation, k: int) -> Storage:
    if isinstance(op, O.FUNCTION_BEGINS):
        return Storage.PARAM
    if isinstance(op, (O.BeginObjectLiteralMethod, O.BeginObjectLiteralComputedMethod, O.BeginObjectLiteralGetter,
                       O.BeginObjectLiteralComputedGetter, O.BeginObjectLiteralSetter, O.BeginObjectLiteralComputedSetter)):
        return Storage.THIS if k == 0 else Storage.PARAM
    if isinstance(op, O.BeginCatch):
        return Storage.CATCH
    if isinstance(op, (O.BeginForLoopCondition, O.BeginForLoopAfterthought)):
        return Storage.LOOPVAR
    if isinstance(op, O.BeginForLoopBody):
        return Storage.LOOPVAR if k < op.num_loop_variables else Storage.LABEL
    if isinstance(op, O.BeginForEachLoop):
        return Storage.FOREACH if k < op.inner_outputs() - 1 else Storage.LABEL
    if isinstance(op, O.BeginRepeatLoop):
        return Storage.COUNTER if (op.exposes_counter and k == 0) else Storage.LABEL
    return Storage.LABEL


def reassigns(op: Operation, k: int) -> bool:
    """Semantics.swift:46-56: which inputs an operation assigns."""
    if isinstance(op, (O.Reassign, O.Update)):
        return k == 0
    if isinstance(op, O.UnaryOperation):
        return op.op.reassigns_input
    if isinstance(op, O.DestructAndReassign):
        return 1 <= k <= O.num_bindings(op.pattern)
    return False


def _structure(code: Sequence[Instruction], strict: bool):
    visible_at: list[tuple[Var, ...]] = []
    storage: dict[Var, Storage] = {}
    defs: dict[Var, int] = {}
    uses: dict[Var, list[int]] = {}
    assignments: dict[Var, list[int]] = {}
    function_of: list[int | None] = []
    strict_at: list[bool] = []
    labels: dict[Var, int] = {}
    scopes: list[list[Var]] = [[]]
    frames: list[tuple[int | None, bool, int]] = [(None, strict, 0)]     # (function begin, strict, block depth in frame)
    ctx = contexts(code)

    for i, instr in enumerate(code):
        op = instr.op
        visible_at.append(tuple(v for s in reversed(scopes) for v in reversed(s)))
        function_of.append(frames[-1][0])
        strict_at.append(frames[-1][1])
        for v in instr.inputs:
            uses.setdefault(v, []).append(i)
        for k, v in enumerate(instr.inputs):
            if reassigns(op, k):
                assignments.setdefault(v, []).append(i)
        if op.is_block_end:
            scopes.pop()
            fn, st, depth = frames[-1]
            if depth == 0:
                frames.pop()
            else:
                frames[-1] = (fn, st, depth - 1)
        fn, st, depth = frames[-1]
        for v in instr.outputs:
            defs[v] = i
            assignments[v] = [i]
            storage[v] = output_storage(op, fn is not None, depth > 0)
            scopes[-1].append(v)
        if op.is_block_start:
            scopes.append([])
            if isinstance(op, O.SUBROUTINE_BEGINS):
                frames.append((i, st or bool(getattr(op, "strict", False)), 0))
            else:
                frames[-1] = (fn, st, depth + 1)
        for k, v in enumerate(instr.inner_outputs):
            defs[v] = i
            assignments[v] = [i]
            storage[v] = inner_storage(op, k)
            scopes[-1].append(v)
            if storage[v] is Storage.LABEL:
                labels[v] = i
    return visible_at, ctx, storage, defs, uses, assignments, function_of, strict_at, labels


# =============================================================================== typer
@dataclass
class _State:
    types: dict[Var, Type] = field(default_factory=dict)
    returns: Type = NOTHING
    yields: Type = NOTHING
    has_returned: bool = False
    is_subroutine: bool = False


class _Branches:
    """JSTyper's AnalyzerState: a stack of levels; each level holds the sibling branch states of
    one conditional construct; the newest state of the top level is active."""

    def __init__(self) -> None:
        self.levels: list[list[_State]] = [[_State()]]
        self.overall: dict[Var, Type] = {}
        self.alias: dict[Var, Var] = {}          # for-loop header copies -> the canonical loop variable

    @property
    def active(self) -> _State:
        return self.levels[-1][-1]

    @property
    def parent(self) -> _State | None:
        return self.levels[-2][-1] if len(self.levels) > 1 else None

    def type_of(self, v: Var) -> Type:
        return self.overall.get(self.alias.get(v, v), ANY)

    def set(self, v: Var, t: Type) -> None:
        v = self.alias.get(v, v)
        p = self.parent
        if p is not None and v not in p.types:
            p.types[v] = self.overall.get(v, NOTHING)
        self.active.types[v] = t
        self.overall[v] = t

    def start_group(self) -> None:
        self.levels.append([])

    def enter_branch(self, entry: Mapping[Var, Type] | None = None) -> None:
        """A new sibling branch: variables the previous sibling changed revert to the parent's types,
        then `entry` (if any) overrides them (JSTyper.swift:3136-3160, plus the entry map)."""
        level = self.levels[-1]
        if level:
            prev = level[-1]
            p = self.parent
            for v in prev.types:
                if p is not None and v in p.types and not p.types[v].is_nothing:
                    self.overall[v] = p.types[v]
                else:
                    self.overall.pop(v, None)
        level.append(_State())
        if entry:
            for v, t in entry.items():
                self.set(v, t)

    def snapshot(self) -> dict[Var, Type]:
        return dict(self.overall)

    def end_group(self) -> None:
        """Merge the sibling branches into the parent (JSTyper.swift:3251-3345)."""
        siblings = self.levels.pop()
        parent = self.active
        changed: dict[Var, list[Type]] = {}
        for s in siblings:
            for v, t in s.types.items():
                changed.setdefault(v, []).append(t)
        for v, ts in changed.items():
            new = NOTHING
            for t in ts:
                new = new.join(t)
            if len(ts) != len(siblings):
                new = new.join(parent.types.get(v, self.overall.get(v, NOTHING)))
            if new.is_nothing:
                continue
            self.set(v, new)
        rets = NOTHING
        ylds = NOTHING
        all_returned = bool(siblings)
        for s in siblings:
            rets = rets.join(s.returns)
            ylds = ylds.join(s.yields)
            all_returned = all_returned and s.has_returned
        parent.returns = parent.returns.join(rets)
        parent.yields = parent.yields.join(ylds)
        if all_returned and not parent.has_returned:
            parent.has_returned = True

    def start_subroutine(self) -> None:
        self.levels.append([_State()])          # the "not called" alternative
        self.levels[-1].append(_State(is_subroutine=True))

    def end_subroutine(self, default_return: Type) -> tuple[Type, Type]:
        body = self.active
        rets = body.returns if body.has_returned else body.returns.join(default_return)
        ylds = body.yields
        body.returns = NOTHING
        body.has_returned = False
        self.end_group()
        return rets, ylds

    def update_return(self, t: Type) -> None:
        s = self.active
        if s.has_returned:
            return
        s.returns = s.returns.join(t)
        s.has_returned = True

    def update_yield(self, t: Type) -> None:
        s = self.active
        if not s.has_returned:
            s.yields = s.yields.join(t)


def _maybe_bigint(t: Type, inputs: list[Type]) -> Type:
    if inputs and all(i.Is(BIGINT) for i in inputs):
        return BIGINT
    if any(i.MayBe(BIGINT) for i in inputs):
        return t.join(BIGINT)
    return t


def binary_result(op: O.BinaryOperator, a: Type, b: Type) -> Type:
    """JSTyper.swift:1961-1988 with the string refinement for ADD."""
    if op is O.BinaryOperator.ADD:
        if a.Is(STRING) or b.Is(STRING):
            return STRING
        if a.Is(NUMBER) and b.Is(NUMBER):
            return NUMBER
        return _maybe_bigint(PRIMITIVE, [a, b])
    if op in O.ARITHMETIC:
        return _maybe_bigint(NUMBER, [a, b])
    if op in O.BITWISE:
        return _maybe_bigint(INTEGER, [a, b])
    return a.join(b)


def unary_result(op: O.UnaryOperator, a: Type) -> Type:
    if op is O.UnaryOperator.LOGICAL_NOT:
        return BOOLEAN
    if op is O.UnaryOperator.BITWISE_NOT:
        return _maybe_bigint(INTEGER, [a])
    if op is O.UnaryOperator.PLUS:
        return NUMBER
    return _maybe_bigint(NUMBER, [a])


def property_type(recv: Type, key: Key, literal_props: Mapping[str, Mapping[Key, Type]]) -> Type:
    if recv.group and recv.group in literal_props and key in literal_props[recv.group]:
        return literal_props[recv.group][key]
    if key == LENGTH_KEY and (recv.Is(STRING) or recv.group in ("Array", "TypedArray", "Arguments")):
        return INTEGER
    group = recv.group or ("String" if recv.Is(STRING) else None)
    if group:
        for m in methods_of(group):
            if m.name == key.name:
                return Type.function(Signature(tuple(Param(p) for p in m.params), m.returns))
    return ANY


def constructs(callee: Type) -> Type:
    if callee.group and callee.group.endswith("Ctor"):
        name = callee.group[:-4]
        return INSTANCE_TYPE.get(name) or Type.object(INSTANCE_GROUP.get(name, name))
    return OBJECT


def calls(callee: Type) -> Type:
    if callee.group and callee.group.endswith("Ctor"):
        return CALLS.get(callee.group[:-4], ANY)
    if callee.signature is not None:
        return callee.signature.returns
    return ANY


def _join_states(pre: Mapping[Var, Type], states: Sequence[Mapping[Var, Type]]) -> dict[Var, Type]:
    out: dict[Var, Type] = {}
    keys = set(pre)
    for s in states:
        keys |= set(s)
    for v in keys:
        ty = pre.get(v, NOTHING)
        for s in states:
            if v in s:
                ty = ty.join(s[v])
        if not ty.is_nothing:
            out[v] = ty
    return out


def _previous_block_is(code: Sequence[Instruction], end: int, cls: type[Operation]) -> bool:
    depth = 0
    for j in range(end - 1, -1, -1):
        op = code[j].op
        if op.is_block_end and not op.is_block_start:
            depth += 1
        elif op.is_block_start:
            if depth == 0:
                return isinstance(op, cls)
            if not op.is_block_end:
                depth -= 1
    return False


def _header_condition(code: Sequence[Instruction], i: int) -> int:
    depth = 0
    for j in range(i - 1, -1, -1):
        op = code[j].op
        if op.is_block_end and not op.is_block_start:
            depth += 1
        elif op.is_block_start and not op.is_block_end:
            if depth == 0:
                raise InvalidProgram("for_header", "for-loop part without a header", i)
            depth -= 1
        elif op.is_block_start and op.is_block_end and depth == 0 and isinstance(op, O.BeginForLoopCondition):
            return j
    raise InvalidProgram("for_header", "for-loop part without a condition block", i)


def loop_pass_start(code: Sequence[Instruction], end: int) -> int:
    """The first block instruction of the loop ending at `end` whose typing a second pass repeats."""
    starts = {
        O.EndWhileLoop: O.BeginWhileLoopHeader, O.EndDoWhileLoop: O.BeginDoWhileLoopBody,
        O.EndForLoop: O.BeginForLoopCondition, O.EndForEachLoop: O.BeginForEachLoop,
        O.EndRepeatLoop: O.BeginRepeatLoop, O.EndCodeString: O.BeginCodeString,
    }
    target = starts[type(code[end].op)]
    depth = 0
    for j in range(end - 1, -1, -1):
        op = code[j].op
        if op.is_block_end and not op.is_block_start:
            depth += 1
        elif op.is_block_start and not op.is_block_end:
            if depth == 0:
                if isinstance(op, target):
                    return j
            else:
                depth -= 1
        elif op.is_block_start and op.is_block_end and depth == 0 and isinstance(op, target):
            return j
    raise InvalidProgram("block_mismatch", "loop end without its start", end)


def destruct_types(pattern: O.Pattern, source: Type, literal_props) -> list[Type]:
    out: list[Type] = []

    def target(tg, value: Type) -> None:
        if isinstance(tg, O.Binding):
            out.append(value)
        elif isinstance(tg, (O.ObjectPattern, O.ArrayPattern)):
            out.extend(destruct_types(tg, value, literal_props))

    if isinstance(pattern, O.ObjectPattern):
        for p in pattern.properties:
            target(p.target, property_type(source, p.key, literal_props) if p.key is not None else ANY)
        if pattern.has_rest:
            out.append(OBJECT)
    else:
        el = source.element or ANY
        for e in pattern.elements:
            if e.target is not None:
                target(e.target, el)
        if pattern.rest is not None:
            target(pattern.rest, ARRAY if isinstance(pattern.rest, O.Binding) else el)
    return out


_LOOP_ENDS = (O.EndWhileLoop, O.EndForEachLoop, O.EndRepeatLoop, O.EndForLoop, O.EndCodeString, O.EndDoWhileLoop)
_SUBROUTINE_ENDS = (O.EndPlainFunction, O.EndArrowFunction, O.EndGeneratorFunction, O.EndObjectLiteralMethod,
                    O.EndObjectLiteralComputedMethod, O.EndObjectLiteralGetter, O.EndObjectLiteralComputedGetter,
                    O.EndObjectLiteralSetter, O.EndObjectLiteralComputedSetter)


class Typer:
    """FuzzIL's per-operation typing over `_Branches`, one instruction at a time. `code` is the
    program so far (the builder's list or the analysis's tuple); `step` looks back into it."""

    def __init__(self, code: Sequence[Instruction], param_types: Mapping[int, tuple[Type, ...]] | None = None):
        self.code = code
        self.param_types: dict[int, tuple[Type, ...]] = dict(param_types or {})
        self.b = _Branches()
        self.returns: dict[Var, Type] = {}
        self.literal_props: dict[str, dict[Key, Type]] = {}
        self._literals: list[str] = []
        self._fns: list[tuple[int, Var | None]] = []
        self._switch_default: list[bool] = []
        self._tries: list[tuple[dict[Var, Type], list[dict[Var, Type]], bool]] = []
        self._groups = 0

    # -- queries
    def type_of(self, v: Var) -> Type:
        return self.b.type_of(v)

    def snapshot(self) -> dict[Var, Type]:
        return self.b.snapshot()

    @property
    def alias(self) -> dict[Var, Var]:
        return self.b.alias

    @property
    def current_literal_group(self) -> str | None:
        return self._literals[-1] if self._literals else None

    # -- one instruction
    def step(self, i: int, instr: Instruction) -> None:
        b = self.b
        op = instr.op
        code = self.code
        ins = [b.type_of(v) for v in instr.inputs]

        def out(ty: Type) -> None:
            b.set(instr.output, ty)

        # scope changes before the instruction's own outputs (JSTyper processScopeChanges)
        if isinstance(op, O.BeginIf):
            b.start_group()
            b.enter_branch()
        elif isinstance(op, O.BeginElse):
            b.enter_branch()
        elif isinstance(op, O.EndIf):
            if len(b.levels[-1]) == 1:
                b.enter_branch()
            b.end_group()
        elif isinstance(op, O.BeginSwitch):
            b.set(instr.inner_output(), LABEL)      # before the group opens: the label lives outside the cases
            b.start_group()
            self._switch_default.append(False)
        elif isinstance(op, (O.BeginSwitchCase, O.BeginSwitchDefaultCase)):
            b.enter_branch()
            if isinstance(op, O.BeginSwitchDefaultCase):
                self._switch_default[-1] = True
        elif isinstance(op, O.EndSwitch):
            if not self._switch_default.pop():
                b.enter_branch()
            b.end_group()
        elif isinstance(op, (O.BeginWhileLoopBody, O.BeginForEachLoop, O.BeginRepeatLoop, O.BeginCodeString,
                             O.BeginForLoopAfterthought)):
            b.start_group()
            b.enter_branch()          # never runs
            b.enter_branch()          # runs
        elif isinstance(op, O.BeginTry):
            b.start_group()
            self._tries.append((b.snapshot(), [], False))
            b.enter_branch()
        elif isinstance(op, O.BeginCatch):
            pre, exits, _ = self._tries[-1]
            exits.append(b.snapshot())
            self._tries[-1] = (pre, exits, True)
            b.enter_branch(_join_states(pre, exits))
        elif isinstance(op, O.BeginFinally):
            pre, exits, has_catch = self._tries[-1]
            exits.append(b.snapshot())
            if not has_catch:
                b.enter_branch(_join_states(pre, exits))
            b.end_group()
            b.start_group()            # the finally block runs unconditionally: a single branch
            b.enter_branch()
        elif isinstance(op, O.EndTryCatchFinally):
            if self._tries[-1][2] or _previous_block_is(code, i, O.BeginFinally):
                b.end_group()
            else:
                b.enter_branch()
                b.end_group()
            self._tries.pop()
        elif isinstance(op, O.SUBROUTINE_BEGINS):
            b.start_subroutine()

        # the instruction's own effects (processTypeChangesAfterScopeChanges)
        if isinstance(op, O.SUBROUTINE_BEGINS):
            params = list(getattr(op, "param_types", ()) or self.param_types.get(i, ()))
            inner = list(instr.inner_outputs)
            if not isinstance(op, O.FUNCTION_BEGINS):
                b.set(inner.pop(0), Type.object(self._literals[-1]) if self._literals else OBJECT)
            if isinstance(op, (O.BeginObjectLiteralSetter, O.BeginObjectLiteralComputedSetter)):
                params = [ANY]
            plist = getattr(op, "parameters", O.Parameters(0))
            for k, v in enumerate(inner):
                ty = params[k] if k < len(params) else ANY
                if plist.has_rest and k == plist.count - 1 and not plist.destructuring:
                    ty = ARRAY
                b.set(v, ty)
            if isinstance(op, O.FUNCTION_BEGINS):
                sig = Signature(tuple(Param(params[k] if k < len(params) else ANY) for k in range(plist.count)), ANY)
                out(Type.function(sig))
                self._fns.append((i, instr.output))
            else:
                self._fns.append((i, None))
        elif isinstance(op, _SUBROUTINE_ENDS):
            begin_i, fvar = self._fns.pop()
            rets, ylds = b.end_subroutine(UNDEFINED)
            begin = code[begin_i].op
            if fvar is not None:
                ft = b.type_of(fvar)
                if isinstance(begin, O.BeginGeneratorFunction):
                    result = GENERATOR_OBJECT.with_element(ylds if not ylds.is_nothing else ANY)
                else:
                    result = rets if not rets.is_nothing else UNDEFINED
                if ft.Is(FUNCTION) and ft.signature is not None:
                    b.set(fvar, ft.with_signature(Signature(ft.signature.params, result)))
                self.returns[fvar] = result
            if self._literals and isinstance(begin, (O.BeginObjectLiteralMethod, O.BeginObjectLiteralGetter)):
                value = rets if not rets.is_nothing else UNDEFINED
                self.literal_props[self._literals[-1]][begin.key] = \
                    Type.function(Signature((), value)) if isinstance(begin, O.BeginObjectLiteralMethod) else value
        elif isinstance(op, O.LoadInteger):
            out(INTEGER)
        elif isinstance(op, O.LoadBigInt):
            out(BIGINT)
        elif isinstance(op, O.LoadFloat):
            out(FLOAT)
        elif isinstance(op, O.LoadString):
            out(STRING)
        elif isinstance(op, O.LoadBoolean):
            out(BOOLEAN)
        elif isinstance(op, O.LoadUndefined):
            out(UNDEFINED)
        elif isinstance(op, O.LoadNull):
            out(NULL)
        elif isinstance(op, O.LoadThis):
            out(OBJECT)
        elif isinstance(op, O.LoadArguments):
            out(ARGUMENTS)
        elif isinstance(op, O.LoadNewTarget):
            out(FUNCTION.join(UNDEFINED))
        elif isinstance(op, O.LoadRegExp):
            out(REGEXP)
        elif isinstance(op, O.LoadBuiltin):
            out(op.builtin.type)
        elif isinstance(op, (O.CreateNamedVariable, O.Declare)):
            out(ins[0])
        elif isinstance(op, O.BeginObjectLiteral):
            self._groups += 1
            group = f"_Object{self._groups}"
            self.literal_props[group] = {}
            self._literals.append(group)
        elif isinstance(op, O.ObjectLiteralAddProperty):
            self.literal_props[self._literals[-1]][op.key] = ins[0]
        elif isinstance(op, O.ObjectLiteralAddElement):
            self.literal_props[self._literals[-1]][Key(str(op.index), KeyKind.INDEX)] = ins[0]
        elif isinstance(op, O.ObjectLiteralSetPrototype):
            self.literal_props[self._literals[-1]][Key("__proto__", KeyKind.PROTO)] = ins[0]
        elif isinstance(op, O.EndObjectLiteral):
            group = self._literals.pop()
            out(Type.object(group, frozenset(self.literal_props[group])))
        elif isinstance(op, O.CreateArray):
            el = NOTHING
            for x in ins:
                el = el.join(x)
            out(Type.array(None if el.is_nothing else el))
        elif isinstance(op, O.CreateIntArray):
            out(Type.array(INTEGER))
        elif isinstance(op, O.CreateFloatArray):
            out(Type.array(FLOAT))
        elif isinstance(op, O.CreateArrayWithSpread):
            el = NOTHING
            for x, spread in zip(ins, op.spreads):
                el = el.join((x.element or ANY) if spread else x)
            out(Type.array(None if el.is_nothing or el == ANY else el))
        elif isinstance(op, O.CreateTemplateString):
            out(STRING)
        elif isinstance(op, O.GetProperty):
            out(property_type(ins[0], op.key, self.literal_props))
        elif isinstance(op, (O.SetProperty, O.UpdateProperty, O.ConfigureProperty)):
            if ins[0].Is(OBJECT) or ins[0].Is(FUNCTION):
                b.set(instr.input(0), ins[0].with_prop(op.key))
        elif isinstance(op, O.DeleteProperty):
            if ins[0].Is(OBJECT):
                b.set(instr.input(0), ins[0].without_prop(op.key))
            out(BOOLEAN)
        elif isinstance(op, (O.GetElement, O.GetComputedProperty)):
            out(ins[0].element if ins[0].element is not None else ANY)
        elif isinstance(op, (O.DeleteElement, O.DeleteComputedProperty)):
            out(BOOLEAN)
        elif isinstance(op, O.TypeOf):
            out(STRING)
        elif isinstance(op, O.Void):
            out(UNDEFINED)
        elif isinstance(op, (O.TestInstanceOf, O.TestIn, O.Compare)):
            out(BOOLEAN)
        elif isinstance(op, O.Return):
            b.update_return(ins[0] if ins else UNDEFINED)
        elif isinstance(op, O.Yield):
            b.update_yield(ins[0] if ins else UNDEFINED)
            out(ANY)
        elif isinstance(op, O.YieldEach):
            b.update_yield(ins[0].element or ANY)
        elif isinstance(op, (O.CallFunction, O.CallFunctionWithSpread)):
            out(calls(ins[0]))
        elif isinstance(op, (O.Construct, O.ConstructWithSpread)):
            out(constructs(ins[0]))
        elif isinstance(op, (O.CallMethod, O.CallMethodWithSpread)):
            out(op.method.returns)
        elif isinstance(op, (O.CallComputedMethod, O.CallComputedMethodWithSpread, O.CallSuperMethod,
                             O.GetSuperProperty, O.GetComputedSuperProperty)):
            out(ANY)
        elif isinstance(op, O.UnaryOperation):
            r = unary_result(op.op, ins[0])
            if op.op.reassigns_input:
                b.set(instr.input(0), r)
            out(r)
        elif isinstance(op, O.BinaryOperation):
            out(binary_result(op.op, ins[0], ins[1]))
        elif isinstance(op, O.TernaryOperation):
            out(ins[1].join(ins[2]))
        elif isinstance(op, O.Reassign):
            b.set(instr.input(0), ins[1])
        elif isinstance(op, O.Update):
            b.set(instr.input(0), binary_result(op.op, ins[0], ins[1]))
        elif isinstance(op, O.Destruct):
            for v, ty in zip(instr.outputs, destruct_types(op.pattern, ins[0], self.literal_props)):
                b.set(v, ty)
        elif isinstance(op, O.DestructAndReassign):
            nb = O.num_bindings(op.pattern)
            for v, ty in zip(instr.inputs[1:1 + nb], destruct_types(op.pattern, ins[0], self.literal_props)):
                b.set(v, ty)
        elif isinstance(op, O.BeginForLoopCondition):
            for v, ty in zip(instr.inner_outputs, ins):
                # on a second pass the loop variable also carries what the afterthought and body made of it
                b.set(v, ty.join(b.overall[v]) if v in b.overall else ty)
        elif isinstance(op, (O.BeginForLoopAfterthought, O.BeginForLoopBody)):
            cond_vars = code[_header_condition(code, i)].inner_outputs
            inner = list(instr.inner_outputs)
            if isinstance(op, O.BeginForLoopBody):
                b.set(inner.pop(), LABEL)
            for v, cv in zip(inner, cond_vars):
                b.alias[v] = cv
        elif isinstance(op, O.BeginForEachLoop):
            inner = list(instr.inner_outputs)
            b.set(inner.pop(), LABEL)
            if op.pattern is None:
                b.set(inner[0], STRING if op.kind is O.ForEachKind.IN else (ins[0].element or ANY))
            else:
                for v, ty in zip(inner, destruct_types(op.pattern, ins[0].element or ANY, self.literal_props)):
                    b.set(v, ty)
        elif isinstance(op, O.BeginRepeatLoop):
            inner = list(instr.inner_outputs)
            b.set(inner[-1], LABEL)
            if op.exposes_counter:
                b.set(inner[0], INTEGER)
        elif isinstance(op, (O.BeginIf, O.BeginElse, O.BeginBlockStatement, O.BeginWhileLoopBody, O.BeginDoWhileLoopBody)):
            for v in instr.inner_outputs:
                b.set(v, LABEL)
        elif isinstance(op, O.BeginCatch):
            b.set(instr.inner_output(), ANY)
        elif isinstance(op, O.BeginCodeString):
            out(STRING)

        # scope changes after
        if isinstance(op, _LOOP_ENDS) and not isinstance(op, O.EndDoWhileLoop):
            b.end_group()


def _types(code: Sequence[Instruction], param_types: Mapping[int, tuple[Type, ...]]):
    """The batch typer: one `Typer` driven over the code, with a second pass per loop and
    snapshots joined across passes so a type "at a point" covers a later iteration too."""
    n = len(code)
    before: list[dict[Var, Type]] = [dict() for _ in range(n)]
    after: list[dict[Var, Type]] = [dict() for _ in range(n)]
    joined: dict[Var, Type] = {}
    typer = Typer(code, param_types)
    loop_passes: set[int] = set()

    def record(slot: list[dict[Var, Type]], i: int) -> None:
        snap = typer.snapshot()
        slot[i] = _join_states(slot[i], [snap]) if slot[i] else snap

    i = 0
    while i < n:
        instr = code[i]
        record(before, i)
        typer.step(i, instr)
        if isinstance(instr.op, _LOOP_ENDS) and not isinstance(instr.op, O.EndCodeString):
            begin_i = loop_pass_start(code, i)
            if begin_i not in loop_passes:
                loop_passes.add(begin_i)
                record(after, i)
                i = begin_i
                continue
        record(after, i)
        for v, ty in after[i].items():
            joined[v] = joined.get(v, NOTHING).join(ty)
        i += 1
    return before, after, joined, dict(typer.returns), dict(typer.alias)


# =============================================================================== Rhino model
def _safe_block_end(code: Sequence[Instruction], head: int) -> int:
    try:
        return block_end(code, head)
    except ValueError:
        return len(code)


def rhino_model(code: Sequence[Instruction], storage: Mapping[Var, Storage], defs: Mapping[Var, int],
                assignments: Mapping[Var, Sequence[int]], function_of: Sequence[int | None]) -> RhinoModel:
    """§5.3: activation, number variables, direct calls. Works on open (unfinished) code too,
    treating an open block as ending at the end of the code."""
    model = RhinoModel()
    functions = [i for i, instr in enumerate(code) if isinstance(instr.op, O.SUBROUTINE_BEGINS)]
    ends = {i: _safe_block_end(code, i) for i in functions}

    for f in functions:
        reasons: list[ActivationReason] = []
        j = f + 1
        while j < ends[f]:
            op = code[j].op
            if isinstance(op, O.SUBROUTINE_BEGINS):
                if ActivationReason.NESTED_FUNCTION not in reasons:
                    reasons.append(ActivationReason.NESTED_FUNCTION)
                j = _safe_block_end(code, j) + 1
                continue
            r = _activation_reason(code, j, defs)
            if r is not None and r not in reasons:
                reasons.append(r)
            j += 1
        fop = code[f].op
        if (isinstance(fop, O.BeginGeneratorFunction) or getattr(fop, "generator", False)) \
                and ActivationReason.YIELD not in reasons:
            reasons.append(ActivationReason.YIELD)
        model.activation[f] = tuple(reasons)

    number_vars: set[Var] = set()
    for f in functions:
        if model.activation[f]:
            continue
        current = {v for v, s in storage.items() if s is Storage.LOCAL_VAR and function_of[defs[v]] == f}
        changed = True
        while changed:
            changed = False
            for v in list(current):
                if any(_rhs_type(code, a, v, current, storage, defs) is RhinoType.ANY for a in assignments.get(v, [])):
                    current.discard(v)
                    changed = True
        number_vars |= current
    model.number_var = frozenset(number_vars)
    for i, instr in enumerate(code):
        if instr.has_value:
            model.expr_type[i] = _expr_type(code, i, model.number_var, storage, defs)

    targets: set[Var] = set()
    sites: set[int] = set()
    for f in functions:
        op = code[f].op
        if not (isinstance(op, O.BeginPlainFunction) and op.named and function_of[f] is None):
            continue
        if model.activation[f] or op.parameters.has_rest or op.parameters.count > 32:
            continue
        fvar = code[f].output
        if len(assignments.get(fvar, [])) > 1:
            continue
        for i, instr in enumerate(code):
            if isinstance(instr.op, O.CallFunction) and instr.input(0) == fvar and function_of[i] is not None \
                    and instr.op.num_arguments == op.parameters.count:
                sites.add(i)
                targets.add(fvar)
    model.direct_call_target = frozenset(targets)
    model.direct_call_site = frozenset(sites)
    return model


def _activation_reason(code: Sequence[Instruction], j: int, defs: Mapping[Var, int]) -> ActivationReason | None:
    op = code[j].op
    if isinstance(op, O.BeginCatch):
        return ActivationReason.CATCH
    if isinstance(op, O.BeginWith):
        return ActivationReason.WITH
    if isinstance(op, O.LoadArguments):
        return ActivationReason.ARGUMENTS
    if isinstance(op, (O.Yield, O.YieldEach)):
        return ActivationReason.YIELD
    if isinstance(op, (O.CallSuperMethod, O.GetSuperProperty, O.SetSuperProperty, O.SetComputedSuperProperty,
                       O.GetComputedSuperProperty, O.UpdateSuperProperty)):
        return ActivationReason.SUPER
    if isinstance(op, (O.CallFunction, O.CallFunctionWithSpread)):
        callee = code[defs[code[j].input(0)]].op
        if isinstance(callee, O.LoadBuiltin) and callee.builtin.name == "eval":
            return ActivationReason.EVAL
    return None


def _rhs_type(code, assign_i: int, v: Var, number_vars: set[Var], storage, defs) -> RhinoType:
    instr = code[assign_i]
    op = instr.op
    if isinstance(op, O.Declare):
        return _var_or_expr(code, instr.input(0), number_vars, storage, defs)
    if isinstance(op, O.Reassign):
        return _var_or_expr(code, instr.input(1), number_vars, storage, defs)
    if isinstance(op, O.Update):
        a = _var_or_expr(code, instr.input(0), number_vars, storage, defs)
        b = _var_or_expr(code, instr.input(1), number_vars, storage, defs)
        return _binary_rhino(op.op, a, b)
    if isinstance(op, O.UnaryOperation):
        return RhinoType.NUMBER
    return RhinoType.ANY


def _var_or_expr(code, v: Var, number_vars: set[Var], storage, defs) -> RhinoType:
    s = storage.get(v, Storage.TEMP)
    if s is Storage.TEMP:
        return _expr_type(code, defs[v], number_vars, storage, defs)
    return RhinoType.NUMBER if v in number_vars else RhinoType.ANY


def _binary_rhino(op: O.BinaryOperator, a: RhinoType, b: RhinoType) -> RhinoType:
    if op is O.BinaryOperator.ADD:
        return RhinoType.NUMBER if a is RhinoType.NUMBER and b is RhinoType.NUMBER else RhinoType.ANY
    if op in O.ARITHMETIC or op in O.BITWISE:
        return RhinoType.NUMBER
    if op in (O.BinaryOperator.LOGIC_AND, O.BinaryOperator.LOGIC_OR):
        return RhinoType.NUMBER if a is RhinoType.NUMBER and b is RhinoType.NUMBER else RhinoType.ANY
    return RhinoType.ANY


def _expr_type(code, i: int, number_vars, storage, defs) -> RhinoType:
    """Block.findExpressionType over one value-producing instruction."""
    instr = code[i]
    op = instr.op
    if isinstance(op, (O.LoadInteger, O.LoadFloat)):
        return RhinoType.NUMBER
    if isinstance(op, O.UnaryOperation):
        return RhinoType.ANY if op.op is O.UnaryOperator.LOGICAL_NOT else RhinoType.NUMBER
    if isinstance(op, O.BinaryOperation):
        return _binary_rhino(op.op, _var_or_expr(code, instr.input(0), number_vars, storage, defs),
                             _var_or_expr(code, instr.input(1), number_vars, storage, defs))
    if isinstance(op, O.TernaryOperation):
        a = _var_or_expr(code, instr.input(1), number_vars, storage, defs)
        b = _var_or_expr(code, instr.input(2), number_vars, storage, defs)
        return RhinoType.NUMBER if a is RhinoType.NUMBER and b is RhinoType.NUMBER else RhinoType.ANY
    if isinstance(op, (O.Declare, O.CreateNamedVariable)):
        return _var_or_expr(code, instr.input(0), number_vars, storage, defs)
    return RhinoType.ANY


# =============================================================================== entry points
def analyse(code: Sequence[Instruction], *, strict: bool = False,
            param_types: Mapping[int, tuple[Type, ...]] | None = None) -> Analysis:
    """Everything the analysis knows about a program. Accepts instructions, a Program or a Builder."""
    code = tuple(getattr(code, "code", code))
    check(code)
    visible_at, ctx, storage, defs, uses, assignments, function_of, strict_at, labels = _structure(code, strict)
    before, after, joined, returns, alias = _types(code, param_types or {})
    rhino = rhino_model(code, storage, defs, assignments, function_of)
    return Analysis(code, visible_at, ctx, storage, defs, uses, assignments, function_of, strict_at, labels,
                    joined, returns, rhino, before, after, alias)


_RECEIVER_READS = (O.GetProperty, O.DeleteProperty, O.GetElement, O.DeleteElement, O.GetComputedProperty,
                   O.DeleteComputedProperty, O.CallMethod, O.CallMethodWithSpread, O.CallComputedMethod,
                   O.CallComputedMethodWithSpread)
_RECEIVER_WRITES = (O.SetProperty, O.UpdateProperty, O.SetElement, O.UpdateElement, O.SetComputedProperty,
                    O.UpdateComputedProperty, O.ConfigureProperty, O.ConfigureElement, O.ConfigureComputedProperty)
LOOP_BODIES = (O.BeginWhileLoopBody, O.BeginDoWhileLoopBody, O.BeginForLoopBody, O.BeginForEachLoop, O.BeginRepeatLoop)
_LOOP_BODIES = LOOP_BODIES     # the block begins a loop label belongs to (break/continue targets)


def explain(code, *, strict: bool = False) -> str:
    """`analyse(code).explain()`: the IL annotated with types, storage and the Rhino model."""
    return analyse(code, strict=strict).explain()


def verify(code: Sequence[Instruction], *, strict: bool = False,
           param_types: Mapping[int, tuple[Type, ...]] | None = None) -> Analysis:
    """`analyse` plus oxpecker's requirements (design §5.4). Raises InvalidProgram."""
    an = analyse(code, strict=strict, param_types=param_types)
    code = an.code
    literal_protos: list[int] = []
    switch_defaults: list[int] = []
    for i, instr in enumerate(code):
        op = instr.op
        for k, v in enumerate(instr.inputs):
            if reassigns(op, k) and an.storage.get(v, Storage.TEMP) in NOT_ASSIGNABLE:
                raise InvalidProgram("assignment", f"v{v} ({an.storage.get(v, Storage.TEMP).name}) is not assignable", i)
            if an.storage.get(v) is Storage.LABEL and not isinstance(op, (O.LoopBreak, O.LoopContinue, O.BlockBreak)):
                raise InvalidProgram("label", f"label v{v} used as a value", i)
        if isinstance(op, (O.LoopBreak, O.LoopContinue, O.BlockBreak)) and instr.inputs:
            target = instr.input(0)
            if an.storage.get(target) is not Storage.LABEL:
                raise InvalidProgram("label", "break/continue target is not a label", i)
            if isinstance(op, (O.LoopBreak, O.LoopContinue)) and not isinstance(code[an.labels[target]].op, _LOOP_BODIES):
                raise InvalidProgram("label", "loop break/continue needs a loop label", i)
        if isinstance(op, (O.CallFunction, O.CallFunctionWithSpread, O.Construct, O.ConstructWithSpread)):
            if not an.type_at(i, instr.input(0)).MayBe(FUNCTION):
                raise InvalidProgram("type", f"v{instr.input(0)} is never a function", i)
        if isinstance(op, _RECEIVER_READS):
            if not op.receiver_optional and an.type_at(i, instr.input(0)).Is(NULLISH):
                raise InvalidProgram("type", f"property access on nullish v{instr.input(0)}", i)
        if isinstance(op, _RECEIVER_WRITES) and an.type_at(i, instr.input(0)).Is(NULLISH):
            raise InvalidProgram("type", f"property write on nullish v{instr.input(0)}", i)
        if isinstance(op, O.BeginObjectLiteral):
            literal_protos.append(0)
        elif isinstance(op, O.ObjectLiteralSetPrototype):
            literal_protos[-1] += 1
            if literal_protos[-1] > 1:
                raise InvalidProgram("singular", "two __proto__ entries in one object literal", i)
        elif isinstance(op, O.EndObjectLiteral):
            literal_protos.pop()
        elif isinstance(op, O.BeginSwitch):
            switch_defaults.append(0)
        elif isinstance(op, O.BeginSwitchDefaultCase):
            switch_defaults[-1] += 1
            if switch_defaults[-1] > 1:
                raise InvalidProgram("singular", "two default cases", i)
        elif isinstance(op, O.EndSwitch):
            switch_defaults.pop()
        if isinstance(op, O.BeginWith) and an.strict_at[i]:
            raise InvalidProgram("strict", "with in strict code", i)
        if isinstance(op, O.Directive) and op.kind is O.DirectiveKind.USE_STRICT and an.function_of[i] is not None:
            params = getattr(code[an.function_of[i]].op, "parameters", None)
            if params is not None and (params.has_rest or params.default_indices or params.destructuring):
                raise InvalidProgram("strict", "a strict directive needs a simple parameter list (no rest, defaults or patterns)", i)
    return an
