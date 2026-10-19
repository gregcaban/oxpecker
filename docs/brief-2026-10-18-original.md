# Project Brief: Differential Testing of Mozilla Rhino — Interpreted vs Compiled Mode

## 1. Goal

Build a pipeline that generates JavaScript programs and checks that **Mozilla Rhino produces identical observable behaviour in its interpreted mode and its compiled (JVM bytecode) mode**. Every program is run in both modes, the results are canonicalised, and any difference is a bug candidate that is automatically shrunk to a small reproducer.

This is a prototype for a closely related internal problem (an experimental engine feature toggled ON/OFF). Rhino is used because it is open source, embeddable from Java, and already has two execution paths that must agree. In this document **OFF = interpreted, ON = compiled**.

The focus is **correctness**, not performance. Startup cost, JIT warm-up and throughput matter only insofar as they affect how many programs we can test.

The pipeline should:

1. **Go deep** on known suspect expressions by generalising and composing them into many variants.
2. **Go broad** over a deliberately scoped slice of the language: the subset Rhino supports, weighted towards constructs that the compiler treats differently from the interpreter.
3. Reuse existing corpora (test262, Rhino's own tests, other engines' regression suites) as seeds and replay inputs.
4. Produce minimal, deterministic reproducers that can be checked into a regression suite.
5. Provide a local UI to inspect generator quality (§12).

## 2. Rhino facts the design depends on

Checked against Rhino `master` at commit `b95f75d` (13 Oct 2026); latest release is 1.9.1 (Feb 2026). Re-verify these when pinning a version.

- **Mode switch:** `Context.setEvaluationMethod(EvaluationMethod.Interpreter | EvaluationMethod.Compiler)` (also `setInterpretedMode(boolean)`). `setOptimizationLevel` is deprecated since 1.8.0; opt levels have collapsed to two methods: interpreter (-1) and compiler (9). The shell exposes `-int` / `-opt`.
- **Third useful configuration:** Rhino's own test262 runner uses three modes: `interpreted`, `compiled`, and `debugger` (interpreter with a no-op `Debugger` attached). We adopt the same three (§5.2).
- **Silent fallback:** if code generation hits a JVM class-file limit (`ClassSizeException`, e.g. the 64 KB method limit), `Context` **falls back to the interpreter**. A large "compiled" test may therefore actually run interpreted and hide every difference. The harness must detect this (§5.4).
- **Compiler internals worth targeting** (`org.mozilla.javascript.optimizer`):
  - `Optimizer` does number-type inference for function locals, but **skips any function that `requiresActivation()`** (e.g. uses `eval`, `with`, `arguments` in certain ways).
  - **Direct calls** between functions (`isTargetOfDirectCall`) get special calling conventions.
  - Code generation uses **`invokedynamic` linkers** that specialise call sites by observed types: `DoubleLinker`, `IntegerLinker`, `StringLinker`, `ConsStringLinker`, `NativeArrayLinker`, `ConstAwareLinker`, `BooleanLinker`. Call sites that relink when the observed type changes are classic bug territory, much like JIT tiering in V8.
  - Generators, `try/finally` and control-flow completion values are compiled into bytecode state machines quite differently from the interpreter.
- **Language level:** default is `Context.VERSION_ES6` (200); `VERSION_ECMASCRIPT` (250) adds features that may break backward compatibility. Both modes must use the same version.
- **Unsupported features** (from Rhino's test262 runner): `class`, class fields, `async` functions, async iteration, `Atomics`, `SharedArrayBuffer`, tail calls, `Temporal`, modules. Partial support: generators (~39% of test262 passing), `let`/`const` (~30%), `for-of` (~34%), destructuring and spread (improving in 1.9). Supported since 1.8/1.9: `Proxy`, `Reflect`, `super` in object literals, `Symbol`, `BigInt`, `Promise`, template literals, newer RegExp features.
- **Known mode-specific behaviour in test262** (present in `tests/testsrc/test262.properties` at the pinned commit):
  - `test/language/statements/try/completion-values-fn-finally-abrupt.js` fails in **compiled only**.
  - `test/language/expressions/super/prop-expr-getsuperbase-before-topropertykey-putvalue-increment.js` fails in **interpreted and debugger only**.
  These are real interpreted/compiled divergences and serve as harness validation seeds (§13).
- **Timeouts:** `setInstructionObserverThreshold` and `ContextFactory.observeInstructionCount` can stop runaway scripts. In compiled mode this enables `setGenerateObserverCount(true)`, which changes the emitted bytecode. Instruction counts are **not** comparable between modes.
- **Recursion:** the interpreter keeps its own heap-allocated stack (bounded by `setMaximumInterpreterStackDepth`); compiled code uses the Java thread stack. Deep-recursion behaviour differs by design.
- **Determinism hooks:** `Context.setLocale` and `Context.setTimeZone` exist and should be fixed for both modes.
- **Existing helpers:** `testutils` has `Utils.runWithAllModes` and `runWithMode`; `Test262SuiteTest` shows how to set up `$262`, harness includes and per-mode execution.

## 3. Constraints and scope

- **Primary tooling:** Python + [Hypothesis](https://hypothesis.readthedocs.io/) for generation, shrinking and failure storage; a **Java harness** that embeds Rhino in-process.
- **Rhino build:** build from source at a pinned commit (publish to Maven local, or a Gradle composite build). We need this to inject deliberate bugs for validation and to run coverage. Java 17+ to run; Java 21+ to build Rhino.
- **Design inspiration, not a dependency:** Fuzzilli's code generators, environment model and program templates (Apache 2.0). We re-implement the ideas as Hypothesis strategies.
- **In scope:** core ECMAScript semantics in both modes. **Out of scope for v1:** Java interop (LiveConnect / `Packages`), E4X, continuations (interpreter-only), the `debugger` statement, performance. Java interop is a strong candidate for v2, since real Rhino embedders rely on it heavily.
- The oracle is **interpreted == compiled**, not "matches the spec". A program that fails identically in both modes is not a finding.

## 4. Architecture overview

```
┌────────────────────────┐  JSON-lines over stdio  ┌──────────────────────────────────┐
│ Python                 │ ──────────────────────▶ │ Java harness (one JVM, Rhino      │
│  Hypothesis strategies │   {id, source, opts}    │ embedded)                         │
│  seeds, templates      │                         │  for each mode:                   │
│  HarnessClient         │ ◀────────────────────── │   fresh Context + fresh scope     │
│  comparator, triage    │  {id, results{mode:…}}  │   compile → verify compiled-ness  │
└──────────┬─────────────┘                         │   execute with budget + watchdog  │
           │                                       │   canonicalise result + trace     │
           │ shrink / target()                     └──────────────────────────────────┘
           ▼
  Minimised reproducers → tests/regressions/   ─┐
  inspector/export.py → ui/data/<run>/  ────────┴─▶  Next.js inspector UI
```

### Repository layout

```
difftest/
  harness-java/                 # Gradle project, depends on pinned Rhino build
    src/main/java/.../Harness.java          # stdio JSON-lines server + CLI
    src/main/java/.../ModeRunner.java       # per-mode execution (§5.3)
    src/main/java/.../Canonicaliser.java    # installs + drives canonicaliser (§6)
    src/main/java/.../Outcome.java          # result record + classification
    src/main/resources/canon.js             # canonicaliser in JS, compiled interpreted
    src/main/resources/prelude.js           # determinism stubs
    src/test/java/...                       # harness self-tests (§5.8)
  generator/
    builder.py          # ProgramBuilder: statements + typed variable pool + probes
    generators/         # one module per family of code generators
    environment/        # builtin model: lib.d.ts filtered by a Rhino capability probe
    templates.py        # program shapes (warm-up then type change, etc.)
    seeds.py            # suspect expressions as typed templates
    strategies.py       # top-level strategies, swarm selection
  difftest/
    client.py           # HarnessClient: spawns/restarts the JVM, sends cases
    compare.py          # comparison rules (§7)
    triage.py           # dedup signatures, reproducer writing
  corpora/
    import_test262.py
    import_rhino_tests.py     # Rhino jstests, legacy Mozilla tests, doctests
    import_regressions.py     # V8 mjsunit, SpiderMonkey jit-test, JSC JSTests/stress
  tests/
    test_differential.py      # Hypothesis property tests
    test_replay.py            # corpus replay
    regressions/              # minimised reproducers, checked in
  inspector/
    export.py                 # samples programs + results into JSON for the UI
  ui/                         # Next.js inspector (§12)
  README.md
```

## 5. Execution harness (Java)

The harness is the part that must be trustworthy first. It is a small Java program that embeds Rhino and runs each case in every configured mode in isolation.

### 5.1 Process model and protocol

- One long-lived JVM, started by the Python `HarnessClient`, speaking **JSON lines over stdin/stdout**. Logging goes to stderr only.
- **Request:** `{"id": "...", "source": "...", "modes": ["interpreted","compiled","debugger"], "languageVersion": 200, "instructionBudget": 5000000, "wallMs": 5000, "strict": false}`.
- **Response:** `{"id": "...", "results": {"interpreted": Outcome, "compiled": Outcome, "debugger": Outcome}}`.
- **One case per request; never concatenate cases into one script.** Startup cost is not a concern here, and concatenation causes cross-case contamination and risks the class-size fallback (§5.4). Requests can be pipelined for throughput.
- **Crash isolation:** if the JVM dies, hangs past a hard deadline, or reports `OutOfMemoryError`, the client records the outcome as `crash` or `timeout` for that case and restarts the JVM. A case that kills the JVM in one mode is re-run with that mode alone to attribute it.
- Optionally recycle the JVM every N cases (e.g. 5,000) to bound any leaked global state (class loaders, static caches).
- **CLI replay mode:** `java -jar difftest-harness.jar --replay case.js [--mode compiled]` prints the outcomes in human-readable form. This is the reproducer command shown in reports and the UI.
- In-process alternatives (JPype, GraalPy) are possible but not recommended: a subprocess survives engine crashes and stack overflows.

### 5.2 Modes

| Mode | Setup |
|---|---|
| `interpreted` | `cx.setEvaluationMethod(EvaluationMethod.Interpreter)` |
| `compiled` | `cx.setEvaluationMethod(EvaluationMethod.Compiler)` |
| `debugger` | interpreter + `cx.setDebugger(new NoOpDebugger(), null)` (as in `Test262SuiteTest`) |

`interpreted` vs `compiled` is the primary oracle. `debugger` is cheap and catches debug-path divergences; it also helps triage: when compiled differs from both interpreter variants, the compiler is the likely culprit.

Everything else is identical across modes: language version, strict flag, locale (`Locale.ROOT`), time zone (UTC), feature flags (`ContextFactory.hasFeature` overrides), the instruction budget (enabled in all modes so codegen is consistent), and the thread stack size.

### 5.3 Per-mode execution steps

For each mode, on a dedicated worker thread created with an explicit large stack size (e.g. 64 MB, the same for all modes):

1. Enter a fresh `Context` from a harness `ContextFactory` subclass. Set the mode, language version, locale, time zone and `setInstructionObserverThreshold(10_000)`.
2. Create a **fresh scope** with `cx.initSafeStandardObjects()`, so there is no LiveConnect/`Packages` and no state shared between cases or modes.
3. Install host functions: `print` (captured to an output list), `__probe` (§6.3), `__isProxy` (Java `instanceof NativeProxy`), and the canonicaliser (§6.1).
4. Run `prelude.js` (determinism stubs, §8) in **interpreter mode**, so the prelude itself never varies between modes.
5. `Script s = cx.compileString(source, "case.js", 1, null)`. Compile-time `EvaluatorException` becomes status `syntax`. Any other exception during compile (`VerifyError`, `ClassFormatError`, `NullPointerException` in codegen, etc.) becomes status `crash`. A crash at compile time in compiled mode only is a high-value finding.
6. **Verify the execution method actually used** (§5.4) and record it as `compiledActually`.
7. `Object r = s.exec(cx, scope, scope)`. This yields the completion value of the script, which is the default result.
8. Drain pending Promise jobs (`cx.processMicrotasks()` or the equivalent in the pinned version) so `.then` callbacks run deterministically before results are collected.
9. Canonicalise the result and collect the trace, the `print` output and the outcome.
10. `ContextFactory.observeInstructionCount` throws a harness `Error` when the instruction budget or the wall-clock deadline is exceeded. A watchdog thread enforces a hard deadline beyond that; if it fires, the harness reports `timeout`, exits, and the client restarts it.

Sketch:

```java
final class HarnessFactory extends ContextFactory {
    @Override protected void observeInstructionCount(Context cx, int n) {
        Budget b = (Budget) cx.getThreadLocal(Budget.class);
        if (b.consume(n) || b.pastDeadline()) throw new BudgetExceeded();
    }
}

Outcome run(String src, Mode mode, Opts o) {
    try (Context cx = factory.enterContext()) {
        mode.configure(cx);                         // evaluation method / debugger
        cx.setLanguageVersion(o.languageVersion);
        cx.setLocale(Locale.ROOT);
        cx.setTimeZone(TimeZone.getTimeZone("UTC"));
        cx.setInstructionObserverThreshold(10_000);
        cx.putThreadLocal(Budget.class, new Budget(o.instructionBudget, o.wallMs));
        ScriptableObject scope = cx.initSafeStandardObjects();
        Host.install(cx, scope);                    // print, __probe, __isProxy, canon
        Script s;
        try { s = cx.compileString(src, "case.js", 1, null); }
        catch (EvaluatorException e) { return Outcome.syntax(e); }
        boolean compiled = Host.isCompiledScript(s);  // §5.4
        Object r = s.exec(cx, scope, scope);
        Host.drainJobs(cx);
        return Outcome.ok(Host.canon(r), Host.trace(), Host.out(), compiled);
    } catch (JavaScriptException e) { return Outcome.thrown(canon(e.getValue()), ...); }
      catch (EcmaError e)          { return Outcome.thrown(e.getName(), e.getErrorMessage(), ...); }
      catch (BudgetExceeded e)     { return Outcome.timeout(...); }
      catch (StackOverflowError e) { return Outcome.resource("stack", ...); }
      catch (EvaluatorException e) { return Outcome.thrown("InternalError", ...); } // e.g. too much recursion
      catch (RuntimeException | LinkageError e) { return Outcome.crash(e); }        // engine bug
}
```

Check exact API names against the pinned Rhino version (e.g. microtask draining and the `Script` code-type check).

### 5.4 Detecting the silent interpreter fallback

Because a class-size failure makes "compiled" quietly run interpreted, the harness must record what actually happened:

- After compiling, check the script's code type. Interpreted scripts carry `InterpreterData` (`Context` itself uses this check for continuation support); compiled scripts don't. Record this as `compiledActually`.
- In compiled mode, `compiledActually == false` produces status `fallback`. The case is **excluded from comparison** and counted in metrics. Generators should keep programs small enough that this is rare.
- Nested functions are compiled into the same class as their script, so the script-level check is sufficient for v1.

### 5.5 Outcome record

```json
{
  "mode": "compiled",
  "compiledActually": true,
  "status": "ok | throw | syntax | timeout | resource | fallback | crash",
  "value": "<canonical string>",
  "thrown": {"kind": "js-value | ecma-error", "name": "TypeError", "message": "...", "value": "<canonical>"},
  "trace": ["<canonical>", "..."],
  "output": ["..."],
  "engineException": {"class": "java.lang.VerifyError", "message": "...", "stack": "..."},
  "ms": 4
}
```

### 5.6 Exception classification

| Java-side event | Status | Notes |
|---|---|---|
| `EvaluatorException` during `compileString` | `syntax` | Should match across modes; a mismatch is a finding. |
| `JavaScriptException` (a JS `throw`) | `throw` | Canonicalise `getValue()`. |
| `EcmaError` (runtime TypeError, RangeError, …) | `throw` | Record `getName()`; message is a soft field. |
| `EvaluatorException` at runtime (e.g. interpreter stack depth) | `throw` or `resource` | Map "too much recursion" and similar to `resource`. |
| `StackOverflowError` | `resource` | Expected to differ between modes. |
| Budget exceeded / watchdog | `timeout` | Instruction counts differ by mode. |
| `ClassSizeException` fallback detected | `fallback` | Excluded from comparison. |
| Any other `RuntimeException`, `VerifyError`, `ClassFormatError`, `LinkageError`, `AssertionError` | `crash` | Engine bug; high value even if both modes crash. |
| `OutOfMemoryError` / JVM death | `crash` (via client) | JVM restarted. |

### 5.7 Throughput

Correctness comes first, but aim for **hundreds of cases per second per JVM** so Hypothesis runs are meaningful. If that's not achievable, run several harness JVMs in parallel with `pytest-xdist`, one JVM per worker. Do not trade isolation for speed.

### 5.8 Harness self-tests (must pass before trusting any diff)

1. **Self-consistency:** each corpus file run twice in the same mode gives identical outcomes.
2. **Isolation canaries:** a case that sets `globalThis.leak = 1` followed by a case that reads `leak` must see `ReferenceError`. The same check applies across modes.
3. **Fallback detection:** a generated script with a huge function body is flagged `fallback` in compiled mode.
4. **Known divergences:** the two test262 files in §2 are reported as mode mismatches (adapted to run without the test262 harness, or with `assert.js`/`sta.js` loaded).
5. **Injected bug:** a deliberate patch in a scratch Rhino build (e.g. flip a branch in `DoubleLinker`, or break an `Optimizer` number-type rule) is detected by the property test and shrunk to ≤10 statements.

## 6. Canonicalisation (the oracle)

### 6.1 Where it runs

The canonicaliser is a small JavaScript function in `canon.js`. It is **compiled once per scope in interpreter mode** and installed as a host function before user code runs, so it behaves identically regardless of the mode under test. It captures references to the builtins it needs at install time (`Reflect.ownKeys`, `Object.getOwnPropertyDescriptor`, `Object.getPrototypeOf`, `Object.is`, `String`, `Array.isArray`, etc.). User code that later monkey-patches `Object` or `Reflect` therefore can't affect it.

(A pure-Java walker over `ScriptableObject` is an acceptable alternative; the JS version is simpler to write and review.)

### 6.2 Rules

- **Primitives:** distinguish `-0` from `0` (`Object.is`), `NaN`, BigInt vs Number (`1n` vs `1`), strings with escapes. Numbers are printed exactly (e.g. with `toString` plus a sign marker for `-0`).
- **Objects:** own keys in `Reflect.ownKeys` order; for each key, the descriptor attributes. Data values are recursed into; for accessors, record only that a getter or setter exists. **Never invoke getters.** Record the prototype by well-known name (`Object.prototype`, `Array.prototype`, …) or by reference ID. Handle cycles with reference IDs. Depth and size caps produce a `…truncated` marker.
- **Arrays:** length plus elements, with holes marked distinctly from `undefined`.
- **Functions:** `typeof`, `name`, `length`. Also record `Function.prototype.toString` output: Rhino keeps source text in both modes, so a difference here is meaningful.
- **Proxies:** emit an opaque `[Proxy]` token via `__isProxy`; never trigger traps.
- **Symbols:** description; well-known symbols by name.
- **Errors (as values or thrown):** constructor name and own properties, **excluding** `stack`, `lineNumber`, `columnNumber` and `fileName`. `message` is compared as a **soft field**: differences are reported separately, not as failures. This can be made strict with a flag.
- **Java objects:** never expected in v1 (`initSafeStandardObjects`); if seen, emit `[Java <class>]`.

### 6.3 State-checksum trace

The generator inserts `__probe(k, expr)` calls after statements (or after every N statements), passing the variables in scope. `__probe` canonicalises its arguments immediately and appends them to the trace. This catches divergences that are later overwritten, as JIT-Picker does with its checksums. The trace is compared element by element, and the first differing index is reported. The probe is a Java host function, so calling it is the same in both modes.

Note that probes change the program. If a suspected bug disappears when probes are added, re-run the case with probes removed. The generator should be able to emit programs both with and without probes from the same draw.

## 7. Comparison rules

Compare `interpreted` and `compiled` (and `debugger` when enabled):

| Situation | Verdict |
|---|---|
| Either mode `fallback` | skip, count in metrics |
| Either mode `crash` | **hard finding** (also when both crash: an engine bug, though not a mode bug) |
| Statuses differ, among `ok` / `throw` / `syntax` | **hard finding** |
| Both `ok`: `value`, `trace` or `output` differ | **hard finding** |
| Both `throw`: thrown name/value differs | **hard finding** |
| Only error messages differ | soft finding (reported, not failed) |
| One mode `timeout` or `resource`, the other `ok` / `throw` | soft finding; re-run with a 10× budget before reporting |
| Both `timeout` / `resource` | discard (generator problem, counted in metrics) |

In Hypothesis, hard findings raise `AssertionError` with a readable diff (via `note()`), which triggers shrinking. Soft findings are logged to a side file for the UI.

## 8. Nondeterminism controls

`prelude.js`, run in interpreter mode before every case:

- `Math.random`: replaced with a seeded PRNG (the seed is part of the request).
- `Date.now()` and `new Date()` with no arguments: fixed epoch. Locale and time zone are fixed via `Context`.
- `WeakRef`, `FinalizationRegistry` (if present), and anything GC-timing-dependent: deleted from the global and excluded from generators.
- Promise jobs: drained explicitly at the end (§5.3 step 8).
- Validated by self-test §5.8-1.

## 9. Generator layer (Hypothesis)

### 9.1 ProgramBuilder

Mutable builder used inside `@st.composite` strategies:

- Emitted statements; **typed variable pool** `[(name, type)]` of variables in scope; scope nesting (function bodies, blocks, loops) so only visible variables are referenced.
- `fresh(type, expr)` emits `var vN = <expr>;` by default (`let`/`const` behind a toggle, given partial support) and registers the variable.
- `pick(draw, type=None)` chooses an in-scope variable by **drawing an integer modulo the pool size** (shrink-friendly, §9.7).
- `probe()` emits `__probe(k, v0, v1, …)` for the current pool.
- Records a `generatorTrace` (which generator emitted which lines) for the UI.
- Coarse types: `number`, `int32`, `bigint`, `string`, `boolean`, `undefined`, `null`, `symbol`, `array`, `object`, `function`, `generator`, `regexp`, `typedarray`, `map`, `set`, `promise`, `proxy`, `unknown`.

### 9.2 Language subset

Generate only what Rhino supports; anything else just inflates the `syntax`-in-both-modes rate.

- **Enabled by default:** `var`, functions (declarations, expressions, arrows, closures, default params, rest params), object/array literals including getters/setters, computed keys, shorthand and `super` in object methods, all operators including `**`, `typeof`/`instanceof`/`in`/`delete`/`void`, template literals, `if`/`switch`/`for`/`for-in`/`while`/`do-while`/labels/`break`/`continue`, `try/catch/finally`, `throw`, `Symbol`, `BigInt`, `Map`/`Set`/`WeakMap`, typed arrays, `RegExp`, `JSON`, `Proxy`/`Reflect`, `Promise` (with job draining).
- **Behind toggles (partial support):** `let`/`const` (block and loop bindings), generators, `for-of`, destructuring, spread.
- **Sloppy-only constructs:** `with` and direct `eval` are included when `strict=false`; they force activation and switch off the optimizer for the enclosing function, which is worth testing both ways.
- **Never:** `class`, `async`/`await`, async iteration, modules, `SharedArrayBuffer`/`Atomics`, Java interop (v1).

### 9.3 Code generators

Each generator is a function `(builder, draw) -> None` that emits one small snippet, chosen from a weighted table. The families come from Fuzzilli's `CodeGenerators.swift`, filtered to the subset above:

- **Values:** numbers (`0`, `-0`, `NaN`, `±Infinity`, `2**31-1`, `-(2**31)`, `2**32`, `2**53`, `2**53+1`, `0.1`, denormals, `Number.MAX_VALUE`), BigInts, strings (empty, unicode, surrogates, numeric-looking `"1"`, `"0x10"`, `" 1 "`), booleans, `null`/`undefined`, symbols, regexps.
- **Containers:** arrays (holes, sparse, very long `length`), objects (accessors, computed keys, `__proto__`, numeric keys), typed arrays, Map/Set.
- **Operations:** unary, binary, comparison, logical, compound assignment, `++`/`--` (prefix/postfix), comma, conditional.
- **Property access:** get/set/delete on named, indexed and computed keys; `Object.defineProperty`/`freeze`/`seal`; prototype mutation; `in`; `for-in` enumeration.
- **Calls:** function and method calls, `new`, `call`/`apply`/`bind`, `Reflect.*`, `arguments` usage.
- **Control flow:** bounded loops (iteration counts drawn from a small range), `switch` with fallthrough, labelled `break`/`continue`, `try/catch/finally` with `return`/`break`/`continue`/`throw` inside `finally`.
- **Functions:** nested functions, closures over loop variables, recursion with bounded depth, generators (behind a toggle).
- **Exotic objects:** `Proxy` with traps, `valueOf`/`toString`/`Symbol.toPrimitive` overrides, getters with side effects.

### 9.4 Compiler-targeted generators

These encode what Rhino's compiler does differently from the interpreter (§2), and should get extra weight:

- **Number-type inference:** functions with only numeric locals and no activation, with arithmetic, `++`, compound assignment, int32 boundaries and `|0` / `>>>0` / `~`. Then **perturb the type** of one local late in the function (assign a string, an object with `valueOf`, `undefined`, a BigInt).
- **Activation vs no activation:** generate the same function body twice, once plain and once with an `arguments` read, `eval`, or `with` inserted, which disables the optimizer for that function.
- **Direct calls:** top-level named functions called directly with matching, fewer and extra arguments; recursion; redefinition of the callee between calls.
- **Call-site relinking (`invokedynamic` linkers):** a loop that hits the same call or property site with one type (int, double, string, `ConsString` from concatenation, dense array, constant) and then a different one. Combine this with property shape changes: add or delete properties, change the prototype, turn a data property into an accessor.
- **Strings:** long concatenation chains (exercises `ConsString`), comparison and `+` with mixed numbers.
- **Completion values and `finally`:** `eval`/script completion values of `try/finally`, `switch` and loops with abrupt completions. The known compiled-only test262 failure is in exactly this area.
- **Generators (toggle):** `yield` inside `try/finally`, `return()`/`throw()` on suspended generators, `yield` in loops with labels.
- **`super` in object literals:** property access and compound updates through `super` (`super.x++`, `super[k] += …`). The known interpreted-only test262 failure is in this area.

### 9.5 Environment model

Derive the builtin model (constructors, methods, argument/return types) from **TypeScript `lib.es20xx.*.d.ts`**. Then **filter it with a Rhino capability probe**: a script run once per harness version that enumerates `Object.getOwnPropertyNames` of the global object, each constructor, and each prototype. This keeps generators calling only methods Rhino actually implements, and re-running the probe after a Rhino upgrade updates the model automatically.

### 9.6 Program templates and swarm testing

- **Templates** (cf. Fuzzilli `ProgramTemplates.swift`):
  - define a function, call it in a loop with one type, then once with another
  - mutate prototypes or properties between calls
  - wrap a snippet in strict vs sloppy mode, inside `with`, inside `eval` or `new Function` (which compile in the same mode), or inside a generator
  - deep closure nesting
- **Swarm testing:** each example draws a random non-empty subset of enabled generators (Groce et al., 2012).

### 9.7 Shrinking-friendly design rules

- Drive program length from a **single `st.lists(...)` draw** of generator invocations, so the shrinker can delete statements.
- Select variables by an integer draw **modulo the current pool size**, so deleting a statement doesn't break later references.
- Avoid `.filter`/`assume` with high rejection rates; generate constructively.
- Small by default: 3–20 top-level statements and bounded nesting. This also keeps compiled-mode fallback rare.

### 9.8 Seeds: generalising suspect expressions

- Convert each suspect expression into a **template with typed holes**, e.g. `?obj[?key] ?op= ?val`, filled from type-aware strategies.
- Pin the originals with `@example(...)`.
- Compose seeds with the templates in §9.6: keep what made the seed interesting and vary its surroundings (DIE-style aspect-preserving mutation).

### 9.9 Steering without built-in coverage

- `target()` on "statements executed before first exception" rewards programs that run deep.
- **JaCoCo coverage of Rhino** (the harness JVM runs with the JaCoCo agent): per-run reports of which classes and methods in `org.mozilla.javascript.optimizer`, `Interpreter` and `ScriptRuntime` were reached, broken down by generator in the UI. Optionally, reset and dump coverage per case through the JaCoCo runtime API, and `target()` on new branches in `optimizer.*`. This is the closest thing to Fuzzilli's coverage feedback without leaving Hypothesis.
- Track and minimise the `syntax`-in-both-modes rate and the early-throw rate.

### 9.10 The property test

```python
@settings(max_examples=2000, deadline=None,
          database=DirectoryBasedExampleDatabase(".hypothesis/difftest"))
@given(programs())
def test_interpreted_equals_compiled(prog):
    res = harness.run(prog.source, modes=("interpreted", "compiled", "debugger"))
    note(res.pretty())
    target(res.min_statements_executed(), label="depth")
    compare.assert_equivalent(res)   # raises on hard findings, logs soft ones
```

## 10. Seed and replay corpora

1. **test262** (as vendored by Rhino, using Rhino's `test262.properties` to skip unsupported features): replay ON vs OFF using **value-level comparison**, not just pass/fail. Rhino CI already checks pass/fail per mode; our extra signal is identical outcomes on tests that fail, or pass for different reasons.
2. **Rhino's own tests:** `tests/testsrc/jstests`, the legacy Mozilla suite in `tests/testsrc/tests`, `doctests`, and JS snippets embedded in Java tests under `tests/src/test`.
3. **Other engines' regression suites:** V8 `test/mjsunit`, SpiderMonkey `js/src/jit-test`, JavaScriptCore `JSTests/stress`. Strip or stub engine intrinsics (`%OptimizeFunctionOnNextCall`, `assertEq`, `noInline`, …) and filter out unsupported syntax.
4. Extract expressions and small functions from all of these as extra seed templates (§9.8).
5. Check licences before vendoring; prefer fetching at build time.

## 11. Triage and reporting

- Shrunk reproducers go to `tests/regressions/<signature>.js`, with all mode outcomes and the replay command.
- **Dedup signature:** `(status pair, first differing trace index kind, thrown name pair, top Rhino frame of engineException if any)`.
- **Mode triangulation:** compiled differs from both interpreted and debugger, so the compiler is suspect; interpreted and debugger differ from each other, so the debug path is suspect.
- **Optional reference engine:** run the reproducer in Node/V8 or QuickJS to suggest which mode is spec-correct.
- **Bisecting:** for a reproducer, `git bisect` over Rhino commits using the CLI replay mode as the test script.
- **Run summary:**
  - cases executed
  - outcome breakdown per mode
  - fallback rate
  - syntax-in-both rate
  - early-throw rate
  - hard and soft findings
  - unique signatures

## 12. Inspector UI (Next.js)

### 12.1 Purpose

A local web UI for **assessing the quality of generated programs** by eye. It lets you browse the generator catalogue, see what each generator and template actually produces, and spot problems quickly: programs that throw immediately, degenerate or repetitive output, generators that never fire, unsupported syntax, or compiled-mode fallbacks. It is a development tool for the person tuning the generators.

### 12.2 Data flow

The Python side writes **static JSON snapshots**; the Next.js app only reads them.

```
inspector/export.py ──▶ ui/data/<run-id>/
                          catalogue.json     # generators, templates, seeds
                          samples.jsonl      # sampled programs + per-mode outcomes
                          metrics.json       # aggregates per generator/template
                          findings.json      # hard + soft findings (optional)
                          coverage.json      # JaCoCo summary per generator (optional)
```

- `export.py` draws N samples per generator and per template using fixed seeds, runs them through the harness in all modes, and writes the files.
- A run is identified by timestamp + difftest git commit + Rhino commit.
- The UI reads `ui/data/` at request time (App Router, server components). No database.
- Optional later: a "regenerate" API route that shells out to `export.py` for one generator with a new seed.

### 12.3 Data schemas (minimum)

**Catalogue entry:**
```json
{
  "id": "gen_numeric_local_type_flip",
  "kind": "generator | template | seed",
  "family": "compiler-targeted",
  "weight": 5,
  "toggle": null,
  "inputTypes": ["number"],
  "outputType": "unknown",
  "description": "Numeric-only function locals, one local switched to a non-number late",
  "sourcePath": "generator/generators/compiler.py",
  "sourceSnippet": "def gen_numeric_local_type_flip(b, draw): ..."
}
```

**Sample:**
```json
{
  "id": "s-000123",
  "runId": "2026-10-18T18-00Z-abc123-rhino-b95f75d",
  "source": "var v0 = -0;\nfunction f(a) { ... }\n...",
  "hypothesisSeed": 12345,
  "template": "warmup_then_type_change",
  "enabledGenerators": ["gen_number", "gen_binop", "gen_numeric_local_type_flip"],
  "generatorTrace": [{"gen": "gen_number", "lines": [0, 0]}],
  "stats": {"statements": 9, "vars": 6, "maxDepth": 2, "bytes": 312, "probes": 4},
  "results": {
    "interpreted": { "status": "ok", "value": "...", "trace": ["..."], "compiledActually": false, "ms": 2 },
    "compiled":    { "status": "ok", "value": "...", "trace": ["..."], "compiledActually": true,  "ms": 5 },
    "debugger":    { "status": "ok", "value": "...", "trace": ["..."], "compiledActually": false, "ms": 3 }
  },
  "verdict": "equal | hard | soft | skipped"
}
```

**Metrics:** per generator/template:
- sample count and usage frequency
- syntax-in-both rate and early-throw rate
- mean statements executed before first throw
- exception-name distribution
- timeout, resource and fallback counts
- mean program size
- duplicate-program rate
- hard/soft finding counts

### 12.4 Pages

1. **Run overview (`/`)**
   - Run picker.
   - Headline numbers: programs, verdict breakdown, early-throw rate, syntax-in-both rate, fallback rate, timeouts and crashes.
   - Charts: program size histogram, exception-name breakdown, generator usage (highlighting generators that never or rarely fire).
2. **Generator catalogue (`/generators`)**
   - Sortable table: id, kind, family, toggle, weight, sample count, early-throw rate, syntax rate, findings.
   - Default sort puts problems first. Filters for family, kind, toggle and "has problems".
3. **Generator detail (`/generators/[id]`)**
   - Description, weight, Python source snippet, and coverage summary (which Rhino optimizer/interpreter classes its programs reached).
   - Grid of sample programs with **the lines this generator emitted highlighted**.
4. **Program browser (`/programs`)**
   - Filters: template, generator, verdict, per-mode status, fallback, exception name, size range, free-text search in source.
   - Compact previews with per-mode status badges.
5. **Program detail (`/programs/[id]`)**
   - Syntax-highlighted source (Shiki or Prism), coloured by originating generator, with a marker where execution first threw.
   - **Three-column mode comparison** (interpreted / compiled / debugger): status, canonical value, thrown, output, `compiledActually`, timing.
   - Diff view highlighting the first differing trace index.
   - Metadata: seed, template, swarm subset, stats.
   - **Copy reproducer** (source plus the `--replay` command).
   - Prev/next within the current filter, with j/k shortcuts.
6. **Findings (`/findings`)**
   - Deduplicated hard and soft findings with counts, the minimised reproducer and the triangulation hint (§11).
7. **Run comparison (`/compare?a=…&b=…`)** *(nice to have)*
   - Per-generator metric deltas between runs, to check whether a generator change helped.

### 12.5 Quality signals to surface

- Early-throw rate and syntax-in-both rate per generator. `SyntaxError` usually means the generator emitted something Rhino doesn't support; `ReferenceError` usually means a builder scope bug.
- Compiled-mode **fallback rate** (programs too large).
- Generators that never or rarely fire.
- Duplicate or near-duplicate programs.
- Size distribution.
- Timeouts and resource outcomes (unbounded loops or recursion slipping through).
- Optimizer coverage per generator (JaCoCo), showing whether compiler-targeted generators actually reach `optimizer.*`.

### 12.6 Implementation notes

- Next.js (App Router) + TypeScript; Tailwind or similar; a lightweight chart library (e.g. Recharts).
- Stream or paginate `samples.jsonl`, which can hold thousands of programs.
- Define the schema once (JSON Schema). Validate it on export in Python and generate TypeScript types from it.
- Local-only (`npm run dev` / `next start` on localhost); no auth.
- The UI must render sensibly with partial data (no findings yet, no coverage, or samples without results).

## 13. Phased plan / milestones

1. **Harness MVP (Java):**
   - stdio protocol, three modes, fresh scope per case
   - canonicaliser, prelude, exception classification, fallback detection, budget and watchdog
   - CLI replay
   - the self-tests in §5.8 items 1–3
2. **Validation:** reproduce the two known test262 mode divergences (§5.8-4). Add the injected-bug build and check that it is detected (§5.8-5, together with milestone 4).
3. **Replay corpora:** test262 (value-level), then Rhino's own tests, compared across modes; triage anything found.
4. **Generator core:**
   - ProgramBuilder with probes and generator trace
   - about 25 generators across values, operations, property access and calls, plus the first compiler-targeted generators (§9.4)
   - swarm selection and the first property test
5. **Inspector UI v1:** export format, generator catalogue, program browser and program detail, so generator quality can be assessed from milestone 4 onwards.
6. **Seeds:** suspect expressions as typed templates, `@example` pins and templates.
7. **Breadth:**
   - environment from `lib.d.ts` plus the capability probe
   - the remaining generator families and toggles (let/const, generators, destructuring)
   - JaCoCo coverage, and `target()` steering on it
8. **Ops:** dedup, run summaries, regression folder, CI/nightly job, findings and comparison pages in the UI.
9. **v2 candidates:** Java interop generators (LiveConnect), a Fuzzilli generation-only replay, porting to the internal engine.

## 14. Acceptance criteria

- Same-mode self-consistency: every corpus file produces identical outcomes when run twice in the same mode (no harness noise).
- Isolation canaries pass; a compiled-mode fallback is detected and excluded.
- Both known test262 divergences (§2) are reported as hard findings with the correct mode attribution.
- An injected compiler bug is detected and shrunk to a reproducer of ≤10 statements.
- On generated programs, the syntax-in-both rate is below 5% and the early-throw rate below 30%. Both are reported per generator.
- The inspector UI:
  - lists every generator, template and seed
  - shows at least 20 sampled programs per generator with all three mode outcomes
  - surfaces the worst generators (by early-throw rate) in one click

## 15. Open questions (ask before building)

1. Which Rhino commit/version to pin, and which Java version to run on?
2. Language version for v1: `VERSION_ES6` only, or also `VERSION_ECMASCRIPT`?
3. Is the `debugger` mode wanted in every run, or only for triage?
4. The list of suspect seed expressions.
5. Should Java interop (LiveConnect) be in v1, given its relevance to real embedders?
6. Compute budget and where it runs (local, CI, nightly)?

## 16. References

- Rhino: `README.md`, `RELEASE-NOTES.md`, `Context.java` (`EvaluationMethod`, instruction observer, `ClassSizeException` fallback), `optimizer/` package, `testutils/.../Utils.java`, `tests/.../Test262SuiteTest.java`, `tests/testsrc/test262.properties`; compatibility table at mozilla.github.io/rhino/compat/engines.html.
- Hypothesis documentation: strategies, `@composite`, `target()`, example database.
- Fuzzilli (Google Project Zero): `CodeGenerators.swift`, `ProgramTemplates.swift`, `JavaScriptEnvironment.swift`, `Docs/HowFuzzilliWorks.md`.
- Bernhard et al., *JIT-Picking: Differential Fuzzing of JavaScript Engines*, ACM CCS 2022: interpreter-vs-JIT oracle, state checksums.
- Park et al., *Fuzzing JavaScript Engines with Aspect-preserving Mutation* (DIE), IEEE S&P 2020.
- Groce et al., *Swarm Testing*, ISSTA 2012.
- Le et al., *Compiler Validation via Equivalence Modulo Inputs* (EMI), PLDI 2014.
- Yang et al., *Finding and Understanding Bugs in C Compilers* (Csmith), PLDI 2011.
- Mozilla jsfunfuzz `compare_jit` mode (archived): prior art for JIT on/off comparison.
- hypothesmith: grammar-driven source generation with Hypothesis.
- JaCoCo runtime API: per-case coverage reset and dump.
