# The generation layer from a Python REPL

A walk through every step of the pipeline by hand: generate a program, read its IL, lift it to
JavaScript, ask the analysis what it knows, build IL yourself, run the smaller strategies one at
a time, mutate, and run the result in both Rhino modes. Every Python block in this file is
executed by `tests/il/test_repl_tutorial.py` in order, in one namespace, so the examples cannot
drift from the code; the blocks marked `# needs the harness` run only when the harness jar is
built. The reader's guide `docs/implementation-generation-layer.md` explains the modules; this
file only shows how to drive them.

Start a shell in the project's environment: `.venv/bin/python` (or `ipython` if installed;
the objects have `_repr_pretty_` so they print compactly there). Nothing has to be loaded
first: the generator and template registries fill themselves on first use (G48's neighbour,
`strategies.registered()`).

## 1. Generate a whole program

```python
from oxpecker.il.strategies import generate

g = generate(seed=3)                    # same seed, same program, in any shell or test
print(g)                                # the probed JavaScript the harness runs
print(g.plain_source)                   # the same without probes and comments: a reproducer
g                                       # <Generated jit2_functions: 52 instructions>
```

`generate` takes the generation options as keywords; the defaults are in
`oxpecker.il.options.GenOptions`:

```python
small = generate(seed=3, max_statements=4, max_depth=1, probe_every=0)
only_control = generate(seed=3, families={"values", "control"})       # restrict the pools
one_generator = generate(seed=3, generators={"while_loop", "integer"})
no_let = generate(seed=3, toggles=frozenset({"generators", "for-of"}))    # drop toggled features
weighted = generate(seed=3, weights={"control": 3.0, "binary": 0.5})      # family or generator multipliers
tmpl = generate(seed=3, template="jit1_function")                       # fix the template
assert small.template and small.code and tmpl.template == "jit1_function"
```

A `Generated` has `.program` (a `Program`: `.code`, `.options`, `.template`), `.code` (the
instructions), `.source` and `.plain_source`. `str(g)` is `.source`.

The templates and generator names, by family:

```python
from oxpecker.il.strategies import generators, templates

templates()                 # ['jit1_function', 'jit2_functions', 'jit_tricky_function', 'plain']
generators("control")       # ['block_statement', 'compare_with_if_else', 'complex_for_loop', ...]
assert "while_loop" in generators("control") and "plain" in templates()
```

## 2. Print the IL

```python
from oxpecker.il.program import dump

print(dump(g))              # one instruction per line, blocks indented
print(g.program)            # the same: str(Program) is dump()
g.program                   # <Program jit2_functions: 52 instructions, <signature>>
```

Each line reads `v3 <- CallFunction(num_arguments=1, ...) v1, v2`: output variable, operation
with its attributes, inputs. Block openers end in `-> v22, v23` (their inner outputs: the
function variable's parameters, a loop's counter, a label). Instructions are plain data:

```python
instr = g.code[3]
instr.op, instr.inputs, instr.outputs     # the operation, input Vars, output Vars (ints)
type(instr.op).__name__                   # 'CallFunction', 'LoadInteger', 'BeginPlainFunction', ...
[type(i.op).__name__ for i in g.code if i.op.is_block_start][:3]
g.program.signature()                     # a hash of the operation shapes, ignoring literals
```

## 3. Lift IL to JavaScript

`js()` accepts anything with instructions: a `Generated`, a `Program`, a `Builder`, or the
tuple of instructions.

```python
from oxpecker.il.lift import js, lift, LiftOptions

print(js(g))                                      # plain: no probes, no comments
print(js(g, probes=True, comments=True))          # what g.source is
print(js(g.code, strict=True))                    # with a "use strict" directive
print(lift(g.code, LiftOptions(probes=True, probe_width=3)))
assert js(g, probes=True, comments=True) == g.source
```

The lifter inlines pure single-use values into their use site (FuzzIL's JavaScriptWriter);
whether a value gets a `var`, `let`, `const` or is inlined is recorded per instruction in its
`lift` field, which `lifting_choices` draws when a program is generated, so the same IL always
lifts to the same text.

## 4. Explain and analyse

```python
from oxpecker.il.analysis import analyse, explain, verify

print(explain(g))           # the IL annotated with each variable's type, storage and lift choice
an = analyse(g)             # the same information as data
an.types[g.code[3].output]  # a Type: str(...) gives 'symbol', 'integer', 'object(Array)', ...
an.storage                  # Var -> Storage (TEMP, TOP_VAR, LOCAL_LET, BLOCK_CONST, PARAM, LABEL, ...)
an.defs, an.uses            # Var -> defining index; Var -> indices of the uses
an.rhino.activation         # function begin index -> why Rhino's optimizer gives up there
an.rhino.number_var         # variables Rhino's compiled mode keeps as unboxed doubles
an.rhino.expr_type          # instruction index -> NUMBER / ANY / MIXED (G32, G47)
verify(g.code)              # analyse plus oxpecker's rules; raises InvalidProgram on a bad program
assert an.code == g.code and len(an.visible_at) == len(g.code)
```

`explain` prints what `analyse` computes, one instruction per line with `; v3: symbol;
lift=var` after it, and the Rhino model's verdict per function (`activation: none (optimizer
runs)` or the reasons it does not). `verify` is what every generated program and every mutation
child passes before it is accepted; from a shell it tells you whether hand-built IL is a
program the layer would produce.

## 5. Build IL by hand

The `Builder` is the same object the generators emit into. Values come back as `Var` (an int);
blocks are context managers.

```python
from oxpecker.il.builder import Builder
from oxpecker.il.ops import BinaryOperator, Comparator, Parameters, DeclarationKind
from oxpecker.il.environment import Builtins, Methods, Keys
from oxpecker.il.program import Program

b = Builder()
n = b.declare(b.load_int(1))                          # var n = 1  (declare: a reassignable variable)
b.update(n, b.load_int(2), BinaryOperator.ADD)        # n += 2
c = b.declare(b.load_int(3), DeclarationKind.CONST)   # const
with b.plain_function(Parameters(1), named=True) as (f, (p,)):
    x = b.declare(b.binary(p, n, BinaryOperator.MUL))
    with b.if_(b.compare(x, c, Comparator.GREATER)) as frame:
        b.call_method(b.builtin(Builtins.MATH), Methods.MATH_ABS, [x])
        with frame.else_():
            b.reassign(x, c)
    b.do_return(x)
b.call_function(f, [b.load_int(41)])
print(dump(b)); print(js(b))                          # a Builder is accepted everywhere
code = b.finish()                                     # the instructions, checked
an = verify(code)
prog = Program(code, b.options, "hand")               # a Program like generate() makes
assert "function f" in js(prog) and an.returns[f] is not None
```

The builder's methods are listed in `oxpecker/il/builder.py`: value constructors (`load_int`,
`load_string`, `builtin`, ...), operations (`unary`, `binary`, `compare`, `get_property`,
`call_method`, ...), control blocks (`plain_function`, `arrow_function`, `if_`, `while_loop`,
`for_loop`, `for_each`, `repeat`, `try_`, `switch`, `object_literal`, `with_`, `code_string`).
`Builtins`, `Methods` and `Keys` are the typed environment from `rhino-capabilities.json`:
`Builtins.MATH`, `Methods.MATH_ABS`, `Keys.LENGTH`; `b.builtin(...)` loads one, `b.call_method`
takes the `Method`. Queries while building: `b.visible()`, `b.pool(NUMBER).exact`,
`b.type_of(v)`, `b.context`, `b.in_function`, `b.rhino` (the model so far). `dump`, `js`,
`explain` and `analyse` all take the builder directly, before `finish()`.

## 6. The smaller strategies, one at a time

This is the part that answers "can I call `statements` myself": yes. Every strategy in
`strategies.py` and `generators/` is a Hypothesis composite that takes the builder as its first
argument and emits into it. `draw(strategy, seed)` runs a strategy exactly once from a seed
(`SeededProvider`, G43), which is the shell's way to run anything that touches a builder.

```python
from oxpecker.il.options import GenOptions
from oxpecker.il.strategies import draw, prefix, statements, one_statement, nested, variable, pools, REGISTRY
from oxpecker.il.types import NUMBER, ANY

opts = GenOptions(max_statements=4, max_depth=2, probe_every=0)
b = Builder(opts)
vs = draw(prefix(b), seed=1)             # 2-5 value makers: the program prefix (Fuzzilli's buildPrefix)
print(dump(b)); vs                       # [0, 1, 2, 3, 4]: the Vars they produced
n = draw(statements(b, 1, 3), seed=2)    # 1-3 statement generators for the open context
print(js(b)); n                          # how many were drawn
draw(one_statement(b, pools(opts).statements), seed=5)     # exactly one more
v = draw(variable(b, NUMBER), seed=3)    # a visible number, or a maker's fresh one (G38)
assert n >= 1 and b.in_scope(v)
```

`pools(opts)` is the weighted, ordered generator list the options select, per context:
`.statements`, `.members` (inside an object literal), `.cases` (inside a switch), `.values`.
Entries repeat by weight, so use a set for the names:

```python
pool = pools(opts).statements
sorted({s.name for s in pool})[:5]       # unique names
spec = REGISTRY["while_loop"]            # one generator's spec
spec.family, spec.inputs, spec.required, spec.context, spec.doc.splitlines()[0]
spec.applicable(b)                       # may it run here (context, toggles, required types)?
```

To run one generator on your own builder, draw its inputs the way `one_statement` does and
then draw its strategy:

```python
inputs = [draw(variable(b, t), seed=7) for t in spec.inputs]     # () for while_loop
draw(spec.strategy(b, *inputs), seed=3)
print(js(b))                             # ... while (n < 4) { ...; n++; }
assert any(type(i.op).__name__ == "BeginWhileLoopBody" for i in b.code)
```

`nested(b, n)` draws up to `n` statements inside a block you opened yourself and spends one
level of the nesting budget (`b.depth`, from `max_depth`):

```python
with b.plain_function(Parameters(1), named=True) as (f, (p,)):
    r = b.declare(b.binary(p, b.load_int(1), BinaryOperator.ADD))
    draw(nested(b, 2), seed=4)           # the function body, generated
    b.do_return(r)
b.call_function(f, [b.load_int(41)])
code = b.finish(); verify(code)
print(js(code))
```

`run_generator(name, seed)` does all of that for a generator in isolation: a prefix of values,
then the generator, then verify. It returns a `Snippet` with `.emitted` (the generator's own
instructions), `.code`, `.js`:

```python
from oxpecker.il.strategies import run_generator

s = run_generator("while_loop", seed=1)
print(s)                                 # the JavaScript, generator spans marked by comments
s.emitted[0], len(s.emitted)
s = run_generator("number_computation", seed=2, prefix_size=5, max_depth=1)
assert s.emitted
```

The helpers the generators are written with are usable the same way: `pick(draw, xs)` indexes
a pool, `maybe(p)` is a boolean with weight `p` that shrinks to False, `variable(b, t)`,
`operand(b)`, `assignable(b, t)`, `arguments_for(b, f)`. To write a generator, see the
reader's guide §5; its decorator is `@generator(name, family=, inputs=, ...)` and it registers
on import.

**`.example()`**: `programs().example()` works (the registries fill on first use), but it
runs the strategy several times with Hypothesis's stock provider, so it is neither seeded nor
cheap, and anything that takes a builder you made will emit into it repeatedly. For
exploration use `draw(strategy, seed)`; for a whole program use `generate(seed)`.

```python
from oxpecker.il.strategies import programs
ex = programs(opts).example()
assert ex.template in templates()
```

## 7. Mutate

```python
from oxpecker.il.mutate import mutate, lineage, mutators, Rejected

mutators()                               # ['operation', 'input', 'declaration_kind', 'reassign', 'codegen',
                                         #  'splice', 'combine', 'activation', 'number_flip']
m = mutate(g, seed=1)                    # one mutation of a Generated / Program / instructions
m                                        # <Mutated input: 52 -> 52 instructions>
print(m.diff())                          # the IL as a unified diff, parent vs child
print(m)                                 # the child's JavaScript
m2 = mutate(g, seed=2, name="codegen")   # a named mutator; step= numbers its comments
try:
    mutate(g, seed=0, name="number_flip")    # needs a number variable in scope
except Rejected as e:
    print("rejected:", e)
l = lineage(seed=5, steps=3, max_statements=6)   # a base program and up to 3 mutation steps
l, [s.name for s in l.steps], l.rejected
print(l)                                 # the final program; l.base, l.steps[i].program for the others
assert m.program.code != g.code or m.name
```

A mutation that does not verify raises `Rejected` from the shell (`try_mutation` inside the
loop just skips it), so try another seed or mutator. The lineage's `steps` may be empty for a
seed whose first "one more?" draw was False; pick another seed.

## 8. Run it in Rhino

```python
# needs the harness
from oxpecker.harness.compare import run_and_compare

verdict, run = run_and_compare(g.source)        # both modes, compared and printed
verdict.kind                                    # 'equal', 'soft', 'java-types', 'hard', 'skip', 'discard'
verdict.signature()                             # e.g. 'java-types:diff Double vs Integer'
run.results["compiled"].status                  # 'ok' | 'throw' | 'timeout' | ...
run.results["compiled"].trace[:2]               # the probe lines, "label | v1 | v2 | ..."
run.results["interpreted"].thrown               # the exception, if any
assert set(run.results) == {"interpreted", "compiled"}
```

`run_and_compare` starts one harness client per process on first use and keeps it. The same
comparison for a lineage, with coverage, is what `oxpecker.fuzz.harness_runner` wraps; the
loop's pieces are callable too:

```python
from pathlib import Path
from random import Random
from oxpecker.fuzz import draw_lineage, replay, extend, Corpus

lin, choices = draw_lineage(GenOptions(max_statements=5), steps=3, seed=4)   # a lineage and its choice sequence
again = replay(choices, GenOptions(max_statements=5), steps=3)                # the same lineage back
longer = extend(choices, GenOptions(max_statements=5), 3, Random(1), lin)     # more steps, drawn fresh (G48)
assert again is not None and again.program.signature() == lin.program.signature()
# Corpus(Path("runs/c1")).entries; Corpus(...).replay(0)   -- the entries of a campaign and their lineages
```

The CLI does the same without a shell: `oxpecker gen -o DIR -n 20 --seed 3 [--family control
--generator while_loop --template plain --evolve 3 --run]`, `oxpecker list --family control`.

## 9. Things that bite

- `draw(strategy, seed)` draws from the seed alone. `strategy.example()` does not, and runs the
  strategy more than once: never give it a builder you keep.
- Each `draw` on the same builder continues where the last one stopped; the seed only fixes
  that one draw. Two draws with the same seed on different builders can differ because the
  pools differ.
- `statements(b)` without bounds uses `b.options.min/max_statements`; `nested` returns 0 once
  `b.depth` is spent, so a block generator drawn at `max_depth=0` has an empty body.
- `pick` on an empty pool raises; `variable(b, t)` never does (a value maker fills in).
- `verify` raising on a generated program is a generator bug, not a filter: report it with
  the seed. On hand-built IL it is the rule you broke (assignment to a const, a label used as
  a value, a strict function with a rest parameter, ...).
- A `Builder`'s `finish()` can be called once; build a new one for another program.
- The harness functions need `harness-java/build/libs/oxpecker-harness-all.jar`; without it
  `run_and_compare` raises at start. Build it with `cd harness-java && ./gradlew fatJar`.
