# Findings vs. what Rhino already knows

Cross-check of oxpecker's findings (`tests/regressions/`, 30 files at the time of the cross-check; 3 more from the 2026-10-19 fuzz re-run in §6) against what upstream Rhino
already records about **interpreted vs. compiled divergences**: GitHub issues/PRs, the test262
expectation file, and the per-mode Mozilla-suite lists.

Date: 2026-10-19. Rhino pinned at `b95f75d` (= `origin/master` on that date, no newer commits).
GitHub data: every issue and PR of `mozilla/rhino` up to #2519 (2026-10-15), 2,385 items with bodies.
Nothing has been filed upstream yet — this is the baseline for doing so.

## 1. Method

1. **GitHub.** All issues + PRs (state=all, bodies) downloaded with `gh api --paginate`, grepped
   locally for mode vocabulary (`interpreted`, `interpreter`, `compiled mode`, `optimization level`,
   `opt -1`, `optLevel`, `VerifyError`, `ClassFileFormatException`, `Stack underflow`,
   `bad local variable type`, `Codegen`, `BodyCodegen`, `Optimizer`, …) plus 26 `gh search`
   queries; candidates were read to confirm the behaviour really differs by mode. Then 2–4
   targeted keyword queries per finding. Comment threads were not all read in full; fix-state comes
   from titles, "fixes #N" references and timeline cross-references.
2. **test262.** `tests/testsrc/test262.properties` and its runner
   `tests/src/test/java/org/mozilla/javascript/tests/Test262SuiteTest.java` in the pinned checkout.
3. **Mozilla suite.** `tests/src/test/resources/interpreted.tests` / `compiled.tests` and
   `MozillaSuiteTest.java`.
4. **Source.** grep of `rhino/src/main` for comments about interpreter/compiled differences.

## 2. Baseline: what Rhino records

### 2.1 test262 expectation file

Format (`Test262SuiteTest`, `LINE_SPLITTER` ≈ l.240, `TestMode` enum ≈ l.852, `getResult` ≈ l.958):

```
<path>[ <failure-info>][ comment]
```

- A listed `.js` is expected to **fail**; an unlisted one is expected to **pass** (an unexpected
  pass is itself a test failure).
- failure-info: none (fails in all modes and strictnesses) · `strict` / `non-strict` (that
  strictness, both modes) · `{mode,…}` or a bare mode token, modes being `interpreted`,
  `compiled`, `debugger` (interpreter + no-op debugger), optionally suffixed `-strict` /
  `-non-strict`; the listed modes are the failing ones · `{unsupported: [feature,…]}`.
- Top-level directory lines carry stats (`built-ins/Array 231/3081 (7.5%)`); `~` excludes a
  folder. **Only top-level folders listed in the file are run** (`addTestFiles` walks only those).
  The file lists `annexB`, `built-ins`, `harness`, `intl402`, `language` — **`staging/` is never
  run by Rhino**, so the SpiderMonkey-derived `staging/sm` tests are invisible to upstream.
- `#`/`!` comments are lost when the file is regenerated (`tests/README.md`).

Entries marked failing in **one mode only** — exactly two in ~6,000 lines:

| test | fails in | feature |
|---|---|---|
| `language/expressions/super/prop-expr-getsuperbase-before-topropertykey-putvalue-increment.js` (l.4730) | `{interpreted,debugger}` | `++super[key]`, GetSuperBase must precede ToPropertyKey |
| `language/statements/try/completion-values-fn-finally-abrupt.js` (l.5919) | `compiled` | abrupt completion from `finally` overriding try/catch completion |

Both are reproduced standalone in `tests/seeds/`.

### 2.2 Mozilla legacy suite

`MozillaSuiteTest.java:89` picks `interpreted.tests` or `compiled.tests`; both are **allow-lists**
(tests expected to pass in that mode). The class javadoc still talks about opt -1/0/9 (stale).

Passes compiled only (i.e. fails interpreted), 11:
`bigo-test.js` (not present in testsrc), `e4x/GC/regress-280844-2.js`, `e4x/Regress/regress-308111.js`,
`e4x/Regress/regress-354998.js`, `ecma/Date/15.9.5.10-2.js`, `js1_5/GC/regress-383269-01.js`,
`js1_5/GC/regress-383269-02.js`, `js1_5/Regress/regress-103602.js`,
`js1_5/extensions/regress-354541-02.js`, `js1_5/extensions/regress-354541-04.js`,
`js1_7/extensions/regress-458679.js`.

Passes interpreted only (i.e. fails compiled), 7:
`ecma/Date/15.9.5.10-1.js`, `js1_5/Exceptions/regress-257751.js`, `js1_5/Regress/regress-167328.js`,
`js1_5/Regress/regress-89443.js`, `js1_5/extensions/regress-226507.js`,
`js1_5/extensions/regress-50447.js`, `js1_8/regress/regress-465460-01.js`.

Most look like stack-depth, GC, timing or `fileName`/`lineNumber` artefacts rather than semantic
divergences. None overlaps an oxpecker finding.

### 2.3 Source comments

No TODO/FIXME about mode divergence. Relevant notes only: `CodeGenUtils.java:142` (interpreted
functions may have duplicate local names), `serialize/ScriptableOutputStream.java:24` (only
interpreted functions serialise), `optimizer/Codegen.java:380` (commented-out `Kit.codeBug`).
No `@Disabled`/mode-only Java tests tied to a mode bug.

### 2.4 GitHub issues and PRs

There is **no label** for this category (closest: `compilation`, `Java Exception Leaking`,
`Ecma Incompatibility`, `test262`, `Continuations`, `Regression`) and **no umbrella issue**.
Mode bugs are filed and fixed one at a time.

**Open, mode-relevant:**

| # | title | direction | notes |
|---|---|---|---|
| [#2407](https://github.com/mozilla/rhino/issues/2407) | Numeric comparison in JavaScript returns an incorrect result | compiled only | `a === 0` false inside a function that later directly calls another with `a`; fine with interpreter or `0 === a`. gbrail: very old (1.7.7.2), tied to the formerly optional "direct call" optimisations. aardvark179: the single-step IR→bytecode/icode design pushes the two back ends through different hoops; also notes Double vs Integer differs by mode. No fix PR. |
| [#1904](https://github.com/mozilla/rhino/issues/1904) | Compiled functions and prototypes | compiled only | compiled `function*` objects handle `prototype` differently. |
| [#350](https://github.com/mozilla/rhino/issues/350) | Double/Integer in javascript body | Java-visible | interpreter → `Double`, compiled → `Integer`. Siblings #1202, #1203 closed as docs. |
| [#410](https://github.com/mozilla/rhino/issues/410) / [#110](https://github.com/mozilla/rhino/issues/110) | NPE in BodyCodegen / CodeGenerator for `[a.b.c] = x` | both crash, different back ends | |
| [#1658](https://github.com/mozilla/rhino/issues/1658) | Rationalize use of optimization levels | policy | what each opt level did; "direct call" at level ≥1. |
| [#1933](https://github.com/mozilla/rhino/issues/1933) | Interpreter stack traces | interpreted | not read in detail. |
| [#2416](https://github.com/mozilla/rhino/issues/2416) / PR [#2426](https://github.com/mozilla/rhino/pull/2426) | Interpreter V2 (instruction-and-operand) | — | landing now (prep PRs #2424–#2510 merged). **Expect new interpreter-only divergences.** |

**Closed / fixed, for context (the shape of bugs upstream accepts):**

| # | gist | direction |
|---|---|---|
| #1412 (fixed by #1566) | generator args wrong in compiled mode | compiled |
| #1876 (fixed by #1912), PR #2114 | argument evaluated before callee ReferenceError; evaluation-order fixes | compiled |
| PR #1940 | strict flag not propagated to nested functions at compile time | compiled |
| #1074 (fixed by #1550/#1553) | `BodyCodegen.visitStandardCall` NPE on a test262 for-of dstr test | compiled |
| #915, #407, #539 (→ #567), PR #831 | VerifyError / bad merge / StackMapTable from codegen (try/finally, generators) | compiled |
| #645 | NPE in `BodyCodegen.visitObjectLiteral` for `for(({});;){}` | compiled |
| #1922 | FAILED ASSERTION for `for(var {};;)debugger` | interpreted |
| #2006, #2016, #2035 (→ #2008, #2017, #2038) | AIOOBE in `Interpreter.getArgsArray`/`initializeArgs` after the CallFrame refactor (apply/call/bind) | interpreted |
| #2044 (→ #2047) | wrong stack line in interpreted mode | interpreted |
| PR #2128 | ≥5 BigInt literals broke interpreter icodes | interpreted |
| PR #2398 | >255 literals overflowed a uint8 in CodeGenerator | interpreted |
| PRs #2392, #2241, #2488 | ClassCompiler register count, illegal method names, line numbers | compiled |
| #1444 (→ #1510, #1578) | continuations through bind/call/apply | interpreted (by design) |
| PRs #1754, #1942, #1987, #1317, #1401, #1769, #1772 | collapse opt levels to interpreted/compiled; `runWithAllModes()` in tests | — |

Policy reading: parity between modes is a testing goal (every test262 and unit test runs in both
modes since #1754/#1942), but there is no tracking of mode divergence as a category, and the
expectation file has only two mode-tagged entries.

## 3. Cross-check, finding by finding

Legend for **upstream**: *filed* = an issue describes this bug · *related* = same area/family, not
this bug · *t262: all modes* = originating test262 file is listed as failing with no mode marker,
so the mode difference is not recorded · *t262: not listed* = Rhino believes it passes /
never runs it · *none* = nothing found.

### 3.1 Divergences between modes

| finding | direction | upstream | notes |
|---|---|---|---|
| `direct-recursive-call-completion.js` | compiled wrong value | **filed: #2407 (open)** | Same root cause (direct-call optimisation + `=== 0`). Ours is smaller and adds: `==`, `<=`, `< 1` agree; non-direct calls (alias, function expression) agree. Candidate for a comment on #2407. |
| `compiled-increment-int32-overflow.js` | compiled wraps at int32 | none | Likely the integer-arithmetic path of PR #1563 ("Aggressively try to use Integers"); same family as #2407. |
| `compiled-negative-zero-lost-in-int-arith.js` | compiled loses -0 | none | #791 (printing -0) and PR #2283 (JSON.parse -0) are unrelated. Same int-typing family. |
| `arrow-function-has-prototype-compiled.js` | compiled adds `prototype` | related: PR #2031 (fixed the same thing for methods), #1904 | New for arrows. |
| `t262-generator-method-prototype-chain.js` | interp wrong chain / compiled missing | related: #1904; t262: all modes (l.4518) | Mode difference not recorded upstream. |
| `interpreted-computed-super-update.js` | interpreter returns key, writes own `"0"` | related: one-mode t262 entry (l.4730), PR #1738/#1735 | Our repro shows the actual symptom; the t262 entry only tests ordering. |
| `interpreted-super-increment-string-concat.js` | interpreter concatenates | related: as above | Not filed. |
| `compiled-proxy-extra-has-trap-on-get.js` | compiled calls `has` before `get` | none | Proxy PRs #2419, #2425, #2223, #2468 don't mention it. |
| `t262-apply-rest-array-like-wrong.js` | interpreter builds wrong rest args | related: #2006/#2016/#2035 family; t262 source (`apply/resizable-buffer.js`) listed all modes | New instance of the interpreter args-array family. |
| `t262-rest-destructure-proxy-symbol-gopd.js` | interpreter throws / compiled skips trap | t262: all modes (l.4347) | Both wrong, differently; not recorded. |
| `t262-bigint-to-number-overflow-throws.js` | compiled throws | none; t262: not run (staging) | |
| `t262-generator-nested-finally-throw-escapes.js` | compiled lets throw escape | none; t262: not run (staging) | Same area as the compiled-only `completion-values-fn-finally-abrupt.js` entry; worth probing together. |
| `java-int-literal-double-vs-integer.js` | Java sees Double vs Integer | related: #350 (open), #1202/#1203 (closed as docs) | Upstream treats it as by design; our value is the precise map of where it leaks (README). |

### 3.2 Crashes in one mode only

| finding | crash | upstream | notes |
|---|---|---|---|
| `compiled-relop-arg-stack-underflow.js` | `IllegalStateException: Stack underflow` | none (area: #407, #915) | Source test `js1_8_1/trace/trace-test.js`. |
| `compiled-yield-call-arg-verifyerror.js` | `VerifyError` | none (area: #539/#567, #915) | JS 1.7 generator syntax. |
| `t262-compound-assign-element-verifyerror.js` | `VerifyError` for `obj[p] &&= v` | none; t262: not run (staging) | Feature from PRs #1597/#1705. |
| `t262-yield-in-default-param-classfile-crash.js` | `ClassFileFormatException` | t262: all modes (l.3311) / `non-strict` (l.4513) | Not recorded as compiled-only; PR #2176 (unmerged) was nearby. |
| `t262-yield-in-forof-catch-verifyerror.js` | `bad local variable type` | **t262: not listed** — `for-of/yield-from-catch.js`, `yield-star-from-catch.js` are expected to pass | **Gap in Rhino's own run.** The crash depends on local-slot numbering (preceding top-level declarations), so Rhino's harness preamble probably hides it. Unverified; check by running Rhino's `Test262SuiteTest` on those two files. |
| `t262-generator-resume-from-executing-crash.js` | interpreter `FAILED ASSERTION` | t262: all modes (l.540–542) | Not recorded as interp-only crash; #2099 nearby. |

### 3.3 Engine crashes in both modes (raw Java exception escapes)

| finding | crash | upstream |
|---|---|---|
| `spread-in-call-crashes-both-modes.js` | `RuntimeException` (bad node) | none; PR #2098 implemented array spread only |
| `callsite-tostring-npe-both-modes.js` | NPE | related: #212 (other CallSite methods), PR #2204 |
| `array-find-no-args-aioobe-both-modes.js` | AIOOBE | related: #213 (Array generics) |
| `string-doubling-overflow-negative-array-size.js` | `NegativeArraySizeException` | none; source `js1_5/Regress/regress-3649-n.js` is in neither allow-list |
| `nested-array-comprehension-bad-node-195.js` | `RuntimeException: 195` | none; JS 1.7 syntax, see #1056 (non-Ecma removal) |
| `let-genexpr-switch-case-null-tokenstream.js` | NPE in parser | none; same #1056 caveat |
| `t262-bigint-asintn-large-bits-crash.js` | `ArithmeticException` | related: #1573/PR #1979 (other asIntN bug); not run (staging) |
| `t262-string-iterator-prototype-confusion-npe.js` | NPE | none; not run (staging) |
| `t262-const-nested-array-dstr-npe.js` | NPE | t262: all modes (l.5136/5137/5139) |
| `t262-proxy-gopd-undefined-npe.js` | NPE | t262: all modes (l.1093/1591/1596) |
| `t262-typedarray-excessive-length-crash.js` | `IllegalArgumentException` | t262: all modes (l.2333/2366) |

### 3.4 Totals

- Already filed: **1** (#2407).
- Related area, not this bug: 7 (#350 family, #1904, PR #2031, PR #1738 ×2, #212, #213).
- Known to Rhino only as a test262 failure, mode difference not recorded: 4.
- Known only as a test262 failure, both modes, consistent with ours: 3.
- **New**: 15+, including 8 mode divergences / compiled-only crashes with no trace upstream
  (int32 increment, -0, proxy `has`, relop stack underflow, yield-arg VerifyError,
  BigInt→Number, `&&=` element VerifyError, generator nested finally) and the
  `yield-from-catch` gap in Rhino's own test262 run.
- 5 of the 14 test262 findings come from `staging/`, which Rhino never runs.

## 4. Unsorted fuzz findings (`tests/findings/`, 2026-10-18)

Not yet reduced. Guesses from signatures, **not verified by replay**:

| file | signature | likely |
|---|---|---|
| `hard-1763154a61.js` | `[-0,-1,-2]` vs `[0,-1,-2]` | the -0 bug; escapes suppression because the array-shaped signature differs from `hard:value -0 vs 0` |
| `hard-3bf16ac33e.js` | `2147549183` vs `-2147418113` | int32 wrap (increment family) |
| `hard-ffd5f53797.js` | own `"-1":"p"` vs `"p":-1` | interpreter computed-super update writing the key as value |
| `hard-436c787fa0.js` | `4294967297` vs `4294967296`, both timed out | unclear; needs reduction |

## 5. Plan for upstream (not done)

1. Comment on #2407 with `direct-recursive-call-completion.js` and its characterisation.
2. File the eight mode divergences / compiled-only crashes from §3.4 individually (the project
   files one bug per issue; label `compilation` for codegen crashes, `Java Exception Leaking` for
   raw Java exceptions).
3. Report the `for-of/yield-from-catch.js` gap after confirming with Rhino's own runner.
4. Ask whether `staging/` should be added to `test262.properties`.
5. Re-run against Interpreter V2 once #2426 lands; it is the most likely source of new
   interpreter-only divergences.

Raw data used for this document (not checked in): the issue dump in the session scratchpad
(`gh api --paginate repos/mozilla/rhino/issues?state=all`); regenerate with that command.

## 6. Fuzz re-run, 2026-10-19

`oxpecker fuzz --corpus runs/fuzz-2026-10-19/corpus --time 2400 --findings runs/fuzz-2026-10-19/findings`,
seeded from `runs/corpus-try` (71 entries), after the fuzzer improvements in the working tree
(`fuzz.py`, `gen/rand.py`, `findings.py`). Result: corpus 71 → 4,298 entries, 18,129 / 46,581
JaCoCo probes, 53 hard signatures recorded, 26 shrunk reproducers, 6 soft (stack/timeout/message
classes, not triaged).

### 6.1 New (checked in as regressions; no upstream issue found)

| regression | what | upstream |
|---|---|---|
| `compiled-bigint-in-number-typed-local.js` | compiled mode: a function local assigned from `-`, `~`, `*`, `++` on BigInts is typed double → `TypeError: Cannot convert BigInt to a number`. `var b = -1n` inside any function is enough. | none. Generalises `t262-bigint-to-number-overflow-throws.js` (same root cause), so that finding is now understood as a symptom of this one. |
| `error-tosource-null-npe-both-modes.js` | `Error.prototype.toSource.call(null)` → raw NPE, both modes | none (`toSource` is non-Ecma, see #1056) |
| `compiled-boxed-number-constant-identity.js` | compiled code reuses one boxed `Double` per numeric constant; via an identity-returning host function `J.echo(c) === J.echo(c)` is `false` interpreted, `true` compiled | none; same Java-boundary family as #350 but about identity, not type |

Characterisation of the BigInt bug (each line a separate program, `oxpecker diff`):

| program | verdict |
|---|---|
| `function f() { var b = 1n; return b; } f();` | equal |
| `function f() { var b = -1n; return b; } f();` | **compiled throws** |
| `function f() { var a = 1n; var b = -a; return b; } f();` | **compiled throws** |
| `function f() { let b = -1n; return b; } f();` | **compiled throws** |
| `function f() { var b = ~1n; return b; } f();` | **compiled throws** |
| `function f(a) { var n = 1n; n++; return n; } f();` | **compiled throws** |
| `function f(a) { var n = a * a; return n; } f(10n);` | **compiled throws** |
| `function f(a) { var n = 0; n = a + a; return n; } f(10n);` | equal (`+` may be string concat, not assumed numeric) |
| `function f() { return -1n; } f();` | equal (no local) |
| `var b = -1n; b;` | equal (top level, no optimizer typing) |

### 6.2 Known families (the bulk of the 53 signatures)

| family | example reproducers | known as |
|---|---|---|
| -0 lost in int arithmetic (arrays of `-0`) | `hard-2aca87b9d9`, `hard-4f83cf0914`, `hard-6c7464ec64`, `hard-9556bcd508`, `hard-e3dded90ff`, `hard-e630e96ec4` | `compiled-negative-zero-lost-in-int-arith.js` |
| int32 wrap on `++`/`--` | `hard-07c088bb4e`, `hard-4bb37440c8` | `compiled-increment-int32-overflow.js` |
| Proxy `has` before `get` | `hard-9ae63086b9`, `hard-ebd76c3473` | `compiled-proxy-extra-has-trap-on-get.js` |
| computed super update writes the key as value (`"NaN":"p"`, `"65534":"p"`, …) | `hard-49b2af89f8`, `hard-c2a25a910c`, `hard-5147da45df` | `interpreted-computed-super-update.js` (5147 is exactly the GetSuperBase-before-ToPropertyKey seed) |
| `super.x++` string concat | `hard-cf4b0740ae` | `interpreted-super-increment-string-concat.js` |
| boxed Double identity | `hard-16b08c88ef`, `441d1ab87e`, `29ed9c527f`, `5812f2bba4`, `7868b87091`, `8f32eb73ef`, `98e7eb9c54`, `eb2a1bb953` | new, §6.1 |
| BigInt in typed local | `hard-ca304333ca`, `hard-e91776e34c` | new, §6.1 |
| `Array.find()` AIOOBE, `BigInt.asIntN` overflow | (unshrunk entries only) | existing regressions |

### 6.3 Not a Rhino bug

- `hard-d28c602594`: `J.typeOf(null).charAt(2147483647)` at top level. `J.typeOf` returns a
  `java.lang.String`, so this is a LiveConnect call to `String.charAt` that throws Java's own
  `StringIndexOutOfBoundsException`; inside `try` it is catchable and both modes agree. The
  harness classes an uncaught wrapped Java exception from a host method as a crash — a
  classification artefact, worth tightening in `compare.py`.

### 6.4 Fuzzer observations

- Signature dedup is too fine for array-shaped values: one root cause (-0, Double identity, super
  key) produces many signatures because array length and element positions survive
  normalisation. `known_signatures` only suppresses the exact scalar forms (`hard:value -0 vs 0`),
  so these families keep re-surfacing and drown out new ones. Worth normalising runs of
  elements, or bucketing by the first differing leaf rather than the whole elided context.
- Several unshrunk entries (`"p2" vs "p1"`, `0 vs 2`, `trace length 4 vs 3`) have no reproducer of
  their own; each was replaced by its shrunk form, which falls in one of the families above, but
  the mapping is not recorded. Recording parent → shrunk signature would make that auditable.
