# Derived from Fuzzilli (https://github.com/googleprojectzero/fuzzilli), Sources/Fuzzilli/Mutators/
# OperationMutator.swift, InputMutator.swift, CodeGenMutator.swift, SpliceMutator.swift,
# CombineMutator.swift and Core/ProgramBuilder.swift (splice, 1735-2050) at commit
# be2811d00282320381fc8007d64873745772011d.
# Copyright 2019 Google LLC. Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0. Unless required by applicable law
# or agreed to in writing, software distributed under the License is distributed on an "AS IS"
# BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under the License.
# Translated to Python and adapted for oxpecker (Rhino, Hypothesis strategies, the Rhino model);
# the declaration-kind, reassign, activation and number-flip mutators are oxpecker's own.
"""Mutators as Hypothesis strategies over a program (design §9, §22; G24, G25).

Each mutator is a composite `(program, an) -> Program` that returns a new program with
contiguous variables, a `lift` for every new instruction and `step` stamped on what it changed;
`mutation(program)` picks an applicable mutator from the fixed simplest-first list, verifies the
child and `reject()`s a child no generator may produce. `evolved(options, steps)` is a lineage:
a `programs()` draw followed by mutation steps, shrinking to fewer steps.

From a shell:

    >>> from oxpecker.il.mutate import mutate, lineage, mutators
    >>> m = mutate(generate(3), seed=1)            # one mutation; m.name, m.program, print(m)
    >>> print(m.diff())                            # the IL before and after
    >>> m = mutate(generate(3), seed=1, name="declaration_kind")
    >>> l = lineage(seed=2, steps=4); [s.name for s in l.steps]; print(l)
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field, replace
from typing import Callable, Sequence

from hypothesis import reject
from hypothesis import strategies as st

from . import ops as O
from .analysis import NOT_ASSIGNABLE, Analysis, Storage, analyse, verify
from .builder import Builder, BuilderError
from .environment import (INTERESTING_FLOATS, INTERESTING_INTEGERS, INTERESTING_KEYS, INTERESTING_STRINGS,
                          REGEXP_PATTERNS, methods_of)
from .lift import LiftOptions, lift
from .ops import Context, DeclarationKind, Lift, NamedVariableMode
from .options import TOGGLE_LET, GenOptions
from .program import InvalidProgram, Instruction, Program, Var, block_end, renumber
from .strategies import LIFT_CHOICES, Generated, lifting_choices, maybe, pick, pools, programs, statements
from .types import ANY, FUNCTION, NULLISH, Type

JUMPS = (O.Return, O.ThrowException, O.LoopBreak, O.LoopContinue, O.BlockBreak, O.SwitchBreak)
LABEL_USERS = (O.LoopBreak, O.LoopContinue, O.BlockBreak)
ELEMENT_INDEXES: tuple[int, ...] = (0, 1, 2, 3, 4, 10, 42, 100, 2 ** 31 - 1, 2 ** 32 - 2)
REPEAT_COUNTS: tuple[int, ...] = (0, 1, 2, 3, 5, 10, 25, 100)
KIND_POOL: tuple[DeclarationKind, ...] = (DeclarationKind.VAR, DeclarationKind.LET, DeclarationKind.CONST)
MODE_OF_KIND = {DeclarationKind.VAR: NamedVariableMode.VAR, DeclarationKind.LET: NamedVariableMode.LET,
                DeclarationKind.CONST: NamedVariableMode.CONST}
KIND_OF_MODE = {m: k for k, m in MODE_OF_KIND.items()}
LIFT_OF_KIND = {DeclarationKind.VAR: Lift.VAR, DeclarationKind.LET: Lift.LET, DeclarationKind.CONST: Lift.CONST}
KIND_OF_LIFT = {l: k for k, l in LIFT_OF_KIND.items()}
REGEXP_FLAG_SETS: tuple[O.RegExpFlag, ...] = (
    O.RegExpFlag.NONE, O.RegExpFlag.GLOBAL, O.RegExpFlag.IGNORE_CASE, O.RegExpFlag.GLOBAL | O.RegExpFlag.IGNORE_CASE,
    O.RegExpFlag.MULTILINE, O.RegExpFlag.STICKY, O.RegExpFlag.DOT_ALL, O.RegExpFlag.UNICODE,
)


# ------------------------------------------------------------------------------- helpers
def _code(x) -> tuple[Instruction, ...]:
    return tuple(getattr(x, "code", x))


def _options(x) -> GenOptions:
    program = getattr(x, "program", x)
    return getattr(program, "options", None) or GenOptions()


def _template(x) -> str:
    program = getattr(x, "program", x)
    return getattr(program, "template", "")


def _finish(code: Sequence[Instruction], options: GenOptions, template: str, draw, step: int,
            changed: Sequence[int] = ()) -> Program:
    """Contiguous variables, lift choices for the new instructions, `step` on the changed ones."""
    code = renumber(code)
    code = draw(lifting_choices(code))
    if changed:
        marks = set(changed)
        code = tuple(replace(i, step=step) if i.index in marks else i for i in code)
    return Program(code, options, template)


def _other(draw, pool: Sequence, current):
    """A drawn element of `pool` that is not `current` (OperationMutator never keeps the value)."""
    candidates = [x for x in pool if x != current]
    if not candidates:
        return None
    return pick(draw, candidates)


def insertion_points(code: Sequence[Instruction], an: Analysis) -> list[int]:
    """Positions where a statement may be inserted: the context allows statements and the position
    is not dead code after a jump."""
    points = []
    for i in range(len(code) + 1):
        ctx = an.context_at[i] if i < len(code) else Context.JAVASCRIPT
        if Context.JAVASCRIPT not in ctx:
            continue
        if i > 0 and isinstance(code[i - 1].op, JUMPS):
            continue
        points.append(i)
    return points


def _usable(an: Analysis, i: int, exclude: Var | None = None) -> list[Var]:
    """Variables usable as an input at `i`: visible, not a label, not the excluded one."""
    return [v for v in an.visible_at[i] if an.storage.get(v) is not Storage.LABEL and v != exclude]


def _visible_of_type(an: Analysis, i: int, t: Type) -> list[Var]:
    return [v for v in _usable(an, i) if an.type_at(i, v).Is(t)]


def _never_assigned(an: Analysis, v: Var) -> bool:
    return len(an.assignments.get(v, [])) <= 1


def _emit_through_builder(code: Sequence[Instruction], pos: int, options: GenOptions,
                          emit: Callable[[Builder], None]) -> tuple[Instruction, ...]:
    """Replay code[:pos] into a builder, let `emit` add instructions, splice them back. Raises
    BuilderError when the new instructions do not fit (the caller rejects)."""
    b = Builder.at(code, pos, options)
    emit(b)
    new = b.new_code()
    if not new:
        raise BuilderError("nothing emitted")
    return tuple(code[:pos]) + new + tuple(code[pos:])


# ------------------------------------------------------------------------------- the mutators
@st.composite
def operation_mutation(draw, program, an: Analysis, step: int = 1) -> Program:
    """One parameter of one mutable instruction (OperationMutator.swift's table): a literal's value
    within its pool, an operator, a key within its kind, a method on the same receiver group, an
    element index, a loop count, property flags, `inverted` on an `if`; or one `lift` choice.
    Receiver/call-optional flags flip with p=0.1 on top (996-1002). Never the current value."""
    code = _code(program)
    candidates = [i for i, instr in enumerate(code) if instr.op.is_mutable or instr.lift is not None]
    if not candidates:
        return None
    i = pick(draw, candidates)
    instr = code[i]
    op = instr.op
    new_op = None
    if instr.lift is not None and (not op.is_mutable or draw(maybe(0.3))):
        new_lift = _other(draw, sorted(set(LIFT_CHOICES), key=lambda l: l.name), instr.lift)
        if new_lift is None:
            return None
        out = list(code)
        out[i] = instr.with_(lift=new_lift)
        return _finish(out, _options(program), _template(program), draw, step, [i])

    if isinstance(op, O.LoadInteger):
        v = _other(draw, INTERESTING_INTEGERS, op.value)
        new_op = replace(op, value=v) if v is not None else None
    elif isinstance(op, O.LoadBigInt):
        v = _other(draw, INTERESTING_INTEGERS, op.value)
        new_op = replace(op, value=v) if v is not None else None
    elif isinstance(op, O.LoadFloat):
        v = _other(draw, INTERESTING_FLOATS, op.value)
        new_op = replace(op, value=v) if v is not None else None
    elif isinstance(op, O.LoadString):
        v = _other(draw, INTERESTING_STRINGS, op.value)
        new_op = replace(op, value=v) if v is not None else None
    elif isinstance(op, O.LoadBoolean):
        new_op = replace(op, value=not op.value)
    elif isinstance(op, O.LoadRegExp):
        if draw(maybe(0.5)):
            pattern, incompatible = pick(draw, [p for p in REGEXP_PATTERNS if p[0] != op.pattern])
            new_op = replace(op, pattern=pattern, flags=op.flags & ~incompatible)
        else:
            incompatible = next((inc for p, inc in REGEXP_PATTERNS if p == op.pattern), O.RegExpFlag.NONE)
            flags = _other(draw, [f & ~incompatible for f in REGEXP_FLAG_SETS], op.flags)
            new_op = replace(op, flags=flags) if flags is not None else None
    elif isinstance(op, O.CreateTemplateString) and op.parts:
        k = draw(st.integers(0, len(op.parts) - 1))
        part = _other(draw, INTERESTING_STRINGS, op.parts[k])
        parts = list(op.parts)
        parts[k] = part
        new_op = replace(op, parts=tuple(parts))
    elif hasattr(op, "key") and isinstance(getattr(op, "key"), type(INTERESTING_KEYS[0])):
        key = _other(draw, [k for k in INTERESTING_KEYS if k.kind is op.key.kind], op.key)
        if key is None:
            return None
        new_op = replace(op, key=key)
        if hasattr(op, "op") and isinstance(op.op, O.BinaryOperator) and draw(maybe(0.5)):
            new_op = replace(new_op, op=_other(draw, list(O.BinaryOperator), op.op))
    elif hasattr(op, "index") and isinstance(getattr(op, "index"), int) and not isinstance(op, O.CallMethod):
        index = _other(draw, ELEMENT_INDEXES, op.index)
        new_op = replace(op, index=index)
        if hasattr(op, "op") and isinstance(op.op, O.BinaryOperator) and draw(maybe(0.5)):
            new_op = replace(new_op, op=_other(draw, list(O.BinaryOperator), op.op))
    elif isinstance(op, O.CallMethod):
        same_group = [m for m in methods_of(op.method.receiver) if m != op.method]
        new_op = replace(op, method=pick(draw, same_group)) if same_group else None
    elif isinstance(op, O.UnaryOperation):
        new_op = replace(op, op=_other(draw, list(O.UnaryOperator), op.op))
    elif isinstance(op, (O.BinaryOperation, O.Update, O.UpdateComputedProperty, O.UpdateSuperProperty)):
        new_op = replace(op, op=_other(draw, list(O.BinaryOperator), op.op))
    elif isinstance(op, O.Compare):
        new_op = replace(op, op=_other(draw, list(O.Comparator), op.op))
    elif isinstance(op, O.BeginIf):
        new_op = replace(op, inverted=not op.inverted)
    elif isinstance(op, O.BeginRepeatLoop):
        n = _other(draw, REPEAT_COUNTS, op.iterations)
        new_op = replace(op, iterations=n)
    elif hasattr(op, "flags") and isinstance(getattr(op, "flags"), O.PropertyFlag):
        flags = _other(draw, list(O.PropertyFlag), op.flags)
        new_op = replace(op, flags=flags) if flags is not None else None
    elif hasattr(op, "spreads"):
        spreads = list(op.spreads)
        if spreads:
            k = draw(st.integers(0, len(spreads) - 1))
            spreads[k] = not spreads[k]
            new_op = replace(op, spreads=tuple(spreads))
    if new_op is None:
        return None
    for flag in ("receiver_optional", "call_optional"):
        if hasattr(new_op, flag) and draw(maybe(0.1)):
            new_op = replace(new_op, **{flag: not getattr(new_op, flag)})
    out = list(code)
    out[i] = instr.with_(op=new_op)
    return _finish(out, _options(program), _template(program), draw, step, [i])


@st.composite
def input_mutation(draw, program, an: Analysis, step: int = 1) -> Program:
    """One input of one instruction rewired to another variable visible there that the slot
    accepts (InputMutator.swift, type-aware mode): callee slots take functions, assignable slots
    take assignable variables, property receivers are never nullish."""
    code = _code(program)
    candidates = [i for i, instr in enumerate(code)
                  if instr.op.inputs() > 0 and not isinstance(instr.op, LABEL_USERS)]
    if not candidates:
        return None
    i = pick(draw, candidates)
    instr = code[i]
    k = draw(st.integers(0, instr.op.inputs() - 1))
    current = instr.input(k)
    pool = _usable(an, i, exclude=current)
    op = instr.op
    if isinstance(op, (O.CallFunction, O.Construct, O.CallFunctionWithSpread, O.ConstructWithSpread)) and k == 0:
        pool = [v for v in pool if an.type_at(i, v).MayBe(FUNCTION)]
    elif (isinstance(op, (O.Reassign, O.Update)) and k == 0) or \
            (isinstance(op, O.UnaryOperation) and op.op in O.INCREMENTS):
        pool = [v for v in pool if an.storage.get(v) not in NOT_ASSIGNABLE]
    elif k == 0 and (isinstance(op, O.CallMethod) or hasattr(op, "key") or hasattr(op, "index")):
        pool = [v for v in pool if not an.type_at(i, v).Is(NULLISH)]
    if not pool:
        return None
    new = pick(draw, pool)
    inouts = list(instr.inouts)
    inouts[k] = new
    out = list(code)
    out[i] = instr.with_(inouts=tuple(inouts))
    return _finish(out, _options(program), _template(program), draw, step, [i])


def _kind_sites(code: Sequence[Instruction], an: Analysis) -> list[tuple[int, DeclarationKind, bool]]:
    """(index, current kind, const allowed) for every declaration site: Declare, named variables,
    loop headers, and lifted temporaries' keywords."""
    sites = []
    for i, instr in enumerate(code):
        op = instr.op
        if isinstance(op, O.Declare):
            sites.append((i, op.kind, _never_assigned(an, instr.output)))
        elif isinstance(op, O.CreateNamedVariable) and op.mode in KIND_OF_MODE:
            sites.append((i, KIND_OF_MODE[op.mode], _never_assigned(an, instr.output)))
        elif isinstance(op, O.BeginForLoopCondition):
            sites.append((i, op.declaration, all(_never_assigned(an, v) for v in instr.inner_outputs)))
        elif isinstance(op, O.BeginForEachLoop):
            sites.append((i, op.declaration, all(_never_assigned(an, v) for v in instr.inner_outputs[:-1])))
        elif isinstance(op, O.BeginRepeatLoop):
            sites.append((i, op.declaration, False))               # the counter is incremented
        elif instr.lift in KIND_OF_LIFT:
            sites.append((i, KIND_OF_LIFT[instr.lift], True))      # a temporary is never reassigned
    return sites


@st.composite
def declaration_kind_mutation(draw, program, an: Analysis, step: int = 1) -> Program:
    """Flips the kind of one declaration (G25): `var <-> let` always, `-> const` only for a
    never-assigned variable; the pool is ordered VAR, LET, CONST so shrinking moves towards var."""
    code = _code(program)
    options = _options(program)
    sites = _kind_sites(code, an)
    if not sites or TOGGLE_LET not in options.toggles:
        return None
    i, current, const_ok = pick(draw, sites)
    pool = [k for k in KIND_POOL if k != current and (const_ok or k is not DeclarationKind.CONST)]
    if not pool:
        return None
    kind = pick(draw, pool)
    instr = code[i]
    op = instr.op
    if isinstance(op, O.Declare) or isinstance(op, (O.BeginForLoopCondition, O.BeginForEachLoop, O.BeginRepeatLoop)):
        field_name = "kind" if isinstance(op, O.Declare) else "declaration"
        new = instr.with_(op=replace(op, **{field_name: kind}))
    elif isinstance(op, O.CreateNamedVariable):
        new = instr.with_(op=replace(op, mode=MODE_OF_KIND[kind]))
    else:
        new = instr.with_(lift=LIFT_OF_KIND[kind])
    out = list(code)
    out[i] = new
    return _finish(out, options, _template(program), draw, step, [i])


@st.composite
def reassign_mutation(draw, program, an: Analysis, step: int = 1) -> Program:
    """Inserts `Reassign(v, w)` for a declared, assignable `v` and a `w` of another type class at a
    drawn point (critique §2: reassignment is where Rhino's typing goes wrong)."""
    code = _code(program)
    points = insertion_points(code, an)
    targets = [(i, v) for i in points for v in _usable(an, i if i < len(code) else len(code) - 1)
               if an.storage.get(v) not in NOT_ASSIGNABLE and an.storage.get(v) is not Storage.TEMP]
    targets = [(i, v) for i, v in targets if i > an.defs.get(v, -1)]
    if not targets:
        return None
    i, v = pick(draw, targets)
    at = i if i < len(code) else len(code) - 1
    others = _usable(an, at, exclude=v)
    vt = an.type_at(at, v)
    different = [w for w in others if an.type_at(at, w).possible != vt.possible]
    pool = different or others
    if not pool:
        return None
    w = pick(draw, pool)
    new = Instruction(O.Reassign(), (v, w), gen="reassign")
    out = list(code[:i]) + [new] + list(code[i:])
    return _finish(out, _options(program), _template(program), draw, step, [i])


@st.composite
def codegen_mutation(draw, program, an: Analysis, step: int = 1) -> Program:
    """`statements()` at a drawn point with the pool of that point's context (CodeGenMutator.swift),
    one to three statements, through a builder positioned at the point."""
    code = _code(program)
    options = _options(program)
    points = insertion_points(code, an)
    if not points:
        return None
    pos = pick(draw, points)

    def emit(b: Builder) -> None:
        draw(statements(b, 1, 3))

    try:
        out = _emit_through_builder(code, pos, options, emit)
    except BuilderError:
        return None
    added = range(pos, pos + (len(out) - len(code)))
    return _finish(out, options, _template(program), draw, step, list(added))


def _slice(donor: Sequence[Instruction], j: int) -> tuple[list[int], list[Var]]:
    """The backward slice of donor[j] (ProgramBuilder.swift:1735-2050, simplified): the whole block
    group if j is a block instruction, then every non-block instruction defining a needed
    variable, transitively. Variables defined by block instructions outside the slice are
    returned as `external` for remapping."""
    first, last = j, j
    if donor[j].op.is_block:
        heads = [i for i, instr in enumerate(donor) if instr.op.is_block_start and i <= j and block_end(donor, i) >= j]
        head = heads[-1] if heads else j
        first, last = head, block_end(donor, head)
    included = set(range(first, last + 1))
    defined = {v for i in included for v in donor[i].all_outputs}
    needed = [v for i in sorted(included) for v in donor[i].inputs if v not in defined]
    external: list[Var] = []
    defs = {v: i for i, instr in enumerate(donor) for v in instr.all_outputs}
    while needed:
        v = needed.pop()
        if v in defined or v in external:
            continue
        d = defs[v]
        if donor[d].op.is_block or d in included:
            external.append(v)
            continue
        included.add(d)
        defined.update(donor[d].all_outputs)
        needed.extend(w for w in donor[d].inputs if w not in defined)
    return sorted(included), external


@st.composite
def splice_mutation(draw, program, an: Analysis, donor=None, step: int = 1) -> Program:
    """A donor instruction's backward slice emitted at a drawn point (SpliceMutator.swift), blocks
    whole, variables renumbered; a needed variable the slice cannot define (a block's output or
    inner output) is remapped to a host variable of the same type, as are slice outputs with
    p=0.10 (ProgramBuilder.swift's remap probabilities). The donor is another `programs()` draw
    unless given."""
    code = _code(program)
    options = _options(program)
    if donor is None:
        donor = draw(programs(options))
    dcode = _code(donor)
    dan = analyse(dcode)
    points = insertion_points(code, an)
    if not points or not dcode:
        return None
    pos = pick(draw, points)
    j = draw(st.integers(0, len(dcode) - 1))
    included, external = _slice(dcode, j)
    host_ctx = an.context_at[pos] if pos < len(code) else Context.JAVASCRIPT
    depth = 0
    for i in included:
        op = dcode[i].op
        if depth == 0 and (op.required_context & ~host_ctx):
            return None
        if op.is_block_start:
            depth += 1
        if op.is_block_end and not op.is_block_start:
            depth -= 1

    def emit(b: Builder) -> None:
        mapping: dict[Var, Var] = {}
        for v in external:
            same = [w for w in b.visible() if b.type_of(w).Is(dan.types.get(v, ANY))] or b.visible()
            if not same:
                raise BuilderError("no host variable for an external slice variable")
            mapping[v] = pick(draw, same)
        for i in included:
            instr = dcode[i]
            inputs = tuple(mapping[v] if v in mapping else mapping.setdefault(v, v) for v in instr.inputs)
            new = b.emit(instr.op, inputs, lift=instr.lift)
            for old, fresh in zip(instr.all_outputs, new.all_outputs):
                if old in instr.outputs and draw(maybe(0.10)):
                    same = [w for w in b.visible() if w != fresh and b.type_of(w).Is(b.type_of(fresh))]
                    if same:
                        mapping[old] = pick(draw, same)
                        continue
                mapping[old] = fresh
            b.code[-1] = replace(new, gen="splice:" + (instr.gen or instr.op.name), origin=instr.origin)

    try:
        out = _emit_through_builder(code, pos, options, emit)
    except BuilderError:
        return None
    added = range(pos, pos + (len(out) - len(code)))
    return _finish(out, options, _template(program), draw, step, list(added))


@st.composite
def combine_mutation(draw, program, an: Analysis, donor=None, step: int = 1) -> Program:
    """A whole donor program emitted at a drawn point (CombineMutator.swift)."""
    code = _code(program)
    options = _options(program)
    if donor is None:
        donor = draw(programs(options))
    dcode = _code(donor)
    points = [p for p in insertion_points(code, an) if (an.context_at[p] if p < len(code) else Context.JAVASCRIPT) == Context.JAVASCRIPT]
    if not points or not dcode:
        return None
    pos = pick(draw, points)

    def emit(b: Builder) -> None:
        mapping: dict[Var, Var] = {}
        for instr in dcode:
            inputs = tuple(mapping[v] for v in instr.inputs)
            new = b.emit(instr.op, inputs, lift=instr.lift)
            for old, fresh in zip(instr.all_outputs, new.all_outputs):
                mapping[old] = fresh
            b.code[-1] = replace(new, gen=instr.gen, origin=instr.origin)

    try:
        out = _emit_through_builder(code, pos, options, emit)
    except BuilderError:
        return None
    added = range(pos, pos + len(dcode))
    return _finish(out, options, _template(program), draw, step, list(added))


ACTIVATION_TRIGGERS: tuple[Callable[[Builder], None], ...] = (
    lambda b: b.emit(O.LoadArguments()),
    lambda b: _empty_arrow(b),
    lambda b: _empty_try_catch(b),
)


def _empty_arrow(b: Builder) -> None:
    with b.arrow_function():
        pass


def _empty_try_catch(b: Builder) -> None:
    with b.try_() as t:
        with t.catch():
            pass


def _removable_trigger(code: Sequence[Instruction], an: Analysis, f: int) -> list[int] | None:
    """Indexes of the one activation trigger of function `f` when it is its only reason and can
    be removed without changing anything else: an unused `arguments`, an empty arrow function, an
    empty try/catch. Removing it makes the optimizer run."""
    from .analysis import ActivationReason as R
    reasons = an.rhino.activation.get(f, ())
    if len(reasons) != 1:
        return None
    end = block_end(code, f)
    depth = 0
    for i in range(f + 1, end):
        op = code[i].op
        if depth == 0:
            if reasons == (R.ARGUMENTS,) and isinstance(op, O.LoadArguments) and an.num_uses(code[i].output) == 0:
                return [i]
            if reasons == (R.NESTED_FUNCTION,) and isinstance(op, O.BeginArrowFunction) \
                    and isinstance(code[i + 1].op, O.EndArrowFunction) and an.num_uses(code[i].output) == 0:
                return [i, i + 1]
            if reasons == (R.CATCH,) and isinstance(op, O.BeginTry) and isinstance(code[i + 1].op, O.BeginCatch) \
                    and isinstance(code[i + 2].op, O.EndTryCatchFinally):
                return [i, i + 1, i + 2]
        if op.is_block_start:
            depth += 1
        if op.is_block_end and not op.is_block_start:
            depth -= 1
    return None


@st.composite
def activation_mutation(draw, program, an: Analysis, step: int = 1) -> Program:
    """Inserts one activation trigger (`arguments`, an empty arrow, an empty try/catch) at the top
    of a function the Rhino model says is optimizer-typed, or removes one from a function it says
    is not: the compiled path changes, the result must not."""
    code = _code(program)
    options = _options(program)
    functions = [i for i, instr in enumerate(code) if isinstance(instr.op, O.FUNCTION_BEGINS)]
    insert = [f for f in functions if an.rhino.optimizer_runs(f)]
    remove = [(f, r) for f in functions if not an.rhino.optimizer_runs(f) for r in [_removable_trigger(code, an, f)] if r]
    choices = [("insert", f, None) for f in insert] + [("remove", f, r) for f, r in remove]
    if not choices:
        return None
    what, f, r = pick(draw, choices)
    if what == "remove":
        drop = set(r)
        out = [instr for i, instr in enumerate(code) if i not in drop]
        return _finish(out, options, _template(program), draw, step, [])
    trigger = pick(draw, ACTIVATION_TRIGGERS)
    try:
        out = _emit_through_builder(code, f + 1, options, trigger)
    except BuilderError:
        return None
    added = range(f + 1, f + 1 + (len(out) - len(code)))
    return _finish(out, options, _template(program), draw, step, list(added))


@st.composite
def number_flip_mutation(draw, program, an: Analysis, step: int = 1) -> Program:
    """For a number variable of the Rhino model, inserts one assignment whose right-hand side is
    AnyType (a string, undefined, a BigInt, an object) at a drawn point in its function: the shape
    of the recorded compiled-mode bugs (§5.3)."""
    code = _code(program)
    options = _options(program)
    number_vars = [v for v in an.rhino.number_var if an.storage.get(v) not in NOT_ASSIGNABLE]
    if not number_vars:
        return None
    v = pick(draw, sorted(number_vars))
    d = an.defs[v]
    f = an.function_of[d]
    end = block_end(code, f) if f is not None else len(code)
    points = [p for p in insertion_points(code, an) if d < p <= end and v in an.visible_at[min(p, len(code) - 1)]]
    if not points:
        return None
    pos = pick(draw, points)
    shape = draw(st.integers(0, 3))

    def emit(b: Builder) -> None:
        if shape == 0:
            w = b.load_string(pick(draw, INTERESTING_STRINGS))
        elif shape == 1:
            w = b.load_undefined()
        elif shape == 2:
            w = b.load_bigint(pick(draw, INTERESTING_INTEGERS))
        else:
            w = b.create_array([])
        b.reassign(v, w)
        for instr in b.code[-2:]:
            b.code[b.code.index(instr)] = replace(instr, gen="number_flip")

    try:
        out = _emit_through_builder(code, pos, options, emit)
    except BuilderError:
        return None
    added = range(pos, pos + (len(out) - len(code)))
    return _finish(out, options, _template(program), draw, step, list(added))


MUTATORS: tuple[tuple[str, Callable], ...] = (
    ("operation", operation_mutation),
    ("input", input_mutation),
    ("declaration_kind", declaration_kind_mutation),
    ("reassign", reassign_mutation),
    ("codegen", codegen_mutation),
    ("splice", splice_mutation),
    ("combine", combine_mutation),
    ("activation", activation_mutation),
    ("number_flip", number_flip_mutation),
)
MUTATOR_BY_NAME = dict(MUTATORS)


def mutators() -> list[str]:
    return [name for name, _ in MUTATORS]


# ------------------------------------------------------------------------------- mutation and lineages
class Rejected(Exception):
    """The mutator produced nothing, or a child `verify` refuses."""


@st.composite
def try_mutation(draw, program, name: str | None = None, step: int = 1, an: Analysis | None = None):
    """One mutation by a drawn (or named) mutator. Returns `(name, child)`; raises `Rejected` when
    the mutator was not applicable or the child does not verify or lift."""
    code = _code(program)
    options = _options(program)
    an = an or analyse(code, strict=options.strict)
    if name is None:
        name, strategy = pick(draw, MUTATORS)
    else:
        strategy = MUTATOR_BY_NAME[name]
    child = draw(strategy(program, an, step=step))
    if child is None:
        raise Rejected(name + ": not applicable")
    try:
        verify(child.code, strict=options.strict)
        lift(child.code, LiftOptions(probes=False))
    except (InvalidProgram, BuilderError, ValueError, KeyError) as e:
        raise Rejected(f"{name}: {e}") from e
    return name, child


@st.composite
def mutation(draw, program, name: str | None = None, step: int = 1) -> Program:
    """A verified child of `program`; a rejected mutation rejects the test case (design §9)."""
    try:
        _, child = draw(try_mutation(program, name, step))
    except Rejected:
        reject()
    return child


@dataclass
class Step:
    name: str
    program: Program


@dataclass
class Lineage:
    """A base program and the mutation steps applied to it; `.program` is the last one."""
    base: Program
    steps: list[Step] = field(default_factory=list)
    rejected: int = 0

    @property
    def program(self) -> Program:
        return self.steps[-1].program if self.steps else self.base

    @property
    def code(self) -> tuple[Instruction, ...]:
        return self.program.code

    @property
    def options(self) -> GenOptions:
        return self.base.options

    @property
    def template(self) -> str:
        return self.base.template

    @property
    def source(self) -> str:
        return Generated(self.program, self.template).source

    @property
    def plain_source(self) -> str:
        return Generated(self.program, self.template).plain_source

    def __str__(self) -> str:
        return self.source

    def __repr__(self) -> str:
        return "<Lineage " + self.template + " + " + ", ".join(s.name for s in self.steps) + ">"


@st.composite
def evolved(draw, options: GenOptions = GenOptions(), steps: int = 4, base=None) -> Lineage:
    """A lineage: a `programs()` draw (or `base`) and up to `steps` mutations, each preceded by a
    "one more?" boolean (proposal §3.5): the loop shrinks to fewer steps, and the last choice of
    a complete lineage is the `False` that ended it, which is what extending a corpus entry
    flips (`fuzz.extend`). A rejected step is skipped and counted, never fatal."""
    base_program = base.program if isinstance(base, Generated) else base if isinstance(base, Program) else draw(programs(options)).program
    lineage = Lineage(base_program)
    k = 0
    while k < steps and draw(st.booleans()):
        k += 1
        which = draw(st.integers(0, len(MUTATORS) - 1))
        try:
            name, child = draw(try_mutation(lineage.program, MUTATORS[which][0], step=k))
        except Rejected:
            lineage.rejected += 1
            continue
        lineage.steps.append(Step(name, child))
    return lineage


# ------------------------------------------------------------------------------- from a shell
@dataclass
class Mutated:
    """One mutation applied by hand: the parent, the child, and the mutator's name."""
    parent: Program
    name: str
    program: Program

    @property
    def code(self) -> tuple[Instruction, ...]:
        return self.program.code

    @property
    def source(self) -> str:
        return Generated(self.program, self.program.template).source

    @property
    def plain_source(self) -> str:
        return Generated(self.program, self.program.template).plain_source

    def diff(self) -> str:
        """The IL before and after, as a unified diff."""
        before = str(self.parent).splitlines()
        after = str(self.program).splitlines()
        return "\n".join(difflib.unified_diff(before, after, "parent", self.name, lineterm="", n=2))

    def __str__(self) -> str:
        return self.source

    def __repr__(self) -> str:
        return f"<Mutated {self.name}: {len(self.parent.code)} -> {len(self.code)} instructions>"


def mutate(program, seed: int = 0, *, name: str | None = None, step: int = 1) -> Mutated:
    """One mutation of a program (a `Program`, a `Generated` or instructions), deterministically
    from a seed; `name` fixes the mutator. Raises `Rejected` when that seed's choice is not
    applicable or does not verify, so another seed can be tried."""
    from .strategies import draw as draw_once

    parent = program.program if isinstance(program, Generated) else program
    if not isinstance(parent, Program):
        parent = Program(tuple(_code(program)), _options(program), _template(program))
    chosen, child = draw_once(try_mutation(parent, name, step), seed)
    return Mutated(parent, chosen, child)


def lineage(seed: int = 0, *, steps: int = 4, template: str | None = None, **options) -> Lineage:
    """A base program and up to `steps` mutations from one seed."""
    from .strategies import draw as draw_once

    opts = GenOptions(**options)
    base = draw_once(programs(opts, template), seed)
    return draw_once(evolved(opts, steps, base=base), seed)
