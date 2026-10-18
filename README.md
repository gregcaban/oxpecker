# oxpecker

Differential testing of Mozilla Rhino: every generated JavaScript program is run in Rhino's
**interpreted** mode and its **compiled** (JVM bytecode) mode, the results are canonicalised,
and any difference is a bug candidate. Design: `~/Downloads/differential-js-fuzzing-brief.md`.

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
bin/harness --replay runs/first/case-0007.js      # one file, full per-mode detail
bin/harness --replay x.js --mode compiled --language-version 250 --budget 0   # no instruction observer
```

Per case: `case-NNNN.js` (with `__probe` state snapshots and `// [generator]` markers),
`case-NNNN.plain.js` (the reproducer form), `case-NNNN.meta.json` (template, generators, line spans,
stats) and after `--run` `case-NNNN.result.json` (both modes' outcomes + verdict).

Generation knobs: `--toggle let|generators|for-of|destructuring|spread` (partial-support features),
`--generator NAME` / `--template NAME` (repeatable allowlists), `--max-statements`, `--max-depth`,
`--probe-every N`, `--no-probes`, `--no-comments`, `--no-swarm`, `--strict`.
Run knobs: `--language-version 200|250`, `--wall-ms`, `--strict-java-types`.

## Tests

```bash
pytest tests/test_generator.py              # no JVM: determinism, every generator fires, node --check
pytest tests/test_harness.py                # harness self-tests: isolation, fallback, budget, known findings
pytest tests/test_differential.py           # the property test; OXPECKER_EXAMPLES=2000, OXPECKER_TOGGLES=let,generators
```

Hard findings from the property test are shrunk by Hypothesis and written to `tests/findings/<sig>.js`;
confirmed ones move to `tests/regressions/` where `test_harness.py` checks they stay detected.

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

Java types are recorded for every number/string leaf the probes and the completion value see,
because Rhino embedders observe them at the Java boundary.

## Layout

- `harness-java/` — Gradle project. `Harness` (JSON-lines server + `--replay`), `ModeRunner`
  (per-mode execution on a 64 MB-stack thread with watchdog), `Host` (print/`__probe`/canon),
  `RhinoCompat` (every version-sensitive Rhino call, reflective), `canon.js`, `prelude.js`.
- `oxpecker/gen/` — `builder.py` (ProgramBuilder: typed variable pool, scopes, probes, generator
  spans), `registry.py`, `generators/*.py` (values, containers, operations, properties, calls,
  control, compiler-targeted, exotic, toggled), `templates.py`, `strategies.py`.
- `oxpecker/harness/` — `client.py` (JVM lifecycle, restarts), `compare.py`.
- `bin/harness` — launcher using JDK 21 (`OXPECKER_JAVA` overrides).
