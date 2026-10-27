# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/FuzzIL/Instruction.swift, Code.swift, Blocks.swift and Analyzer.swift
# (ContextAnalyzer) at commit be2811d00282320381fc8007d64873745772011d.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Instructions, programs, static validity (`check`), block navigation and renumbering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Iterable, NewType, Sequence

from .ops import (
    Attr,
    BeginForLoopAfterthought,
    BeginForLoopBody,
    BeginForLoopCondition,
    Context,
    Lift,
    Operation,
    matches_end,
)
from .options import GenOptions

Var = NewType("Var", int)


class InvalidProgram(Exception):
    def __init__(self, kind: str, message: str, index: int | None = None):
        super().__init__(f"{kind}: {message}" + (f" (at {index})" if index is not None else ""))
        self.kind = kind
        self.index = index


@dataclass(frozen=True, slots=True)
class Instruction:
    """An operation applied to variables: inputs, then outputs (visible in the enclosing scope),
    then inner outputs (visible only inside the block this instruction opens)."""
    op: Operation
    inouts: tuple[Var, ...] = ()
    index: int = -1
    gen: str = ""           # provenance: generator name
    origin: int = 0         # provenance: generator invocation
    step: int = 0           # provenance: mutation step that added or changed it
    lift: Lift | None = None

    def __post_init__(self) -> None:
        expected = self.op.inputs() + self.op.outputs() + self.op.inner_outputs()
        if len(self.inouts) != expected:
            raise ValueError(f"{self.op.name}: expected {expected} inouts, got {len(self.inouts)}")

    @property
    def inputs(self) -> tuple[Var, ...]:
        return self.inouts[: self.op.inputs()]

    @property
    def outputs(self) -> tuple[Var, ...]:
        n = self.op.inputs()
        return self.inouts[n: n + self.op.outputs()]

    @property
    def inner_outputs(self) -> tuple[Var, ...]:
        return self.inouts[self.op.inputs() + self.op.outputs():]

    @property
    def all_outputs(self) -> tuple[Var, ...]:
        return self.inouts[self.op.inputs():]

    @property
    def output(self) -> Var:
        if self.op.outputs() != 1:
            raise ValueError(f"{self.op.name} has {self.op.outputs()} outputs")
        return self.inouts[self.op.inputs()]

    def input(self, i: int) -> Var:
        return self.inputs[i]

    def inner_output(self, i: int = 0) -> Var:
        return self.inner_outputs[i]

    @property
    def variadic_inputs(self) -> tuple[Var, ...]:
        if self.op.first_variadic is None:
            return ()
        return self.inouts[self.op.first_variadic: self.op.inputs()]

    @property
    def has_value(self) -> bool:
        return self.op.outputs() == 1

    def with_(self, **changes: object) -> Instruction:
        return replace(self, **changes)

    def __str__(self) -> str:
        outs = ", ".join(f"v{v}" for v in self.outputs)
        inner = ", ".join(f"v{v}" for v in self.inner_outputs)
        ins = ", ".join(f"v{v}" for v in self.inputs)
        head = f"{outs} <- " if outs else ""
        tail = f" -> {inner}" if inner else ""
        return f"{head}{self.op!r} {ins}{tail}".rstrip()

    def __repr__(self) -> str:
        return f"<{self}>"

    def _repr_pretty_(self, p, cycle) -> None:    # Hypothesis prints dataclasses field by field unless told otherwise
        p.text(repr(self))


Code = tuple[Instruction, ...]


@dataclass(frozen=True)
class Program:
    code: Code
    options: GenOptions
    template: str = ""

    def __len__(self) -> int:
        return len(self.code)

    def signature(self) -> str:
        return signature(self.code)

    def __str__(self) -> str:
        return dump(self.code)

    def __repr__(self) -> str:
        return f"<Program {self.template or 'untitled'}: {len(self.code)} instructions, {self.signature()}>"

    def _repr_pretty_(self, p, cycle) -> None:    # Hypothesis prints dataclasses field by field unless told otherwise
        p.text(repr(self))


# ------------------------------------------------------------------------------- context analysis
def next_context(stack: list[Context], instr: Instruction) -> list[Context]:
    """ContextAnalyzer.analyze (Analyzer.swift:160-206) as a pure function on a stack of contexts."""
    stack = list(stack)
    op = instr.op
    if op.is_block_end:
        stack.pop()
    if op.is_block_start:
        new = op.opened()
        if op.propagates_context:
            new |= stack[-1]
        if op.resumes_context:
            if len(stack) < 2:
                raise InvalidProgram("context", f"{op.name} resumes a context that does not exist", instr.index)
            new |= stack[-2]
        if op.opened() & (Context.SWITCH_BLOCK | Context.SWITCH_CASE):
            new &= ~Context.LOOP
        elif op.opened() & Context.LOOP:
            new &= ~(Context.SWITCH_BLOCK | Context.SWITCH_CASE)
        stack.append(new)
    return stack


def contexts(code: Sequence[Instruction]) -> list[Context]:
    """The context in which each instruction sits (before it is applied)."""
    stack = [Context.JAVASCRIPT]
    out = []
    for instr in code:
        out.append(stack[-1])
        stack = next_context(stack, instr)
    return out


# ------------------------------------------------------------------------------- check
def check(code: Sequence[Instruction]) -> None:
    """Static validity (Code.swift:215-321): indexes, contiguous variable numbering, inputs defined
    and visible, required contexts, balanced and matching blocks, consistent for-loop headers."""
    defined: dict[Var, int] = {}                  # variable -> scope id
    scope_counter = 0
    active: list[tuple[int, Operation | None]] = [(0, None)]
    for_header: list[int] = []
    stack = [Context.JAVASCRIPT]

    def define(v: Var, scope: int, index: int) -> None:
        if v in defined:
            raise InvalidProgram("numbering", f"v{v} defined twice", index)
        if v != 0 and Var(v - 1) not in defined:
            raise InvalidProgram("numbering", f"v{v} defined before v{v - 1}", index)
        defined[v] = scope

    for idx, instr in enumerate(code):
        if instr.index != idx:
            raise InvalidProgram("index", f"instruction has index {instr.index}", idx)
        op = instr.op
        for v in instr.inputs:
            scope = defined.get(v)
            if scope is None:
                raise InvalidProgram("undefined", f"v{v} is never defined", idx)
            if all(s != scope for s, _ in active):
                raise InvalidProgram("invisible", f"v{v} is not visible", idx)
        if op.required() & ~stack[-1]:
            raise InvalidProgram("context", f"{op.name} needs {op.required()} but is in {stack[-1]}", idx)
        if op.is_block_end:
            if len(active) <= 1:
                raise InvalidProgram("block_mismatch", f"{op.name} closes nothing", idx)
            _, head = active.pop()
            if head is None or not matches_end(head, op):
                raise InvalidProgram("block_mismatch", f"{op.name} does not close {head.name if head else 'anything'}", idx)
        stack = next_context(stack, instr)
        for v in instr.outputs:
            define(v, active[-1][0], idx)
        if op.is_block_start:
            scope_counter += 1
            active.append((scope_counter, op))
            if isinstance(op, BeginForLoopCondition):
                for_header.append(op.num_loop_variables)
            elif isinstance(op, BeginForLoopAfterthought):
                if not for_header or op.num_loop_variables != for_header[-1]:
                    raise InvalidProgram("for_header", "afterthought does not match the condition", idx)
            elif isinstance(op, BeginForLoopBody):
                if not for_header or op.num_loop_variables != for_header.pop():
                    raise InvalidProgram("for_header", "body does not match the header", idx)
        for v in instr.inner_outputs:
            define(v, active[-1][0], idx)
    if len(active) != 1:
        raise InvalidProgram("block_mismatch", f"{len(active) - 1} block(s) left open")


def is_valid(code: Sequence[Instruction]) -> bool:
    try:
        check(code)
        return True
    except InvalidProgram:
        return False


# ------------------------------------------------------------------------------- blocks
def block_end(code: Sequence[Instruction], head: int) -> int:
    """Index of the instruction closing the block opened at `head` (Code.swift:393-413)."""
    if not code[head].op.is_block_start:
        raise ValueError("not a block start")
    depth = 1
    for i in range(head + 1, len(code)):
        op = code[i].op
        if op.is_block_end:
            depth -= 1
            if depth == 0:
                return i
        if op.is_block_start:
            depth += 1
    raise ValueError("unterminated block")


def block_begin(code: Sequence[Instruction], tail: int) -> int:
    if not code[tail].op.is_block_end:
        raise ValueError("not a block end")
    depth = 1
    for i in range(tail - 1, -1, -1):
        op = code[i].op
        if op.is_block_start:
            depth -= 1
            if depth == 0:
                return i
        if op.is_block_end:
            depth += 1
    raise ValueError("unopened block")


def block_group(code: Sequence[Instruction], head: int) -> list[int]:
    """Indexes of all block instructions of the group starting at `head` (BeginIf, BeginElse, EndIf)."""
    if not code[head].op.is_block_group_start:
        raise ValueError("not a block group start")
    out = [head]
    depth = 1
    i = head + 1
    while i < len(code):
        op = code[i].op
        if op.is_block_end:
            depth -= 1
        if op.is_block_start:
            if depth == 0:
                out.append(i)
            depth += 1
        if depth == 0:
            out.append(i)
            return out
        i += 1
    raise ValueError("unterminated block group")


def block_groups(code: Sequence[Instruction]) -> list[list[int]]:
    """All block groups, inner before outer, earlier before later (Code.swift:479-500)."""
    groups: list[list[int]] = []
    stack: list[list[int]] = []
    for i, instr in enumerate(code):
        op = instr.op
        if op.is_block_start and not op.is_block_end:
            stack.append([i])
        elif op.is_block_end:
            stack[-1].append(i)
            if not op.is_block_start:
                groups.append(stack.pop())
    return groups


def enclosing_subroutine(code: Sequence[Instruction], index: int, begins: tuple[type[Operation], ...]) -> int | None:
    """Index of the innermost subroutine begin whose block contains `index`, or None at top level."""
    depth = 0
    for i in range(index - 1, -1, -1):
        op = code[i].op
        if op.is_block_end and not op.is_block_start:
            depth += 1
        elif op.is_block_start and not op.is_block_end:
            if depth == 0:
                if isinstance(op, begins):
                    return i
            else:
                depth -= 1
    return None


# ------------------------------------------------------------------------------- renumbering, signatures
def renumber(code: Iterable[Instruction]) -> Code:
    """Contiguous variable numbers in definition order and fresh indexes (Code.swift:166-181)."""
    mapping: dict[Var, Var] = {}
    out: list[Instruction] = []
    for i, instr in enumerate(code):
        for v in instr.all_outputs:
            if v in mapping:
                raise InvalidProgram("numbering", f"v{v} defined twice", i)
            mapping[v] = Var(len(mapping))
        try:
            inouts = tuple(mapping[v] for v in instr.inouts)
        except KeyError as e:
            raise InvalidProgram("undefined", f"v{e.args[0]} used before definition", i) from None
        out.append(replace(instr, inouts=inouts, index=i))
    return tuple(out)


def reindex(code: Iterable[Instruction]) -> Code:
    return tuple(replace(instr, index=i) for i, instr in enumerate(code))


def signature(code: Sequence[Instruction]) -> str:
    """A structural hash: operations, their parameters, variables and lifting choices; provenance
    is ignored, so two programs that lift the same have the same signature."""
    h = hashlib.sha1()
    for instr in code:
        h.update(repr(instr.op).encode())
        h.update(repr(instr.inouts).encode())
        h.update(repr(instr.lift).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


def dump(code) -> str:
    """The IL as indented text. Accepts instructions, a Program or a Builder."""
    lines = []
    depth = 0
    for instr in getattr(code, "code", code):
        if instr.op.is_block_end:
            depth = max(0, depth - 1)
        lines.append("    " * depth + str(instr))
        if instr.op.is_block_start:
            depth += 1
    return "\n".join(lines)
