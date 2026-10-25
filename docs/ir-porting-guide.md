# Porting a generator family to the IR

How to turn a string generator (`oxpecker/gen/generators/<family>.py`) into an IR generator
(`oxpecker/ir/generators/<family>.py`). The finished examples to copy from are
`oxpecker/ir/generators/values.py`, `operations.py` and `compiler.py`, and the templates in
`oxpecker/ir/templates.py`. Background: `docs/proposal-hypothesis-ir.md` (§3, §3.8 rules) and
`docs/ir-implementation-decisions.md`.

## The shape of an IR generator

```python
@generator("binary_bitwise", family="operations", weight=3, output=T.INT32)
@st.composite
def binary_bitwise(draw, b: Builder):
    """Bitwise/shift operators, which force int32 conversion."""          # copied from the original
    op = draw(st.sampled_from(BITWISE))
    return b.binary(op, draw(operand(b)), draw(operand(b, T.INT32)))
```

- A generator is a plain Hypothesis strategy: `@st.composite` over `(draw, b)`. **Every choice is an
  explicit `draw(...)`.** `@generator(...)` only registers it (name, family, weight, toggle,
  sloppy_only, output) — keep exactly the original's metadata and docstring. There is no `inputs=`:
  draw inputs yourself (`draw(pick_or_make(b, T.NUMBER))`).
- The `Builder` `b` never draws. It holds the program being built and emits instructions:
  `b.binary`, `b.unary`, `b.call`, `b.method`, `b.construct`, `b.declare`, `b.reassign`,
  `b.emit(Op(...), inputs)`, `b.const(t, text)` (a fixed-form literal, always inline),
  `b.builtin(name, t)`, `b.eval(code, *inputs, out=T|None, pure=False)`, `b.do_call` /
  `b.do_method` (statement form), `b.error_name(e)`, `b.ret`, `b.probe`, `b.pin`, `b.replay`.
- Blocks are context managers: `with b.function(arity, style=..., strict=..., param_types=[...]) as
  (f, params)`, `with b.if_(c) as blk:` / `with blk.else_():`, `with b.for_(n) as i`,
  `with b.while_(n, do=False, label=None) as c`, `with b.for_each(iterable, kind="of"|"in") as x`,
  `with b.switch_(d) as sw:` / `with sw.case(value_or_None):`, `with b.with_(obj)`,
  `with b.block(label)`, `with b.try_() as t:` / `with t.catch() as e:` / `with t.finally_():`.
  Statements: `b.break_(label)`, `b.continue_(label)`, `b.yield_(v, delegate=, result=)`.
  `b.fresh_label()` gives a label no enclosing statement uses. `b.strict` says whether code here is
  strict (whole program or inside a `"use strict"` function) — check it, not `opts.strict`.
- Shared strategies (`oxpecker/ir/builder.py`): `pick(xs)`, `maybe(p)`, `pick_or_make(b, t)`,
  `target(b, t)` (a declared, assignable variable), `make(b, t)`, `literal(b, t, pool)`,
  `statements(b, lo, hi)`, `nested(b, n)` (the old `b.run_some(n)`).

## Translation table

| string builder | IR |
|---|---|
| `b.choice(CONSTANT_POOL)` | `draw(st.sampled_from(POOL))` |
| `b.choice(list built from variables / context)` | `draw(pick(xs))` (rule 2) |
| `b.rand_int(lo, hi)` | `draw(st.integers(lo, hi))` |
| `b.maybe(p)` | `draw(maybe(p))` — shrinks towards False |
| `for _ in range(b.rand_int(lo, hi)): ...` | `draw(st.lists(<composite>, min_size=lo, max_size=hi))` (rule 1) |
| `b.pick_or_make(T)` / `b.expr(T, literal)` | `draw(pick_or_make(b, T))` |
| a variable that is later assigned (`x += …`, `x = …`, `x++`) | `b.declare(init)` or `draw(target(b, T))` — only declared variables can be assigned |
| `b.fresh(T, "expr")` | the instruction(s) computing `expr` (they are temporaries; probes observe them) |
| `b.emit("x.push(y);")` | `b.do_method(x, "push", y)` |
| literal spelled inside a form (`"x | 0"`) | `b.const(T.INT32, "0")` |
| literal value from a pool (`b.choice(INT32S)`) | `draw(literal(b, T.INT32, INT32S))` (keep the pools from `gen/generators/values.py`) |
| `with b.define_function(n, param_types=…) as (f, params)` | `with b.function(n, param_types=…) as (f, params)` |
| `b.try_block()` … | `with b.try_() as t: …; with t.catch() as e: …` |
| `b.run_some(n)` | `draw(nested(b, n))` |
| JS syntax with no op (getters, classes, `new.target`, `super`, TDZ, Proxy handlers) | `b.eval("code with {0} {1}", in0, in1, out=T)` — placeholders `{k}` only; other braces are literal |
| `with (o) {…}` / direct eval | sloppy only: `if b.strict:` use the strict-safe alternative |

## Rules (from proposal §3.8 — they are why shrunk reproducers are small)

1. Repetition is `st.lists`, never "draw a count, then loop".
2. Context-dependent pools: `pick(xs)`, never `st.sampled_from` (a one-element sampled_from
   records no choice).
3. Order every constant pool simplest first (index 0 is where shrinking goes).
4. Draw what a later choice depends on before it; draw everything a block needs BEFORE emitting
   the block when the draw would otherwise land inside unrelated code (e.g. call arguments).
5. Parts a generator does not need for its point should be drawn (`maybe`, `st.lists(min_size=0)`),
   not fixed: fixed scaffolding is free to the shrinker and ends up in every reproducer.
6. Never emit and then reject; emit only valid code. `verify` failing is a generator bug.
7. Keep the original's semantics — same JS shapes, same pools, same weights. Where an exact shape is
   impossible without a new op, use `b.eval` and say so in a comment.

## Done means

- Every generator of the family is registered under its original name, family, weight, toggle,
  sloppy_only and output, with its docstring.
- `pytest tests/test_ir_registry.py -k "<generator names>"` passes (it draws each generator alone,
  verifies, lifts, and node-checks the output).
- `oxpecker gen --ir -n 40 -o runs/port-<family> --generator <name> ... --run` runs without
  `syntax` statuses.
- Do not modify the IR core (`oxpecker/ir/{ops,analysis,lift,builder,registry,strategies}.py`); if
  an op is missing, use `b.eval` and report which op would be better.
