"""Mutators as Hypothesis strategies, and lineages (`evolved`) -- docs/proposal-hypothesis-ir.md §3.4-3.5.

A mutator is `(prog, ...) -> strategy[prog]`: every decision it makes is a draw, so a mutation is
just more of the test case. The child is re-verified from scratch (types are recomputed, decision
D17) and rejected with `hypothesis.reject()` if it breaks an invariant -- an input rewired to a
function whose return type then makes a call elsewhere ill-typed, say.

`evolved(opts)` is a lineage: a base program, then mutation steps while a boolean says "one more".
The base is DRAWN in the same test case, so Hypothesis's shrinker can shrink all of it -- delete
mutation steps, shrink the base, simplify each step (proposal §3.5).

Mutators, simplest first (the shrinker lowers the index towards `operation`):
- operation  -- change one parameter: a literal's spelling (same type), an operator, a loop bound,
                a property name, a lifting choice (`keep`). Never to the current value.
- input      -- rewire one operand to another visible variable whose type at that point fits.
- reassign   -- assign a declared variable a visible value of a DIFFERENT type class (Rhino's
                typed-local bugs: `var n = 1; ... n = 1n;`).
- codegen    -- generate statements at a point, with the context there (`Builder.at`).
- splice     -- copy a closed slice of a donor program (here: a freshly drawn one) in at a point.
- activation -- give a function an `arguments` read, moving its locals off the optimizer path.
A mutator that does not apply to the program falls back to codegen, which always does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hypothesis import reject, strategies as st

from ..gen.builder import GenOptions
from ..gen.generators.values import BIGINTS, INT32S, NUMBERS, REGEXPS, STRINGS, SYMBOLS
from ..gen.types import PRIMITIVES, T
from .analysis import Analysis, InvalidProgram, analyse, requirement_ok, verify
from .builder import Builder, lifting_choices, maybe, pick, statements
from .ops import (ARITH, BITWISE, BINARY_OPS, COMPARE, COMPOUND_OPS, LOGICAL, UNARY_OPS, BeginFor, BeginFunction,
                  BeginWhile, Binary, CompoundAssign, Eval, GetProperty, Instr, LoadLiteral, Program, Reassign,
                  SetProperty, Unary, Update)
from .registry import available_generators, weighted
from .strategies import ir_programs

LITERALS = {T.NUMBER: NUMBERS, T.INT32: INT32S, T.BIGINT: BIGINTS, T.STRING: STRINGS, T.BOOLEAN: ["true", "false"],
            T.UNDEFINED: ["undefined", "void 0"], T.SYMBOL: SYMBOLS, T.REGEXP: REGEXPS}
KEYS = ["x", "y", "a", "length", "0", "1", "constructor", "__proto__", "valueOf", "toString", "prototype"]
# Type classes for `reassign`: crossing classes is what changes a typed local's representation.
CLASSES = [frozenset({T.INT32, T.NUMBER}), frozenset({T.STRING}), frozenset({T.BIGINT}), frozenset({T.BOOLEAN}),
           frozenset({T.UNDEFINED, T.NULL}), frozenset({T.SYMBOL})]
ACTIVATION_MARK = "var __oxp_activation = arguments.length"


def _class(t: T) -> int:
    return next((k for k, c in enumerate(CLASSES) if t in c), len(CLASSES))


def _replace(prog: Program, i: int, ins: Instr) -> Program:
    return prog[:i] + (ins,) + prog[i + 1:]


def _insertion_points(prog: Program, an: Analysis) -> list[int]:
    """Positions code may be inserted before: anywhere except directly in a switch (outside a case)."""
    return [p for p in range(len(prog) + 1) if an.frame_at[p] != "switch"]


def _fresh_base(prog: Program) -> int:
    return 1 + max((v for ins in prog for v in ins.outputs + ins.inner), default=-1)


# ---------------------------------------------------------------------------- operation

def _alternatives(ins: Instr) -> list:
    """What instruction i's parameter could be changed to (never the current value)."""
    op = ins.op
    out: list = []
    if isinstance(op, LoadLiteral) and op.type in LITERALS:
        out = [LoadLiteral(op.type, t) for t in LITERALS[op.type] if t != op.text]
    elif isinstance(op, Binary):
        group = next(g for g in (ARITH, BITWISE, COMPARE, LOGICAL, BINARY_OPS) if op.op in g)
        out = [Binary(o) for o in group if o != op.op]
    elif isinstance(op, Unary):
        out = [Unary(o) for o in UNARY_OPS if o != op.op]
    elif isinstance(op, CompoundAssign):
        out = [CompoundAssign(o) for o in COMPOUND_OPS if o != op.op]
    elif isinstance(op, Update):
        out = [Update(o, p, op.value) for o in ("++", "--") for p in (False, True) if (o, p) != (op.op, op.prefix)]
    elif isinstance(op, BeginFor):
        out = [BeginFor(n, op.label) for n in range(1, 9) if n != op.count]
    elif isinstance(op, BeginWhile):
        out = [BeginWhile(n, op.do, op.label) for n in range(1, 9) if n != op.count]
    elif isinstance(op, (GetProperty, SetProperty)):
        out = [type(op)(k) for k in KEYS if k != op.name]
    return out


@st.composite
def operation_mutation(draw, prog: Program, an: Analysis, opts: GenOptions) -> Program:
    """Change one parameter of one instruction -- or flip one lifting choice (`keep`)."""
    sites = [(i, alts) for i, ins in enumerate(prog) if (alts := _alternatives(ins))]
    keeps = [i for i, ins in enumerate(prog) if ins.outputs and ins.keep is not None]
    if keeps and (not sites or draw(maybe(0.2))):
        i = draw(pick(keeps))
        return _replace(prog, i, prog[i].replace(keep=not prog[i].keep))
    i, alts = draw(pick(sites))
    return _replace(prog, i, prog[i].replace(op=draw(pick(alts))))


def _operation_applies(prog: Program, an: Analysis) -> bool:
    return any(_alternatives(ins) or (ins.outputs and ins.keep is not None) for ins in prog)


# ---------------------------------------------------------------------------- input

def _input_slots(prog: Program, an: Analysis) -> list[tuple[int, int, list[int]]]:
    """(instruction, slot, candidates): candidates are other variables visible there (most recent
    first) that the slot accepts under the types at that point."""
    slots = []
    for i, ins in enumerate(prog):
        for k, cur in enumerate(ins.inputs):
            cands = []
            for v in an.visible_recent(i):
                if v == cur:
                    continue
                trial = ins.replace(inputs=ins.inputs[:k] + (v,) + ins.inputs[k + 1:])
                if requirement_ok(an, i, trial, k) and _same_kind(an, cur, v, i):
                    cands.append(v)
            if cands:
                slots.append((i, k, cands))
    return slots


def _same_kind(an: Analysis, cur: int, v: int, i: int) -> bool:
    """Keep the rewiring meaningful: a function slot gets a function, a value slot a value."""
    return (an.type_at(i, cur) == T.FUNCTION) == (an.type_at(i, v) == T.FUNCTION)


@st.composite
def input_mutation(draw, prog: Program, an: Analysis, opts: GenOptions) -> Program:
    """Rewire one input to another visible, type-compatible variable (Fuzzilli's InputMutator)."""
    i, k, cands = draw(pick(_input_slots(prog, an)))
    ins = prog[i]
    return _replace(prog, i, ins.replace(inputs=ins.inputs[:k] + (draw(pick(cands)),) + ins.inputs[k + 1:]))


# ---------------------------------------------------------------------------- reassign

def _reassign_sites(prog: Program, an: Analysis) -> list[tuple[int, int, list[int]]]:
    """(position, declared variable, values of another type class visible there)."""
    sites = []
    for p in _insertion_points(prog, an):
        vis = an.visible_recent(p)
        for v in vis:
            if not an.assignable(v) or an.type_at(p, v) not in PRIMITIVES:
                continue
            cls = _class(an.type_at(p, v))
            values = [w for w in vis if w != v and an.type_at(p, w) in PRIMITIVES and _class(an.type_at(p, w)) != cls]
            if values:
                sites.append((p, v, values))
    return sites


@st.composite
def reassign_mutation(draw, prog: Program, an: Analysis, opts: GenOptions) -> Program:
    """`v = w` where w has a different type class than v has there."""
    p, v, values = draw(pick(_reassign_sites(prog, an)))
    w = draw(pick(values))
    return prog[:p] + (Instr(Reassign(), (v, w), gen="mutation:reassign"),) + prog[p:]


# ---------------------------------------------------------------------------- codegen

@st.composite
def codegen_mutation(draw, prog: Program, an: Analysis, opts: GenOptions) -> Program:
    """Generate code at a point, with what is visible there (Fuzzilli's CodeGenMutator). It uses
    the same generators -- the same strategies -- as fresh programs: one grammar, one place."""
    pos = draw(pick(_insertion_points(prog, an)))
    b = Builder.at(prog, pos, opts, weighted(available_generators(opts)))
    if b.strict:                                   # inside a "use strict" function: no sloppy-only code
        b.pool = [g for g in b.pool if not g.sloppy_only] or b.pool
    draw(statements(b, 1, 3))
    new = draw(lifting_choices(b.program()))
    return prog[:pos] + new + prog[pos:]


# ---------------------------------------------------------------------------- splice

def backward_slice(prog: Program, an: Analysis, i: int) -> list[int]:
    """Instruction i plus everything its inputs depend on, transitively. A block is taken whole
    (picking one, or depending on a variable it defines); the slice is closed: no free variables."""
    need: set[int] = set()
    stack = [i]
    while stack:
        j = stack.pop()
        if j in need:
            continue
        block = range(j, an.block_end[j] + 1) if j in an.block_end else (j,)
        for b_ in block:
            need.add(b_)
            for v in prog[b_].inputs:
                d = an.def_index[v]
                if d not in need:
                    stack.append(d)
    return sorted(need)


@st.composite
def splice_mutation(draw, prog: Program, an: Analysis, opts: GenOptions) -> Program:
    """Copy a closed slice of a donor program in at a point (Fuzzilli's SpliceMutator). The donor is
    drawn here, inside the same test case, so it is shrinkable too (proposal §3.5)."""
    donor, _ = draw(ir_programs(GenOptions(min_statements=1, max_statements=4, probes=False,
                                           strict=opts.strict, toggles=opts.toggles)))
    dan = analyse(donor)
    top = [j for j, ins in enumerate(donor) if dan.depth_at[j] == 0 and (ins.outputs or j in dan.block_end)]
    if not top:
        reject()
    piece = [donor[j] for j in backward_slice(donor, dan, draw(pick(top)))]
    base = _fresh_base(prog)
    piece = [ins.renamed(lambda v: base + v) for ins in piece]
    pos = draw(pick(_insertion_points(prog, an)))
    return prog[:pos] + tuple(piece) + prog[pos:]


# ---------------------------------------------------------------------------- activation

def _functions(prog: Program, an: Analysis) -> list[int]:
    return [i for i, ins in enumerate(prog) if isinstance(ins.op, BeginFunction) and ins.op.style != "arrow"
            and not an.activation.get(ins.outputs[0], False)]


@st.composite
def activation_mutation(draw, prog: Program, an: Analysis, opts: GenOptions) -> Program:
    """Give a function an `arguments` read: its locals leave the optimizer-typed path (in Rhino a
    function using `arguments` needs an activation). A one-instruction change that flips the
    compiled-mode strategy of a whole function."""
    i = draw(pick(_functions(prog, an)))
    return prog[:i + 1] + (Instr(Eval(ACTIVATION_MARK), gen="mutation:activation"),) + prog[i + 1:]


# ---------------------------------------------------------------------------- mutation, lineages

MUTATORS = [
    ("operation", operation_mutation, _operation_applies),
    ("input", input_mutation, lambda p, an: bool(_input_slots(p, an))),
    ("reassign", reassign_mutation, lambda p, an: bool(_reassign_sites(p, an))),
    ("codegen", codegen_mutation, lambda p, an: True),
    ("splice", splice_mutation, lambda p, an: True),
    ("activation", activation_mutation, lambda p, an: bool(_functions(p, an))),
]


@st.composite
def mutation(draw, prog: Program, opts: GenOptions = GenOptions()) -> tuple[str, Program]:
    an = verify(prog)
    # Fixed list (rule 3): codegen is the fallback, it always applies.
    name, mutator, applies = draw(st.sampled_from(MUTATORS))
    if not applies(prog, an):
        name, mutator = "codegen", codegen_mutation
    child = draw(mutator(prog, an, opts))
    try:
        verify(child)
    except InvalidProgram:
        reject()
    return name, child


@dataclass
class Lineage:
    program: Program
    meta: dict
    steps: list[str] = field(default_factory=list)       # mutator name per step


@st.composite
def evolved(draw, opts: GenOptions = GenOptions(), max_steps: int = 50) -> Lineage:
    """A base program, then mutation steps while a boolean says "one more" -- a loop, not
    recursion, so every step's span sits at the same depth and extending a lineage only changes the
    end of its choice sequence (proposal §3.5)."""
    prog, meta = draw(ir_programs(opts))
    steps: list[str] = []
    while len(steps) < max_steps and draw(st.booleans()):
        name, prog = draw(mutation(prog, opts))
        steps.append(name)
    return Lineage(prog, meta, steps)
