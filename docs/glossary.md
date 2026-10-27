# Glossary

The vocabulary of the generation layer (`oxpecker/il/`), the loop (`oxpecker/fuzz.py`) and the
harness, in the order the concepts build on each other. Where a term is a class or function,
its name is given in code; the reader's guide `docs/implementation-generation-layer.md` says
where it lives, `docs/repl-tutorial.md` shows it from a shell.

## Programs

- **Operation** (`ops.py`): one kind of IL instruction, a frozen dataclass such as
  `LoadInteger(value=4)` or `BeginWhileLoopBody()`, with its attributes and its input/output
  counts. About 110, mirroring FuzzIL's inventory.
- **Instruction** (`program.Instruction`): an operation applied to input variables, producing
  output variables; `v3 <- CallFunction(...) v1, v2`. Block openers also have *inner outputs*
  (a function's parameters, a loop counter, a label).
- **Var**: a variable, an integer; `v3` is `Var(3)`. Variables are defined once and may be
  reassigned only if their storage allows it.
- **Program** (`program.Program`): a tuple of instructions plus the options it was generated
  with and the template name. The unit mutators take and return.
- **Block / context** (`ops.Context`): instructions between a `Begin*` and its `End*`. The
  context flags (JAVASCRIPT, SUBROUTINE, LOOP, OBJECT_LITERAL, SWITCH_BLOCK, ...) say what may
  be emitted where.
- **Signature** (`program.signature`): a hash of a program's operation shapes, ignoring
  literal values; used to tell "the same program" across replays.
- **Template** (`templates.py`): the overall shape of a generated program: `plain`,
  `jit1_function`, `jit2_functions`, `jit_tricky_function`. Drawn by weight, then filled by
  generators.

## Generating

- **Builder** (`builder.py`): the object generators emit into. Tracks scopes, visible
  variables, types, contexts, the nesting budget (`depth`) and the Rhino model so far.
  `finish()` returns the instructions.
- **Generator** (`generators/*`, `@generator`): a Hypothesis composite that takes the builder
  (and typed inputs) and emits a few instructions: `while_loop`, `binary`, `number_computation`.
  Each has a *family*, a *weight*, a *rank* (simpler first, for shrinking), a required
  context and declared input types. **Value maker**: a generator that produces one value of a
  type (`integer`, `string`, ...), used for the program prefix and for missing inputs.
- **Family**: a group of generators (`values`, `operations`, `control`, `calls`, `objects`,
  `compiler-targeted`, `java-interop`, `continuations`, ...). Options can restrict or reweight
  by family.
- **Registry** (`strategies.REGISTRY`, `TEMPLATES`): name to spec for every generator and
  template, filled when their modules import; `registered()` does that on first use.
- **Pool** (`strategies.pools`): the fixed, ordered, weight-expanded list of generators the
  options select, per context (`statements`, `members`, `cases`, `values`). Fixed order and
  index draws are what keep Hypothesis choices stable (G6).
- **Strategy**: a Hypothesis `SearchStrategy`. Generators, `prefix`, `statements`, `nested`,
  `programs`, `evolved` and the mutators are all strategies.
- **Draw**: one random choice made by a strategy, through Hypothesis. **Choice**: the recorded
  value of a draw (an integer index, a boolean, a string).
- **Seed**: an integer that fixes every draw of one strategy run (`generate(seed)`,
  `draw(strategy, seed)`, through `SeededProvider`). Same seed, same program.
- **Prefix** (`strategies.prefix`): the two to five value makers at the start of a program,
  so later generators have variables to use. (Not the Hypothesis "prefix" below.)
- **Lifting choices** (`strategies.lifting_choices`, `ops.Lift`): per-instruction draws of
  how a value is spelled in JavaScript: inlined, `var`, `let`, `const`. Part of the program,
  so the same IL always lifts to the same text.
- **Verify** (`analysis.verify`): the analysis plus oxpecker's rules (no assignment to a
  const, labels only in break/continue, strict functions with simple parameters, ...). Every
  generated program and every mutation child must pass; a failure on a generated program is a
  generator bug.
- **Generated** (`strategies.Generated`): what `generate`/`programs()` return: the program,
  its template, `.source` (probed JavaScript) and `.plain_source`.
- **Snippet** (`strategies.Snippet`): what `run_generator` returns: one generator run after a
  prefix, with `.emitted`.
- **Options** (`options.GenOptions`): statement bounds, depth, probes, strictness, toggles,
  family/generator restriction and weights. **Toggle**: a feature that can be switched off
  (`let`, `generators`, `for-of`, `destructuring`, `spread`, `continuations`, `with`, `eval`).

## Analysing and lifting

- **Analysis** (`analysis.analyse`, `Analysis`): everything static about a program: scopes and
  visible variables, def/use, storage, flow-sensitive types, labels, the Rhino model.
  `explain()` prints it on the IL.
- **Storage** (`analysis.Storage`): how a variable is held: TEMP (inlined or a plain local),
  TOP_VAR, LOCAL_LET, BLOCK_CONST, PARAM, LABEL, ... Decides what is assignable.
- **Type** (`types.py`): a lattice value such as `integer`, `object(Array)`, `function`,
  `any`; `Typer` computes it per instruction, flow-sensitively.
- **Rhino model** (`analysis.RhinoModel`): the prediction of what Rhino's compiled mode does
  with a function: *activation* (why the optimizer gives up: `arguments`, `eval`, `with`,
  nested functions, ...), *number variables* (locals kept as unboxed doubles), *direct calls*,
  and the *expression type* NUMBER / ANY / MIXED (G32, G47).
- **Lift / lifter** (`lift.py`, `js()`): IL to JavaScript text. The only place JavaScript is
  spelled. **Probe**: a `__probe("step", v1, v2, ...)` call the lifter inserts so both Rhino
  modes report values along the way; the harness compares the probe traces.
- **Plain source**: the lifted program without probes and comments, the reproducer form.

## Mutating

- **Mutator** (`mutate.py`): a strategy that takes a program and its analysis and returns a
  changed program: `operation`, `input`, `declaration_kind`, `reassign`, `codegen`, `splice`,
  `combine`, `activation`, `number_flip`.
- **Mutation / mutation step**: one application of one mutator. `mutate(program, seed)` from
  the shell returns a `Mutated` with `.diff()`.
- **Rejected**: a mutation whose child did not verify, or that was not applicable. In a lineage
  it is skipped and counted; from the shell it raises `Rejected`.
- **Lineage** (`mutate.Lineage`): a base program and the ordered mutation steps applied to it:
  `.base`, `.steps` (each a `Step` with the mutator name and the resulting program), `.program`
  (the last one, the program that runs), `.rejected`, `.depth` (steps plus rejected). It is a
  mutation history; the name follows the proposal. `lineage(seed, steps=)` makes one from a
  shell; `evolved(options, steps)` is the strategy behind it.
- **"One more?" / stop flag**: in `evolved`, each mutation step is preceded by a boolean draw;
  `False` ends the lineage and is its last choice. The draw is skipped once the depth reaches
  the cap, so a lineage at the cap has no stop flag.
- **Step cap** (`steps`, default 5): the maximum depth of a lineage, counted from its base
  across all extensions, not per extension.

## The loop

- **Choice sequence**: the ordered list of every choice a strategy consumed while producing a
  value. For a lineage it covers the base program, the lifting choices, and every step's
  "one more?", mutator index and mutator draws. Replaying it through the same strategy
  reproduces the lineage exactly (`ConjectureData.for_choices`). This is what the corpus stores.
- **Replay** (`fuzz.replay`): the lineage a stored choice sequence draws.
- **Hypothesis prefix**: choices a fresh draw must reproduce before drawing randomly
  (`seeded_data(prefix=)`); how `extend` supplies the flipped stop flag.
- **Extension** (`fuzz.extend`): more mutation steps on a corpus entry's lineage: the stored
  sequence's final `False` becomes `True`, the new steps are drawn fresh, and the result is
  stored as the parent's choices plus the new ones. Since G48 the parent lineage object is
  continued (`evolved(parent=)`) rather than replayed. An extension is made of mutations; the
  word names the corpus-level operation, "mutation" the program-level one.
- **Dead end**: an entry at the step cap, which `extend` refuses (no stop flag to flip).
  **Rebase** (follow-up item 2, not implemented): start a new lineage whose base is that
  entry's final program, so evolution continues with a fresh choice sequence at the price of
  the base being outside it.
- **LineageCache** (`fuzz.LineageCache`): the lineages behind corpus entries, by entry id, so
  extension does not replay; entries loaded from disk are replayed on first use.
- **Corpus** (`fuzz.Corpus`, `corpus.jsonl`): the entries kept because they reached Rhino
  probes nothing before them had, each stored as its choice sequence plus metadata (template,
  step names, parent id, probes). `coverage.exec` is the JaCoCo store beside it.
- **Entry** (`fuzz.Entry`): one corpus line.
- **Probes (coverage)**: JaCoCo probe ids in Rhino the harness reports per run; *new probes*
  are those no earlier run reached. Not the same as the `__probe` calls in the source.
- **Runner / Observation** (`fuzz.Runner`, `Observation`): the function the loop calls to run
  a lineage's program in both modes, and what it returns: the verdict, the run, the new
  probes. Tests use a fake runner with planted probes and a planted bug.
- **Fresh** vs **extended**: each iteration draws a brand-new lineage with probability
  `p_new`, otherwise extends a corpus entry.
- **Compaction** (`fuzz.compact`, `--compact-budget`): shrinking an entry while it still
  reaches its new probes, before storing it. Off by default (G45).

## Comparing and findings

- **Harness** (`harness-java/`, `HarnessClient`): the Java process that runs a program in
  Rhino's *interpreted* and *compiled* modes with probes, traces and Java types.
- **Mode**: `interpreted` or `compiled`; the differential test compares the two.
- **Verdict** (`harness/compare.py`): the comparison of the modes: `equal`, `soft` (a
  difference that is expected or uninteresting), `java-types` (an Integer-versus-Double
  difference at the Java boundary), `hard` (a real difference: value, exception, status),
  `skip` (both modes unsupported), `discard`.
- **Signature**: a string naming the shape of a verdict (`hard:diff true vs false`,
  `java-types:diff Double vs Integer`); `tests/known_signatures.txt` lists the known ones.
- **Finding** (`findings.record`): a recorded hard, soft or java-types verdict; a new hard
  signature is shrunk and written with a reproducer.
- **Shrinking**: two kinds. *Choice shrinking* (`fuzz.Shrinker`): Hypothesis's shrinker on
  the lineage's choice sequence, within a call budget. *Statement-level shrinking*
  (`shrink_lifted`, `oxpecker shrink`): deleting statements from the lifted source while the
  verdict's coarse signature holds, which gets further.
- **Regression** (`tests/regressions/*.js`): a reduced reproducer of a Rhino bug the loop
  found, run by the test suite with its expected verdict.
- **Completion rate** (`test_completion_rhino.py`): the share of a family's generated
  programs that run to the end without throwing; floors per family guard generator quality.
