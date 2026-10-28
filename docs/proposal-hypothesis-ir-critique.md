# Critique of `proposal-hypothesis-ir.md`

Status: review, 2026-10-25, of the proposal as of the same date. Hypothesis claims were checked
against the installed Hypothesis 6.170.5 (`.venv/lib/python3.13/site-packages/hypothesis`);
repository claims against the working tree; cost figures against `runs/fuzz-2026-10-19`. The
spike directory is outside the working tree and was not re-read; its results are taken from the
proposal.

## 0. Verdict

The core of the proposal stands: generators and mutators as strategies over an IR, the lineage
trick that keeps the whole history inside one test case, and Hypothesis's shrinker as the only
minimiser. The §3.8 rules are the most valuable part and should survive whatever else changes.

Three things need to change before the migration starts:

1. **The IR must model variable storage and reassignment.** Strict SSA cannot express the
   programs behind most of the compiled-mode bugs this project has found (§2).
2. **The lifter is part of the test case, not a presentation layer.** Inlining changes
   evaluation order and compiled-mode paths, and the reproducer the user reads must be the
   program that failed. Readability of lifted programs is a stated deliverable and the proposal
   does not design for it (§3).
3. **Shrink-on-insert, as specified, costs more than the fuzzing it protects.** On the
   2026-10-19 run it would have turned forty minutes of fuzzing into days of shrinking (§4).

One worry in the proposal is smaller than stated: today's programs use under 8 % of the 8 KB
choice budget (§5, measured), so lineage depth is a compaction problem, not a budget problem.

Sections 5 to 9 list smaller problems: factual errors about Hypothesis internals, a straw-man
baseline in the evidence, missing strategy rules, and editorial issues. §10 is a recommended
order of work, most of it measurements that need no IR.

## 1. Factual corrections (Hypothesis 6.170.5)

| proposal says | source says | where |
|---|---|---|
| `fuzz_one_input` saves a failure to `<key>.secondary` (§2.2, §3.6) | **correct as stated.** A first draft of this critique said "primary key" after reading the local name `database_key`; that local is assigned `function_digest(test) + b".secondary"` (`core.py:2337`). One detail to add: it saves only when the failure is no larger than the smallest seen for that interesting origin in this process | `core.py` `fuzz_one_input` |
| on misalignment Hypothesis "uses the simplest value for that draw and everything after it" (§2.2); "it and everything after get the simplest values" (§2.3) | only the misaligned draw gets the index-0 value; `self.index += 1` and later draws keep popping the prefix at their positions | `data.py` `_pop_choice`, the `choice_permitted` branch |
| "Hypothesis exposes no shrink budget", so a budget must be built from the test body (§5.3) | true that there is no setting, but the engine **saves every improving shrink to the database** as it goes, so a shrink run killed from outside leaves its best result behind | `engine.py`, `save_choices` in the interesting-case update path |
| `design.md` §6.6 says the loop mutates choices "the same way Fuzzilli mutates FuzzIL" (§1) | `design.md` §6.6 says the operators are "applied to generator choices instead of IR or bytes"; the overstatement is in the `gen/rand.py` docstring only | `docs/design.md:267`, `oxpecker/gen/rand.py:11` |
| `MAX_SHRINKS`, `MAX_SHRINKING_SECONDS` listed with the shrinker passes (§2.2) | both live in `engine.py`, not `shrinker.py`; values are right | `engine.py:83`, `engine.py:95` |

The misalignment correction matters beyond accuracy. The worked example still ends at `0 * 0`,
but only because the leftover choice at position 5 happens to be `0` and in range. Rule 2 of
§3.8 is really about **positional shift**: a one-element `sampled_from` records nothing, so every
later choice moves one slot and is reinterpreted, not zeroed. Rewrite the §2.3 bullet to say the
right side re-reads positions 4 and 5 as kind and literal, with position 4 misaligned to the
index-0 kind.

Verified as stated: `BUFFER_SIZE = 8 * 1024` with the quoted comment; `MAX_DEPTH = 100`; the
`BytestringProvider` draws one byte per boolean with `>= 128` true at p = 0.5, and
`(max - min).bit_length()` bits per integer with rejection resampling; `sampled_from` of one
element is `just`; the length accounting uses `choices_size` per draw against `max_length`.

## 2. The IR must model storage kinds and reassignment

### 2.1 The problem

§3.1 says the IR is SSA, "each variable defined once". Rhino's optimizer specialises **mutable
function locals** by the types assigned to them over the function's life, and its compiled-mode
bugs cluster exactly there. From `tests/regressions/`:

| regression | the construct that matters |
|---|---|
| `compiled-bigint-in-number-typed-local.js` | a local assigned from an operator the optimizer treats as always numeric; `var n = 1n; n++` |
| `compiled-increment-int32-overflow.js` | `n++` wraps at int32 **only when the target is not an optimizer-typed local**: top-level vars, locals of functions needing an activation, properties, elements |
| `compiled-negative-zero-lost-in-int-arith.js` | a **top-level** variable holding a small integer kept as int |
| `java-int-literal-double-vs-integer.js` | `var i = 0; i = a + 1;` a local reassigned from a parameter expression |
| `design.md` control-ops family | "cross-type reassignment", "chained assignment", "number computation chains" |

Four distinctions decide which Rhino path a variable takes, and strict SSA erases all of them:

1. **Reassignment**, and the sequence of types assigned (`int` then `double` then `BigInt`).
2. **Storage kind**: top-level `var`, function local, `let`/`const` in a block, parameter,
   object property, array element.
3. **Activation**: a function local stops being optimizer-typed when the function uses `eval`,
   `arguments` or `with`, or has a closure capture in some cases.
4. **Update operators** in place: `++`, `--`, `+=`, which Rhino compiles differently from
   `n = n + 1`.

An IR mutator that cannot produce `var n = 1n; n++` cannot reach these bugs except through a
fresh generator, which gives up the compounding the proposal is built on.

### 2.2 Proposed fix

Follow FuzzIL, which is not SSA either: it has `Reassign` and `Update` instructions and variables
that are **defined once but assigned many times**.

- **Instructions.** `DeclareVar(kind)` with `kind ∈ {var_top, var_local, let, const, param}`
  defining a variable id; `Reassign(v, input)`; `Update(op)(v, input)` for `+=` and friends;
  `Increment(op, prefix)(v)` for `++`/`--`; `SetProperty`, `SetElement` as today. SSA temporaries
  stay for expression values; **declared variables are a second class** that can be targets.
- **Analysis.** `analyse` becomes flow-sensitive for declared variables: the type of `v` at a
  point is the join of the types assigned on paths reaching it. Keep the join simple: a frozenset
  of `T`, with `UNKNOWN` as the top element, merged at block ends. `verify` rejects writes to
  `const` and reads before declaration. The fixpoint iteration §3.2 already needs for calls
  covers loops.
- **Activation flag.** `analyse` records per function whether it needs an activation (`eval`,
  `arguments`, `with`, nested function capturing a local). Generators and the inspector can then
  say which storage path a variable is on, and a mutator can **toggle** it deliberately: insert
  or remove an `arguments` reference to move every local between the typed and untyped paths.
  This is a one-line mutation that flips the compiled-mode strategy for a whole function, which
  no choice-level mutation does.
- **Mutators.** Add a `reassign` mutator: pick a declared variable and a visible value of a
  **different** type class and insert `Reassign` after a drawn point. Order the type-class pool
  so that index 0 is the same class (shrinks to the harmless case) and higher indices cross
  classes. The input mutator's "compatible alternative" test uses the type **at the use point**,
  not a global type.
- **Lifter.** `DeclareVar` lifts to `var`/`let`/`const` by kind, top-level vars to the program
  prologue or in place, `Update` and `Increment` to their operators. Because reassignment exists,
  the lifter must keep declared variables as statements and may inline only SSA temporaries (§3).

### 2.3 Cost

One more instruction class and a type lattice with joins; roughly the `JSTyper` subset Fuzzilli
has. It is cheaper to add now than to retrofit after the generators are ported, because every
generator that touches a variable chooses between a temporary and a declared variable.

## 3. The lifter is part of the test case

### 3.1 Inlining is a semantic choice

§3.1 presents the inlining policy as a knob that "becomes a drawn choice" and a Rhino feature:
`let v3 = v0 + v2` and `(v0 + 3)` take different compiled paths. Both are true, and they have two
consequences the proposal does not draw:

1. **Inlining can reorder side effects.** `v1 = call f(); v2 = get o.p; v3 = v1 + v2` inlined to
   `f() + o.p` evaluates `o.p` after the call, as before; but inlining `v2` instead gives
   `v1 + o.p` with the get moved past any statements between `v2` and `v3`. A getter, a `with`
   scope, a proxy, or a Java host object makes the difference observable, and the compare step
   will report a difference that is the lifter's, not Rhino's. The legality rule must be: inline
   a single-use temporary only into the **next** instruction in the same block, or only when
   nothing between the definition and the use can have side effects (pure loads of constants and
   of locals that are not reassigned in between).
2. **The reproducer must be the program that ran.** A finding is a verdict on lifted source.
   If the policy is not recorded, replaying the IR with another policy can lose the bug. So the
   policy is **drawn inside the lineage** as part of `programs()`, or derived deterministically
   from the IR with no free parameter. "Even a drawn choice" in §3.1 has to become "always a
   drawn choice or a fixed function".

Suggested shape: one draw per single-use temporary at lift time is too many choices under the
8 KB budget. Draw **one policy index per program** from a fixed list ordered simplest-first,
where index 0 is "inline every legal single-use temporary" (the most readable form) and later
indices keep progressively more temporaries. The shrinker then produces the readable form unless
the bug needs a temporary, in which case the kept temporary **is** the finding's point of
interest and will stand out.

### 3.2 Readability is a deliverable, not a table row

`feedback-readable-generated-programs` records that readable generated programs are a first
deliverable, and the inspector UI and the hand-written regression files set the bar: short
programs with meaningful variable names and one `// [generator]` comment per statement. The
proposal gives readability one row ("needs an inlining lifter") and dismisses the AST alternative
in a bullet. Flat IR lifted naively is `v0 … v567`, and §4.2 shows lineages reaching hundreds of
instructions before compaction.

What the lifter needs, written down as requirements:

- **Names from types and roles.** `n0`, `s1`, `f2`, `arr3`, `o4`, `p5` for parameters, from
  the inferred type at definition, with the generator's role when it has one (`acc`, `label`,
  `ctor`). Names are a pure function of the IR, so they are stable under shrinking.
- **Statement grouping.** A declared variable and its initialiser lift as one statement; a
  function's `Begin…End` lifts as a declaration with its parameters named; dead SSA temporaries
  are dropped (the §3.7 dead-code pass, run always, not only "if the residual matters").
- **Provenance comments.** Each instruction carries the generator that emitted it and the
  lineage step that introduced or changed it. The lifter emits today's `// [generator]` marker
  per top-level statement and, for a shrunk finding, a `// step k: input mutation on v3`
  comment where a mutation survived shrinking. That comment is new information the current
  design cannot give: it names what the bug needed.
- **Probe placement** after every N top-level statements as today, as a lifter option.
- **A golden test.** Lift twenty seeds at `GenOptions` defaults and commit them; a reviewer
  reads them once. Add a metric to the property tests: temporaries per statement and the share of
  single-use temporaries that were inlined, with thresholds.

With these in place the readability gap between flat IR and AST closes for the output. The AST
remains the better choice only if the team values writing recursive strategies over simpler
mutators; the proposal's §3.1 table already makes that case, and this section answers the
"readable JS: needs an inlining lifter" row with a design rather than a hope.

### 3.3 Visibility: what the inspector and the user see

Three things should be visible for every finding, and the IR makes all three cheap:

1. the lifted reproducer in its readable form (above);
2. the **lineage**: base program size, steps, which steps survived shrinking and what they
   changed, so the user sees whether a bug came from generation or from mutation;
3. per declared variable, the **storage path** the analysis inferred (optimizer-typed local,
   activation local, top-level, property), so the user can read "`n` is an optimizer-typed
   double local" next to a BigInt finding without re-deriving it.

The inspector currently gets generator attribution from `generator_spans`; the proposal replaces
that with provenance. Add items 2 and 3 to the same provenance record.

## 4. Shrink-on-insert, as specified, does not fit the budget

### 4.1 The numbers

From `runs/fuzz-2026-10-19/corpus/last-run.json` and `fuzz.log`:

| quantity | value |
|---|---|
| runs | 14 954 in 2 430 s, 6.2 runs/s with JaCoCo on |
| corpus inserts | 4 298, i.e. **29 % of runs inserted** |
| harness time per run, both modes, with coverage | ~160 ms |
| spike shrink cost per finding (§4.1 of the proposal) | 41 to 333 test calls, median ~200 |

Shrinking every insert with Hypothesis's shrinker at 200 calls and 160 ms per call is ~32 s per
insert. Over 4 298 inserts that is about **38 hours of shrinking for 40 minutes of fuzzing**.
Even at the spike's cheapest shrink it is nine hours. The insert rate falls as coverage saturates,
but the early phase is exactly when the corpus is built.

Fuzzilli affords minimize-on-insert because its executions take a few milliseconds and its
minimizer is a small set of targeted IR passes, each a handful of executions, not a generic
500-shrink search. The proposal borrows the policy without the cost structure.

### 4.2 Proposed fix: tiered compaction

Keep the decision "lineages stay shallow" and change the mechanism:

1. **Tier 0, free, every insert: IR-level cleanup without executing anything.** Drop dead
   temporaries and dead mutation steps (a step whose instructions were all later deleted or
   overwritten). This is a pure function of the IR and keeps the lineage choices intact only if
   the lifter drops dead code, so do it in the lifter and count the step as dead in provenance.
2. **Tier 1, cheap, every insert: step-deletion only, with the harness.** Try deleting each
   mutation step's span, newest first, keeping the entry if the probe set is still reached. That
   is at most `steps` harness runs per insert, not hundreds, and it is the pass that produced
   the proposal's "all steps removed" result. Implement it as a `ConjectureData.for_choices`
   replay with the step span removed, which is the shrinker's `node_program` deletion pass
   applied by hand to known span boundaries; or, if that is too close to internals, rebuild the
   buffer from the step boundaries the driver already knows because it appended them.
3. **Tier 2, full Hypothesis shrink, on a budget and lazily.** Run the `[reuse, shrink]`
   compaction when an entry is **first chosen as a parent** or when its lineage depth or
   serialized size crosses a threshold, in a subprocess with a wall-clock limit. Because the
   engine saves every improving shrink to the database, killing the subprocess loses nothing:
   read the smallest entry back. Budget this at a fixed share of wall-clock, say 20 %, and skip it
   when over.
4. **Rebase** stays as the last resort, as the proposal says.

This keeps Fuzzilli's invariant (corpus programs are small) at Fuzzilli's cost (a few executions
per insert) and uses the generic shrinker where it pays: on entries that are actually reused.

### 4.3 Compaction mechanics the proposal does not spell out

"No compaction code of our own" undersells the plumbing:

- The compaction property ("reaches probe set P") is a **different test function**, so a
  different database key. Each compaction needs an in-memory `ExampleDatabase`, the lineage's
  choices written under that key, the run, and the smallest entry read back. P must reach the
  test body through a closure or a module global set per run.
- **Flakiness.** Hypothesis aborts a shrink with `Flaky` if the property fails to reproduce on
  the first replay. The pre-run that intersects P with the probes reached again is mandatory, not
  optional, and once-per-JVM probes (static initialisers, class loading, lazy caches) will make
  up a share of every "new" set. Measure that share on the 2026-10-19 coverage before designing
  around it; if it is large, the harness should report **probe deltas per run against a per-run
  baseline** rather than against the JVM's cumulative store.
- **Harness API.** `Coverage.drain()` reports probes new against the cumulative store. The
  harness needs "the probe ids this run hit" or "did this run hit all of P", with P sent by the
  client. The second is a smaller payload and enough for compaction.
- **Settings.** `deadline=None`, `suppress_health_check=[too_slow, data_too_large]`,
  `phases=[reuse, shrink]`, `database=` the in-memory one, and `derandomize=False`.

This is a few hundred lines, not zero, and it should appear in §6 as its own step.

## 5. The choice budget can be measured today

§6 step 1 defers "the serialized choice size of fresh programs" until after the IR and lifter
exist. It needs neither. `oxpecker/gen/strategies.py` already draws today's programs through
`HypothesisRand`, so:

```python
from random import Random
from hypothesis.internal.conjecture.data import ConjectureData
from hypothesis.internal.conjecture.choice import choices_size
from oxpecker.gen.strategies import programs

for seed in range(100):
    d = ConjectureData(random=Random(seed))
    d.draw(programs())            # GenOptions defaults: 3..12 statements
    print(len(d.nodes), d.length, d.length / (8 * 1024))
```

Run once while writing this critique, 40 seeds, `GenOptions` defaults:

| | min | median | max |
|---|---|---|---|
| choices (`len(d.nodes)`) | 94 | 201 | 374 |
| serialized bytes (`d.length`) | 145 | 340 | 630 |
| share of `BUFFER_SIZE` | 1.8 % | 4.2 % | 7.7 % |
| source lines | 24 | 72 | 150 |

So the budget is **not** the constraint the proposal's §5.2 treats it as: a fresh program at
today's size leaves room for well over a hundred mutation steps at the spike's ~11 choices per
step, and for several corpus donors. What will cost is the IR version's string draws (property
names, string literals, regexps), which the string builder mostly takes from constant pools via
`sampled_from`; keep that habit in the port. The number to watch is therefore lineage **depth**
and donor size, which is a compaction question (§4), not a budget one. Put the script in
`scripts/` or a property test so the measurement is repeated after each family is ported, per the
project rule against ad hoc checks.

## 6. Evidence

- **The locality baseline is a straw man.** §4.3 compares IR mutation with blind byte mutation
  through the generator. The relevant competitor is **span-aware choice mutation**, which §2.3
  itself describes and which Hypothesis's shrinker uses: replace a span with a donor span,
  delete a span, duplicate a span. That is also the cheapest upgrade to today's `ChoiceRand`
  loop, so without that row the table does not show that IR mutation beats the alternative that
  keeps the current architecture. Add the row; the spike has spans for free via
  `ConjectureData.spans`.
- **Fix the no-op mutations before quoting the table.** 29 % identical children are an
  operation mutator that draws the current value. Exclude it and re-run; the "identical" column
  for IR should be near zero and the preserved mean will move.
- **Eight seeds on planted predicates do not support a search claim.** Keep §4.1 as a
  shrinking result, which is what it is, and cut "finds the structural bugs in fewer calls".
- **The current loop found real bugs.** The BigInt-in-numeric-local regression and the
  increment overflow were found by `oxpecker fuzz` on 2026-10-19 with choice mutation. §1 should
  say what the existing design achieved; the argument for replacing it is architectural, not
  that it does not work.
- **The long-lineage table measures the wrong property.** "Has k steps" forbids deleting steps,
  so §4.2 shows shrink cost for a lineage whose steps all matter, which compaction is designed to
  make rare. It is still useful as a worst case; label it so.

## 7. Rules to add to §3.8

12. **Never `reject()` after emitting.** `Ctx` is mutable and `st.lists` restarts only the
    rejected element, so a generator that appends then rejects leaves partial instructions in
    the context. Either make `Ctx.emit` transactional (snapshot at span start, restore on
    rejection) or check applicability before the first emit. State which.
13. **Rule 2 fixes the count, not the range.** `pick(draw, xs)` records a choice for a
    one-element pool, but when a pool shrinks below a replayed index the draw misaligns to
    index 0, which under rule 5 is the most recent variable. That is benign and is **why** rules
    2 and 5 work together; say so, and note that the first misalignment in a test case is
    recorded in `misaligned_at`, which the property tests can assert is `None` for unshrunk
    replays.
14. **Count the fallback.** Rule 3's "fixed list with a fallback when the pick cannot run here"
    biases the mutator distribution towards the fallback whenever many picks are inapplicable,
    which is most of the time on small programs. Record the effective distribution per run and
    make the fallback a weighted draw among applicable mutators rather than always code
    generation, or accept the bias and document it.
15. **Inlining policy is drawn once per program** (§3.1 above), index 0 = most inlined.

## 8. Smaller points

- **IR feature coverage.** §6 step 1 lists variables, literals, operators, property access,
  calls, `new`, functions, closures, loops, `try`, classes. Today's generators also use
  generators and `yield`, continuations (interpreter-only, in scope), getters and setters,
  `with`, labels, destructuring, template literals, regexps, `arguments`, `eval`, `super`,
  comprehensions, and sloppy-versus-strict bodies. Step 6 removes the string builder, so the
  union must be covered before then. List it, and mark which constructs need block-structured
  instructions and which are calls.
- **Splice donors** from the corpus bring their whole lineage's choices, not just their
  program, into the host's budget. With tiered compaction donors stay small; still, cap donor
  serialized size and prefer donors that were compacted.
- **HypoFuzz** is AGPL-licensed as far as I know; say so in §3.6 rather than "check its licence
  first", and decide whether that rules it out for this project.
- **The `reuse` phase replays every primary-key entry.** With one database key for all
  findings, a `[reuse, shrink]` run on a new finding replays every old one first. Use a database
  per finding or clear the key after recording the reproducer.
- **`fuzz_one_input` saves only improvements per interesting origin per process.** A restarted
  driver forgets `minimal_failures` and will save again; harmless, but the findings path should
  dedupe by signature as `findings.py` does today. Since the saves go to `.secondary`, the
  `[reuse, shrink]` run finds them only as a top-up when the primary corpus is short, which is
  the normal case for a fresh finding key.
- **References 15 and 16** (Löscher and Sagonas; Claessen and Hughes) are never cited.

## 9. Editorial

- §0 is too long for a summary and lines 31 to 39 have broken wrapping from a late edit. Keep
  §0 to the idea, the spike's one-sentence result, and the open cost; move the compaction detail
  to §5.3.
- Reference the §5.1 three-sizes table the first time "size" appears, in §0.
- §2.3's "everything after" bullet and §2.2's misalignment bullet need the correction in §1.
- The "misleading comparison" paragraph in §1 should cite `gen/rand.py` only.

## 10. Recommended order of work

Measurements first, each a day or less, each able to change the plan:

1. **Choice budget of today's programs** (§5). Done once here: at most 8 % of the budget per
   fresh program. Turn the snippet into a repeatable script and re-run per ported family.
2. **Insert rate and probe determinism** from `runs/fuzz-2026-10-19`: share of runs that
   insert, and share of "new" probes that are once-per-JVM. Sizes the compaction tiers (§4).
3. **Span-aware mutation baseline** in the spike (§6). Decides whether IR mutation is worth the
   IR, or whether span-aware choice mutation plus `fuzz_one_input` is the cheaper first step.
4. **Readability sample**: lift the spike's programs with a first version of the naming and
   inlining rules in §3.2 and look at twenty of them.

Then the design changes: `DeclareVar`/`Reassign`/`Update` with flow-sensitive types and the
activation flag (§2); the lifter as a drawn policy with the legality rule (§3.1); tiered
compaction with the harness probe-set API as its own migration step (§4). Then §6 as written.

## Response notes (implementation, 2026-10-25)

Added by the implementation on branch `feat/il-strategy`; the review above is unchanged.

- §1, row 1 (`fuzz_one_input` key): in 6.170.5 the failure goes to the **secondary** key. The
  variable is named `database_key`, but it is assigned `function_digest(test) + b".secondary"`
  (`core.py:2337`, just before `fuzz_one_input` is defined), and the save in the
  `except BaseException` branch uses it. The spike's `[reuse, shrink]` runs found the fuzzer's
  failures through the secondary corpus (`reuse_existing_examples` tops up from it when the
  primary is short). The proposal keeps `.secondary`.
- §1, row 2 (misalignment): confirmed in `data.py` `_pop_choice`; the proposal is corrected.
- §2 (storage kinds, reassignment) and §3.1–3.2 (lifter policy, legality, names, dead code):
  implemented in the IR core before porting further generators; see
  `docs/ir-implementation-decisions.md` D15–D23. Differences from the suggestion: the inlining
  policy is a per-instruction `keep` flag rather than one index per program (D16), and types are
  joined to a single `T` (not a set) at block ends.
- §4 (tiered compaction): to be designed with the fuzz loop.
