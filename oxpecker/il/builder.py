# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/Base/ProgramBuilder.swift at commit be2811d00282320381fc8007d64873745772011d:
# the emitting core (emit/internalAppend/analyze, 2868-2902, 7863-8153), hidden variables
# (1444-1503, 7920-7957), the block helpers (3079-3086, 3873-3991, 4450-4685) and the
# ObjectLiteral/SwitchBlock helper classes (2980-3076, 4474-4509). Randomness is left out: the
# builder never draws; generators draw and the builder emits (docs/design-generation-layer.md §6, §18).
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""The program builder: emits instructions, tracks scopes, context, strictness, hidden variables
and types as it goes, and answers the queries generators make (pools of visible variables, types,
the Rhino model). Every block construct is a context manager that emits its End on exit."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Iterator, Sequence

from . import ops as O
from .analysis import (
    DECLARED,
    NOT_A_VALUE,
    NOT_ASSIGNABLE,
    RhinoModel,
    Storage,
    Typer,
    inner_storage,
    output_storage,
    reassigns,
    rhino_model,
)
from .environment import Builtin, Method
from .ops import Context, Lift, Operation
from .options import GenOptions
from .program import Instruction, Var, check
from .types import ANY, Key, Type


class BuilderError(Exception):
    """A generator asked the builder for something the IL cannot express here."""


@dataclass(frozen=True)
class Pool:
    exact: tuple[Var, ...]      # statically of the type
    maybe: tuple[Var, ...]      # may be of the type (unknowns included), not exact
    all: tuple[Var, ...]        # every usable visible variable


_HIDING_CONTEXTS = Context.SUBROUTINE | Context.OBJECT_LITERAL | Context.SWITCH_BLOCK


class Builder:
    def __init__(self, options: GenOptions | None = None, *, start_var: int = 0):
        self.options = options or GenOptions()
        self.code: list[Instruction] = []
        self._next_var = start_var
        self._scopes: list[list[Var]] = [[]]
        self._hidden: set[Var] = set()
        self._hidden_by_scope: list[list[Var]] = [[]]
        self._contexts: list[Context] = [Context.JAVASCRIPT]
        self._frames: list[tuple[int | None, bool, int]] = [(None, self.options.strict, 0)]   # (fn begin, strict, block depth)
        self.storage: dict[Var, Storage] = {}
        self.defs: dict[Var, int] = {}
        self.labels: dict[Var, int] = {}
        self.assignments: dict[Var, list[int]] = {}
        self.function_of: list[int | None] = []
        self.typer = Typer(self.code)
        self._literals: list[ObjectLiteralFrame] = []
        self._switches: list[SwitchFrame] = []
        self.depth = self.options.max_depth      # nesting budget, managed by the strategies
        self.default_kind = O.DeclarationKind.VAR   # the kind `declare()` uses when none is given (G25)
        self.current_gen = ""
        self.origin = 0
        self.step = 0
        self._rhino_cache: tuple[int, RhinoModel] | None = None

    # ------------------------------------------------------------------ state queries
    @property
    def context(self) -> Context:
        return self._contexts[-1]

    @property
    def strict(self) -> bool:
        return self._frames[-1][1]

    @property
    def in_function(self) -> bool:
        return self._frames[-1][0] is not None

    @property
    def current_function(self) -> int | None:
        return self._frames[-1][0]

    @property
    def next_index(self) -> int:
        return len(self.code)

    @property
    def current_literal(self) -> ObjectLiteralFrame:
        if not self._literals:
            raise BuilderError("no open object literal")
        return self._literals[-1]

    @property
    def current_switch(self) -> SwitchFrame:
        if not self._switches:
            raise BuilderError("no open switch")
        return self._switches[-1]

    def type_of(self, v: Var) -> Type:
        return self.typer.type_of(v)

    @property
    def rhino(self) -> RhinoModel:
        if self._rhino_cache is None or self._rhino_cache[0] != len(self.code):
            self._rhino_cache = (len(self.code), rhino_model(self.code, self.storage, self.defs, self.assignments, self.function_of))
        return self._rhino_cache[1]

    def in_scope(self, v: Var) -> bool:
        return any(v in s for s in self._scopes)

    def is_declared(self, v: Var) -> bool:
        return self.storage.get(v, Storage.TEMP) in DECLARED

    def is_assignable(self, v: Var) -> bool:
        return self.storage.get(v, Storage.TEMP) not in NOT_ASSIGNABLE

    def visible(self, *, assignable: bool | None = None, declared: bool | None = None) -> list[Var]:
        """Usable visible variables, most recent first: in scope, not hidden, not a label."""
        out = []
        for scope in reversed(self._scopes):
            for v in reversed(scope):
                if v in self._hidden or self.storage.get(v, Storage.TEMP) in NOT_A_VALUE:
                    continue
                if assignable is not None and self.is_assignable(v) != assignable:
                    continue
                if declared is not None and self.is_declared(v) != declared:
                    continue
                out.append(v)
        return out

    def pool(self, t: Type, *, assignable: bool | None = None) -> Pool:
        vs = self.visible(assignable=assignable)
        exact = tuple(v for v in vs if self.type_of(v).Is(t))
        maybe = tuple(v for v in vs if v not in exact and self.type_of(v).MayBe(t))
        return Pool(exact, maybe, tuple(vs))

    def hide(self, v: Var) -> None:
        if not self.in_scope(v):
            raise BuilderError(f"cannot hide v{v}: not in scope")
        self._hidden.add(v)

    def unhide(self, v: Var) -> None:
        self._hidden.discard(v)

    # ------------------------------------------------------------------ emitting
    def _fresh(self) -> Var:
        v = Var(self._next_var)
        self._next_var += 1
        return v

    def emit(self, op: Operation, inputs: Sequence[Var] = (), *, lift: Lift | None = None) -> Instruction:
        inputs = tuple(inputs)
        if len(inputs) != op.inputs():
            raise BuilderError(f"{op.name} takes {op.inputs()} inputs, got {len(inputs)}")
        for v in inputs:
            if not self.in_scope(v):
                raise BuilderError(f"v{v} is not visible here ({op.name})")
        if op.required() & ~self.context:
            raise BuilderError(f"{op.name} needs {op.required()} but the context is {self.context}")
        if op.is_block_end and not self._matches_open_block(op):
            raise BuilderError(f"{op.name} does not close the open block")
        outs = [self._fresh() for _ in range(op.outputs())]
        inner = [self._fresh() for _ in range(op.inner_outputs())]
        instr = Instruction(op, inputs + tuple(outs) + tuple(inner), index=len(self.code),
                            gen=self.current_gen, origin=self.origin, step=self.step, lift=lift)
        self._apply(instr)
        return instr

    def _matches_open_block(self, end: Operation) -> bool:
        depth = 0
        for instr in reversed(self.code):
            op = instr.op
            if op.is_block_end and not op.is_block_start:
                depth += 1
            elif op.is_block_start:
                if depth == 0:
                    return O.matches_end(op, end)
                if not op.is_block_end:
                    depth -= 1
        return False

    def _apply(self, instr: Instruction) -> None:
        """The analysers' bookkeeping for one appended instruction (ProgramBuilder.swift:7893-8153)."""
        op = instr.op
        i = len(self.code)
        self.code.append(instr)
        self.function_of.append(self._frames[-1][0])
        for k, v in enumerate(instr.inputs):
            if reassigns(op, k):
                self.assignments.setdefault(v, []).append(i)
        if op.is_block_end:
            closed = self._scopes.pop()
            for v in closed:
                self._hidden.discard(v)
            for v in self._hidden_by_scope.pop():
                self._hidden.discard(v)
            self._contexts.pop()
            fn, st, depth = self._frames[-1]
            if depth == 0:
                self._frames.pop()
            else:
                self._frames[-1] = (fn, st, depth - 1)
        fn, st, depth = self._frames[-1]
        for v in instr.outputs:
            self.defs[v] = i
            self.assignments[v] = [i]
            self.storage[v] = output_storage(op, fn is not None, depth > 0)
            self._scopes[-1].append(v)
        if op.is_block_start:
            new = op.opened()
            if op.propagates_context:
                new |= self._contexts[-1]
            if op.resumes_context:
                new |= self._contexts[-2]
            if op.opened() & (Context.SWITCH_BLOCK | Context.SWITCH_CASE):
                new &= ~Context.LOOP
            elif op.opened() & Context.LOOP:
                new &= ~(Context.SWITCH_BLOCK | Context.SWITCH_CASE)
            self._contexts.append(new)
            self._scopes.append([])
            hidden_now: list[Var] = []
            if op.opened() & _HIDING_CONTEXTS:
                for scope in self._scopes[:-1]:
                    for v in scope:
                        if self.storage.get(v) is Storage.LABEL and v not in self._hidden:
                            self._hidden.add(v)
                            hidden_now.append(v)
            self._hidden_by_scope.append(hidden_now)
            if isinstance(op, O.SUBROUTINE_BEGINS):
                self._frames.append((i, st or bool(getattr(op, "strict", False)), 0))
            else:
                self._frames[-1] = (fn, st, depth + 1)
        for k, v in enumerate(instr.inner_outputs):
            self.defs[v] = i
            self.assignments[v] = [i]
            self.storage[v] = inner_storage(op, k)
            self._scopes[-1].append(v)
            if self.storage[v] is Storage.LABEL:
                self.labels[v] = i
        self.typer.step(i, instr)

    def finish(self) -> tuple[Instruction, ...]:
        if len(self._scopes) != 1:
            raise BuilderError(f"{len(self._scopes) - 1} block(s) left open")
        code = tuple(self.code)
        check(code)
        return code

    @classmethod
    def at(cls, code: Sequence[Instruction], pos: int, options: GenOptions | None = None) -> Builder:
        """A builder positioned to emit before `code[pos]`: replays `code[:pos]` as it is. New
        variables are numbered above every variable of the whole program, so the caller renumbers
        after splicing the new instructions back in."""
        top = max((v for instr in code for v in instr.inouts), default=-1) + 1
        b = cls(options, start_var=top)
        for instr in code[:pos]:
            b._apply(replace(instr, index=len(b.code)))
        b.mark = len(b.code)
        return b

    def new_code(self) -> tuple[Instruction, ...]:
        return tuple(self.code[getattr(self, "mark", 0):])

    def replay(self, start: int, end: int, var_map: dict[Var, Var] | None = None) -> dict[Var, Var]:
        """Re-emit code[start:end] with fresh variables ("the same body twice", D22/D31): inputs are
        mapped through `var_map` (identity for variables defined outside the range), outputs get
        fresh variables, provenance is kept. Returns the mapping."""
        mapping = dict(var_map or {})
        for instr in list(self.code[start:end]):
            inputs = tuple(mapping.get(v, v) for v in instr.inputs)
            new = self.emit(instr.op, inputs, lift=instr.lift)
            for old, fresh in zip(instr.all_outputs, new.all_outputs):
                mapping[old] = fresh
            self.code[-1] = replace(new, gen=instr.gen, origin=instr.origin)
        return mapping

    # ------------------------------------------------------------------ value constructors
    def load_int(self, value: int, radix: O.Radix = O.Radix.DEC) -> Var:
        return self.emit(O.LoadInteger(value, radix)).output

    def load_float(self, value: float, style: O.FloatStyle = O.FloatStyle.PLAIN) -> Var:
        return self.emit(O.LoadFloat(value, style)).output

    def load_bigint(self, value: int) -> Var:
        return self.emit(O.LoadBigInt(value)).output

    def load_string(self, value: str) -> Var:
        return self.emit(O.LoadString(value)).output

    def load_bool(self, value: bool) -> Var:
        return self.emit(O.LoadBoolean(value)).output

    def load_undefined(self) -> Var:
        return self.emit(O.LoadUndefined()).output

    def load_null(self) -> Var:
        return self.emit(O.LoadNull()).output

    def load_regexp(self, pattern: str, flags: O.RegExpFlag = O.RegExpFlag.NONE) -> Var:
        return self.emit(O.LoadRegExp(pattern, flags)).output

    def builtin(self, b: Builtin) -> Var:
        return self.emit(O.LoadBuiltin(b)).output

    def this(self) -> Var:
        return self.emit(O.LoadThis()).output

    def arguments(self) -> Var:
        return self.emit(O.LoadArguments()).output

    def new_target(self) -> Var:
        return self.emit(O.LoadNewTarget()).output

    def declare(self, value: Var, kind: O.DeclarationKind | None = None) -> Var:
        """`var|let|const v = value`. Without `kind`, the builder's `default_kind` applies: the
        strategies draw it once per generator invocation (G25), so every declaration site gets a
        drawn kind without each generator drawing one. A generator that reassigns what it
        declares passes a mutable kind explicitly (a drawn const would not verify)."""
        return self.emit(O.Declare(kind or self.default_kind), [value]).output

    def named_variable(self, name: Key, mode: O.NamedVariableMode, value: Var) -> Var:
        return self.emit(O.CreateNamedVariable(name, mode), [value]).output

    # ------------------------------------------------------------------ operators
    def unary(self, op: O.UnaryOperator, x: Var) -> Var:
        return self.emit(O.UnaryOperation(op), [x]).output

    def binary(self, x: Var, y: Var, op: O.BinaryOperator) -> Var:
        return self.emit(O.BinaryOperation(op), [x, y]).output

    def compare(self, x: Var, y: Var, op: O.Comparator) -> Var:
        return self.emit(O.Compare(op), [x, y]).output

    def ternary(self, cond: Var, a: Var, b: Var) -> Var:
        return self.emit(O.TernaryOperation(), [cond, a, b]).output

    def typeof(self, x: Var) -> Var:
        return self.emit(O.TypeOf(), [x]).output

    def void(self, x: Var) -> Var:
        return self.emit(O.Void(), [x]).output

    def test_in(self, key: Var, obj: Var) -> Var:
        return self.emit(O.TestIn(), [key, obj]).output

    def test_instance_of(self, x: Var, ctor: Var) -> Var:
        return self.emit(O.TestInstanceOf(), [x, ctor]).output

    def reassign(self, target: Var, value: Var) -> None:
        self.emit(O.Reassign(), [target, value])

    def update(self, target: Var, value: Var, op: O.BinaryOperator) -> None:
        self.emit(O.Update(op), [target, value])

    # ------------------------------------------------------------------ properties
    def get_property(self, obj: Var, key: Key, *, optional: bool = False) -> Var:
        return self.emit(O.GetProperty(key, optional), [obj]).output

    def set_property(self, obj: Var, key: Key, value: Var) -> None:
        self.emit(O.SetProperty(key), [obj, value])

    def update_property(self, obj: Var, key: Key, value: Var, op: O.BinaryOperator) -> None:
        self.emit(O.UpdateProperty(key, op), [obj, value])

    def delete_property(self, obj: Var, key: Key, *, optional: bool = False) -> Var:
        return self.emit(O.DeleteProperty(key, optional), [obj]).output

    def configure_property(self, obj: Var, key: Key, flags: O.PropertyFlag, kind: O.PropertyType, *values: Var) -> None:
        self.emit(O.ConfigureProperty(key, flags, kind), [obj, *values])

    def get_element(self, obj: Var, index: int, *, optional: bool = False) -> Var:
        return self.emit(O.GetElement(index, optional), [obj]).output

    def set_element(self, obj: Var, index: int, value: Var) -> None:
        self.emit(O.SetElement(index), [obj, value])

    def update_element(self, obj: Var, index: int, value: Var, op: O.BinaryOperator) -> None:
        self.emit(O.UpdateElement(index, op), [obj, value])

    def delete_element(self, obj: Var, index: int, *, optional: bool = False) -> Var:
        return self.emit(O.DeleteElement(index, optional), [obj]).output

    def configure_element(self, obj: Var, index: int, flags: O.PropertyFlag, kind: O.PropertyType, *values: Var) -> None:
        self.emit(O.ConfigureElement(index, flags, kind), [obj, *values])

    def get_computed(self, obj: Var, key: Var, *, optional: bool = False) -> Var:
        return self.emit(O.GetComputedProperty(optional), [obj, key]).output

    def set_computed(self, obj: Var, key: Var, value: Var) -> None:
        self.emit(O.SetComputedProperty(), [obj, key, value])

    def update_computed(self, obj: Var, key: Var, value: Var, op: O.BinaryOperator) -> None:
        self.emit(O.UpdateComputedProperty(op), [obj, key, value])

    def delete_computed(self, obj: Var, key: Var, *, optional: bool = False) -> Var:
        return self.emit(O.DeleteComputedProperty(optional), [obj, key]).output

    def configure_computed(self, obj: Var, key: Var, flags: O.PropertyFlag, kind: O.PropertyType, *values: Var) -> None:
        self.emit(O.ConfigureComputedProperty(flags, kind), [obj, key, *values])

    # ------------------------------------------------------------------ calls
    def call_function(self, f: Var, args: Sequence[Var] = (), *, optional: bool = False) -> Var:
        return self.emit(O.CallFunction(len(args), optional), [f, *args]).output

    def call_function_spread(self, f: Var, args: Sequence[Var], spreads: Sequence[bool], *, optional: bool = False) -> Var:
        return self.emit(O.CallFunctionWithSpread(tuple(spreads), optional), [f, *args]).output

    def construct(self, ctor: Var, args: Sequence[Var] = ()) -> Var:
        return self.emit(O.Construct(len(args)), [ctor, *args]).output

    def construct_spread(self, ctor: Var, args: Sequence[Var], spreads: Sequence[bool]) -> Var:
        return self.emit(O.ConstructWithSpread(tuple(spreads)), [ctor, *args]).output

    def call_method(self, obj: Var, method: Method, args: Sequence[Var] = (), *,
                    receiver_optional: bool = False, call_optional: bool = False) -> Var:
        return self.emit(O.CallMethod(method, len(args), receiver_optional, call_optional), [obj, *args]).output

    def call_method_spread(self, obj: Var, method: Method, args: Sequence[Var], spreads: Sequence[bool], *,
                           receiver_optional: bool = False, call_optional: bool = False) -> Var:
        return self.emit(O.CallMethodWithSpread(method, tuple(spreads), receiver_optional, call_optional), [obj, *args]).output

    def call_computed_method(self, obj: Var, key: Var, args: Sequence[Var] = (), *,
                             receiver_optional: bool = False, call_optional: bool = False) -> Var:
        return self.emit(O.CallComputedMethod(len(args), receiver_optional, call_optional), [obj, key, *args]).output

    def call_computed_method_spread(self, obj: Var, key: Var, args: Sequence[Var], spreads: Sequence[bool], *,
                                    receiver_optional: bool = False, call_optional: bool = False) -> Var:
        return self.emit(O.CallComputedMethodWithSpread(tuple(spreads), receiver_optional, call_optional), [obj, key, *args]).output

    def call_super_method(self, key: Key, args: Sequence[Var] = ()) -> Var:
        return self.emit(O.CallSuperMethod(key, len(args)), list(args)).output

    def get_super_property(self, key: Key) -> Var:
        return self.emit(O.GetSuperProperty(key)).output

    def set_super_property(self, key: Key, value: Var) -> None:
        self.emit(O.SetSuperProperty(key), [value])

    def update_super_property(self, key: Key, value: Var, op: O.BinaryOperator) -> None:
        self.emit(O.UpdateSuperProperty(key, op), [value])

    def get_computed_super_property(self, key: Var) -> Var:
        return self.emit(O.GetComputedSuperProperty(), [key]).output

    def set_computed_super_property(self, key: Var, value: Var) -> None:
        self.emit(O.SetComputedSuperProperty(), [key, value])

    # ------------------------------------------------------------------ arrays, strings, destructuring
    def create_array(self, elements: Sequence[Var] = ()) -> Var:
        return self.emit(O.CreateArray(len(elements)), list(elements)).output

    def create_array_spread(self, elements: Sequence[Var], spreads: Sequence[bool]) -> Var:
        return self.emit(O.CreateArrayWithSpread(tuple(spreads)), list(elements)).output

    def create_int_array(self, values: Sequence[int]) -> Var:
        return self.emit(O.CreateIntArray(tuple(values))).output

    def create_float_array(self, values: Sequence[float]) -> Var:
        return self.emit(O.CreateFloatArray(tuple(values))).output

    def create_template(self, parts: Sequence[str], values: Sequence[Var]) -> Var:
        return self.emit(O.CreateTemplateString(tuple(parts)), list(values)).output

    def destruct(self, source: Var, pattern: O.Pattern, kind: O.DeclarationKind = O.DeclarationKind.VAR,
                 extra: Sequence[Var] = ()) -> tuple[Var, ...]:
        return self.emit(O.Destruct(pattern, kind), [source, *extra]).outputs

    def destruct_and_reassign(self, source: Var, pattern: O.Pattern, targets: Sequence[Var], extra: Sequence[Var] = ()) -> None:
        self.emit(O.DestructAndReassign(pattern), [source, *targets, *extra])

    # ------------------------------------------------------------------ statements
    def do_return(self, value: Var | None = None) -> None:
        self.emit(O.Return(value is not None), [value] if value is not None else [])

    def yield_(self, value: Var | None = None) -> Var:
        return self.emit(O.Yield(value is not None), [value] if value is not None else []).output

    def yield_each(self, value: Var) -> None:
        self.emit(O.YieldEach(), [value])

    def throw(self, value: Var) -> None:
        self.emit(O.ThrowException(), [value])

    def directive_strict(self) -> None:
        self.emit(O.Directive())

    def probe(self, label: O.ProbeLabel = O.ProbeLabel.STEP, number: int = 0) -> None:
        self.emit(O.Probe(label, number))

    def break_(self, label: Var | None = None) -> None:
        self.emit(O.LoopBreak(label is not None), [label] if label is not None else [])

    def continue_(self, label: Var | None = None) -> None:
        self.emit(O.LoopContinue(label is not None), [label] if label is not None else [])

    def block_break(self, label: Var) -> None:
        self.emit(O.BlockBreak(), [label])

    def switch_break(self) -> None:
        self.emit(O.SwitchBreak())

    # ------------------------------------------------------------------ blocks
    def _function(self, begin: Operation, defaults: Sequence[Var], types: Sequence[Type] | None, recursive: bool):
        if types is not None:
            begin = replace(begin, param_types=tuple(types))
        instr = self.emit(begin, list(defaults))
        f = instr.output
        if not recursive:
            self.hide(f)
        return f, instr.inner_outputs

    @contextmanager
    def plain_function(self, params: O.Parameters = O.Parameters(0), *, named: bool = False, strict: bool = False,
                       defaults: Sequence[Var] = (), types: Sequence[Type] | None = None,
                       recursive: bool = False) -> Iterator[tuple[Var, tuple[Var, ...]]]:
        f, args = self._function(O.BeginPlainFunction(params, named=named, strict=strict), defaults, types, recursive)
        yield f, args
        self.unhide(f)
        self.emit(O.EndPlainFunction())

    @contextmanager
    def arrow_function(self, params: O.Parameters = O.Parameters(0), *, defaults: Sequence[Var] = (),
                       types: Sequence[Type] | None = None, recursive: bool = False) -> Iterator[tuple[Var, tuple[Var, ...]]]:
        f, args = self._function(O.BeginArrowFunction(params), defaults, types, recursive)
        yield f, args
        self.unhide(f)
        self.emit(O.EndArrowFunction())

    @contextmanager
    def generator_function(self, params: O.Parameters = O.Parameters(0), *, named: bool = False, strict: bool = False,
                           defaults: Sequence[Var] = (), types: Sequence[Type] | None = None,
                           recursive: bool = False) -> Iterator[tuple[Var, tuple[Var, ...]]]:
        f, args = self._function(O.BeginGeneratorFunction(params, named=named, strict=strict), defaults, types, recursive)
        yield f, args
        self.unhide(f)
        self.emit(O.EndGeneratorFunction())

    @contextmanager
    def object_literal(self) -> Iterator[ObjectLiteralFrame]:
        self.emit(O.BeginObjectLiteral())
        frame = ObjectLiteralFrame(self)
        self._literals.append(frame)
        yield frame
        self._literals.pop()
        frame.result = self.emit(O.EndObjectLiteral()).output

    @contextmanager
    def if_(self, cond: Var, *, inverted: bool = False) -> Iterator[IfFrame]:
        instr = self.emit(O.BeginIf(inverted), [cond])
        frame = IfFrame(self, instr.inner_output())
        yield frame
        self.emit(O.EndIf())

    @contextmanager
    def while_loop(self) -> Iterator[WhileFrame]:
        self.emit(O.BeginWhileLoopHeader())
        frame = WhileFrame(self)
        yield frame
        if not frame.opened:
            raise BuilderError("a while loop needs loop.body(cond)")
        self.emit(O.EndWhileLoop())

    @contextmanager
    def do_while_loop(self) -> Iterator[DoWhileFrame]:
        instr = self.emit(O.BeginDoWhileLoopBody())
        frame = DoWhileFrame(self, instr.inner_output())
        yield frame
        if frame.cond is None:
            raise BuilderError("a do-while loop needs loop.header() with done(cond)")
        self.emit(O.EndDoWhileLoop(), [frame.cond])

    @contextmanager
    def for_loop(self) -> Iterator[ForFrame]:
        self.emit(O.BeginForLoopInitializer())
        frame = ForFrame(self)
        yield frame
        if frame.stage != 3:
            raise BuilderError("a for loop needs condition, afterthought and body")
        self.emit(O.EndForLoop())

    @contextmanager
    def for_each(self, kind: O.ForEachKind, iterable: Var, pattern: O.Pattern | None = None,
                 extra: Sequence[Var] = (), *,
                 declaration: O.DeclarationKind = O.DeclarationKind.VAR) -> Iterator[tuple[tuple[Var, ...], Var]]:
        instr = self.emit(O.BeginForEachLoop(kind, pattern, declaration), [iterable, *extra])
        inner = instr.inner_outputs
        yield inner[:-1], inner[-1]
        self.emit(O.EndForEachLoop())

    @contextmanager
    def repeat(self, iterations: int, *, counter: bool = True,
               declaration: O.DeclarationKind = O.DeclarationKind.LET) -> Iterator[tuple[Var | None, Var]]:
        instr = self.emit(O.BeginRepeatLoop(iterations, counter, declaration))
        inner = instr.inner_outputs
        yield (inner[0] if counter else None), inner[-1]
        self.emit(O.EndRepeatLoop())

    @contextmanager
    def try_(self) -> Iterator[TryFrame]:
        self.emit(O.BeginTry())
        frame = TryFrame(self)
        yield frame
        if not frame.handled:
            raise BuilderError("a try needs a catch or a finally")
        self.emit(O.EndTryCatchFinally())

    @contextmanager
    def switch(self, value: Var) -> Iterator[SwitchFrame]:
        instr = self.emit(O.BeginSwitch(), [value])
        frame = SwitchFrame(self, instr.inner_output())
        self._switches.append(frame)
        yield frame
        self._switches.pop()
        self.emit(O.EndSwitch())

    @contextmanager
    def block(self) -> Iterator[Var]:
        instr = self.emit(O.BeginBlockStatement())
        yield instr.inner_output()
        self.emit(O.EndBlockStatement())

    @contextmanager
    def with_(self, obj: Var) -> Iterator[None]:
        self.emit(O.BeginWith(), [obj])
        yield None
        self.emit(O.EndWith())

    @contextmanager
    def code_string(self) -> Iterator[CodeStringFrame]:
        instr = self.emit(O.BeginCodeString())
        frame = CodeStringFrame(instr.output)
        yield frame
        self.emit(O.EndCodeString())


# ------------------------------------------------------------------ frames
@dataclass
class CodeStringFrame:
    result: Var


class IfFrame:
    def __init__(self, b: Builder, label: Var):
        self.b = b
        self.label = label
        self.has_else = False

    @contextmanager
    def else_(self) -> Iterator[Var]:
        if self.has_else:
            raise BuilderError("an if has one else")
        self.has_else = True
        instr = self.b.emit(O.BeginElse())
        yield instr.inner_output()


class WhileFrame:
    def __init__(self, b: Builder):
        self.b = b
        self.opened = False

    @contextmanager
    def body(self, cond: Var) -> Iterator[Var]:
        if self.opened:
            raise BuilderError("one body per while loop")
        self.opened = True
        instr = self.b.emit(O.BeginWhileLoopBody(), [cond])
        yield instr.inner_output()


class DoWhileFrame:
    def __init__(self, b: Builder, label: Var):
        self.b = b
        self.label = label
        self.cond: Var | None = None
        self._header = False

    @contextmanager
    def header(self) -> Iterator[DoWhileFrame]:
        if self._header:
            raise BuilderError("one header per do-while loop")
        self._header = True
        self.b.emit(O.BeginDoWhileLoopHeader())
        yield self

    def done(self, cond: Var) -> None:
        self.cond = cond


class ForFrame:
    """`with b.for_loop() as loop:` then `loop.condition(initial)`, `loop.afterthought(cond)`,
    `loop.body()` in that order; the initial values are computed inside the `for_loop` block."""

    def __init__(self, b: Builder):
        self.b = b
        self.stage = 0
        self.n = 0

    @contextmanager
    def condition(self, initial: Sequence[Var], *,
                  declaration: O.DeclarationKind = O.DeclarationKind.LET) -> Iterator[tuple[Var, ...]]:
        if self.stage != 0:
            raise BuilderError("condition comes first")
        self.stage = 1
        self.n = len(initial)
        instr = self.b.emit(O.BeginForLoopCondition(self.n, declaration), list(initial))
        yield instr.inner_outputs

    @contextmanager
    def afterthought(self, cond: Var) -> Iterator[tuple[Var, ...]]:
        if self.stage != 1:
            raise BuilderError("afterthought comes after the condition")
        self.stage = 2
        instr = self.b.emit(O.BeginForLoopAfterthought(self.n), [cond])
        yield instr.inner_outputs

    @contextmanager
    def body(self) -> Iterator[tuple[tuple[Var, ...], Var]]:
        if self.stage != 2:
            raise BuilderError("body comes after the afterthought")
        self.stage = 3
        instr = self.b.emit(O.BeginForLoopBody(self.n))
        inner = instr.inner_outputs
        yield inner[:-1], inner[-1]


class TryFrame:
    def __init__(self, b: Builder):
        self.b = b
        self.handled = False
        self._catch = False
        self._finally = False

    @contextmanager
    def catch(self) -> Iterator[Var]:
        if self._catch or self._finally:
            raise BuilderError("catch comes once, before finally")
        self._catch = self.handled = True
        instr = self.b.emit(O.BeginCatch())
        yield instr.inner_output()

    @contextmanager
    def finally_(self) -> Iterator[None]:
        if self._finally:
            raise BuilderError("one finally per try")
        self._finally = self.handled = True
        self.b.emit(O.BeginFinally())
        yield None


class SwitchFrame:
    def __init__(self, b: Builder, label: Var):
        self.b = b
        self.label = label
        self.has_default = False

    @contextmanager
    def case(self, value: Var, *, falls_through: bool = False) -> Iterator[None]:
        self.b.emit(O.BeginSwitchCase(), [value])
        yield None
        self.b.emit(O.EndSwitchCase(falls_through))

    @contextmanager
    def default(self, *, falls_through: bool = False) -> Iterator[None]:
        if self.has_default:
            raise BuilderError("one default per switch")
        self.has_default = True
        self.b.emit(O.BeginSwitchDefaultCase())
        yield None
        self.b.emit(O.EndSwitchCase(falls_through))


class ObjectLiteralFrame:
    """ProgramBuilder.swift:2980-3076."""

    def __init__(self, b: Builder):
        self.b = b
        self.keys: set[Key] = set()
        self.elements: set[int] = set()
        self.has_prototype = False
        self.result: Var | None = None

    def add_property(self, key: Key, value: Var) -> None:
        self.keys.add(key)
        self.b.emit(O.ObjectLiteralAddProperty(key), [value])

    def add_element(self, index: int, value: Var) -> None:
        self.elements.add(index)
        self.b.emit(O.ObjectLiteralAddElement(index), [value])

    def add_computed(self, key: Var, value: Var) -> None:
        self.b.emit(O.ObjectLiteralAddComputedProperty(), [key, value])

    def copy_properties(self, obj: Var) -> None:
        self.b.emit(O.ObjectLiteralCopyProperties(), [obj])

    def set_prototype(self, proto: Var) -> None:
        if self.has_prototype:
            raise BuilderError("one __proto__ per object literal")
        self.has_prototype = True
        self.b.emit(O.ObjectLiteralSetPrototype(), [proto])

    @contextmanager
    def method(self, key: Key, params: O.Parameters = O.Parameters(0), *, generator: bool = False,
               defaults: Sequence[Var] = (), types: Sequence[Type] | None = None) -> Iterator[tuple[Var, tuple[Var, ...]]]:
        self.keys.add(key)
        instr = self.b.emit(O.BeginObjectLiteralMethod(key, params, tuple(types or ()), generator), list(defaults))
        inner = instr.inner_outputs
        yield inner[0], inner[1:]
        self.b.emit(O.EndObjectLiteralMethod())

    @contextmanager
    def computed_method(self, key: Var, params: O.Parameters = O.Parameters(0), *, generator: bool = False,
                        defaults: Sequence[Var] = (), types: Sequence[Type] | None = None) -> Iterator[tuple[Var, tuple[Var, ...]]]:
        instr = self.b.emit(O.BeginObjectLiteralComputedMethod(params, tuple(types or ()), generator), [key, *defaults])
        inner = instr.inner_outputs
        yield inner[0], inner[1:]
        self.b.emit(O.EndObjectLiteralComputedMethod())

    @contextmanager
    def getter(self, key: Key) -> Iterator[Var]:
        self.keys.add(key)
        instr = self.b.emit(O.BeginObjectLiteralGetter(key))
        yield instr.inner_output()
        self.b.emit(O.EndObjectLiteralGetter())

    @contextmanager
    def computed_getter(self, key: Var) -> Iterator[Var]:
        instr = self.b.emit(O.BeginObjectLiteralComputedGetter(), [key])
        yield instr.inner_output()
        self.b.emit(O.EndObjectLiteralComputedGetter())

    @contextmanager
    def setter(self, key: Key) -> Iterator[tuple[Var, Var]]:
        self.keys.add(key)
        instr = self.b.emit(O.BeginObjectLiteralSetter(key))
        yield instr.inner_output(0), instr.inner_output(1)
        self.b.emit(O.EndObjectLiteralSetter())

    @contextmanager
    def computed_setter(self, key: Var) -> Iterator[tuple[Var, Var]]:
        instr = self.b.emit(O.BeginObjectLiteralComputedSetter(), [key])
        yield instr.inner_output(0), instr.inner_output(1)
        self.b.emit(O.EndObjectLiteralComputedSetter())
