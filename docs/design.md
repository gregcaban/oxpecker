# oxpecker — design (as built)

Differential testing of Mozilla Rhino: every JavaScript program is run in Rhino's **interpreted**
mode and its **compiled** (JVM bytecode) mode, the observable results are canonicalised, and any
difference is a bug candidate, shrunk to a small reproducer. This document describes the system as
it exists in the repository, not the brief it grew from (§2 lists where the two parted).

Status: 2026-10-28. Rhino 1.9.2 from Maven Central (switched from master `b95f75d`, where the findings were
made); 33 reproducers in `tests/regressions/` show on 1.9.2, 7 more under `master-only/` only on master.

## 1. Goal and scope

- Oracle: **interpreted == compiled**. Not "matches the spec". A program that fails identically in
  both modes is not a mode finding, but an engine *crash* (a raw Java exception escaping Rhino)
  is always a finding, even when both modes crash.
- In scope: core ECMAScript as Rhino supports it; **Java interop** (values crossing the JS/Java
  boundary — embedders observe `Integer` vs `Double` there); **continuations** (interpreter-only
  by design, which is itself a mode difference the harness records).
- Out of scope: E4X, the `debugger` statement, performance. The `debugger` *mode* (interpreter +
  no-op `Debugger`) is implemented in the harness but not used by default.
- Language version is a per-run parameter (`languageVersion` 200 = ES6, 250 = ECMASCRIPT); both
  modes always use the same one.

## 2. Decisions that changed from the brief

| brief | as built | why |
|---|---|---|
| three modes incl. `debugger` in every run | interpreted + compiled; `debugger` available via `--modes` | user decision; triage only |
| Java interop out of scope for v1 | in: `J` host object (`JavaProbe`), `java-interop` generators, Java leaf types recorded everywhere | this is where embedders see numeric divergences |
| continuations out of scope | in: `__pause()` host function, `continuations` toggle | interpreter-only feature = mode difference |
| Java-type differences folded into "soft" | own verdict kind `java-types`, always reported, deduplicated, own property test | user: "don't discard it"; also: don't make it the only thing |
| JS canonicaliser as the only option | JS canonicaliser (`canon.js`) plus Java-side runtime class recording | the representation is only visible from Java |
| Rhino as composite build | Rhino built from `../rhino` and published to Maven local; `rhinoVersion` is one Gradle property; every version-sensitive call goes through `RhinoCompat` (reflective) | easy version bumps |
| pytest-xdist for throughput | not needed yet: ~15–60 cases/s per JVM | — |

## 3. Architecture

```
Python (Hypothesis)                                       Java harness (one JVM, Rhino embedded)
  oxpecker/gen/        ProgramBuilder → JS source text      harness-java/
    generators/*.py    133 generators, 15 families            Harness        JSON lines on stdio; --replay; --info
    templates*.py      9 templates                            ModeRunner     per mode: fresh Context + safe scope,
    strategies.py      swarm subset + template → Program                     64 MB-stack thread, watchdog, budget
    environment/       Rhino capability probe (API model)     Host           print/__probe/__pause/J, canon.js, prelude.js
  oxpecker/harness/                                           Coverage       JaCoCo exec data per bucket (optional)
    client.py          JVM lifecycle, restarts, coverage       JavaProbe      the Java side of the interop boundary
    compare.py         verdicts + dedup signatures             RhinoCompat    all version-sensitive Rhino calls
  oxpecker/corpora/    test262, rhino-jstests, rhino-mozilla
  oxpecker/inspector.py  export for the UI
  oxpecker/cli.py      gen | run | diff | list | corpus | probe | export | metrics
  tests/               property tests, harness self-tests, regressions, seeds
  ui/                  Next.js inspector (reads ui/data/<run>/)
```

### Repository layout

```
harness-java/            Gradle; rhinoVersion in gradle.properties; ./gradlew fatJar → build/libs/
bin/harness              launcher (JDK 21; OXPECKER_JAVA, OXPECKER_COVERAGE)
oxpecker/                Python package (installed editable into .venv)
tests/regressions/*.js   minimal reproducers; each must stay detected (test_harness.py)
tests/seeds/*.js         the two test262 cases Rhino tags as mode-specific, standalone
tests/findings/          property-test output (gitignored): <kind>.jsonl logs + shrunk .js
runs/                    scratch output of gen/run/corpus (gitignored)
ui/data/<run>/           inspector exports (gitignored)
docs/                    this document, the original brief
scripts/nightly.sh       build → regression check → coverage-steered property run → corpora → export
```

## 4. Execution harness

### 4.1 Protocol

One long-lived JVM per client, JSON lines over stdin/stdout, logging on stderr. One case per
request; never concatenated.

Request: `{id, source, sourceName, modes: ["interpreted","compiled"], languageVersion, strict,
instructionBudget, wallMs, seed, continuations, maxResumes, coverageBucket}`; `cmd` may be
`run` (default), `info`, `coverage`, `exit`.

Response: `{id, results: {mode: Outcome}, restart?: true}`. `restart` means the JVM is unhealthy
(stuck worker thread after a watchdog timeout, or `OutOfMemoryError`); the client restarts it and
reports the modes that did not run as `timeout(not-run-after-watchdog)` or `crash(not-run)`.

### 4.2 Per-mode execution

On a dedicated thread (64 MB stack, same in all modes):

1. Fresh `Context` from `HarnessFactory`; mode (`RhinoCompat.setInterpreted`), language version,
   `Locale.ROOT`, UTC, instruction observer every 10k instructions (in all modes, so the compiler
   emits the same observer code; `instructionBudget <= 0` disables it for attribution runs),
   interpreter frame cap 20k.
2. Fresh scope: `initSafeStandardObjects(new TopLevel())` — no `Packages`, no shared state.
3. `Host.install`: `print`, `__probe`, `__isProxy`, `__jtype`, `__pause`, the `J` Java object,
   then `canon.js` and `prelude.js` compiled and run **in interpreter mode** whatever the mode
   under test, so the oracle code never varies between modes.
4. `compileString`; `RhinoException` at compile time → `syntax`. Then `compiledActually` is
   determined from the script's code object (`InterpreterData` ⇒ interpreted). Compiled mode with
   `compiledActually == false` → status `fallback` (class-size limit), excluded from comparison.
5. `exec` (or `executeScriptWithContinuations` + resume loop when `continuations` is set and the
   script is interpreted), `processMicrotasks()`, canonicalise the completion value.
6. Classification: `JavaScriptException` → `throw(js-value)`, `EcmaError` → `throw(ecma-error)`,
   budget → `timeout`, `StackOverflowError`/"too much recursion" → `resource`,
   `IllegalStateException` about continuations → `unsupported(continuations)`, any other
   `RuntimeException`/`LinkageError`/`AssertionError` → `crash` with class, message and stack.

### 4.3 Outcome

```
{mode, compiledActually, status: ok|throw|syntax|timeout|resource|fallback|unsupported|crash,
 value, valueJavaTypes, thrown: {kind, name, message, value, valueJavaTypes},
 trace[], traceJavaTypes[], javaTrace[], javaTraceTypes[], continuations[], soft[], output[],
 engineException: {clazz, message, stack}, reason, ms, newProbes}
```

### 4.4 Canonicalisation (`canon.js`)

Captures every builtin it needs at install time, so user code cannot affect it. Renders
primitives exactly (`-0`, `NaN`, BigInt suffix, exact numbers), objects by own keys in
`Reflect.ownKeys` order with descriptor attributes, accessors as "has getter/setter" (never
invoked), prototypes by well-known name or reference id, cycles by reference id, arrays with
holes distinct from `undefined`, typed arrays by elements, Map/Set contents, functions by
name/length/`toString`, errors without `stack`/`fileName`/`lineNumber`/`columnNumber`/
`rhinoException`/`javaException` and with `message` as a soft field, Proxies as `[Proxy]`
without triggering traps, Java objects as `[Java class]`. The global object, builtin prototypes
and native functions render as tokens (walking them cost thousands of nodes per probe and hung
programs that bound `this` or `Object` into a probed variable). Depth/node/array/string caps.

Alongside the text it returns the **Java runtime class** of every number/string leaf in
traversal order (`Integer`, `Double`, `ConsString`, `String`, `BigInteger`…).

### 4.5 Probes and the Java boundary

- `__probe(label, v0, v1, …)` canonicalises immediately and appends to `trace`; the generator
  inserts one after every top-level statement (configurable; the reproducer form has none).
- `J` (`JavaProbe`) is a plain Java object wrapped by LiveConnect: typed parameters
  (`takesInt/Long/Float/Double/String/BigInteger/…`), `echo`/`typeOf` (records the runtime class
  Rhino passed), overloads (`over(int|long|double|String|boolean|Object)`), values coming back
  (long beyond 2^53, float, byte, char, boxed, BigInteger, null, arrays, List, Map). Each call
  logs the received/returned value (`javaTrace`, compared strictly) and runtime classes
  (`javaTraceTypes`, java-types verdict).
- Where the raw representation leaks: host `Callable`s (`__probe` and anything an embedder
  installs), the completion value, `ScriptableObject.getProperty` — compiled code hands Java an
  `Integer` where the interpreter hands a `Double`. LiveConnect *method* arguments are coerced
  first (`NativeJavaObject.coerceTypeImpl`: every number → `Double` for `Object` parameters), so
  differences there are conversion/overload bugs, not representation leaks.

### 4.6 Continuations

`__pause(v)` calls `Context.captureContinuation()`, stashes `v`, and throws the
`ContinuationPending`; `ModeRunner.execute` catches it, logs `pause(<canon v>)`, resumes with
`v` as the call's result, up to `maxResumes`. Compiled scripts cannot capture (Rhino throws
`IllegalStateException`) → `unsupported(continuations)`, a hard finding with its own signature.

### 4.7 Determinism (`prelude.js`)

`Math.random` → seeded mulberry32 (seed from the request); `Date.now()`/`new Date()` → fixed
epoch; `WeakRef`/`FinalizationRegistry` deleted; locale/time zone fixed on the Context; Promise
jobs drained before results are read. Checked by `test_self_consistency`.

### 4.8 Coverage (JaCoCo, optional)

`OXPECKER_COVERAGE=1` makes `bin/harness` attach the JaCoCo agent (`includes=org.mozilla.javascript.*`,
`excludes=…gen.*`). After each mode `Coverage.drain(bucket)` takes the agent's execution data
(and resets it), counts probes hit that the **cumulative** store had not seen
(`run[i] && !cumulative[i]`), merges into the cumulative and the per-bucket (`<focus>|<mode>`)
stores, and returns the count as `newProbes`. `cmd: coverage` analyses every bucket against the
Rhino classes once (per-package method/branch/line counters; per-class method counters for
`optimizer.*`, `Interpreter`, `ScriptRuntime`, …). The JVM is never recycled while coverage is on.

### 4.9 Harness self-tests (`tests/test_harness.py`)

Self-consistency; isolation canaries (`globalThis.leak`, `Object.prototype` pollution across
cases and modes); fallback detection with a 20k-statement function; small scripts really
compiled; syntax/timeout/stack classification; determinism stubs; Java types reported per mode;
thrown values canonicalised; language version honoured; every regression and seed still detected
(a `// oxpecker-expect: <kind>` header names the expected verdict kind, default `hard`).

## 5. Comparison rules (`compare.py`)

| situation | verdict |
|---|---|
| either mode `fallback` | `skip` (counted) |
| either mode `crash` (also both) | **hard** |
| statuses differ (incl. `unsupported`) | **hard** |
| both `ok`: `value`, `trace`, `output`, `javaTrace` or `continuations` differ | **hard** (first differing trace index and argument reported) |
| both `throw`: name or canonical value differ | **hard** |
| only Java representation differs (`valueJavaTypes`, `traceJavaTypes`, `javaTraceTypes`, thrown types) | **java-types** (hard with `--strict-java-types`) |
| only error messages differ | soft |
| one mode `timeout`/`resource`, the other `ok`/`throw` | soft |
| both `timeout`/`resource` | discard |

Every verdict has a **signature** for dedup: kind + the first reason, with probe/arg indices,
reference ids and numbers normalised (`#`), except that zero keeps its sign (`-0 vs 0` is its
own bug class). `oxpecker run` prints counts per signature; the property test uses it to
suppress known findings and to give each new finding its own Hypothesis exception class.

## 6. Generator layer

### 6.1 ProgramBuilder (`gen/builder.py`)

Mirrors Fuzzilli's `ProgramBuilder` surface but emits JS **source text** directly (the owner reads
the programs). Typed variable pool with scopes (`fresh`, `declare`, `pick`, `pick_or_make`,
`expr`), `define_function`, `for_loop`, two-phase `try_block`, `capture`/`emit_lines` to emit the
same body twice, `run_some` for nested statements, `probe()`, per-line generator attribution
(`generator_spans`) and stats. All randomness goes through Hypothesis `draw`.

Shrink-friendly rules kept: program length is one `st.lists` draw; variables are picked by an
integer draw modulo the pool size; no `assume`/filter. Variable names carry their type
(`n1`, `s2`, `o3`, `f4`, `a5`…).

### 6.2 Registry and catalogue

`@generator(name, family, weight, inputs, toggle, sloppy_only, output)` registers a function
`(b, *inputs)`; `@template(name, weight)` a program shape; `@value_maker(T)` the generator used
when `pick_or_make` finds no variable of a type. `registry.catalogue()` is what the UI reads
(description = docstring, source snippet, path).

### 6.3 Families (133 generators)

| family | n | examples |
|---|---|---|
| values | 9 | boundary numbers, int32, BigInt, strings (surrogates, numeric-looking), symbols, regexps |
| containers | 8 | arrays, sparse/holey arrays, objects with accessors, typed arrays, Map/Set |
| operations | 14 | unary/binary/bitwise/compare/logical/compound/update, `|0`/`>>>0`, templates, concat, number conversions |
| properties | 10 | get/set/delete/in/compound through properties, defineProperty, freeze/seal, for-in, prototype mutation |
| calls | 10 | function defs, calls, builtin method/global calls, construct, call/apply/bind, `arguments`, default/rest params, bounded recursion |
| control | 8 | loops, if/switch with fallthrough, try/catch/finally, abrupt `finally`, labelled break, completion values via `eval` |
| compiler-targeted | 9 | numeric-local type flip, activation vs no activation (same body twice), direct-call arity/redefinition, call-site relinking, property-site shape change, ConsString chains, int32 boundary arithmetic, `super` in object literals, constant sites |
| exotic | 8 | Proxy with logging traps, valueOf/toString/toPrimitive, getters with side effects, `with`, direct/indirect eval, Promise jobs, unicode strings |
| java-interop | 8 | typed calls, echo/typeOf, arithmetic results handed to Java, overload resolution, values returned from Java, Java arrays, collections, numeric pipeline |
| objects (Fuzzilli) | 13 | int/float/holey arrays, builtin instances and prototype calls on wrong receivers, object builder/constructor functions, `this`, named/undeclared variables, builtin overwrite, homomorphic objects, prototype hierarchies |
| control-ops (Fuzzilli) | 12 | dup, cross-type reassignment, chained assignment, complex for loops, throw/conditional throw, block break, number computation chains, type tests, method-as-property, computed method calls, `new.target` |
| exotic-arrays (Fuzzilli) | 9 | element-kind transitions, length changes, imitation objects, custom iterators with `return()`, `super` method/property get/set/update, yield patterns |
| api | 4 | probed prototype methods on matching and wrong receivers, static functions, constructors (from the capability table) |
| toggled | 6 | let/const + TDZ, generators, for-of, destructuring, spread (array/object only: call spread crashes Rhino) |
| continuations | 5 | pause in function/loop/try-finally, pause across non-JS frames, legacy `Continuation` |

Toggles: `let`, `generators`, `for-of`, `destructuring`, `spread`, `continuations`. `with` and
direct eval are `sloppy_only` and dropped for strict programs (and inside the strict half of
`strict_vs_sloppy_body`).

### 6.4 Templates (9)

`plain`, `warmup_then_type_change`, `mutate_between_calls`, `deep_closures`,
`strict_vs_sloppy_body`, and the Fuzzilli ports `jit1_function`, `jit2_functions`,
`jit_tricky_function`, `json_fuzzer`. Each program draws one template and (swarm testing) a
random non-empty subset of the enabled generators; the template calls `statements()` which draws
the top-level list and probes after each statement.

### 6.5 Environment model (`gen/environment/`)

`oxpecker probe` runs `probe.js` through the harness (globals, constructors, prototype and
static members with arities, including `%TypedArray%.prototype`) and runs ~50 syntax snippets as
individual two-mode cases (a parse difference or crash between modes is itself a finding — this
is how the call-spread crash was found). The table is cached in `rhino-capabilities.json`; the
`api` generators draw from it, so a Rhino upgrade only needs a re-probe. Fuzzilli's
`JavaScriptEnvironment`/TypeScript lib approach was not needed.

### 6.6 Randomness backends (`gen/rand.py`)

The builder draws through a `Rand` interface with two implementations: `HypothesisRand` (a
Hypothesis `draw`; the property tests get shrinking and the example database) and `ChoiceRand`
(a recorded list of ints; replaying rebuilds the same program, and because every value is folded
into the range the builder asks for at that point, *any* mutated list is a valid program).
`mutate()` offers point changes, block delete/insert/duplicate, splice from another sequence,
havoc and truncation — the same operator family as Fuzzilli's mutators and AFL's havoc stage,
applied to generator choices instead of IR or bytes.

### 6.7 Coverage-guided corpus loop (`oxpecker fuzz`, `fuzz.py`)

```
corpus ← corpus.jsonl (choice sequences) ; harness ← coverage.exec (frontier)   [resume]
loop:
  parent ← uniform(corpus) with prob 1-p_new, else fresh
  choices ← mutate(parent.choices, others=sample(corpus))
  program ← build_program(ChoiceRand(choices))
  run both modes with coverage; verdict ← compare
  if newProbes > 0: corpus.add(choices)               # it reached something new
  if hard and signature unknown: shrink (choice-level ddmin, then statement/line-level source
      ddmin under the *coarse* signature), record reproducer, mark signature known
  every N runs: save coverage.exec
```

Parents are drawn uniformly, not by score, so a region that has been "spent" stops attracting
mutations naturally; the frontier persists across runs. Shrinking uses the coarse signature (kind
plus the *type* of the first difference) because values and lengths change while shrinking.
Throughput is ~2–5 runs/s with the agent attached, dominated by shrinking when findings are
frequent. The property test remains the better tool for a quick, reproducible, seed-driven run;
the corpus loop is the long-running one (`scripts/nightly.sh` can call either).

### 6.8 Property tests (`tests/test_differential.py`)

```
test_interpreted_equals_compiled   default oracle; hard findings raise a per-signature
                                   AssertionError subclass (Hypothesis shrinks each distinct
                                   signature separately); signatures already in tests/regressions
                                   are logged, not raised
test_java_representation_matches   strict oracle (java-types ⇒ hard); run separately
```

`target()` labels: trace depth (statements executed before the first throw) and, with
`OXPECKER_COVERAGE=1`, newly covered Rhino probes. Shrunk reproducers go to
`tests/findings/<kind>-<sig>.js` (with probes, since trace findings need them); every finding is
appended once per signature to `tests/findings/<kind>.jsonl`. `OXPECKER_EXAMPLES`,
`OXPECKER_TOGGLES` configure a run.

## 7. Corpora (`oxpecker corpus …`)

- `test262`: Rhino's vendored tree, filtered like `Test262SuiteTest` (unsupported features
  `class`, async, Atomics, Temporal…; `~`-excluded folders; async/module flags), harness includes
  inlined, a small `$262` shim (no `createRealm`/`detachArrayBuffer`), strict and sloppy variants,
  `--known-divergences` lists what `test262.properties` tags per mode (exactly two files at the
  pinned commit; both reproduced as hard findings). Full replay: 59k cases, ~15 min.
- `rhino-jstests`: `tests/testsrc/jstests` with `load()` inlined.
- `rhino-mozilla`: the legacy Mozilla suite with its `shell.js` chain (E4X/LiveConnect suites
  skipped). `reportCompare` output makes the comparison value-level.

## 8. Reproducers, dedup, reporting

- `tests/regressions/<name>.js`: header with per-mode behaviour, exception, origin, date and a
  `// Replay: oxpecker diff …` line; `test_harness.py` asserts each is still a finding of the
  expected kind.
- `oxpecker diff file.js [--context N]`: per-field first difference between the modes.
- `oxpecker run`: verdict per file, counts per verdict and per status per mode, unique signatures.
- `oxpecker export`: samples every generator (plain template, no swarm) and template, runs them,
  writes `catalogue.json`, `samples.jsonl`, `metrics.json` (per focus: fired, syntax-in-both,
  early-throw, throw rate, mean probes before throw, timeouts/resource/fallback/crash, verdict
  counts, size, duplicate rate, exception names), `findings.json` (dedup by signature) and,
  with `--coverage`, `coverage.json`; `oxpecker metrics <run>` prints the worst-first table.

## 9. Inspector UI (`ui/`)

Next.js 15 (App Router, TypeScript, Tailwind, Recharts, Shiki), server components reading
`ui/data/<run>/` at request time, streaming `samples.jsonl`. Pages: run overview (stat tiles,
size histogram, exception breakdown, generator usage with never-firing ones highlighted),
generator catalogue (sortable, problems first, filters), generator detail (source snippet,
metrics, sample grid with the generator's lines tinted), program browser (filters incl. verdict,
per-mode status, fallback, exception, size, text search; paginated), program detail (per-line
generator tinting, two-column mode comparison, trace/java/continuation diff with the first
differing index marked back onto the source, copy-reproducer, j/k navigation), findings (grouped
by kind with triangulation hints). `coverage.json` is not yet visualised.

## 10. Findings against Rhino b95f75d

Thirty regressions; the behavioural mode divergences (full list with reproducers in
`tests/regressions/`, summarised in `README.md`):

- compiled: `++`/`--` wraps at int32 outside optimizer-typed locals; `var n = -1; n * 0` is `0`
  (lost `-0`); direct recursive call with `n === 0` base case returns a wrong value; arrow
  functions get a `prototype`; `p.x` through a Proxy fires `has` before `get`; `Number(2n**1024n)`
  through a local throws; a nested `finally`/`throw` inside a generator escapes; codegen crashes
  on `z((yield 3))`, `a(1 < 2)` from inside a function, `yield` in a default parameter, `yield`
  from a catch inside for-of, `obj[p] &&= v`.
- interpreter: `++super[k]` returns the key and `super[k]--` creates an own `"0"` property;
  `super.x++` on a string concatenates; `f.apply(null, arrayLike)` with a rest parameter builds
  wrong arguments; generator-method `prototype` has the wrong `[[Prototype]]` (and is absent
  compiled); object-rest destructuring from a Proxy with a Symbol key throws; re-entrant
  `generator.next()` hits a `FAILED ASSERTION`.
- Java boundary: the same number reaches a host `Callable` as `Double` (interpreter) vs
  `Integer` (compiler) — pervasive, reported as `java-types`.
- both modes (engine crashes with raw Java exceptions): call spread `f(...a)`,
  `String(new CallSite())`, `Array.find()`, Proxy `getOwnPropertyDescriptor` returning
  undefined, `for (const [[x] = [1]] = []; …)`, `new Int8Array({length: 2**53})`,
  `BigInt.asIntN(2**32-1, 1n)`, string-iterator prototype confusion, JS1.7/1.8 comprehension
  forms, a 2^31-character string.

## 11. Acceptance criteria — status

| criterion | status |
|---|---|
| same-mode self-consistency, no harness noise | met (`test_self_consistency`; corpus replays show no flapping) |
| isolation canaries; fallback detected and excluded | met |
| both known test262 divergences reported with correct attribution | met (`tests/seeds/`, `corpus test262 --only-known`) |
| injected compiler bug detected and shrunk to ≤10 statements | **not done** as an injected patch; real compiler bugs were found and shrunk to 1–5 statements instead |
| syntax-in-both < 5 %, early-throw < 30 % on generated programs, per generator | met in the default configuration (export table: 0 % syntax; early-throw worst case `proto_mutation` was fixed) |
| UI lists every generator/template/seed, ≥ 20 samples each, worst generators in one click | met (`export --per-generator 20`; catalogue sorts problems first) |

## 12. Sources and prior art

What each part of the design borrows from:

- **Rhino** — [mozilla/rhino](https://github.com/mozilla/rhino), pinned at
  [`b95f75d`](https://github.com/mozilla/rhino/commit/b95f75d). `Context.EvaluationMethod`, the
  `ClassSizeException` fallback and `captureContinuation` in
  [`Context.java`](https://github.com/mozilla/rhino/blob/master/rhino/src/main/java/org/mozilla/javascript/Context.java);
  the compiler in [`optimizer/`](https://github.com/mozilla/rhino/tree/master/rhino/src/main/java/org/mozilla/javascript/optimizer)
  (`Optimizer` number-type inference, the `invokedynamic` linkers `DoubleLinker`, `IntegerLinker`,
  `StringLinker`, `ConsStringLinker`, `NativeArrayLinker`, `ConstAwareLinker`); per-mode test
  setup in [`Test262SuiteTest.java`](https://github.com/mozilla/rhino/blob/master/tests/src/test/java/org/mozilla/javascript/tests/Test262SuiteTest.java)
  and the mode tags in [`test262.properties`](https://github.com/mozilla/rhino/blob/master/tests/testsrc/test262.properties);
  LiveConnect coercion in [`NativeJavaObject.java`](https://github.com/mozilla/rhino/blob/master/rhino/src/main/java/org/mozilla/javascript/NativeJavaObject.java);
  feature table at [mozilla.github.io/rhino/compat/engines.html](https://mozilla.github.io/rhino/compat/engines.html).
- **Fuzzilli** (Google Project Zero, Apache 2.0) — [googleprojectzero/fuzzilli](https://github.com/googleprojectzero/fuzzilli).
  The `ProgramBuilder` method surface, the generator catalogue in
  [`CodeGenerators.swift`](https://github.com/googleprojectzero/fuzzilli/blob/main/Sources/Fuzzilli/CodeGen/CodeGenerators.swift)
  (families `objects`, `control-ops`, `exotic-arrays` are ports of it), the templates in
  [`ProgramTemplates.swift`](https://github.com/googleprojectzero/fuzzilli/blob/main/Sources/Fuzzilli/CodeGen/ProgramTemplates.swift)
  (`jit1_function`, `jit2_functions`, `jit_tricky_function`, `json_fuzzer`), the environment model
  idea from `JavaScriptEnvironment.swift`, and coverage-guided generation
  ([Docs/HowFuzzilliWorks.md](https://github.com/googleprojectzero/fuzzilli/blob/main/Docs/HowFuzzilliWorks.md)).
  Re-implemented as Hypothesis strategies; Fuzzilli is not a dependency.
- **Hypothesis** — [hypothesis.readthedocs.io](https://hypothesis.readthedocs.io/): composite
  strategies, shrinking, the example database, and
  [`target()`](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.target)
  for metric-guided search ("targeted property-based testing": Löscher & Sagonas,
  [*Targeted Property-Based Testing*, ISSTA 2017](https://doi.org/10.1145/3092703.3092711)).
- **JIT-Picking** — Bernhard, Scharnowski, Schloegel, Blazytko, Holz,
  [*JIT-Picking: Differential Fuzzing of JavaScript Engines*](https://doi.org/10.1145/3548606.3560624),
  ACM CCS 2022 ([code](https://github.com/RUB-SysSec/JIT-Picker)). The interpreter-vs-JIT oracle
  and the state-checksum probes after statements (`__probe`, `trace`).
- **DIE** — Park, Xu, Jang, Lee, Kim, [*Fuzzing JavaScript Engines with Aspect-preserving
  Mutation*](https://doi.org/10.1109/SP40000.2020.00067), IEEE S&P 2020 — keep what made a seed
  interesting, vary its surroundings (the plan for seed templates in §9.8 of the brief).
- **Swarm testing** — Groce, Zhang, Eide, Chen, Regehr,
  [*Swarm Testing*](https://doi.org/10.1145/2338965.2336763), ISSTA 2012 — each program enables a
  random subset of generators (`strategies.py`).
- **EMI** — Le, Afshari, Su, [*Compiler Validation via Equivalence Modulo
  Inputs*](https://doi.org/10.1145/2594291.2594334), PLDI 2014 — the `activation_pair` and
  `strict_vs_sloppy_body` generators emit the same body twice in variants that must agree.
- **Csmith** — Yang, Chen, Eide, Regehr, [*Finding and Understanding Bugs in C
  Compilers*](https://doi.org/10.1145/1993498.1993532), PLDI 2011 — random programs that are
  valid and observable by construction (bounded loops, guarded calls, typed variable pool).
- **jsfunfuzz / compare_jit** — Mozilla's archived
  [funfuzz](https://github.com/MozillaSecurity/funfuzz) (`compare_jit`): prior art for JIT on/off
  comparison of a JS shell.
- **test262** — [tc39/test262](https://github.com/tc39/test262) as vendored by Rhino (replay
  corpus, `$262` host shim); Mozilla's legacy jstests (`tests/testsrc/tests`) and Rhino's own
  `jstests`.
- **JaCoCo** — [jacoco.org](https://www.jacoco.org/jacoco/trunk/doc/): the Java agent, probe
  insertion ([*implementation design*](https://www.jacoco.org/jacoco/trunk/doc/implementation.html)),
  and the runtime API (`org.jacoco.agent.rt.IAgent.getExecutionData(reset)`) used for per-run
  coverage deltas; `org.jacoco.core` for analysis.
- **AFL / libFuzzer** — edge-coverage feedback as the general idea behind "new probes" scoring
  ([AFL technical details](https://lcamtuf.coredump.cx/afl/technical_details.txt)); oxpecker's
  score is probe-level, not edge-level, and has no hit-count buckets.

## 13. Open items

- Coverage page in the UI; weight the novelty score toward `optimizer.*`/`Interpreter` probes.
- Dedup across manifestations: the exact signature re-reports known bugs when they surface
  through a different probe context; a per-regression "bug class" matcher would help triage.
- `$262.createRealm`/`detachArrayBuffer` in the shim (a few test262 tests need them).
- `FEATURE_INTEGER_WITHOUT_DECIMAL_PLACE` and other `ContextFactory` features as run flags.
- More Java interop: `JavaAdapter`, `Packages`/`importClass` (needs `initStandardObjects`),
  Java exceptions crossing into JS.
- The injected-bug validation from the brief (§5.8-5), as a scratch patch to `DoubleLinker`.
- Automated UI tests; `pytest-xdist` with one JVM per worker if throughput matters.
- File the findings upstream (mozilla/rhino) with the reproducers.
- The generation layer and the loop keep their own list: `docs/design-generation-layer.md` §27.
