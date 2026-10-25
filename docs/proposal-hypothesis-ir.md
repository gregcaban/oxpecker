# Proposal: an IR with generators *and mutators* as Hypothesis strategies

Status: proposal, 2026-10-25. Not implemented in oxpecker; a working spike lives in
`~/Dev/hypothesis-scratchpad/ir-mutation-spike/` (code, tests, raw results). This document
captures the design discussion that led to it, the background it relies on, the evidence the
spike produced, and a migration plan. `docs/design.md` describes the system as built today.

## 0. Summary

oxpecker's generators emit JavaScript **strings** directly, and its coverage-guided loop
(`oxpecker fuzz`) runs on `ChoiceRand`, a re-implementation of the core of Hypothesis's engine
(a program as a list of ints, folded into each draw's range) with its own mutation operators and
its own two shrinkers. Both are at odds with the intended design:

- generators should be **composable Hypothesis strategies** built from stock strategies,
- producing an **IR** (FuzzIL-style: instructions over SSA variables) that a lifter turns into JS,
- with **structural mutators** (input, operation, code generation, splice — Fuzzilli's) that are
  themselves strategies,
- and **Hypothesis's shrinker** minimising every finding, including findings reached by many
  mutation steps.

The enabling idea is the **lineage**: a corpus entry is not a program but "generate a program,
then mutate, mutate, mutate", drawn by one composite strategy (`evolved()`, §3.5). The whole
history is one Hypothesis test case, so the shrinker can delete mutation steps, shrink the base
program, and simplify each step. An external loop drives it through public API
(`fuzz_one_input`, the example database, a normal `[reuse, shrink]` run).

The spike shows the core is sound: fuzz-loop findings shrink to the exact minimum with every
mutation step removed (as small as, or smaller than, findings from plain generation), IR mutations
keep 98% of the parent where blind mutations keep 77%, and lineages up to 100 steps replay and
shrink (at 200 steps shrinking hits Hypothesis's 300 s cap). The open issues are performance, not soundness: one lineage execution
costs O(steps × program size), Hypothesis caps a test case at ~8 KB of serialized choices, and
lineage depth grows with corpus generations — bounded the way Fuzzilli bounds it, by minimizing
every entry on insert. Here that is no new code: corpus compaction is the same Hypothesis shrink
run as finding minimisation, with "reaches these new probes" as the property; it needs probe
*sets* from the harness, a deterministic property and a shrink budget (§5.3). Rebasing remains
the escape hatch. Shrink quality also
depends heavily on writing strategies by a set of rules (§3.8) that are not obvious and were all
learned the hard way. §4 has the numbers.

## 1. Motivation: what is wrong today

**Strings, not structure.** Every generator builds text with f-strings, e.g. `direct_call_arity`:

```python
f = b.declare(T.FUNCTION)
with b.block(f"function {f}(a0, a1) {{", kind="function"):
    b.scopes[-1].vars.extend([Var("a0", T.UNKNOWN), Var("a1", T.UNKNOWN)])
    b.emit(f"return {b.choice(['a0 + a1', '[a0, a1]', 'a0 * 2 + (a1 | 0)', ...])};")
res = b.fresh(T.ARRAY, "[]")
b.emit(f"{res}.push({f}({b.choice(INT32S)}, {b.choice(INT32S)}));")
```

and syntax is patched by hand, generator by generator (`gen/generators/operations.py`):

```python
if x[:1] in "-+":
    x = f"({x})"        # `--1` / `+-1` would parse as an update operator
if op == "**":
    left = f"({left})"  # `-1 ** 2` is a SyntaxError
```

With an IR, precedence and parenthesisation are the lifter's job, once. And without structure
there is nothing to mutate except the random choices that produced the text — which is why
`ChoiceRand` exists.

**A parallel engine.** `gen/rand.py` has two backends behind one `Rand` interface:
`HypothesisRand` (Hypothesis `draw`, used by `oxpecker gen` and the property tests) and
`ChoiceRand` (a recorded `list[int]`, values folded modulo the requested range, fresh values from
`random.Random` when the list runs out, used by `oxpecker fuzz`). `fuzz.py` mutates those lists
(point changes, block insert/delete/duplicate, splice, havoc), keeps the ones that hit new JaCoCo
probes, and shrinks findings with its own choice-level ddmin and then a statement/line-level
source ddmin. None of that uses Hypothesis — a re-implementation of what Hypothesis already does,
minus its shrinker.

**A misleading comparison.** `rand.py` and `design.md` §6.6 say the corpus loop mutates choice
sequences "the same way Fuzzilli mutates FuzzIL". It does not: it mutates the *input of a
generator*, which is Zest's design (§2.1), not Fuzzilli's. The difference shows in what survives
a mutation (§2.3, §4.4).

## 2. Background

### 2.1 Three families of mutation-based fuzzer

| | what is mutated | output valid? | examples |
|---|---|---|---|
| **byte-level** | the target's raw input bytes | rarely, for structured input: bit-flips in JS source are syntax errors | AFL [1], libFuzzer [2] |
| **generator-input** | the random choices fed *into a generator*, which decodes any choice sequence into a valid input | always | Zest/JQF [3], `oxpecker fuzz` today, Hypothesis `fuzz_one_input` [6], HypoFuzz [8] |
| **IR / grammar-tree** | the program itself, as an IR or a parse tree | always, if mutators keep the invariants | Fuzzilli [4], Nautilus [10], Superion [11], CodeAlchemist [12], DIE [13] |

AFL's mutations (bit flips, arithmetic, block insert/delete, splicing two inputs, "havoc"
stacks) are blind; its strength is coverage feedback (edge coverage with hit-count buckets) and
corpus scheduling. Zest keeps AFL's loop but mutates a generator's *parameters* — the sequence of
random choices — so every mutant is valid by construction; the generator acts as a decoder.
Fuzzilli goes further: the corpus holds programs in FuzzIL, and mutators edit the program, with
full knowledge of variables, scopes and (inferred) types:

- `InputMutator` — replace an instruction's input with another visible variable;
- `OperationMutator` — change an instruction's parameter (constant, operator, property name);
- `SpliceMutator` — copy an instruction and the code it depends on from another program;
- `CombineMutator` — insert a whole other program;
- `CodeGenMutator` — run code generators at a point, using the variables visible there;
- plus exploration/probing/fixup mutators that use runtime feedback.

Its minimizer works on the IR too (remove instructions, simplify, inline), and its `JSTyper`
re-infers types after each change.

### 2.2 Hypothesis internals that matter here

Hypothesis [5] separates *what* a strategy generates from *how* the randomness is represented.
The representation is the **choice sequence** (the Conjecture engine; MacIver & Donaldson [7]
explain why this makes a generic shrinker possible).

**A worked example.** With `env = ["a", "b"]`, `depth = 2`, this strategy (the one that started
the discussion):

```python
@st.composite
def expressions(draw, env, depth=3):
    if depth <= 0:
        if env and draw(st.booleans()):
            return Identifier(draw(st.sampled_from(env)))
        return IntLiteral(draw(st.integers(-10, 10)))
    choices = ["literal"] + (["identifier"] if env else []) + ["binary"]
    kind = draw(st.sampled_from(choices))
    if kind == "literal":
        return IntLiteral(draw(st.integers(-10, 10)))
    if kind == "identifier":
        return Identifier(draw(st.sampled_from(env)))
    return BinaryExpr(draw(st.sampled_from(["+", "-", "*"])),
                      draw(expressions(env, depth - 1)),
                      draw(expressions(env, depth - 1)))
```

produces `(a + 3) * b` from this choice sequence — verified by replaying it with
`ConjectureData.for_choices` (`ir-mutation-spike/../ast-expr-generator/choices_demo.py`):

```
index:   0  1  2  3  4     5  6      7  8  9
choice:  2  2  2  0  True  0  False  3  1  1
         │  │  │  │  │     │  │      │  │  └ right: identifier index 1 → b
         │  │  │  │  │     │  │      │  └ right: kind 1 = identifier
         │  │  │  │  │     │  │      └ left.right: integers(-10, 10) → 3
         │  │  │  │  │     │  └ left.right: boolean → literal
         │  │  │  │  │     └ left.left: identifier index 0 → a
         │  │  │  │  └ left.left: boolean → identifier
         │  │  │  └ left: op 0 = "+"
         │  │  └ left: kind 2 = binary
         │  └ root: op 2 = "*"
         └ root: kind 2 = binary
```

- **Only primitive draws record choices** (`draw_integer`, `draw_boolean`, `draw_float`,
  `draw_string`, `draw_bytes`), each as a `ChoiceNode(type, value, constraints)`.
  `sampled_from` is an integer index draw: position 0 is `('integer', 2, 0..2)`. A composite
  records nothing itself; the order of choices is the order Python executes the draws —
  depth-first, left to right, because arguments evaluate left to right.
- **Spans** record the nesting: each `draw(strategy)` opens a span over the choices it makes.
  Here: root `[0, 10)`, left subtree `[2, 8)`, `left.left` `[4, 6)`, right `[8, 10)`. The flat
  list plus spans is the tree.
- **Replay is re-running the strategy**: it is deterministic given its choices, so the choice
  sequence *is* the test case. The example database stores choice sequences; shrinking is editing
  them and replaying.
- **Misalignment**: if a replayed choice does not fit the draw that asks for it (wrong type, out
  of range), Hypothesis records `misaligned_at` and uses the **simplest** value for that draw and
  everything after it. (Not a reinterpretation of the leftovers: that is what `ChoiceRand` does.)
- **Providers** decide fresh values: `HypothesisProvider` (random with heuristics),
  `BytestringProvider` (decodes a byte buffer — one byte per boolean, `>= 128` is `True`; an
  integer takes `bits_to_bytes(range)` bytes with rejection resampling), and pluggable
  **alternative backends** (`settings(backend=...)`, the `PrimitiveProvider` interface with
  `draw_*`, `span_start`/`span_end`, `replay_choices`; hypothesis-crosshair [9] is one).
- **Limits**: every `ConjectureData` has `max_length = BUFFER_SIZE` (8 * 1024), a cap on the
  choice sequence's *serialized* size (`choices_size`, the database byte format), checked on every
  draw; going over is an *overrun* (details in §5.2). Span nesting is capped at `MAX_DEPTH = 100`.
- **Engine phases**: `explicit`, `reuse` (replay the database), `generate`, `target` (hill-climb
  `target()` scores; the best are kept in the database as a Pareto front), `shrink`, `explain`.
- **Database**: entries are choice sequences keyed by a digest of the test function. The primary
  key holds minimal failures, `<key>.secondary` other failing cases; `reuse` replays the primary
  corpus and tops up from the secondary one when the primary is short. `@seed` disables writes.
- **`fuzz_one_input(buffer)`** runs the test once on `buffer` with `BytestringProvider`. Valid →
  returns the bytes consumed (canonical, replayable); invalid (filtered, too short, overrun) →
  `None`; failing → saves the *choice sequence* to `<key>.secondary` and re-raises. A normal run
  of the test afterwards replays and shrinks it. This is the documented bridge to external
  fuzzers (Atheris, libFuzzer, AFL) [6].
- **The shrinker** minimises the choice sequence in shortlex order (fewer choices, then smaller
  values), with passes `try_trivial_spans`, `node_program("X"*k)` (adaptive deletion of runs),
  `pass_to_descendant` (replace a span with a sub-span: a tree with its subtree),
  `reorder_spans`, `minimize_duplicated_choices`, `minimize_individual_choices` (lower a value;
  if the size changes, also try deleting what follows), `redistribute_numeric_pairs`,
  `lower_integers_together`, and a few for strings. Budgets: `MAX_SHRINKS = 500`,
  `MAX_SHRINKING_SECONDS = 300`.

All of this is about choices. Whether "smaller choices" means "smaller program" is decided by how
strategies map choices to structure — the subject of §3.8.

### 2.3 Mutating choices versus mutating the program

On the same example, `(a + 3) * b`:

**Blind point mutation of a choice** (what an AFL-style mutator on Hypothesis's input does):

- change position 3 from `0` to `1` → `(a - 3) * b`. Local: this choice does not decide structure.
- change position 2 from `2` (binary) to `0` (literal) → the left side reads one integer
  (position 3, `0`); the right side then asks for an integer in `0..2` and finds `True` —
  misaligned — so it and everything after get the simplest values: `0 * 0`. The untouched `b`
  is gone. One edit to a structural choice discards the parent's tail. (With `ChoiceRand`'s
  folding the result is `0 * a`: different garbage, same loss.)
- replace `(a + 3)` with `b - (a * 7)` from another corpus program: a blind mutator would have to
  overwrite exactly positions 2–7 with exactly the right values. It happens only by accident.

**Span-aware splice of choices**: replace span `[2, 8)` with a donor's `expressions` span. The
right subtree survives; the donor's shape transfers, but its variable picks are re-read in the new
environment (index 2 into the donor's `["x", "y", "z"]` folds to whatever index 2 means here).
Hypothesis's own shrinker uses spans this way.

**IR mutation** (Fuzzilli): edit the program and nothing else. In FuzzIL form, `(a + 3) * b` is

```
2: v2 = LoadInt 3
3: v3 = BinOp + v0 v2
4: v4 = BinOp * v3 v1
```

| mutator | on this program | what it knows that choices don't |
|---|---|---|
| operation | `+` → `-` | which slot is the operator |
| input | `3: v3 = BinOp + v1 v2` → `(b + 3) * b` | variables visible at the instruction, and their types |
| code generation | insert `v5 = LoadInt 7; v6 = BinOp - v1 v5` after 2 | the context at the insertion point |
| splice | copy a donor instruction with its backward slice, renamed | the donor's data flow |

"Replace an expression subtree" is not a primitive in a flat IR; it is code generation plus an
input mutation:

```
2: v2 = LoadInt 3
3: v3 = BinOp + v0 v2
   v5 = LoadInt 7              ← code generation
   v6 = BinOp - v1 v5          ←
4: v4 = BinOp * v6 v1          ← input mutation: v3 → v6
```

(`v2`, `v3` become dead; the minimizer or the lifter drops them.) Everything not touched stays
byte-identical, so whatever compiled-mode path the parent reached is still reached, now with a
perturbation — the reason Fuzzilli's corpus compounds.

## 3. Proposal

### 3.1 IR

FuzzIL-style: a program is a tuple of `Instr(op, inputs, outputs, inner)` over integer variable
ids, SSA (each variable defined once). Blocks are matched `Begin…`/`End…` instructions;
`inner` holds variables visible only inside the block (parameters). Operations are small frozen
dataclasses (`LoadInt(value)`, `BinOp(op)`, `BeginFunction(arity)`, `Call()`, `Return()`, …).

Flat IR rather than an AST, because the mutators — the point of the exercise — get simpler:

| | AST | flat IR |
|---|---|---|
| strategy style | recursive, returns nodes | generators append to a context |
| input mutation | needs scope analysis of the tree | "visible at index i" is a scan |
| subtree replace | direct | code generation + input mutation |
| splice | subtree + renaming free variables | backward slice + renaming |
| minimising | replace subtrees, delete statements | delete instructions |
| readable JS | free | needs an inlining lifter |
| JS shape | arbitrary nesting | temporaries unless inlined |

The last row is a feature for Rhino: its optimizer type-specialises locals, so `let v3 = v0 + v2`
and an inlined `(v0 + 3)` take different compiled-mode paths. The lifter's **inlining policy**
(which single-use values to inline) becomes a knob — even a drawn choice — that reaches both.

Probes (`__probe`), `// [generator]` comments and the `strict` prologue become lifter concerns.
Generator attribution for the inspector UI comes from instruction provenance instead of
`generator_spans`.

### 3.2 Analysis, types, invariants

Types are **never stored** on instructions. `analyse(prog)` recomputes scopes, definitions and
types from scratch (iterating to a fixpoint, because call results depend on function return
types, which can depend on calls). `verify(prog)` checks the invariants every mutator must keep:

1. every input is defined before its use and visible there (scopes, closed blocks);
2. blocks are balanced; `Return` only inside a function;
3. each input slot's type requirement holds *under freshly inferred types* (only functions can be
   called; no arithmetic on functions; in oxpecker: constructors for `new`, objects for property
   access, …).

Stale types are therefore impossible by construction; the cost is one analysis per mutation step.
The case this guards against is real (spike test `test_types_are_recomputed_after_a_mutation`):
`f` returns a function `g`, so `c = f()` is callable and `c()` is valid; an input mutation on
`f`'s `Return` makes it return an int, `c` becomes an int, and `c()` is now a call of a
non-function. Only re-inference notices. This is Fuzzilli's `JSTyper` re-run, in miniature.

### 3.3 Generators as strategies

Each generator is a `@st.composite` taking a `Ctx` — the instructions being built and the scope
and type state at the insertion point — and appending instructions:

```python
@st.composite
def binop(draw, ctx, depth):
    pool = ctx.visible(func=False)                      # most recent first, see §3.8
    a, b = pick(draw, pool), pick(draw, pool)
    ctx.emit(BinOp(draw(st.sampled_from(BINOPS))), (a, b))

@st.composite
def function_decl(draw, ctx, depth):
    f = ctx.begin_function(draw(st.integers(0, 3)))
    draw(code(ctx, depth - 1, (1, 4)))                  # the body sees params + outer variables
    ctx.ret(f, pick(draw, ctx.visible(func=False) or [ctx.emit(LoadInt(0))]))
    ctx.end_function()

@st.composite
def code(draw, ctx, depth, n=(1, 8)):                   # "make some code here"
    draw(st.lists(one_generator(ctx, depth), min_size=n[0], max_size=n[1]))

@st.composite
def programs(draw):
    ctx = Ctx()
    draw(code(ctx, depth=2))
    prog = tuple(ctx.instrs)
    verify(prog)             # a failure is a generator bug, not something to filter
    return prog
```

`code(ctx)` is the single entry point both for fresh programs (empty context) and for the
code-generation mutator (`Ctx.at(prog, pos)`: the context before `prog[pos]`). One grammar, one
place. `Ctx` is mutable but lives inside one draw, which is all Hypothesis needs. Templates and
swarm selection (`design.md` §6.4, Groce et al. [14]) become strategies over the same `Ctx`.

### 3.4 Mutators as strategies

A mutator is `program -> strategy[program]`: every decision it makes is a Hypothesis draw.

```python
@st.composite
def input_mutation(draw, prog, an):
    i, k, cands = pick(draw, _input_slots(prog, an))    # slots with a compatible alternative
    ins = prog[i]
    inputs = ins.inputs[:k] + (pick(draw, cands),) + ins.inputs[k + 1:]
    return replace(prog, i, Instr(ins.op, inputs, ins.outputs, ins.inner))

@st.composite
def mutation(draw, prog):
    an = verify(prog)
    name, mutator, ok = draw(st.sampled_from(MUTATORS))  # fixed list, simplest first
    if not ok(prog, an):
        name, mutator = "codegen", codegen_mutation      # always applicable
    child = draw(mutator(prog, an))
    try:
        verify(child)                                    # fresh types: §3.2
    except InvalidProgram:
        reject()
    return name, child
```

The spike has operation, input, code generation and splice. Splice takes a donor instruction at
the top level of a donor program, its backward slice (whole blocks when it depends on a function),
renames the slice's variables above every id in the host, and inserts it at a drawn point.
Fuzzilli's further step — rewiring some of the slice's inputs to host variables — is a follow-up
input mutation here, or a refinement. Combine is splice of the whole donor.

Because mutation randomness goes through `draw`, there is no `.example()` outside a test and no
second source of randomness: a mutation is just more of the test case.

### 3.5 Lineages: the parent must be drawn

The question that shaped the design: if mutators are strategies, why can't Hypothesis shrink
`mutate(program)` like any other composite? It can — **if the program is drawn in the same test
case**:

```python
# (A) parent DRAWN: one composite; the choice sequence covers parent and mutation
@st.composite
def mutant(draw):
    parent = draw(programs())
    return draw(mutation(parent))

# (B) parent PASSED IN: a corpus value; to Hypothesis it is a constant
def mutant_of(parent):
    return mutation(parent)
```

In (A) the shrinker can shrink the parent, the mutation, or drop the mutation. In (B) it can only
shrink the mutation's few choices; the parent stays at full size, and a custom minimiser is needed
— oxpecker's situation today, and Fuzzilli's. So every corpus entry is a **lineage** in form (A):

```python
@st.composite
def evolved(draw, max_steps=10_000):
    prog = draw(programs())
    steps = []
    while len(steps) < max_steps and draw(st.booleans()):   # "one more?"; shrinks towards False
        name, prog = draw(mutation(prog))
        steps.append(name)
    return Lineage(prog, steps)
```

- A **loop**, not recursion: every step's span is at the same depth (`MAX_DEPTH = 100`), and
  extending a lineage only changes the *end* of its choice sequence.
- **Extending** a corpus entry = its bytes with the final "stop" boolean flipped to `True`
  (`b"\xff"`) plus fresh bytes for the new step. The prefix replays identically, so the parent is
  reproduced exactly; `fuzz_one_input` returns the consumed bytes of the child — the new entry.
- **Splice donors** are drawn inside the step (`draw(programs())` in the spike). In the full
  design a donor is another corpus entry: its choices go into that span, so it is shrinkable too.
  (Not built in the spike; see §5.)
- **Shrinking a finding** is Hypothesis's shrinker on that one choice sequence: it deletes
  mutation steps (spans inside the loop), shrinks the base program, simplifies each step. The
  spike's findings shrink with no mutation steps left — the shrinker discovers which steps
  the bug needed.

### 3.6 The fuzz loop

```
corpus: list of buffers (bytes consumed by fuzz_one_input), persisted; coverage.exec as today
loop:
  with prob p_new: buffer ← fresh random bytes                     (a new lineage, 0+ steps)
  else:            parent ← choice(corpus)
                   repeat up to N times (Fuzzilli: 5):             (consecutive mutations)
                     buffer ← extend(parent)                       (one more mutation step)
                     out ← test.hypothesis.fuzz_one_input(buffer)
                       None   → rejected/overrun; count it, keep the old parent
                       raises → a finding: saved to the example database (<key>.secondary)
                       bytes  → parent ← out;  newProbes > 0 ?  insert(out)
insert(entry): ALWAYS shrink the lineage keeping "reaches these probes" (§5.3) → corpus.append(minimal)
findings: run the same test with phases=[reuse, shrink] → minimal lineage → lift → reproducer
```

The test body is today's: run the lifted program in both modes through `HarnessClient`, compare,
raise on a hard verdict (per-signature exception classes, as `tests/test_differential.py` does, so
distinct bugs shrink separately), and report `newProbes` back to the driver. JaCoCo coverage,
`compare.py`, `findings.py`, the corpora and the UI stay as they are.

The driver could be HypoFuzz [8] instead of a hand-written loop (check its licence first), or a
custom `PrimitiveProvider` backend if byte-level extension turns out too blunt. The spike's loop
is ~30 lines.

### 3.7 Shrinking

Hypothesis only. `fuzz.shrink` (choice-level ddmin) and `shrink.shrink_source` (line-level ddmin)
go away for generated programs; `shrink_source` may stay for corpus replays (test262 etc.), which
have no strategy behind them. If the residual of §3.8 rule 9 matters in practice, a final
IR-level dead-code pass on the reproducer is cheap and safe.

### 3.8 Rules for writing generator and mutator strategies

Learned while building the spike; each was measured or read in Hypothesis's source. The effect is
large: with rules 1–3 broken, plain `programs()` shrank `mod_zero` (minimal 2 instructions) to 3–4
instructions; with them, to exactly 2 on 8/8 seeds.

1. **Collections are `st.lists`, never "draw a count, then loop".**
   `for _ in range(draw(st.integers(lo, hi)))` makes removing a middle element two coordinated
   edits (lower the count *and* delete its span), which the shrinker rarely finds — shrunk programs
   kept dead `let v1 = 0;` lines. `st.lists` records a "more?" boolean before each element, so
   deleting an element is deleting one contiguous run of choices. A side-effecting element
   strategy (one that appends to `Ctx`) works fine inside `st.lists`.
2. **Never `sampled_from` a pool whose size depends on context.** A one-element `sampled_from` is
   `just` and records *no* choice; `st.integers(0, 0)` records one. When a context-dependent pool
   shrinks to one element, every later choice shifts and the rest of the test case is misaligned
   (seen directly in `misaligned_at`). Use `pick(draw, xs) = xs[draw(st.integers(0, len(xs)-1))]`.
   Constant pools (`INTS`, `BINOPS`) can stay `sampled_from`.
3. **Give every choice a context-independent meaning.** Choose generators and mutators by index
   into a **fixed** list, with a fallback when the pick cannot run here — not from "the
   generators runnable here", where index 4 means `call` in one context and `function_decl` in
   another. Deleting an earlier function would otherwise re-mean later choices.
4. **Order every pool simplest-first.** The shrinker lowers indices towards 0: index 0 should be
   the simplest generator (`load_int`), constant (`0`, `""`), mutator (operation).
5. **Order visible variables most-recent-first.** A variable reference is an index into the
   visible variables, so it is positional. Oldest-first, deleting an earlier definition shifted
   every later reference, and shrinking pulled references to the *oldest* variables, keeping old
   code alive. Most-recent-first, shrinking pulls references towards local values and older code
   becomes dead, then deleted. (`recursive_arith` under `evolved()`: from sizes 5–11 to the
   minimum 5 on 8/8 seeds.) Fuzzilli's generators prefer recent variables for a similar reason.
6. **Draw the parent; never pass a program in.** Only choices made inside the test case shrink
   (§3.5). Same for splice donors.
7. **Lineages are a loop over a boolean.** Constant span depth, and extension only appends.
8. **Recompute, then verify, after every mutation; `reject()` what fails.** Never store types.
   Count rejections: each is a wasted harness run.
9. **Know the residual.** Some minima need a deletion *and* a reference whose index goes *up*
   (`v3 + v1` → `v1 + v1`); no single pass does that, so plain generation sometimes stops two
   instructions short. Candidate mitigations: references by recency among same-type variables;
   an IR dead-code pass on reproducers.
10. **Spend choices deliberately.** A lineage's whole history shares one ~8 KB budget of
    serialized choices (§5.2): no draws whose value is thrown away, small integer ranges where a
    small range will do, few mutation steps per round, donors kept small.
11. **Tooling facts**: `@seed` disables database writes; two `@given` wrappers around one body
    function share a database key (that is how a fuzz target and a shrink run share findings);
    `@given` mixed with pytest fixtures needs keyword arguments.

## 4. Evidence: the spike

`ir-mutation-spike/` in the scratchpad: an IR with int/num/str constants, binary operators,
functions (with recursion), calls and returns; type inference; verify; a JS lifter (every lifted
program parses with `node --check`); generators, the four mutators, `evolved()`; a fuzz loop over
`fuzz_one_input` with a feature-set stand-in for JaCoCo; planted bugs as IR predicates (no JVM).
It measures the search and shrinking machinery, not Rhino.

Full run, 2026-10-25 (`results/20261025-171842.json`): 8 seeds per cell, a budget of 4000 test
calls per search, medians shown. "Size" is in IR instructions. Shrinking is always Hypothesis's,
via a `[reuse, shrink]` run (for `fuzz`, of the failure `fuzz_one_input` saved).

### 4.1 Do fuzz-loop findings shrink?

| bug (minimal size) | condition | found | calls to find | size at find | shrunk | steps left | shrink calls | shrink s |
|---|---|---|---|---|---|---|---|---|
| `mod_zero` (2) | programs | 8/8 | 848 | – | **2** | – | 41 | 0.3 |
| | evolved | 8/8 | 516 | – | **2** | 0 | 54 | 0.4 |
| | fuzz | 8/8 | 602 | 25 | **2** | 0 | 78 | 0.6 |
| `short_call_mul` (5) | programs | 8/8 | 449 | – | **5** | – | 217 | 1.7 |
| | evolved | 8/8 | 1467 | – | **5** | 0 | 211 | 2.0 |
| | fuzz | 8/8 | 184 | 24 | **5** | 0 | 333 | 2.7 |
| `recursive_arith` (5) | programs | 8/8 | 137 | – | 6 | – | 127 | 1.2 |
| | evolved | 8/8 | 195 | – | **5** | 0 | 159 | 1.6 |
| | fuzz | 8/8 | 31 | 23 | **5** | 0 | 216 | 1.9 |

Findings reached through mutation lineages shrink to the **exact minimum** — the same as, or
better than, plain generation — and the shrinker removes every mutation step (the minimal
lineage is a base program alone). Shrinking costs 1.5–2× the calls of shrinking a fresh program.
The feature-guided loop finds the structural bugs in fewer calls than generation (`fuzz` 31 vs
`programs` 137, 184 vs 449); the planted bugs are too easy to say more about search.

These numbers are *after* the rules of §3.8. Before them, plain `programs()` shrank `mod_zero` to
3–4 instructions and lineages to 5–11 for `recursive_arith`.

### 4.2 Long lineages

Forced lineages of k steps (no selection), saved as a failure, replayed and shrunk:

| steps | saved choices | program size | replays | shrunk size | shrink calls | shrink s |
|---|---|---|---|---|---|---|
| 5 | 112 | 13 | yes | 1 | 111 | 1.0 |
| 20 | 306 | 41 | yes | 1 | 206 | 5.0 |
| 50 | 610 | 70 | yes | 1 | 575 | 29.9 |
| 100 | 1023 | 116 | yes | 1 | 1160 | 103.2 |
| 200 | 2271 | 268 | yes | **267** | 772 | **301.6** (cap) |

(Here the property is "has k steps", so the steps cannot be deleted; the shrinker reduces each to
a trivial mutation of a 1-instruction program.) Up to 100 steps everything works; at 200 the
shrink runs into `MAX_SHRINKING_SECONDS = 300` and stops almost where it started.

Growing one lineage step by step until extension fails:

| steps | entropy bytes | program size | one extension (ms) |
|---|---|---|---|
| 1 | 62 | 9 | 5 |
| 10 | 284 | 31 | 31 |
| 50 | 1083 | 111 | 211 |
| 100 | 2099 | 225 | 564 |
| 200 | 4612 | 464 | 3605 |

Extension stops at **step 236** (5574 entropy bytes, 567 instructions): the test case overruns
`BUFFER_SIZE`. Per-extension time grows super-linearly (O(steps × program size), §5.1; the
timing includes retries after rejected mutations). Both tables argue for the same thing: lineages
must stay shallow — hence shrink on every insert (§5.3).

### 4.3 Locality

One mutation of a parent (40 parents × 25 mutations each); "preserved" is the LCS of instruction
signatures over the parent's length:

| mutation | invalid | identical | preserved (mean) | preserved (median) | ≥ 90 % preserved |
|---|---|---|---|---|---|
| blind byte mutation (AFL through the generator) | 8.3 % | 38.8 % | 0.77 | 0.91 | 50.7 % |
| one IR mutation step | 0.4 % | 28.6 % | **0.98** | **1.00** | **92.5 %** |

Blind mutation either does nothing (39 % identical — the byte landed somewhere irrelevant) or
loses a quarter of the parent on average; IR mutation changes something small and keeps the rest.
The IR mutations' 29 % "identical" are no-op operation mutations (a constant replaced by itself),
an easy fix (exclude the current value).

## 5. Limits and open problems

### 5.1 What a lineage costs, precisely

Three different quantities get called "size"; they are not interchangeable.

| quantity | what it is | grows with |
|---|---|---|
| **mutation steps** | iterations of the `evolved()` loop (`Lineage.steps`) | corpus generations (§5.3) |
| **choices** | `ChoiceNode`s recorded, one per primitive draw (`draw_integer`, `draw_boolean`, …); typed values with constraints, not bits | base program size + ~11 per step in the spike + each splice donor's whole program |
| **serialized size** | `choices_size(choices) = len(choices_to_bytes(choices))`: the choice sequence in the database's byte format (`internal/conjecture/choice.py`) | the number of choices and the size of their values (booleans and small integers are a couple of bytes; big integers, strings, bytes more) |

(`fuzz_one_input`'s buffer is a fourth measure: entropy bytes consumed by `BytestringProvider` —
one per boolean, `bits_to_bytes(range)` per integer, plus rejection-sampling retries. It is the
corpus format, not what the cap counts.)

**Execution time.** Executing a lineage test case — a fuzz run, a database replay, each shrink
attempt — re-runs the strategy from the top: `programs()`, then every mutation step in order,
each with two full analyses (`verify` before and after) linear in the *current* program size,
which itself grows with the steps. So one execution is **O(steps × program size)**, not O(steps);
in the spike, closer to O(steps²). Growing a lineage one step at a time multiplies that by the
number of steps again, because each extension re-executes everything before it. The analysis
dominates, not the number of choices. For oxpecker the yardstick is the harness: one run is
~200–500 ms in both modes, a 50-step replay in the spike is tens of milliseconds. Replay cost
shows only for long lineages, and in shrinking (hundreds of executions) first. Caching analyses
of shared prefixes is an obvious optimisation if it ever matters.

### 5.2 The size cap

`BUFFER_SIZE = 8 * 1024` (`internal/conjecture/engine.py`) is a **per-test-case cap on the
serialized size of the choice sequence**:

- every `ConjectureData` starts with `length = 0`, `max_length = BUFFER_SIZE` (`data.py`);
  each recorded draw adds `choices_size([value])`; when `length + size > max_length` the test case
  is marked **overrun** and abandoned (`mark_overrun`);
- it applies to every execution: generation, database replay, shrink attempts, and
  `fuzz_one_input`, which builds its own `ConjectureData` — an overrun there returns `None`.
  Shrinking only makes sequences smaller, so it never trips the cap; generating and extending can;
- during generation there is also a health check: if even the simplest test case uses more than
  half the budget, the test fails with "data too large" (`engine.py`,
  `zero_data.length * 2 > BUFFER_SIZE`);
- a separate optional `max_choices` caps the *number* of choices; the engine sets it in some
  internal calls, `None` (unlimited) by default;
- it is a module constant, not a setting. The source says its unit "does not have any defined
  semantics, and you should not rely on it, except that a linear increase BUFFER_SIZE will
  linearly increase the amount of data". Monkeypatching it is possible but is reliance on
  internals; treat ~8 KB of serialized choices as a fixed budget per lineage.

Everything in a lineage counts against it: base program, every step, every splice donor (a whole
drawn program each — donors can cost more than the step that uses them). The **base program**
matters as much as the steps: oxpecker's real programs (133 generators, templates, up to 12
statements plus nesting) will make many more choices than the spike's, so measuring a fresh
program's share of the budget is the first thing to do in the migration (§6 step 1).

### 5.3 Why lineages get deep, and how to keep them shallow

Steps per *round* are ours to choose and small: Fuzzilli's `MutationEngine` applies
`--consecutiveMutations` (**default 5**) mutations to a corpus sample per round, promoting a mutant
to be the next parent only if it executed successfully. But corpus entries are mutated from other
corpus entries, so lineage depth grows with **corpus generations** — five per round is a 100-step
lineage after twenty generations of discoveries. Fuzzilli has the same growth and bounds it by
**minimizing every new-coverage sample before adding it to the corpus** (`HowFuzzilliWorks.md`:
otherwise programs "would eventually become too large to execute within the time limit", and
unnecessary features degrade later mutations).

The design therefore **shrinks every entry on insert** (decided 2026-10-25), and keeps steps per
round small (Fuzzilli's 5 as a starting point; the spike uses 1). Rebasing is the escape hatch.

**Shrink on insert.** Corpus compaction is the same mechanism as finding minimisation: a
`[reuse, shrink]` run of the lineage test, with a different property in the test body — "raises
when the run reaches probe set P" instead of "raises on this hard verdict". The shrinker deletes
the mutation steps that were not needed (on the planted bugs it removed all of them, §4.1), shrinks the base
program and simplifies each step; the minimal lineage becomes the corpus entry. No compaction code
of our own. What it needs:

1. **Probe sets from the harness, not counts.** `Coverage.drain()` reports probes new against the
   JVM's cumulative store, so on a re-run they are no longer new and the count is 0. The property
   must be "this run hits P", P being the probes that were new at discovery: the harness returns
   the hit probe ids for a run (or checks a set the client sends).
2. **A deterministic property.** Some Rhino probes fire once per JVM (static initialisers, lazy
   caches, first class loading); a program can show them as new once and never reach them again,
   which Hypothesis reports as a flaky test. Before shrinking, re-run the entry and keep only the
   probes of P it reaches again.
3. **A budget.** The spike's shrinks took 50–400 test calls; at ~2–5 harness runs/s that is minutes
   per insert, and inserts are frequent early in a campaign. Hypothesis exposes no shrink budget
   (`MAX_SHRINKS`, `MAX_SHRINKING_SECONDS` are internal), so the budget is ours to build — open
   design point (e.g. stop the shrink run from the test body after N calls and take the smallest
   lineage seen so far).
4. **A deliberate trade-off.** Fuzzilli minimizes on insert; DIE [13] argues that minimizing strips
   the "aspects" around the new behaviour that made a seed productive for later mutation.
   Shrinking to "reaches P" keeps exactly what was new. Fuzzilli's results make that the default;
   a "keep some context" variant is worth comparing later.

**Rebase**, the escape hatch: when a lineage nears the cap anyway, make its program the new base,
outside the choice sequence (form (B), §3.5), and start a fresh lineage on top. Mutation
continues; the cost is that findings in the rebased prefix are no longer shrinkable by Hypothesis
and need an IR-level minimizer for that part.

### 5.4 Other open problems

- **Corpus donors for splice** (not built in the spike): putting another entry's choices into the
  splice span makes the donor part of this lineage's budget and replay cost. Shrink-on-insert
  keeps donors small; a donor-size limit may be needed.
- **Residual shrink failures** (§3.8 rule 9).
- **Rejection rate** of mutations (invalid children): every rejection is a harness-free but
  wasted iteration; worth tracking per mutator.
- **Shrink time on real findings**: the spike's planted bugs shrink in seconds because a "run" is
  a predicate; with the harness each shrink attempt is a JVM round trip in both modes. Hypothesis
  caps shrinking at 500 successful shrinks / 300 s; per-signature exception classes keep distinct
  bugs from competing.

## 6. Migration plan for oxpecker

1. **IR and lifter** (`oxpecker/ir/`): instructions for the JS subset the generators use today
   (variables, literals, operators, property access, calls, `new`, functions, closures, loops,
   `try`, classes, …); analysis with Rhino-relevant types (the `T` enum of `gen/types.py` is a
   start); `verify`; lifter with probes, comments, strict prologue and an inlining policy.
   Property tests: generated and mutated programs verify; lifted programs parse (node) and run
   (harness, both modes). **First measurement:** the serialized choice size of fresh programs at
   today's statement counts, as a share of `BUFFER_SIZE` (§5.2) — it decides how much budget is
   left for mutation steps.
2. **Port generators by family**, starting with `operations` (where the string hacks are) and the
   `compiler-targeted` family; templates and swarm selection as strategies. Keep the registry and
   catalogue (`@generator(name, family, weight, …)`), weights becoming list order/frequency.
   `oxpecker gen` and the property tests switch per family behind a flag.
3. **Mutators**: operation, input, code generation, splice (with corpus donors), combine; each
   with property tests (invariants hold; mutation is local; `reject` rate bounded).
4. **Harness: per-run probe sets** (`Coverage` returns or checks the probe ids a run hit, not
   only the count of new ones) — prerequisite for shrink on insert (§5.3).
5. **Fuzz loop** over `fuzz_one_input` with lineage corpus entries, N consecutive mutations per
   round, shrink on every insert; findings through the database and a `[reuse, shrink]` run.
   Replaces `fuzz.py`'s mutate/shrink.
6. **Remove** `gen/rand.py` (`ChoiceRand`, `HypothesisRand`, `mutate`), `fuzz.shrink`, and the
   string `ProgramBuilder`, once every family is ported. Update `design.md`.

Unchanged: the Java harness, `compare.py`, findings recording and signatures, the corpora
(test262, jstests, legacy Mozilla suite), the inspector UI (fed from IR provenance), JaCoCo
coverage.

## 7. Alternatives considered

- **Keep strings, use Hypothesis better** (`fuzz_one_input` over today's `programs()`): removes
  `ChoiceRand` but leaves blind generator-input mutation (§2.3) and the string hacks.
- **AST instead of flat IR**: closer to the grammar-per-strategy sketch, readable JS for free;
  input mutation and splicing need tree scope analysis. A reasonable choice if readability wins
  over mutation; everything in §3.4–3.8 still applies.
- **Corpus of programs, Fuzzilli-style, with an IR minimizer of our own**: simplest fuzz loop, no
  lineage costs, but findings leave Hypothesis (form (B), §3.5) and need our own shrinker — what
  the intended design wants to avoid.
- **A custom `PrimitiveProvider` driving replay and mutation**: the cleanest seam for a corpus
  loop, but a larger piece of code against a newer interface; keep as the fallback for §3.6.
- **HypoFuzz** [8]: coverage-guided fuzzing of Hypothesis tests with a managed corpus. Might
  replace the hand-written driver; does not provide IR mutation by itself.

## 8. References

1. M. Zalewski, *American fuzzy lop — technical details*. <https://lcamtuf.coredump.cx/afl/technical_details.txt>
2. LLVM, *libFuzzer – a library for coverage-guided fuzz testing*. <https://llvm.org/docs/LibFuzzer.html>
3. R. Padhye, C. Lemieux, K. Sen, M. Papadakis, Y. Le Traon, *Semantic Fuzzing with Zest*, ISSTA 2019. <https://doi.org/10.1145/3293882.3330576>
4. S. Groß, S. Koch, L. Bernhard, T. Holz, M. Johns, *FUZZILLI: Fuzzing for JavaScript JIT Compiler Vulnerabilities*, NDSS 2023; code and docs: <https://github.com/googleprojectzero/fuzzilli> (`Docs/HowFuzzilliWorks.md`, `Sources/Fuzzilli/Mutators/`, `Sources/Fuzzilli/Minimization/`, `Sources/Fuzzilli/Engines/MutationEngine.swift` for the consecutive-mutation loop, `Sources/FuzzilliCli/main.swift` for `--consecutiveMutations` (default 5)).
5. D. R. MacIver, Z. Hatfield-Dodds, et al., *Hypothesis: A new approach to property-based testing*, JOSS 2019. <https://doi.org/10.21105/joss.01891>
6. Hypothesis documentation: `fuzz_one_input`, the example database, settings and phases, alternative backends. <https://hypothesis.readthedocs.io/>; source read for this proposal: Hypothesis 6.170.5, `core.py`, `internal/conjecture/{data,engine,shrinker,providers}.py`.
7. D. R. MacIver, A. F. Donaldson, *Test-Case Reduction via Test-Case Generation: Insights from the Hypothesis Reducer*, ECOOP 2020. <https://doi.org/10.4230/LIPIcs.ECOOP.2020.13>
8. HypoFuzz — adaptive fuzzing of Hypothesis tests. <https://hypofuzz.com/>
9. hypothesis-crosshair — a CrossHair (symbolic execution) backend for Hypothesis. <https://github.com/pschanely/hypothesis-crosshair>
10. C. Aschermann, T. Frassetto, T. Holz, P. Jauernig, A.-R. Sadeghi, D. Teuchert, *NAUTILUS: Fishing for Deep Bugs with Grammars*, NDSS 2019.
11. J. Wang, B. Chen, L. Wei, Y. Liu, *Superion: Grammar-Aware Greybox Fuzzing*, ICSE 2019.
12. H. Han, D. Oh, S. K. Cha, *CodeAlchemist: Semantics-Aware Code Generation to Find Vulnerabilities in JavaScript Engines*, NDSS 2019.
13. S. Park, W. Xu, I. Yun, D. Jang, T. Kim, *Fuzzing JavaScript Engines with Aspect-preserving Mutation*, IEEE S&P 2020. <https://doi.org/10.1109/SP40000.2020.00067>
14. A. Groce, C. Zhang, E. Eide, Y. Chen, J. Regehr, *Swarm Testing*, ISSTA 2012. <https://doi.org/10.1145/2338965.2336763>
15. A. Löscher, K. Sagonas, *Targeted Property-Based Testing*, ISSTA 2017. <https://doi.org/10.1145/3092703.3092711>
16. K. Claessen, J. Hughes, *QuickCheck: A Lightweight Tool for Random Testing of Haskell Programs*, ICFP 2000. <https://doi.org/10.1145/351240.351266>
