# IR implementation decisions

Running log of decisions made while implementing `docs/proposal-hypothesis-ir.md` on branch
`feat/il-strategy`. The proposal says *what* and *why*; this file records the concrete choices
the implementation makes, so they can be reviewed and changed deliberately. Newest last. When a
decision changes the design itself, the proposal is updated too and the entry says so.

Format: **D<n>. Decision.** Why. Consequences / alternatives rejected.

## Layout and compatibility

**D1. New package `oxpecker/ir/`, next to `oxpecker/gen/`, until the migration is done.**
The string generators keep working (and keep finding bugs) while families are ported; both
produce the same `Program` dataclass (`gen/strategies.py`), so `findings`, `oxpecker run`, the
inspector export and the property tests need no changes. `Program` gains an optional `ir` field.

**D2. Reuse `gen/types.T` as the IR's type lattice**, plus `None` as bottom ("no information
yet") during inference and `T.UNKNOWN` as top. Same names the inspector and generators already use;
no second type system.

## Operations

**D3. Literals carry their JavaScript spelling and a declared type**: `LoadLiteral(T.NUMBER,
"0x7fffffff")` (no inline flag on the op any more: see D16). The spelling is what Rhino's parser sees (`1/3`, `-0`, `0x...`, `1e21` are
different code paths), and the existing pools in `gen/generators/values.py` (and `PERTURB`) are
reused unchanged. Alternative rejected: storing values and printing them (loses spellings).

**D4. An `Eval` escape hatch, as in Fuzzilli**: code with `{0}`, `{1}`, ... placeholders for
inputs, an optional output type, and a `pure` flag. For constructs not worth modelling yet
(object literals with methods, `with`, getters...). Inputs stay real IR variables, so scoping,
input mutation and verification still apply; only the code text is opaque. Expect it to shrink as
operations are added.

**D5. Operation set (first cut)**: literals, builtins (`LoadBuiltin("Math")`), array/object
creation, unary/binary/conditional, property and element get/set/delete, function/method calls,
`new`, reassign/compound-assign/update, functions (decl | expr | arrow, optional strict), if/else,
bounded for, try/catch/finally, throw, probe. Loops are only `BeginFor(count)` with a counter
nothing may assign: every program terminates by construction, as with today's generators.
`while`/`break`/labels/switch come with the `control` family port.

## Analysis and verification

**D6. ~~Types are flow-insensitive.~~ Superseded by D17** (flow-sensitive for declared
variables), after the critique (`docs/proposal-hypothesis-ir-critique.md` §2): Rhino's optimizer
specialises locals by the sequence of types assigned to them, so the type *at the point of use* is
what generators and mutators must see. Unchanged: nothing is stored on instructions, so mutations
cannot leave stale types (proposal §3.2).

**D7. `verify` rejects only definite violations.** Structural: each id defined once,
defined-before-use and visible, balanced and well-formed blocks (else in an if; catch/finally in a try; a try has a
handler — `try {}` alone is a SyntaxError; return inside a function). Assignment: only declared,
non-`const` variables, never a loop counter (D15). Type requirements, under the types at the point
of use (D17): a callee or constructor must be `FUNCTION` or `UNKNOWN`; no property access on a
definitely `null` / `undefined` value. Anything that is merely likely to throw at
run time (BigInt mixing, calling an `UNKNOWN`) is allowed: both modes must throw the same way, and
that is a test too.

**D8. IR scoping is stricter than JavaScript's.** A variable defined inside a block is visible only
inside it, although it is lifted as `var` (function-scoped). The IR's view is a subset of what
JS allows, so lifted programs are always valid; generators just cannot rely on `var` hoisting.

## Lifting

**D9. Probes are instructions** (`Probe(label)`, no inputs); the lifter fills in the variables
visible there (not inlined; the last `probe_width` in definition order) and drops a probe that
directly follows another at the same indent — today's `ProgramBuilder.probe` behaviour. Templates
place probes exactly where they do now. Probes having no inputs means mutations never break them.

**D10. Provenance is metadata on `Instr`** (`gen`, `origin`): which generator emitted it, and which
invocation. It drives `// [generator]` comments and the inspector's `generator_spans`, and is
ignored by analysis, verification and `signature()`.

**D11. Inlining policy** (the lifter decides the JS *shape*; only temporaries, never declared
variables, D15):
- a primitive literal temporary with at least one use is spelled out at every use unless its
  instruction is marked `keep` (D16), so locals (`var n1 = 5; n1 * 2`) and constants (`5 * 2`)
  are both reachable — they take different paths in Rhino's optimizer. A literal nothing uses
  stays a variable: it was generated to be observed by probes;
- an expression with exactly one use, where that use is the next emitted statement, is folded into
  it if either (a) it sits in the first operand slot that statement evaluates (the right-hand side
  of `t = v`; slot 0 of most others; never for compound assignment/update, which read their target
  first, or Eval, whose order is opaque) -- then nothing can be reordered, whatever the types; or
  (b) it is a unary/binary/conditional/builtin reference/pure `Eval` whose inputs are all
  primitive-typed -- then no user code (valueOf, toString, getters) runs while it is evaluated.
  This keeps today's shapes for compiler-targeted code: `n2 = n1 + p1`, `n1 = Math.imul(n1, p2)`;
- a property read is never folded into a callee position (`o.f()` vs `var t = o.f; t()` differ in
  `this`).
  *(Revised while porting `compiler-targeted`: the first version allowed only (b), which turned
  `n2 = n1 + p1` into `var x5 = n1 + p1; n2 = x5;` because parameters are `UNKNOWN`.)*
  *(Revised again: (a) is now "every operand the use evaluates before this slot is a temporary" —
  temporaries are never assigned, so they read the same whether the folded expression runs before
  or after them; only declared variables can be changed by its side effects. This covers
  `acc.push(f(x))` (slot 1 after the temporary `acc`), which "first slot" did not. And a pure
  expression may move past other folded pure expressions, so `[n1, n1 | 0, n1 >>> 0]` folds
  completely; fold decisions are computed from the last instruction backwards. `.length` of a
  string or array is pure: an own property, no getter can run.)*
Alternative rejected: Fuzzilli-style aggressive inlining with an effect analysis — more code, and
the conservative rule already gives nested expressions for the common numeric cases.

**D12. One precedence table** in the lifter (`_BINARY_PREC`, unary 16, `**` right-associative with
no unary left operand, `??` never mixed with `&&`/`||` without parentheses, numeric literals
parenthesised as member receivers: `(1).toString()`). Replaces the per-generator string patches in
`gen/generators/operations.py`.

**D13. Names by type**, from `gen/types.PREFIX` (`n1`, `s2`, `o3`, `f4`), with `p` for parameters,
`i` for loop counters, `ex` for catch variables; one counter per prefix. A variable keeps the name
of the type it was defined with.

**D14. Function declarations only at top level or directly in a function body**; a declaration
requested inside an if/for/try block is lifted as `var f = function(...) {...};` to stay clear of
Annex B block-function semantics.

## After the critique (2026-10-25)

`docs/proposal-hypothesis-ir-critique.md` reviewed the proposal; the user endorsed in particular
its §2 (storage kinds and reassignment), §3 (the lifter is part of the test case, readability is a
deliverable) and §4 (shrink-on-insert cost). D15–D20 implement §2 and the core-relevant parts of §3
before more generators are ported, because every generator chooses between a temporary and a
declared variable. §4 (tiered compaction) is planned with the fuzz loop (D24, pending).

**D15. Two classes of variables, as in FuzzIL: temporaries and declared variables.**
Temporaries are expression outputs: defined once, never assigned, inlinable. Declared variables —
`DeclareVar(kind)` outputs with kind `var | let | const`, function parameters, catch variables,
function names — have a storage kind and are the only assignable class (`Reassign`,
`CompoundAssign` = FuzzIL's Update, `Update` = FuzzIL's increment/decrement). The proposal's "SSA,
each variable defined once" was wrong for this project: the typed-local bugs in
`tests/regressions/` (`compiled-bigint-in-number-typed-local.js`,
`compiled-increment-int32-overflow.js`, ...) need `var n = 1n; n++`. Proposal §3.1–3.2 updated.

**D16. One `keep` flag per instruction replaces `LoadLiteral.inline`.** `keep` = "lift as its own
statement; never inline or fold". The builder draws it for every value-producing instruction
(`maybe(0.2)`), shrinking towards `False`, so a shrunk reproducer is the most-inlined, most
readable form unless the bug needs the statement — then the kept statement is the point of
interest. Lifting is a pure function of the IR including `keep`, so the reproducer is the program
that ran (critique §3.1). Alternative considered: the critique's single policy index per program;
rejected because the choice budget is not the constraint (critique §5: fresh programs use ≤ 8 % of
`BUFFER_SIZE`) and a per-instruction flag can be flipped by the operation mutator at one site.
`keep` is part of `signature()`.

**D17. Flow-sensitive types for declared variables.** A structured walk: assignments are strong
updates; if/else and try/catch(/finally) join their branches (a catch is entered from anywhere in
the try); a loop body is iterated to a fixpoint with the loop entry (it may run zero times); a
declared variable assigned inside a function other than its owner is `escaping` and typed by the
join of everything assigned to it anywhere, since any call may change it. Each pass starts from
scratch with the previous pass's return types and escaping summaries (the first version joined
across passes, so a call typed `UNKNOWN` before return types were known stayed `UNKNOWN`).
`Analysis.type_at(i, v)` is what requirement checks, the lifter's purity test and
`Builder.at()` use; `types[v]` (the join over the program) is for names.

**D18. Activation and storage are part of the analysis.** `activation[f]`: the function's own body
(not nested functions) uses `arguments`, `eval` or `with` (via `LoadBuiltin` or `Eval` code).
`storage[v]`: `top-var|let|const`, `local-…`, `block-let|const`, `activation-local-…`, `param`,
`activation-param`, `catch`, `counter`, `function`, `temp-top`, `temp-local`. `captured`: used
inside a nested function. These are reported, not interpreted: the analysis claims nothing about
Rhino beyond "uses arguments/eval/with". For the inspector (critique §3.3) and for a future
activation-toggle mutator.

**D19. Dead pure temporaries are dropped only without probes** (the plain reproducer). With probes
every visible variable is observed, so nothing is dead. "Pure" = literal, builtin reference,
unary/binary/conditional, array/object creation, with primitive-typed inputs.

**D20. Role names.** `Instr.hint` names an output by role (`acc1`, `ctor2`) instead of by type.
Names stay a pure function of the IR, so they are stable under shrinking.

**D21. Statement forms.** `CallFunction`/`CallMethod`/`Construct` take `discard=True` and
`Update` takes `value=False` for `a.push(x);` / `x++;`: results nothing captured before do not
become probed variables, matching today's programs.

**D22. The §3.8 rules are in the builder, not left to generator authors.** `choice()` is for
constant pools (`sampled_from`); `pick()` for context-dependent ones (an integer index);
`visible()` is most-recent-first; `maybe()` shrinks to `False`; `statement_list()` and
`repeat(fn, lo, hi)` are `st.lists` draws with one span per element; every generator invocation is
its own composite (its own span); `replay()` re-emits straight-line code ("the same body twice").

**D23. Swarm selection is one "excluded" boolean per generator, in a fixed order.** Shrinking
(towards `False`) re-enables everything, so the pool — and the meaning of every generator index —
returns to the full fixed weighted list.
