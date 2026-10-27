# The generation layer as implemented (`oxpecker/il/`)

A reader's guide to the code, written 2026-10-26 after design §11 steps 1–4. The design
(`docs/design-generation-layer.md`) says what the layer should be and why; the decision log
(`docs/generation-layer-decisions.md`, G1–G32) says what was settled; this document says what
is there, how the pieces fit, how to drive each one from a Python shell, how to add a generator,
and what the tests guard. Status and next steps are in `docs/handoff-2026-10-26.md`.

## 1. The pipeline in one picture

```
GenOptions ──► strategies.programs()
                  │  picks a template, runs it on a Builder
                  ▼
               Builder ──emit──► Program (tuple of Instruction, FuzzIL-style)
                  │  generators draw values and emit operations
                  ▼
            lifting_choices()  (per-instruction Lift: inline | var | let | const)
                  ▼
              verify()         (analysis: scopes, types, Rhino model; raises on a generator bug)
                  ▼
               lift() / js()   JavaScript text, the ONLY place text is produced
                  ▼
          harness (Rhino interpreted + compiled) ──► compare ──► findings
```

Every arrow is one call. Nothing between `GenOptions` and `lift()` contains JavaScript text:
operations are typed dataclasses, names come from registries (`Builtins`, `Keys`, `Methods`),
operators and keywords are enums. A lint test enforces that (§6).

## 2. From a shell

```python
>>> from oxpecker.il.strategies import generate, run_generator, draw, generators, templates
>>> g = generate(seed=3)                 # a whole program; same seed, same program
>>> print(g)                             # probed JavaScript (what the harness runs)
>>> print(g.plain_source)                # the reproducer without probes/comments
>>> from oxpecker.il.program import dump; print(dump(g))        # the IL
>>> from oxpecker.il.analysis import explain; print(explain(g))  # types, storage, Rhino model, lift choices
>>> s = run_generator("while_loop", seed=1)   # one generator after a small prefix of values
>>> generators("compiler-targeted")           # names by family
>>> from oxpecker.harness.compare import run_and_compare
>>> run_and_compare(g.source)                 # both Rhino modes, compared, printed
```

Hand-written IL goes through the same functions:

```python
>>> from oxpecker.il.builder import Builder
>>> from oxpecker.il.ops import BinaryOperator, Parameters
>>> from oxpecker.il.environment import Builtins, Methods
>>> b = Builder()
>>> with b.plain_function(Parameters(1), named=True) as (f, (p,)):
...     x = b.declare(b.binary(p, b.load_int(1), BinaryOperator.ADD))
...     b.do_return(x)
>>> b.call_function(f, [b.load_int(41)])
>>> from oxpecker.il.lift import js; print(js(b))
>>> from oxpecker.il.analysis import verify; verify(b.finish())
```

`generate`, `run_generator` and `draw(strategy, seed)` run a Hypothesis strategy exactly once
from a seed. Do not use `strategy.example()` on anything that touches a `Builder`: it runs the
strategy several times and the builder is emitted into repeatedly (§7).

## 3. Module by module

Sizes are lines of code on 2026-10-26; "FuzzIL" means the file is a translation of the Swift
source named in its Apache-2.0 header.

### `ops.py` (1 546) — the operations

`Operation` is a frozen dataclass with class-level counts (`num_inputs`, `num_outputs`,
`num_inner_outputs`, `first_variadic`), `attrs` (block start/end, pure, mutable, ...), the
`required_context` and the `context_opened`. About 110 subclasses mirror FuzzIL's inventory
minus what Rhino lacks (classes, async, private fields, modules) plus oxpecker's own:
`Declare(kind)` (a declared, reassignable variable from a temporary; replaces FuzzIL's Dup),
`Probe(label, number)`, `BeginCodeString`/`EndCodeString`, `LoadBuiltin(builtin)`,
`CreateNamedVariable(identifier, mode)`. Function begins carry `parameters`, `param_types`,
`named`, `strict`; loops carry a `DeclarationKind` for their variables.

Enums are the closed vocabularies: `UnaryOperator`, `BinaryOperator`, `Comparator`,
`RegExpFlag` (Rhino's six flags), `PropertyFlag`, `PropertyType`, `DeclarationKind`, `Lift`,
`ForEachKind`, `Radix`, `FloatStyle`, `DirectiveKind`, `ProbeLabel`, `Context` (a `Flag`).
Destructuring patterns are small dataclasses (`ObjectPattern`, `ArrayPattern`, ...).

The only text fields are `LoadString.value`, `LoadRegExp.pattern` and
`CreateTemplateString.parts`; `test_ops.py` enumerates every class to prove it.

### `program.py` (350) — instructions and programs

`Var` is an int. `Instruction(op, inouts, index, gen, origin, step, lift)`: inputs, outputs and
inner outputs are slices of `inouts`; `gen`/`origin`/`step` say which generator emitted it and
in which invocation; `lift` is the keyword choice. `Program(code, options, template)` prints as
its dump. `check(code)` is FuzzIL's `Code.check`: variables defined before use, blocks balanced,
contexts satisfied; it raises `InvalidProgram(kind, message, index)`. Helpers: `block_end`,
`block_begin`, `block_group`, `enclosing_subroutine`, `renumber`, `signature` (a structural
hash ignoring provenance), `dump(x)` for anything with `.code`.

### `types.py` (244) — the type lattice

`Type(possible: Kind, group, signature, element, props)` with `Is`, `MayBe`, `join`,
`with_prop`; constants `ANY`, `NOTHING`, `UNDEFINED`, `NULL`, `BOOLEAN`, `INTEGER`, `FLOAT`,
`NUMBER`, `BIGINT`, `STRING`, `SYMBOL`, `OBJECT`, `FUNCTION`, `ARRAY`, ... `Key(name, kind)`,
`Param`, `Signature`. `test_types.py` checks the lattice laws with Hypothesis.

### `environment.py` (456) — the registries

Built at import from `oxpecker/environment/rhino-capabilities.json` (what `oxpecker probe`
found in Rhino) plus a hand-written overlay (types the probe cannot see, the harness's host
objects `J`, `__pause`, `Continuation`, `eval`, `Function`, `globalThis`). Three namespaces:

- `Builtins.MATH`, `Builtins.J`, ... → `Builtin(name, type, constructs, calls, signature, group)`
- `Keys.LENGTH`, `Keys.A`, `Keys.TO_STRING`, ... → `Key`
- `Methods.ARRAY_PUSH`, `Methods.J_TYPE_OF`, `Methods.OBJECT_CTOR_SET_PROTOTYPE_OF`, ... → `Method(receiver, name, arity, params, returns)`

plus `methods_of(group)`, `props_of(group)`, `INSTANCE_GROUP`/`INSTANCE_TYPE` (what `new X()`
makes), and value pools (`INTERESTING_INTEGERS`, `INTERESTING_FLOATS`, `INTERESTING_STRINGS`,
`INTERESTING_KEYS`, `REGEXP_PATTERNS`, `BUILTIN_POOL`). Attribute names are the JavaScript
names in SNAKE_UPPER (`charCodeAt` → `STRING_CHAR_CODE_AT`). `test_environment.py` checks the
overlay against the probe table.

### `options.py` (40) — `GenOptions`

`min/max_statements`, `max_depth`, `probe_every`, `probe_width`, `strict`, `language_version`,
`toggles` (`let`, `generators`, `for-of`, `destructuring`, `spread`, `continuations`, `with`,
`eval`), `families`/`generators` (restrict the pools), `weights` (name or family → multiplier).

### `analysis.py` (1 115) — what a program means

`analyse(code, strict=, param_types=) -> Analysis` computes in one pass: `visible_at[i]` (in
scope before instruction i, most recent first), `context_at`, `storage` (`TEMP`, `LOCAL_VAR`,
`PARAM`, `LABEL`, `THIS`, ...), `defs`/`uses`/`assignments`, `function_of`, `strict_at`,
`labels`, `types` (join over the program), `returns`, and `rhino`, the Rhino model.

The typer is FuzzIL's JSTyper: a state stack with strong updates on assignment and a join at
every merge point (if/else, switch cases, try/catch), loops typed in two passes so a type "at a
point" covers a later iteration. It is a steppable class (`Typer.step(i, instr)`) so the builder
can keep types current while emitting.

`RhinoModel` predicts the compiled mode from Rhino's own source (`b95f75d`): per function the
activation reasons (nested function, catch, with, eval, `arguments`, yield, super) and hence
`optimizer_runs(fn)`; `number_var` (locals every assignment of which is NumberType by
`Block.findExpressionType`); `direct_call_site`/`direct_call_target`; `expr_type`.
`tests/il/test_rhino_model.py` checks it against Rhino through probes (G32).

`verify(code)` runs `check` and the analysis and raises `InvalidProgram` for a program no
generator may produce: assignment to a non-assignable variable, a label used as a value, a call
of something that is never a function, property access on a nullish value, two `__proto__`
entries, two default cases, `with` in strict code, a strict directive under a non-simple
parameter list. `explain(code)` prints the IL annotated with all of the above.

### `builder.py` (850) — emitting IL

`Builder(options)` holds the code, the open blocks, the analysis state (visible variables,
storage, types, the Rhino model on demand) and checks every `emit` (inputs visible, context
satisfied, block matched). One method per operation: `load_int`, `load_string`, `builtin`,
`declare(value, kind)`, `binary`, `compare`, `get_property(obj, Keys.X)`, `call_method(obj,
Methods.ARRAY_PUSH, args)`, `construct`, `reassign`, `update`, `destruct`, `do_return`,
`probe`, ... Blocks are context managers that yield their variables:

```python
with b.plain_function(Parameters(2), named=True, types=(NUMBER, NUMBER)) as (f, (p, q)): ...
with b.if_(cond) as frame: ...;  with frame.else_(): ...
with b.while_loop() as w: cond = ...; with w.body(cond): ...
with b.for_loop() as fr: with fr.condition([init]) as (i,): ...; with fr.afterthought(c): ...; with fr.body(): ...
with b.for_each(ForEachKind.OF, iterable) as ((x,), label): ...
with b.repeat(10) as (i, label): ...
with b.try_() as t: ...; with t.catch() as e: ...; with t.finally_(): ...
with b.switch(v) as s: with s.case(value): ...; with s.default(): ...
with b.object_literal() as lit: lit.add_property(Keys.A, v); with lit.method(Keys.M, Parameters(1)) as (this, (k,)): ...
with b.code_string() as cs: ...   # cs.result is the string
```

Pools: `b.visible()`, `b.pool(type)` (exact / may-be / all), `b.is_assignable(v)`. Two FuzzIL
rules the builder enforces that trip people up: the values an object literal or switch uses must
be loaded before the block opens (`LoadInteger` needs the JavaScript context), and labels of
enclosing blocks are hidden inside a subroutine.

`Builder.at(code, pos)` replays a program up to a point (for the codegen mutator) and
`replay(start, end, var_map)` re-emits a range with renamed variables (used by
`activation_pair`).

### `strategies.py` (471) — generators as Hypothesis strategies

- `@generator(name, family=, weight=, rank=, context=, inputs=, required=, produces=, toggle=,
  sloppy_only=, source=, block=)` registers an `st.composite` function `(draw, b, *inputs)` in
  `REGISTRY` as a `GeneratorSpec`; the wrapper stamps `gen`/`origin` on what it emits.
  `@value_maker(type)` marks the generator that can make a value of a type from nothing;
  `@template(name, weight=)` registers a program shape.
- `pools(options)` builds the fixed pools once per options: `statements`, `members` (object
  literal context), `cases` (switch), `values`; weights are repetition in the list, times
  `options.weights`; order is family, `rank`, name (so shrinking moves towards integers).
- `pick(draw, xs)` draws an index, never the element (context-dependent pools stay shrinkable);
  `maybe(p)` is a `sampled_from` over a fixed bool list that shrinks to `False`.
- `variable(b, type)`: an exact match, else a may-be match, else any visible variable, else the
  value maker for the type. `arguments_for(b, f)` draws arguments by the callee's signature.
- `prefix(b)` (value makers), `statements(b, lo, hi)`, `nested(b)` (one level deeper, bounded by
  `max_depth`), `one_statement`.
- `lifting_choices(code)` draws a `Lift` per value-producing instruction (inline ×3, var, let,
  const) and `programs(options, template=None)` is the whole pipeline up to `verify`.
- Shell API: `draw`, `generate`, `run_generator`, `generators`, `templates`; `Generated`
  (`.code`, `.program`, `.template`, `.source`, `.plain_source`) and `Snippet`.

### `generators/` — the families

| file | family | what |
|---|---|---|
| `values.py` | values | FuzzIL's value generators: integers, floats, strings, booleans, arrays, regexps, builtin instances, typed arrays, trivial functions, symbols; the value makers |
| `operations.py` | operations | unary/binary/ternary/compare/update/reassign, typeof/void/in/instanceof, number computation, int32 coercions, templates, string concatenation and conversions |
| `control.py` | control | if/else, while, do-while, for, for-in, for-of, repeat, break/continue, try/catch/finally, throw, blocks, switch, with, eval (a code string) |
| `calls.py` | calls | plain/strict/arrow/generator functions, calls, constructs, method and computed-method calls, return/yield, this/arguments/new.target, apply/call/bind |
| `objects.py` | objects, properties, members | object literals and their members (properties, elements, methods, getters, setters, prototypes), property/element/computed ops, destructuring, Proxy, super.* from literal methods |
| `compiler.py` | compiler-targeted | the Rhino-model shapes: number-var locals flipped late, activation pairs, direct-call arity, callsite relinking, property-site shape change, ConsString chains, int32 boundaries, super in literals, constant sites, negative zero |
| `java.py` | java-interop | values across the `J` boundary: typed parameters, echo/typeOf, overloads, Java-returned values, Java arrays and collections, a numeric pipeline |
| `continuations.py` | continuations | `__pause` in functions, loops, try/finally, across non-JS frames; `new Continuation()` |

`templates.py`: `plain` (prefix + statements with probes), `jit1_function`, `jit2_functions`,
`jit_tricky_function` (functions warmed in loops, then called with other arguments).

### `lift.py` (1 118) — IL to JavaScript

FuzzIL's `JavaScriptLifter` policy: pure expressions are inlined into their single use,
effectful ones are kept in a pending list and flushed at statement boundaries or when used out
of order; everything else becomes `var`/`let`/`const name = expr;` by the instruction's
`Lift`. Names come from storage and type (`n4`, `s3`, `arr5`, `f2`, `o9`, `p11`, `i29`).
Spellings for literals, keys (dot, index or quoted), regexps and templates live in one table.
Loop headers with more than one instruction lift as an immediately-invoked arrow (FuzzIL does
the same). Code strings are lifted recursively into an escaped string literal. `Probe`
instructions become `__probe("label", <up to probe_width visible declared variables>)`;
`// [generator]` comments mark top-level statements. `LiftOptions(probes, comments, strict,
probe_width)`; `lift(code, options)` and `js(code, **options)`.

### `cli.py` and `harness/compare.py`

`oxpecker gen -o DIR -n N [--seed S] [--template T] [--family F]... [--generator G]...
[--without TOGGLE]... [--max-statements] [--max-depth] [--strict] [--run]` writes
`case_NNNN.js` (probed), `.plain.js`, `.il` and `.meta.json` (seed, template, generators used);
case k is `generate(S + k)`. `oxpecker list [--family F] [--templates]` prints the registry.
`run_and_compare(source)` keeps one harness client per process for the shell and the tests.

### `mutate.py` (~600) — mutators and lineages

Nine mutators, each a composite `(program, an, step) -> Program | None` in a fixed
simplest-first list `MUTATORS`: `operation` (one parameter of one mutable instruction, or one
`lift` choice; never the current value), `input` (one input rewired to a visible variable the
slot accepts), `declaration_kind` (`var <-> let` anywhere, `-> const` only for a never-assigned
variable; Declare, named variables, loop headers, lifted temporaries), `reassign` (inserts
`Reassign(v, w)` with `w` of another type class), `codegen` (`statements()` at a point through
`Builder.at`), `splice` (a donor instruction's backward slice, blocks whole, external block
variables remapped to host variables of the same type), `combine` (a whole donor program at a
point), `activation` (inserts an activation trigger into an optimizer-run function or removes
the only trigger of one that is not), `number_flip` (assigns an AnyType value to a model number
variable). Every child is renumbered, gets `lift` choices for its new instructions and `step`
on the changed ones.

`mutation(program)` draws a mutator and `reject()`s a child `verify` refuses; `try_mutation`
raises `Rejected` instead. `evolved(options, steps)` is a lineage: the base `programs()` draw,
then one `st.lists` draw of mutator indexes so Hypothesis shrinks a lineage to fewer and simpler
steps; a rejected step is skipped and counted. Shell: `mutate(program, seed, name=)` returns a
`Mutated` with `.diff()`; `lineage(seed, steps=)` a `Lineage` with `.steps` and `.program`;
`oxpecker gen --evolve K` writes the end of a K-step lineage per case and lists the steps in
the meta file.

### `oxpecker/fuzz.py` — the loop

Outside the package because it needs the harness. A corpus entry is a lineage as the choice
sequence that draws it (G35): `draw_lineage(options, steps, prefix=, rng=)` draws one (the
prefix replays exactly, the rest is fresh), `replay(choices)` reproduces it, `extend(choices,
...)` flips the lineage's final "one more?" choice and draws more steps. `fuzz(corpus_dir=,
...)` is the loop: a fresh lineage with `p_new`, otherwise a corpus entry extended; the program
runs in both Rhino modes through `harness_runner` (any `Runner` will do: the tests use a fake
one with planted probes and a planted bug); new probes add the entry to `corpus.jsonl` (with
`compact_budget`, shrunk first while it still reaches the probes a re-run reached); a hard
verdict with an unknown signature is shrunk by `Shrinker` (G36) and recorded with
`findings.record`. `oxpecker fuzz -o DIR [--iterations N | --seconds S] [--steps K] [--p-new P]
[--shrink-budget N] [--compact-budget N] [--findings-dir D] [--ignore-known]` plus the
generation options of `gen`.

## 4. A worked example

`generate(seed=1, min_statements=2, max_statements=3, max_depth=1, probe_every=0)`:

```
v0 <- LoadString(value='\ud800')
v1 <- LoadInteger(value=0, radix=<Radix.OCT: 8>)
v2 <- LoadBuiltin(builtin=Builtin(name='Symbol', ...))
v3 <- LoadString(value='"')
v4 <- CallFunction(num_arguments=1, call_optional=False) v2, v3
v5 <- CreateArray(num_elements=2) v3, v4
v6 <- LoadString(value='Øaÿ')
v7 <- LoadBuiltin(builtin=Builtin(name='Uint16Array', ...))
v8 <- LoadInteger(value=35, radix=<Radix.DEC: 10>)
v9 <- Construct(num_arguments=1) v7, v8
v10 <- TypeOf() v5
v11 <- LoadString(value='undefined')
v12 <- Compare(op=<Comparator.STRICT_EQUAL: 'strict-equal'>) v10, v11
Probe(label=<ProbeLabel.END: 'end'>, number=0)
```

lifts (without probes) to

```javascript
var s0 = "\ud800";
const f2 = Symbol;
var s3 = "\"";
const arr5 = [s3, f2(s3)];
var f7 = Uint16Array;
let o9 = new f7(35);
let s10 = typeof arr5;
var s11 = "undefined";
s10 === s11;
```

`v1` and `v6` are unused temporaries with `lift=inline`, so they vanish; `v4` is a pure
expression inlined into the array; `s3` is `var` by its lift choice, so it is declared and the
array refers to it. `explain` shows the same lines with `; v5: object(Array of string|symbol
{length}); lift=const` style annotations and, inside functions, the activation reasons and
number variables.

## 5. Writing a generator

```python
@generator("int32_boundary_arith", family="compiler-targeted", weight=4, produces=NUMBER, block=True)
@st.composite
def int32_boundary_arith(draw, b: Builder) -> Var:
    """A number-var local at an int32 boundary, one arithmetic step with a parameter, ..."""
    with b.plain_function(Parameters(1), types=(NUMBER,)) as (f, (p,)):
        x = b.declare(b.load_int(draw(st.sampled_from(INT32_BOUNDARIES)), draw(st.sampled_from(Radix))))
        draw(st.sampled_from(BOUNDARY_STEPS))(b, x, p)       # a table of (b, x, p) -> None callables
        b.do_return(b.create_array([x, b.binary(x, b.load_int(0), BinaryOperator.BIT_OR)]))
    return b.declare(b.call_function(f, [b.load_int(1)]))
```

Rules, in the order they bite:

1. The docstring is the specification; the first line is what `oxpecker list` prints.
2. Inputs: declare `inputs=(OBJECT, FUNCTION)` and the registry draws them with `variable()`;
   `required=(STRING,)` means an exact-typed variable must exist or the generator is skipped.
   Draw anything else with `variable(b, t)` or `pick(draw, b.pool(t).exact)`.
3. Shapes are tables of callables over the builder (`NUMERIC_STEPS`, `SITE_FORMS`), never
   strings. Property names are `Keys.*`; when a string is needed (`load_string` for a computed
   key) it is `Keys.A.name`. Methods are `Methods.*`; builtins are `Builtins.*`.
4. Load every value before opening an object literal or a switch; draw before building where a
   lambda would otherwise draw lazily.
5. A helper that emits IL without drawing is a plain function, not a composite.
6. A generator that opens a block is `block=True` (depth accounting); one that needs a context
   (`context=Context.LOOP`) is only drawn there, or it scaffolds its own context (the `super_*`
   generators).
7. Add it to `MUST_EMIT` in `tests/il/test_generators.py`: the operation classes every draw must
   contain and, sparingly, the ones some draws must contain (forty draws do not cover a
   five-way choice evenly).
8. Run `pytest tests/il/test_lint.py tests/il/test_generators.py -k <name>`, then
   `tests/il/test_lift_rhino.py` and, for the family, `OXPECKER_FAMILIES=<family> pytest
   tests/il/test_completion_rhino.py -s`.

## 6. What the tests guard

| file | guards |
|---|---|
| `test_ops.py` | arities, block matching, and that no operation carries text beyond the three declared fields |
| `test_program.py` | `check`, block navigation, renumbering, signatures |
| `test_types.py` | the lattice laws (Hypothesis) |
| `test_environment.py` | overlay vs probe table; every registry attribute resolves |
| `test_lint.py` | no JavaScript token or keyword in any string constant of `strategies.py`, `templates.py`, `generators/*` (docstrings, `load_string` arguments, repr bodies and raise messages excepted; the allowlist is empty) |
| `test_analysis.py` | scopes, def-use, typer joins and two-pass loops, Rhino model predicates, every `verify` rule |
| `test_builder.py` | every block helper, hiding, pools, `at`, `replay` |
| `test_lift.py` | FuzzIL's inlining examples, lift choices, spellings, code strings, probes |
| `test_strategies.py` | pools, `pick`/`maybe`, `programs()` verifies, lifting choices, provenance |
| `test_generators.py` | each generator alone: verifies, lifts, emits its operations; op-class enumeration |
| `test_shell_api.py` | one call per pipeline step, deterministic from a seed |
| `test_cli.py` | `gen` writes the four files per case deterministically; `list` names everything |
| `tests/test_inspector.py` | catalogue, trace spans, and an export run against Rhino |
| `tests/test_fuzz.py` | the loop with a fake runner: replay/extend round trip, corpus growth, a planted bug found and shrunk, compaction keeps the probes; one slow run against Rhino with coverage |
| `test_golden.py` | the lifted source of seeds 0–19 against `tests/il/golden/` (G41) |
| `test_mutate.py` | each mutator changes the program and the child verifies; < 35 % rejected; locality (median >= 90 % of the parent kept); lineages verify, are deterministic from a seed and shrink to the needed step; activation and number-flip move the Rhino model |
| `test_lift_rhino.py` | property: Rhino's parser accepts every lifted program (probed, plain, strict, deep, mutated lineages); `OXPECKER_EXAMPLES=N` |
| `test_completion_rhino.py` | completion rate per family with floors; syntax errors fail it, engine crashes are printed as findings with their seed |
| `test_rhino_model.py` | number variables are a Java `Double` at a probe in optimized code, `Integer` in the activation copy |

`pytest tests/il` runs everything but the completion test in about a minute (the parse and
model tests need the built harness and are skipped without it).

## 7. Things that are easy to get wrong

The full list with the reasons is in `docs/handoff-2026-10-26.md` ("Gotchas from implementing
oxpecker/il"). The short version: never `.example()` a builder strategy; `.map` on a bare draw
needs a build context; Java types are observable through `__probe` (`traceJavaTypes`,
`"String | Double | Integer"`, label first), not through `J.typeOf`; Rhino `b95f75d` has no `d`
regexp flag and rejects `"use strict"` under a rest parameter; the lint reads every string.

### Findings so far, and the tools around them

`oxpecker shrink FILE` reduces a recorded reproducer at statement level while its coarse
signature holds (G40); the loop does the same after Hypothesis's shrink. `tests/regressions/`
holds what the first campaigns found: `super-logical-assign-proto-writeback-interpreted.js`
(the interpreter writes the read value back on `super.__proto__ ||= v`), `compiled-top-level-
multiply-by-zero-loses-sign.js` (`x * 0` is +0 at script level in the compiled mode),
`super-compound-assign-wrong-receiver-interpreted.js` (the interpreter reads a super property
with the wrong receiver on a compound assignment) and
`typed-array-set-no-args-aioobe-both-modes.js` (an engine crash). Expected mode differences
(continuations are interpreter-only) are listed in `tests/known_signatures.txt` and logged, not
shrunk; a program both modes report as unsupported is skipped (`compare.py`).

### `oxpecker/inspector.py` — the export for the UI

`oxpecker export -o ui/data --per-generator N --per-template N` samples every generator
(`run_generator`, a prefix of values then the generator once) and every template
(`generate(seed, template=)`), runs the samples through the harness and writes
`catalogue.json` (registry entries with source listings), `samples.jsonl` (source, plain source,
generator trace spans from the lifter's comments, stats, per-mode results, verdict),
`metrics.json` (per focus: fired, syntax/early-throw/throw rates, verdict counts, duplicates,
top exceptions) and `findings.json`, the schema in `ui/lib/types.ts`. `oxpecker metrics DIR`
prints the quality table.

## 8. Not there yet

Splice donors from the corpus were decided against (G44);
the model's "mixed" expression type (G32); the remaining Fuzzilli translations listed in the
handoff; the runtime-assisted mutators and Fuzzilli's minimizer are not ported by design (G24).
