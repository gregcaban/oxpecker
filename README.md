<p align="center"><img src="docs/oxpecker.jpg" width="480" alt="Red-billed oxpeckers on a white rhino, Kruger National Park"></p>
<p align="center"><sub>Photo: Joel Herzog, Kruger Park, South Africa (<a href="https://commons.wikimedia.org/wiki/File:Kruger_Park,_South_Africa_(Unsplash_1xetOxStk_Y).jpg">Wikimedia Commons</a>, CC0)</sub></p>

# oxpecker

Differential testing of Mozilla Rhino: every generated JavaScript program is run in Rhino's
**interpreted** mode and its **compiled** (JVM bytecode) mode, the results are canonicalised,
and any difference is a bug candidate. Design: [`docs/design.md`](docs/design.md) (the original
brief is `docs/brief-2026-10-18-original.md`).

```
Python (Hypothesis)                        Java harness (one JVM, Rhino embedded)
  oxpecker/gen/      programs as JS text     harness-java/   fresh Context+scope per case & mode,
  oxpecker/harness/  client + comparator ──▶                 canonicaliser, probes, budget, watchdog
  tests/             property test, self-tests, regressions, test262 seeds
```

## Setup

```bash
# 1. Rhino at the pinned commit (JDK 21 to build): publishes org.mozilla:rhino:2.0.0-SNAPSHOT to ~/.m2
(cd ../rhino && git checkout b95f75d && JAVA_HOME=/usr/local/opt/openjdk@21 ./gradlew :rhino:publishToMavenLocal -x test)
# 2. Harness jar (the Rhino version is `rhinoVersion` in harness-java/gradle.properties)
(cd harness-java && ./gradlew fatJar)
# 3. Python
python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev]'
source .venv/bin/activate
```

## Try it

```bash
oxpecker list                                     # generators (82) and templates, with toggles/weights
oxpecker gen -n 50 -o runs/first --seed 1         # write case-0001.js … for reading
oxpecker gen -n 50 -o runs/first --seed 1 --run   # … and run them in both modes, print findings
oxpecker run runs/first tests/regressions -v      # run existing .js files
oxpecker diff runs/first/case-0007.js             # one file: per-field first difference between modes
oxpecker corpus test262 --filter language/statements/try   # replay test262 (value-level comparison)
oxpecker corpus test262 --known-divergences       # files Rhino's test262.properties tags per mode
oxpecker probe                                    # refresh the Rhino capability table (API generators)
oxpecker run runs/first --coverage                # JaCoCo over Rhino: which optimizer/interpreter code was reached
oxpecker fuzz --corpus runs/corpus --time 600     # coverage-guided corpus fuzzing; resumes corpus + coverage frontier
oxpecker export -o ui/data --per-generator 20     # sample every generator/template for the inspector UI (+ --coverage)
bin/harness --replay runs/first/case-0007.js      # one file, full per-mode detail
bin/harness --replay x.js --mode compiled --language-version 250 --budget 0   # no instruction observer
```

Per case: `case-NNNN.js` (with `__probe` state snapshots and `// [generator]` markers),
`case-NNNN.plain.js` (the reproducer form), `case-NNNN.meta.json` (template, generators, line spans,
stats) and after `--run` `case-NNNN.result.json` (both modes' outcomes + verdict).

Generation knobs: `--toggle let|generators|for-of|destructuring|spread|continuations` (partial-support features),
`--generator NAME` / `--template NAME` (repeatable allowlists), `--max-statements`, `--max-depth`,
`--probe-every N`, `--no-probes`, `--no-comments`, `--no-swarm`, `--strict`.
Run knobs: `--language-version 200|250`, `--wall-ms`, `--strict-java-types`.

## Tests

```bash
pytest tests/test_generator.py              # no JVM: determinism, every generator fires, node --check
pytest tests/test_harness.py                # harness self-tests: isolation, fallback, budget, known findings
pytest tests/test_differential.py           # the property test; OXPECKER_EXAMPLES=2000, OXPECKER_TOGGLES=let,generators
OXPECKER_COVERAGE=1 pytest tests/test_differential.py   # ... steering on newly covered Rhino probes (JaCoCo)
```

Hard findings from the property test are shrunk by Hypothesis and written to `tests/findings/<sig>.js`;
confirmed ones move to `tests/regressions/` where `test_harness.py` checks they stay detected.

`oxpecker fuzz` is the second search strategy: a Fuzzilli/AFL-style loop that keeps a corpus of
programs (as choice sequences) which each reached Rhino code nothing before them had, mutates a
uniformly chosen corpus entry (or draws fresh with `--p-new`), and adds the result when it covers
new probes. Findings with an unknown signature are shrunk by delta-debugging the choice sequence and
written to `tests/findings/`. The corpus and the JaCoCo coverage frontier persist in `--corpus`, so
the next run continues where the last stopped. Every mutated program is valid by construction
because mutation happens on the generator's choices, not on JS text.

## Verdicts

| situation | verdict |
|---|---|
| compiled mode silently fell back to the interpreter (class-size limit) | `skip` |
| either mode crashed (VerifyError, NPE in codegen, OOM…) | **hard** |
| status differs (ok / throw / syntax), or value / trace / output differ | **hard** |
| thrown name or value differs | **hard** |
| one mode timeout/resource, the other ok | soft |
| only error messages differ | soft |
| only the **Java representation** differs (`Integer` vs `Double`, `ConsString` vs `String`) | soft, or hard with `--strict-java-types` |
| both timeout/resource | discard |
| a mode reports `unsupported(continuations)` (Rhino refuses continuation capture outside the interpreter) | **hard**, own signature |

## Findings so far (pinned Rhino b95f75d)

Checked in under `tests/regressions/` (each must stay detected; `test_harness.py` enforces it):

- `direct-recursive-call-completion.js` — direct recursive call with an `n === 0` base case returns a wrong value in compiled mode.
- `compiled-negative-zero-lost-in-int-arith.js` — `var n = -1; n * 0` is `-0` interpreted, `0` compiled (int-typed top-level variable).
- `java-int-literal-double-vs-integer.js` — the same number reaches Java as `Double` (interpreter) vs `Integer` (compiler).
- `spread-in-call-crashes-both-modes.js` — `f(...a)` throws a raw `RuntimeException` from both evaluators instead of a SyntaxError.
- `callsite-tostring-npe-both-modes.js` — `String(new CallSite())` is a `NullPointerException` in both modes.
- `compiled-increment-int32-overflow.js` — `++`/`--` on top-level vars, activation locals, properties and elements wrap at int32 in compiled mode.
- `arrow-function-has-prototype-compiled.js` — arrow functions get a `prototype` property in compiled mode.
- `interpreted-computed-super-update.js` — `++super[k]` returns the key and `super[k]--` creates an own `"0"` property in the interpreter.
- `interpreted-super-increment-string-concat.js` — `super.x++` on a string concatenates (`"a1"`) in the interpreter.
- `compiled-proxy-extra-has-trap-on-get.js` — `p.x` through a Proxy fires `has` before `get` in compiled mode.
- `array-find-no-args-aioobe-both-modes.js` — `Array.find()` throws a raw `ArrayIndexOutOfBoundsException` past JS try/catch.
- From test262 (`oxpecker corpus test262`), `t262-*.js`, 14 root causes. Mode divergences: `apply` with a rest parameter on an array-like builds wrong arguments in the interpreter; generator-method `prototype` present interpreted / absent compiled; object-rest destructuring from a Proxy with a Symbol key (interpreter throws, compiler skips the trap); `Number(2n**1024n)` through a local throws only compiled; nested finally/throw inside a generator lets the throw escape only compiled; re-entrant `generator.next()` hits `FAILED ASSERTION` interpreted only; `yield` in a default parameter → `ClassFileFormatException`, `yield` from a catch inside for-of → `bad local variable type`, and `obj[p] &&= v` → `VerifyError`, all compiled only. Both-mode engine crashes: Proxy `getOwnPropertyDescriptor` returning undefined (NPE), `for (const [[x] = [1]] = []; …)` (NPE), `new Int8Array({length: 2**53})`, `BigInt.asIntN(2**32-1, 1n)`, string-iterator prototype confusion.
- From Rhino's legacy Mozilla suite (`oxpecker corpus rhino-mozilla`): `compiled-yield-call-arg-verifyerror.js` (`z((yield 3))` → `VerifyError` in compiled mode), `compiled-relop-arg-stack-underflow.js` (`a(1 < 2)` from inside a function → `Stack underflow` in compiled mode), `nested-array-comprehension-bad-node-195.js` and `let-genexpr-switch-case-null-tokenstream.js` (JS1.7/1.8 comprehension forms crash both code generators), `string-doubling-overflow-negative-array-size.js` (2^31-char string).
- `tests/seeds/` — the two test262 cases Rhino's own `test262.properties` tags as failing in one mode only, reproduced standalone.

Java types are recorded for every number/string leaf the probes and the completion value see,
because Rhino embedders observe them at the Java boundary. Where the raw representation is
visible to Java, and where it is not:

- **Host `Callable`s** (`__probe`, any `LambdaFunction`/`FunctionObject` an embedder installs),
  the **script completion value**, `Function.call` return values and **properties read from JS
  objects** (`ScriptableObject.getProperty`) all hand Java the raw object: `Integer` from
  compiled code, `Double` from the interpreter. This is what the `java-types` verdict catches.
- **LiveConnect method calls** (`J.echo(x)`, any `NativeJavaMethod`) coerce arguments first:
  every JS number becomes `Double` for an `Object` parameter (`NativeJavaObject.coerceTypeImpl`),
  or `Long` when `FEATURE_INTEGER_WITHOUT_DECIMAL_PLACE` is on. Differences there are
  conversion/overload bugs rather than representation leaks; `JavaProbe` traces both.

## Layout

- `harness-java/` — Gradle project. `Harness` (JSON-lines server + `--replay`), `ModeRunner`
  (per-mode execution on a 64 MB-stack thread with watchdog), `Host` (print/`__probe`/canon),
  `RhinoCompat` (every version-sensitive Rhino call, reflective), `canon.js`, `prelude.js`.
- `oxpecker/gen/` — `builder.py` (ProgramBuilder: typed variable pool, scopes, probes, generator
  spans), `registry.py`, `generators/*.py` (values, containers, operations, properties, calls,
  control, compiler-targeted, exotic, toggled), `templates.py`, `strategies.py`.
- `oxpecker/harness/` — `client.py` (JVM lifecycle, restarts), `compare.py`.
- `bin/harness` — launcher using JDK 21 (`OXPECKER_JAVA` overrides).
