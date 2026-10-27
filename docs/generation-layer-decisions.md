# Generation layer: decisions

Running log for `docs/design-generation-layer.md`. The design says what and why at length; this
file is the index of choices, so they can be reviewed and changed deliberately. Newest last.
Format: **G<n>. Decision.** Why. Alternatives rejected. A decision from the deleted layer that
is kept on purpose names its old number (D<n>, `docs/ir-implementation-decisions.md`).

## Scope and sources (2026-10-26)

**G1. New package `oxpecker/il/`; nothing from `oxpecker/ir` restored by default.** The deleted
layer's ideas listed in the post-mortem §2.9 are re-introduced one by one, each named here with
its D number. Alternative rejected: restoring `ir/` and patching out `Eval` — the string habits
were in the generators and the pools, not only in the op.

**G2. The reference is Fuzzilli `be2811d`, read in full for the parts we translate.** The design
cites Swift line numbers; every translated file carries the Apache-2.0 header naming its source.

**G3. The op inventory is decided up front from `JsOperations.swift` against the capability
table**, not grown on demand (post-mortem §1.5 item 3). Dropped: classes, private members,
async, `using`, workers, modules, wasm, `Eval`, `Print/Explore/Probe/Fixup`, `Nop`, `CreateMap`,
`BindFunction/BindMethod`, `BeginConstructor`. Added: `Declare`, `LoadBuiltin`, oxpecker's
`Probe`, radix/style fields on number literals.

## The IL

**G4. Two classes of variables: temporaries and declared variables (D15 kept); `Declare(kind)`
replaces FuzzIL's `Dup`.** Rhino specialises storage; the generator chooses it and the lifter
never infers `let`/`const` from assignment counts as FuzzIL does. Assignable: `Declare`
outputs except `CONST`, parameters, catch and loop variables, destructured bindings, named
variables, function names. Not assignable: temporaries, repeat-loop counters, labels, `this`.

**G5. Spread calls are supported like any op, on by default with a low generator weight;
`GenOptions.weights` scales any generator or family from configuration, down to zero.** They
crash Rhino's compiler in both modes today (recorded regression), so a program containing one
yields nothing else; the user wants them available for later Rhino versions and tunable rather
than gated. *(Revised 2026-10-26 at the user's review; the first version gated them behind an
option.)*

**G6. Generator scheduling: nested Python blocks and per-context fixed pools; no Begin/End
stubs, no `ContextGraph`, no synthetic generators.** Fuzzilli's stub machinery serves an
imperative builder with one weighted list; here a block generator is one composite that draws
`statements()` for the context it opened, which gives one Hypothesis span per block and per
statement (proposal §3.8 rules 1–3). *(Refined 2026-10-26 after the trade-off in design §7.2:)*
there is **one fixed statement list** shared by every statement context, so an index means the
same generator everywhere and Hypothesis's `pass_to_descendant` can replace a block by its
body; **context-requiring generators scaffold their own context** (a `super` generator outside a
method opens an object literal with a method around itself; fixed code, free to the shrinker),
which recovers Fuzzilli's synthesis and its weight-fairness; the six that cannot scaffold
(`return`, `yield`, `break`, `continue`, `switch_break`, `arguments`) fall back to the first
applicable entry; object-literal members and switch cases have their own lists. The codegen
mutator uses the context at the insertion point.

**G7. No guards.** FuzzIL's `isGuarded` try/catch wrapper is dropped: a `catch` forces a Rhino
activation (G14), which would move every guarded site off the optimizer path, and the user does
not want everything under try/catch. An exception ends the program with a `throw` outcome both
modes must agree on; probes observe; a generator that must continue past a throw builds a real
`try`/`catch` recording the error into a declared variable. A completion-rate test (design §10
item 10) keeps early throws visible as a generator problem. `receiver_optional` and
`call_optional` stay: `?.` is a language feature. *(Decided at the user's review; the first
version kept guards with a probe in the catch.)*

**G8. Literals hold values with typed spelling fields** (`Radix`, `FloatStyle`); negative
numbers and `-0` are `UnaryOperation(MINUS, …)`; `NaN`/`Infinity` are builtins. Regexp patterns
are string values built from a fixed pattern table and Fuzzilli's combinators.

**G9. Provenance (`gen`, `origin`, `step`) and the per-instruction lifting choice
`lift: INLINE | VAR | LET | CONST` are instruction metadata** (D10, D16 kept and extended),
ignored by analysis and `check`; `lift` is part of the signature, drawn after generation by
`lifting_choices()` from a fixed list with `INLINE` first, and may be pinned by a generator.

**G10. `Probe` has no inputs; the lifter observes the declared variables and kept temporaries
visible at the probe** (D9 kept). Giving probes inputs would add uses and change the inlining
of every observed value, so removing probes for the plain reproducer would change the program.

## Environment

**G11. Registries (`Builtins`, `Keys`, `Methods`) are generated from `rhino-capabilities.json`
with a hand-typed overlay, and a test diffs the overlay against the table.** Fuzzilli's
hand-written environment is the model for the shape (groups, properties, method signatures),
the probe is the source of truth for what exists. `J` (Java interop) and `__pause`
(continuations) are registry entries, not operations.

## Types and analysis

**G12. Types are FuzzIL's possible-kinds bitset with a group, signature, element type and known
keys; merged types are dropped; `NULL` is its own kind; `INTEGER` and `FLOAT` stay distinct.**
The Java boundary and Rhino's int representation need null and the number split; merges
(`string + iterable + object`) only served Fuzzilli's method lookup, which the registry does.

**G13. The typer is FuzzIL's single forward pass with branch states and union at joins, plus two
changes: loop bodies are analysed twice (entry joined with end-of-body), and try/catch/finally
join.** D17's full fixpoint and escaping-variable rules are not restored; FuzzIL's function-as-
"if called" treatment covers assignments from nested functions soundly for later code.

**G14. The analysis predicts Rhino's compiled-mode paths from Rhino's own rules (`b95f75d`):
activation (nested function, catch, with, eval, arguments, yield, super), number variables
(`Block.findExpressionType` over IL ops, fixpoint over assignments), direct-call targets (named
top-level function statements called with exact arity, no activation).** Compiler-targeted
generators build against these predicates; the activation and number-flip mutators use them;
the inspector shows them. A slow harness test checks the predictions against observed
`J.typeOf` representations.

**G15. `verify` keeps D7's policy: reject definite violations only; anything merely likely to
throw is allowed** because both modes must throw alike.

## Builder and strategies

**G16. The builder never draws** (D24 kept). It emits, tracks state and returns pools ordered
most-recent-first; generators `pick` by index. Hidden variables and scope ordering are taken from
FuzzIL's builder; `build(n:)`, `complete`, `buildPrefix`, `findOrGenerateType`, adoption and
splicing are not (they are strategies or the splice mutator).

**G17. Inputs follow Fuzzilli's loose/strict modes.** Loose draws from the exact-type pool with
an unconditional `maybe(0.8)` then falls back to may-be and any; strict skips the generator
(falls back to the pool's index 0) when no exact variable exists. The boolean is drawn whether or
not it is used, so a choice never changes meaning with the pool (rule 3).

**G18. Pools are fixed ordered lists per context; weights are repetition in the list; swarm
selection is a boolean per generator** (D22, D23, D30 kept). Toggle-off and sloppy-only
generators fall back to index 0 rather than renumbering.

**G19. Value makers are generators registered as the fallback for `b.variable(type)` on an empty
pool; the program prefix is an `st.lists` over them** (D25 kept, Fuzzilli's `useInPrefix` idea).

**G20. Fuzzilli's generators are translated by the inventory's three classes: 97 as is, 9
adapted (async flag off, call spread off, class branch removed, trap and prototype lists from
the registry), 76 dropped.** oxpecker's own families (compiler-targeted, java-interop,
continuations, and the old values/exotic/control generators Fuzzilli lacks) are designed against
the builder with the ops each must emit stated for its structure test.

## Lifter

**G21. Expression inlining is FuzzIL's `JavaScriptWriter` policy** (pure expressions inlined
everywhere; effectful single-use expressions pending in evaluation order, inlined only as an
in-order suffix, flushed at every statement and block boundary) **plus `keep` and "declared
variables are never inlined".** D11's folding rules are replaced; they guaranteed the same thing
with less inlining.

**G22. The keyword for a declared temporary is the instruction's own `lift` choice (G9), not a
program-level option and not FuzzIL's assignment-count rule.** `var`, `let` and `const`
temporaries take different Rhino paths and a bug may need one `const` among `var`s, so the
choice is per instruction, drawn, recorded in the signature, and mutable (G25). *(Revised at
the user's review from a per-program `TempKeyword`.)*

**G23. Code strings are lifted by the same lifter and emitted as an escaped string literal**, not
Fuzzilli's template literal with `2^n - 1` backslashes. Strings are fully escaped; FuzzIL's
`"`-only escaping would be a lifter bug here. Function declarations at top level or directly in
a function body only (D14); `do … while (c);` keeps its semicolon; names by type prefix (D13).

## Mutators

**G24. Eight mutators as strategies in a fixed simplest-first list: operation, input,
declaration-kind, reassign, codegen, splice, activation, number-flip** (D34's four kept;
`declaration-kind` and `number-flip` new). Operation mutation follows `OperationMutator.swift`'s
per-op table and never picks the current value; splice follows FuzzIL's backward-slice algorithm
with its remap probabilities; donors are drawn in the same test case. Runtime-assisted mutators
and Fuzzilli's minimizer are not ported.

## After the user's review (2026-10-26)

**G25. `let`/`const` are a first-class axis: drawn at every declaration site, placed in blocks
and loop bodies, TDZ expressed through named variables, and flipped by the `declaration-kind`
mutator** (`var ↔ let` always, `→ const` only for a never-assigned variable; pool order
`VAR, LET, CONST`). User: Rhino never implemented them properly; the deleted layer only had a
`let_const_block` generator. Extends G4.

**G27. Every module exposes its step as one shell-friendly call, and the high-level tests are
written against those calls** (no separate REPL module, at the user's request): `Builder()` for
hand-made IL, `program.dump`, `lift.js`, `analysis.analyse/explain/verify` (IL with types, storage
and the Rhino predictions), `strategies.draw(strategy, seed)` to run any strategy exactly once
from a seed (a builder-mutating generator must never go through `.example()`, which runs a
strategy several times), `strategies.generate(seed)`, `strategies.run_generator(name, seed)`,
`harness.compare.run_and_compare(source)`. Every such function accepts instructions, a Program or
a Builder. Generators that make no choices are plain functions `(b, *inputs)`, callable directly;
the registry invokes both kinds the same way. User requirement, 2026-10-26.

**G26. The Rhino model targets Rhino `b95f75d`, the project's pinned commit, and says so in
code** (`RHINO_MODEL_SOURCE`); `oxpecker probe` records the probed Rhino version and the
registry test warns when the two differ. A Rhino upgrade re-reads three predicates (activation
triggers in `Parser`/`IRFactory`, `Block.findExpressionType`, `OptTransformer.detectDirectCall`)
and the slow harness test confirms the predictions. The user confirmed targeting that version.

**G28. The regexp flag set is Rhino's: `g i m s u y`.** `d` (hasIndices) and `v` (unicodeSets)
are parse errors at `b95f75d`; the parse property test found `d`. Fuzzilli's `RegExpFlags` is
translated minus both.

**G29. Instance types may be functions.** `new Continuation()` is a callable (a `BaseFunction`),
so the environment's `INSTANCE_TYPE` overlay gives a constructor's instances a non-object type
where the probe table cannot say so; `constructs()` consults it before `INSTANCE_GROUP`.

**G30. `oxpecker gen` is the first user of the layer**: case `k` of `gen --seed s` is
`generate(s + k)` with the same options, and writes the probed `.js`, the `.plain.js`
reproducer, the `.il` dump and a `.meta.json` (seed, template, generators used). `oxpecker list`
prints the registry by family. The fuzz loop and `export` wait for the mutators.

**G31. Completion rate and the Rhino model are standing harness tests** (`tests/il/
test_completion_rhino.py`, `tests/il/test_rhino_model.py`, marked `slow`): the first sets
per-family floors from its first measurement; the second checks an optimizer-typed function
against an activation-forced copy of the same body at `J.typeOf`.

**G32. The Rhino model's number-variable predicate is confirmed by probes, and one refinement
is recorded for later**: `tests/il/test_rhino_model.py` shows a number variable boxed as
`Double` in the compiled, optimizer-run function where the activation copy and the interpreter
show `Integer` (literal, `p | 0`, `p * 2`, `length | 0`). A local the model calls AnyType is
boxed like the activation copy (`length`, the bare parameter) except when its expression has a
NumberType operand (`p + 1`): Codegen then emits `ScriptRuntime.add(Object, double)`, a Double,
where the activation copy keeps `Integer + Integer` an Integer. `RhinoModel.expr_type` does
not yet say "mixed"; the test records the observation until it does.

**G33. Splice is FuzzIL's backward slice, simplified at blocks**: the donor instruction's slice
includes the whole block group when the instruction is a block instruction (contents too), then
every non-block instruction defining a needed variable, transitively; a needed variable defined
by a block instruction outside the slice (a function, a parameter, a loop variable) is remapped
to a host variable of the same type rather than pulling the block in, and slice outputs are
remapped with p=0.10 as in `ProgramBuilder.swift`. Inner-output remapping at p=0.75 and the
"may mutate" inclusion at p=0.5 are not implemented; the structure tests did not need them.

**G34. A lineage is the proposal's loop: a "one more?" `st.booleans()` draw, then the mutator
index, then that step's choices** (§3.5). With `st.booleans()` (not `maybe()`) Hypothesis deletes
a step's span cleanly, so a failing lineage shrinks to the one needed step
(`test_lineages_shrink_to_the_needed_step`, ~30 s). The last choice of a complete lineage is the
`False` that ended it, which `fuzz.extend` flips to continue the lineage. A rejected step (a
child `verify` refuses) is skipped and counted on the lineage, never fatal to the test case;
`mutation()` alone `reject()`s, as the design says.

**G35. Corpus entries are Hypothesis choice sequences, not provider bytes.** `fuzz.py` stores
`choices_to_bytes(data.choices)` (the example-database format) per entry: replay is
`ConjectureData.for_choices`, extension is `ConjectureData(prefix=choices[:-1] + (True,),
random=rng)`, and the example Hypothesis saves after shrinking is already in this format, so a
shrunk finding or a compacted entry needs no re-encoding. `fuzz_one_input`'s byte buffers
(proposal §3.6) were tried first and dropped: a shrunk example cannot be turned back into
provider bytes without re-running the strategy.

**G36. Shrinking goes through an in-memory example database under one fixed key** (`Shrinker`
in `fuzz.py`): the failing choices are saved under the key's `.secondary` sub-key (an example
under the primary key counts as already shrunk: the engine's "reused previously shrunk" fast
path, which is why `fuzz_one_input` saves to the secondary corpus), the lineage test runs with
phases reuse + shrink,
one exception class per finding signature keeps distinct bugs apart, and the test body enforces
the call budget by passing unconditionally once it is spent (Hypothesis has no public shrink
budget; proposal §5.3 item 3). Compaction on insert uses the same shrinker with the property
"still reaches the probes a re-run reached" (D36) and is off by default (`--compact-budget`).

**G37. Declaration kinds are drawn once per generator invocation** (`Builder.default_kind`,
set by the `generator` wrapper from `declaration_kinds(options)`: var twice, let, const; var only
without the `let` toggle), so every `b.declare(value)` site gets a drawn kind without each
generator drawing one; a generator that reassigns what it declares passes
`declaration_kinds(options, mutable=True)`. Found when the loop's planted-bug test showed no
`const` declaration in 120 lineages (G25 was only honoured by the mutator).

**G38. A loose input is half a may-be variable, half a fresh value.** `variable(b, t)` with no
variable known to have type `t` picks a may-be one (an unknown, usually a parameter) or draws
the value maker for `t`, half and half; before, it always took the may-be one and the
completion test showed the cost (`undefined` callees, Symbols in arithmetic: calls 45 %,
control 52 %). After, with for-header variables never `const`, non-constructible and
non-callable builtins skipped by the registry (`NOT_CONSTRUCTIBLE`, `Builtin.calls is None`)
and BigInt weight 10 → 4: calls 57 %, control 88 %, objects 88 %. Type confusion stays, at half
the rate.

**G39. Java identity hashes are canonicalised before comparison** (`compare.canon_identity`):
a Java object's default `toString` (`JavaProbe@3bde59e5`) differs between any two runs; the
first campaign reported four such "findings". Values, traces and output are normalised in
`compare()`; the harness's own `@N` object ids are untouched.

**G40. `oxpecker shrink FILE` and statement-level shrinking after Hypothesis.** Hypothesis's
shrinker within a 150-run budget left reproducers at 150–170 lines; the deleted layer's
`shrink.shrink_source` (delta debugging over top-level statements, then lines, keeping the
coarse signature) is kept for that second pass in `fuzz.fuzz` (`--source-shrink-budget`, 300)
and as a command for recorded findings. Proposal §3.7 allowed exactly this residual pass.

**G41. The golden twenty** (`tests/il/test_golden.py`, `tests/il/golden/seed_NN.js`): the lifted
source of seeds 0–19 at 8 statements, depth 3. Any change to a generator, the strategies or the
lifter fails it; the diff is the review; `OXPECKER_UPDATE_GOLDEN=1` regenerates.

**G42. Continuation generators carry no try/catch, and the harness classifies the compiled
mode's refusals.** `native_continuation` and `pause_through_frames` used to wrap their bodies in
try/catch and record `e.name`; in the compiled mode `new Continuation()` fails with an
EvaluatorException ("Direct call is not supported") that a script catches as InternalError, so
every such program produced a value-specific hard signature (fifteen in one campaign) that
could not be listed as known. Unguarded, the harness now reports it as
`unsupported(continuations)` (`ModeRunner.java`, next to the IllegalStateException case), the
known signature; programs both modes report unsupported are skipped by `compare()`.

**G43. Seeded draws use Hypothesis's provider without its source-scraped constants.**
`HypothesisProvider._maybe_draw_constant` answers an integer, float or string draw with p=0.05
from constants collected by parsing the source of every loaded non-stdlib module
(`hypothesis.internal.constants_ast`), so `generate(seed)` depended on which modules the
process had imported: importing `oxpecker.fuzz` (which contains the literal 150) changed seed
11's program, and the golden test failed only when run after the CLI or loop tests.
`strategies.SeededProvider` returns no constant; `seeded_data()` builds every seeded
`ConjectureData` (`draw`, `generate`, `fuzz.draw_lineage`, the choice-budget script) with it.
Interesting values come from oxpecker's own pools, as designed. `@given` tests keep the stock
provider: there the constants are a feature, and replays use the recorded choices.

**G44. Splice and combine donors stay fresh `programs()` draws, not corpus entries.** A corpus
donor would have to be either a constant (form B of proposal §3.5: not shrinkable, and the
entry would decode differently once the corpus changes) or its recorded choices replayed
inside the step (which the public strategy API cannot do). A fresh donor keeps every entry
self-contained and replayable from its own choices alone (G35). Revisit if campaigns show that
splicing from the frontier finds more than splicing from fresh programs.

**G45. Compaction on insert stays off by default: measured.** An hour with
`--compact-budget 30` ran 585 programs (0.2 runs/s, every one of the 150 entries compacted)
and reached 16 699 probes; ten minutes without compaction ran 2 139 (3.6 runs/s), reached
19 107 probes with 491 entries, and found the same bugs. Early in a campaign nearly every run
adds an entry, so compaction costs its budget almost every iteration (proposal §5.3 item 3).
Keep `--compact-budget 0` until entries are rare, or compact in a separate pass over an
existing corpus.
