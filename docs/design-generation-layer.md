# The generation layer, rebuilt from FuzzIL: architecture and implementation design

Status: design, 2026-10-26, for review before any code is written. Part I (§1–§12) is the
architecture and the reasons; Part II (§13–§25) is the implementation: module by module, the
interfaces, the algorithms, the translation recipe for Fuzzilli's generators, worked examples,
and the brief a delegated implementer gets. Supersedes the generator
sections of `docs/design.md`, the IR parts of `docs/proposal-hypothesis-ir.md` (§3.1–3.4) and the
decision log `docs/ir-implementation-decisions.md`, which record the deleted layer. The brief it
answers is `docs/what_went_wrong_with_ir_generators.md` (part 2, the specification; part 3, the
guards). Decisions made here are logged in `docs/generation-layer-decisions.md` (G1–G24) and
every later change goes there first.

Sources read for this design: Fuzzilli at commit `be2811d00282320381fc8007d64873745772011d`
(`~/Dev/Compilers/fuzzilli`): `Sources/Fuzzilli/FuzzIL/{JsOperations,Instruction,Variable,Program,
Code,Context,Blocks,Semantics,Analyzer,JSTyper,TypeSystem}.swift`, `Base/ProgramBuilder.swift`,
`CodeGen/{CodeGenerator,CodeGenerators,ProgramTemplates,CodeGeneratorWeights}.swift`,
`Lifting/{JavaScriptLifter,Expression,JSExpressions,ScriptWriter}.swift`, `Mutators/*.swift`,
`Minimization/*.swift`, `Environment/JavaScriptEnvironment.swift`, `Docs/HowFuzzilliWorks.md`;
Rhino at `b95f75d`: `Parser.java`, `IRFactory.java`, `optimizer/{Optimizer,Block,OptTransformer,
Codegen}.java`; oxpecker's harness (`Host.java`, `JavaProbe.java`), `compare.py`, the capability
table `oxpecker/environment/rhino-capabilities.json`, and the deleted generators (git history
before `796b11a`) as a description of intended program shapes only.

Line references below are to those files at those commits.

---

## 1. What the layer must do

1. **Produce programs as structure.** The one rule from the brief: the generation layer contains
   no JavaScript text. Every construct is an operation; closed vocabularies are enums; builtins,
   keys and methods come from typed registries; JavaScript text exists only as the lifter's output.
2. **Target Rhino.** Interpreted and compiled modes of Rhino `b95f75d` at language version 200 or
   250. The capability table says what parses: no `class`, no `async`/`await`, no private fields,
   no static blocks, no modules; call spread crashes both modes (a recorded finding). Everything
   else FuzzIL expresses that is ES2015+ parses: arrows, generators, destructuring, defaults,
   rest, template literals, computed keys, getters/setters, `super` in object-literal methods,
   `new.target`, optional chaining, `??`, `**`, logical assignment, BigInt, `with`, labels.
3. **Be written as Hypothesis strategies.** Generators, value makers, templates, mutators and
   lineages are `@st.composite` functions; every choice is an explicit `draw`; the shrinker is the
   only minimiser; the strategy rules of the proposal (§3.8) are built into the shared plumbing.
4. **Reach Rhino's compiled-mode paths on purpose.** Rhino's optimizer specialises mutable
   function locals by the types assigned to them, only in functions without an activation, and
   links direct calls to top-level functions. The IL models storage and reassignment; the analysis
   predicts which path each variable and function is on; generators and mutators use that.
5. **Keep programs readable and reproducible.** Lifting is a pure function of the IL plus
   per-instruction lifting choices that are drawn inside the test case, so the reproducer is the
   program that ran, and shrunk reproducers are the most-inlined form unless a bug needs a statement.
6. **Feed the rest of oxpecker unchanged**: `HarnessClient.run` takes source text; `compare`,
   `findings`, the corpora, the probe sets from `9ed5a22`, and the inspector (through provenance).

---

## 2. Architecture

```
oxpecker/il/                      new package; nothing from oxpecker/ir is restored by default
  ops.py          operations, enums, Context, attributes          (Fuzzilli header: JsOperations.swift, Operation.swift, Context.swift)
  program.py      Variable, Instruction, Program, blocks, check()  (Instruction.swift, Code.swift, Blocks.swift, Semantics.swift)
  types.py        Type lattice, Signature                          (TypeSystem.swift, simplified)
  environment.py  Builtin/Key/Method registries from the capability table + hand-typed signatures
                                                                   (JavaScriptEnvironment.swift; data from rhino-capabilities.json)
  analysis.py     scopes, contexts, def-use, typer, Rhino model, verify   (Analyzer.swift, JSTyper.swift, plus Rhino's Optimizer/Block/IRFactory)
  builder.py      ProgramBuilder: emit, block context managers, pools   (ProgramBuilder.swift, without randomness)
  strategies.py   pick/maybe/statements/registry/swarm/lifting choices/programs()/evolved()
  generators/     one file per family; Fuzzilli translations carry the attribution header
  templates.py    program shapes
  lift.py         JavaScript lifter                                (JavaScriptLifter.swift, Expression.swift, JSExpressions.swift)
  mutate.py       mutators as strategies                           (InputMutator, OperationMutator, CodeGenMutator, SpliceMutator, CombineMutator)
tests/il/         unit tests per module + the guard tests (§10)
```

**Every module exposes its step as one shell-friendly call** (user requirement; G27): `Builder()`
for hand-written IL, `program.dump(x)`, `lift.js(x)`, `analysis.explain(x)` (the IL with types,
storage and the Rhino model's predictions), `strategies.generate(seed)` for a whole program,
`strategies.run_generator(name, seed)` for one generator after a small prefix,
`strategies.draw(strategy, seed)` to run any strategy exactly once, and
`harness.compare.run_and_compare(source)` to run through Rhino and compare. Each accepts
instructions, a `Program` or a `Builder`. The high-level tests use the same calls. Generators that
make no choices are plain functions `(b, *inputs)` callable directly.

Who does what:

- **Generators draw; the builder never draws.** A generator is `(draw, b: Builder) -> ...`; it asks
  the builder for *pools* (visible variables of a type, most recent first) and picks with `pick`.
- **The builder emits and keeps state**: the code so far, scopes, context, the typer, the Rhino
  model, open object literals and switch blocks. It is the only place that constructs instructions.
- **The analysis is recomputed, never stored on instructions** (proposal §3.2): `analyse(program)`
  gives scopes, types at every point, storage kinds, the Rhino predictions, and `verify` raises on
  anything a mutation could have broken.
- **The lifter is a pure function** of `(program, lifting_choices, options)`.
- **Mutators** are strategies `program -> st[program]`; lineages draw the base program and the
  steps in one test case (proposal §3.5); the fuzz loop (later) drives `fuzz_one_input`.

---

## 3. The IL

### 3.1 Instructions, variables, blocks, contexts — FuzzIL as it is

Taken over without change in meaning (`Instruction.swift:20-`, `Code.swift`, `Context.swift`):

- `Instruction(op, inouts, index)`: inputs, then outputs (visible in the enclosing scope), then
  inner outputs (visible only inside the block the instruction opens). `Variable` is an integer,
  program-local, numbered contiguously in definition order; `check()` enforces that, so a mutator
  that inserts code renumbers, as Fuzzilli's `adopt` does.
- Blocks are matched `Begin…`/`End…` instructions; a block group is `BeginIf … BeginElse … EndIf`.
  `isMatchingEnd` (Semantics.swift:62-) becomes a table on the op classes.
- `Context` is a flag set: `JAVASCRIPT, SUBROUTINE, GENERATOR_FUNCTION, METHOD, LOOP,
  OBJECT_LITERAL, SWITCH_BLOCK, SWITCH_CASE, WITH` (the last is ours: sloppy-only ops need it).
  Each op has `required_context` and `context_opened`; block starts carry
  `propagates_surrounding_context` or `resumes_surrounding_context` exactly as FuzzIL (switch cases
  resume the context from outside the switch; loops drop the switch flags and vice versa,
  `Analyzer.swift:180-204`).
- Op attributes: `mutable, call, block_start, block_end, jump, variadic, singular,
  propagates_context, resumes_context` (`Operation.swift:99-152`). `isInternal` and `isNop` are
  not needed: there are no internal ops and our minimiser is Hypothesis.

### 3.2 Variables: temporaries and declared variables

FuzzIL has one kind of variable and lets `Reassign` target any of them; its lifter decides
`const` versus `let` from the number of assignments (`JavaScriptLifter.swift:2882-2889`). For
Rhino the *storage* of a variable is the thing under test (critique §2), so the IL says it:

- **Temporaries** are outputs of expression operations. They are defined once, never assigned,
  and the lifter may inline them. This is what FuzzIL calls a variable.
- **Declared variables** have a storage kind and are the only assignable variables:
  - `Declare(kind: VAR | LET | CONST)` with one input, the initial value. It replaces FuzzIL's
    `Dup` (`JsOperations.swift:2096`): `var n = <v>`. A `CONST` output is not assignable.
  - parameters (inner outputs of function begins), catch variables, the loop variables of a
    four-part `for` header, `for-in`/`for-of` variables, destructured bindings, named variables
    (`CreateNamedVariable` with a `var | let | const | global` mode), and function names (JS
    allows `f = 1` after `function f() {}`; the deleted `direct_call_arity` shape needs it).
  - not assignable: `CONST` variables, the counter of `BeginRepeatLoop`, labels, `this`.
- Assignment ops, as FuzzIL's: `Reassign(target, value)`, `Update(op)(target, value)` for
  `x op= y` including `&&=`, `||=`, `??=` (FuzzIL's `BinaryOperator` has the logical operators, so
  `Update` covers the logical assignments Rhino supports), and `UnaryOperation` with
  `PRE_INC | PRE_DEC | POST_INC | POST_DEC`, which reassign their input and produce the
  expression value (`JsOperations.swift:1998-2018`).
- `verify` rejects an assignment to anything but a declared, non-const variable. The typer treats
  an assignment as a strong update (§5.2).

Storage kinds the analysis reports per declared variable (not stored in the IL; derived from the
op and its position): `top-var | top-let | top-const | local-var | local-let | local-const |
block-let | block-const | param | catch | loopvar | forin | function | named-global`. Rhino's
optimizer only types `local-var` and parameters of direct-call targets (§5.3).

**`let` and `const` are an axis of their own** (user, 2026-10-26: Rhino never implemented them
properly). The generation layer draws the kind wherever a variable is declared (`Declare`,
named variables, for-loop headers, `for-in`/`for-of` variables, the keyword of a lifted
temporary §3.6), places `let`/`const` in blocks and loop bodies so per-iteration bindings and
closures over them occur, expresses TDZ reads through a `CreateNamedVariable(LET)` that a
function defined earlier reads by name, and has a `declaration-kind` mutator (§9) that flips a
kind where legal (`const` only for a never-assigned variable). The analysis reports the kind and
the block it lives in so the inspector shows what Rhino was given.

### 3.3 The operation inventory for Rhino

The inventory is FuzzIL's `JsOperations.swift`, decided up front against the capability table.
Names and input/output layouts follow FuzzIL so the Swift source stays the reference; the
"line" column is the Swift definition.

**Kept as in FuzzIL** (line in `JsOperations.swift`):

| group | operations |
|---|---|
| literals | `LoadInteger` 70, `LoadBigInt` 83, `LoadFloat` 95, `LoadString` 106, `LoadBoolean` 119, `LoadUndefined` 130, `LoadNull` 138, `LoadRegExp` 386 |
| scope | `LoadThis` 146, `LoadArguments` 154 (context SUBROUTINE), `LoadNewTarget` 3046 (SUBROUTINE), `CreateNamedVariable` 214 (modes `global/var/let/const`; the `none` mode becomes `LoadBuiltin`, below) |
| object literal | `BeginObjectLiteral` 428, `ObjectLiteralAddProperty` 437, `…AddElement` 449, `…AddComputedProperty` 461, `…CopyProperties` 470 (object spread parses in Rhino), `…SetPrototype` 479, `Begin/EndObjectLiteralMethod` 489/513 (`isGenerator` kept, `isAsync` removed), `…ComputedMethod` 518/539, `…Getter` 544/559, `…ComputedGetter` 563/576, `…Setter` 581/596, `…ComputedSetter` 600/613, `EndObjectLiteral` 617 |
| arrays, strings | `CreateArray` 1010, `CreateIntArray` 1024, `CreateFloatArray` 1035, `CreateArrayWithSpread` 1046, `CreateTemplateString` 1063 |
| properties | `GetProperty` 1082, `SetProperty` 1099, `UpdateProperty` 1117, `DeleteProperty` 1136, `ConfigureProperty` 1180, `GetElement` 1195, `SetElement` 1212, `UpdateElement` 1229, `DeleteElement` 1248, `ConfigureElement` 1265, `GetComputedProperty` 1280, `SetComputedProperty` 1295, `UpdateComputedProperty` 1310, `DeleteComputedProperty` 1327, `ConfigureComputedProperty` 1342 |
| tests | `TypeOf` 1355, `Void` 1363, `TestInstanceOf` 1371, `TestIn` 1379 |
| functions | `Begin/EndPlainFunction` 1504/1507, `Begin/EndArrowFunction` 1527/1530, `Begin/EndGeneratorFunction` 1535/1544, `Directive` 1616 (`"use strict"` only, as FuzzIL), `Return` 1631, `Yield` 1647, `YieldEach` 1663 |
| calls | `CallFunction` 1682, `Construct` 1749, `CallMethod` 1799, `CallComputedMethod` 1902; the `…WithSpread` call forms are kept as ops but **off by default** (§3.3 note) |
| operators | `UnaryOperation` 2020, `BinaryOperation` 2054, `TernaryOperation` 2066, `Reassign` 2075, `Update` 2084, `Compare` 2120 |
| super (object-literal methods, context METHOD) | `CallSuperMethod` 2181, `GetSuperProperty` 2393, `SetSuperProperty` 2404, `SetComputedSuperProperty` 2415, `GetComputedSuperProperty` 2423, `UpdateSuperProperty` 2431 |
| control flow | `BeginIf` 2446 (`inverted`), `BeginElse` 2462, `EndIf` 2473, `BeginWhileLoopHeader/Body` 2522/2533, `EndWhileLoop` 2544, `BeginDoWhileLoopBody/Header` 2553/2564, `EndDoWhileLoop` 2575, `BeginForLoopInitializer/Condition/Afterthought/Body` 2615-2655, `EndForLoop` 2670, `ForLoop` 2739 (for-in/for-of, simple or destructuring header; no `isAsync`, no `using`), `BeginRepeatLoop/EndRepeatLoop` 2795/2814, `LoopBreak` 2822, `LoopContinue` 2832, `BeginTry/Catch/Finally` 2842-2860, `EndTryCatchFinally` 2868, `ThrowException` 2876, `BeginBlockStatement/End` 2902/2913, `BlockBreak` 2921, `BeginSwitch` 2982, `BeginSwitchCase` 2994, `BeginSwitchDefaultCase` 3007, `EndSwitchCase` 3018 (`fallsThrough`), `EndSwitch` 3030, `SwitchBreak` 3038, `BeginWith/EndWith` 2149/2159 |
| destructuring | `Destruct` 3770, `DestructAndReassign` 3791 with FuzzIL's `DestructuringPattern` 3571 minus the `superProperty/superElement/superComputedProperty/privateProperty` targets |
| code strings | `BeginCodeString/EndCodeString` 2885/2893: a nested program whose lifted source is the output string; the only way to express the text `eval` runs (§8.2) |

**Added for oxpecker:**

| op | meaning |
|---|---|
| `Declare(kind)` | §3.2; replaces `Dup` |
| `LoadBuiltin(builtin: Builtin)` | a global from the registry (§4); FuzzIL spells this `CreateNamedVariable(name, .none)` and looks the type up by string (`JSTyper.swift:2049-2057`); ours carries the registry entry so there is no string and the type travels with it |
| `Probe(label: ProbeLabel)` | no inputs; the lifter observes the declared variables visible there (D9 kept, see §8.3) |
| `LoadInteger.radix`, `LoadFloat.style` | typed spelling fields (brief §2.4): `Radix.DEC/HEX/OCT/BIN`, `FloatStyle.PLAIN/EXPONENT` |

**Dropped** (not expressible in Rhino, or Fuzzilli-internal): all class and private-member
operations (`JsOperations.swift:674-1002, 2217-2339`), `BeginConstructor` (a plain function with
`LoadNewTarget` covers it), all async ops and flags, `Await`, `LoadDisposableVariable` and the
`using` family, `BeginWorkerFunction`, bundles and modules (3054-3545), wasm (3071-3298, 3417),
`Print`, `Explore`, `Probe` (Fuzzilli's; ours has the name), `Fixup`, **`Eval`** (2133; the
brief's one rule), `Nop`, `CreateMap` (`Construct(LoadBuiltin(MAP), …)` says the same), `BindMethod`,
`BindFunction` (`CallMethod(Methods.FUNCTION_BIND)`).

**Call spread.** `CallFunctionWithSpread`, `ConstructWithSpread`, `CallMethodWithSpread`,
`CallComputedMethodWithSpread` crash Rhino's compiler in both modes today
(`tests/regressions/spread-in-call-crashes-both-modes.js`), so a program containing one yields
nothing else. They are supported like every other op; their generators carry a low default
weight (1), and `GenOptions.weights` can scale any generator or family, down to zero, from
configuration (user decision, 2026-10-26).

**No guards.** FuzzIL's `isGuarded` wraps an operation in `try { … } catch {}` so a program
continues past an exception (`JsOperations.swift:55-67`). Not here: a `catch` clause forces an
activation in Rhino (§5.3), which would move every guarded site off the optimizer path, and the
user does not want everything under a try/catch. An operation that throws ends the program with
a `throw` outcome that both modes must agree on, which is a test in itself. What keeps programs
running instead: `verify`'s type requirements, pools that prefer exact types, and a standing
measurement (§10 item 10) of how many programs run to the end. A generator that wants to
continue past a throw builds a real `try`/`catch` whose catch body records the error into a
declared variable, as the old `try_catch` and the call-site shapes did; that is structure the
mutators can see. `isReceiverOptional` (`o?.p`) and `isCallOptional` (`f?.()`) stay as fields:
Rhino parses `?.` and they are language features, not wrappers.

### 3.4 Enums

Defined once in `ops.py`, taken from `JsOperations.swift`; only the lifter knows the tokens:

```python
class UnaryOperator(Enum):   PRE_INC, PRE_DEC, POST_INC, POST_DEC, LOGICAL_NOT, BITWISE_NOT, PLUS, MINUS   # 1998
class BinaryOperator(Enum):  ADD, SUB, MUL, DIV, MOD, BIT_AND, BIT_OR, LOGIC_AND, LOGIC_OR, XOR, LSHIFT, RSHIFT, EXP, U_RSHIFT, NULL_COALESCE  # 2033
class Comparator(Enum):      EQUAL, STRICT_EQUAL, NOT_EQUAL, STRICT_NOT_EQUAL, LESS, LESS_EQUAL, GREATER, GREATER_EQUAL    # 2101
class RegExpFlag(Flag):      IGNORE_CASE, GLOBAL, MULTILINE, DOT_ALL, UNICODE, STICKY, HAS_INDICES   # 281; UNICODE_SETS dropped (Rhino: no `v`)
class PropertyFlag(Flag):    WRITABLE, CONFIGURABLE, ENUMERABLE                                       # 1150
class PropertyType(Enum):    VALUE, GETTER, SETTER, GETTER_SETTER                                     # 1171
class DeclarationKind(Enum): VAR, LET, CONST
class NamedVariableMode(Enum): GLOBAL, VAR, LET, CONST                                                # 181, minus NONE
class ForEachKind(Enum):     OF, IN                                                                   # 2672
class Radix(Enum):           DEC, HEX, OCT, BIN
class FloatStyle(Enum):      PLAIN, EXPONENT
```

Groups are data: `ARITHMETIC = {ADD, SUB, MUL, DIV, MOD, EXP}`, `BITWISE = {BIT_AND, BIT_OR,
XOR, LSHIFT, RSHIFT, U_RSHIFT}`, `LOGICAL = {LOGIC_AND, LOGIC_OR, NULL_COALESCE}`,
`INCREMENTS = {PRE_INC, …}`, `RELATIONAL`, `EQUALITY`. The operation mutator changes an operator
within its group; Rhino's optimizer treats these groups differently (§5.3), so the groups are
also what compiler-targeted generators draw from.

### 3.5 Literals hold values

`LoadInteger(value: int, radix)`, `LoadFloat(value: float, style)`, `LoadBigInt(value: int)`,
`LoadString(value: str)` (the characters; the lifter quotes and escapes), `LoadBoolean(bool)`,
`LoadRegExp(pattern: str, flags: RegExpFlag)`. JavaScript has no negative literals: `-1` is
`UnaryOperation(MINUS, LoadInteger(1))`, `-0` is `UnaryOperation(MINUS, LoadInteger(0))`, `1/3` is
`BinaryOperation(DIV, …)`, `NaN` and `Infinity` are `LoadBuiltin`. The old pools' spellings
(`"0x7fffffff"`, `"1e21"`) are `Radix.HEX` and `FloatStyle.EXPONENT` on the value. The lifter
rejects a `LoadInteger` whose value has no exact JS representation (|v| > 2^53) unless it is
a `LoadBigInt`.

A regexp pattern is a string value in the IL, as in FuzzIL. It is built by a value maker from
a fixed pattern table (Fuzzilli's `interestingRegExps`, `JavaScriptEnvironment.swift:76-303`,
filtered to what Rhino's regexp engine accepts) plus the same small combinators Fuzzilli uses
(`ProgramBuilder.swift:452-497`: concatenate, quantify, group, class), never from spelled code.

### 3.6 Provenance and lifting choices

Every instruction carries two metadata fields that analysis, `check` and signatures ignore:

- `gen: str`, `origin: int` — which generator emitted it and in which invocation (D10). The
  lifter's `// [generator]` comments and the inspector's generator spans come from these. A
  mutation step records `step: int` on the instructions it added or changed (critique §3.2).
- `lift: Lift | None` (D16, extended per the user's review): `Lift.INLINE` lets the lifter
  inline the value, `Lift.VAR | LET | CONST` lifts it as its own declared temporary with that
  keyword. `None` is drawn after generation by `lifting_choices()` from the fixed list
  `(INLINE, VAR, LET, CONST)`, shrinking towards `INLINE`; a value the policy cannot inline
  (two uses, a construct) with `INLINE` drawn is lifted as `VAR`. Generators pin `INLINE` on
  fixed-form constants and pin both copies of a "same body twice" shape. The keyword is per
  instruction, not per program, because `var` versus `let`/`const` temporaries take different
  Rhino paths and a bug may need one kept `const` among `var`s.

---

## 4. Environment: the registries

FuzzIL types its builtins in a 5,000-line hand-written environment (`JavaScriptEnvironment.swift`:
`ObjectGroup`s with `properties: [String: ILType]` and `methods: [String: [Signature]]`,
`registerBuiltin(name, ofType:)`, lines 922-1033). oxpecker already probes Rhino for the same
facts (`oxpecker/environment/probe.js` → `rhino-capabilities.json`: 74 globals, 57 constructors,
42 prototypes with method arities, 71 static tables, 51 syntax probes). The registries are
generated from that table at import time, with a hand-written overlay for what the probe cannot
see (result types, parameter types for the methods generators care about):

```python
@dataclass(frozen=True)
class Builtin:
    name: str                          # the global binding; spelled by the lifter only
    type: Type                         # what LoadBuiltin gives
    constructs: Type | None = None     # type of `new B(...)`; None if not a constructor
    calls: Type | None = None          # type of `B(...)` if callable
    signature: Signature | None = None

class Builtins:                        # one attribute per global in the table, minus EXCLUDED_GLOBALS
    MAP = Builtin("Map", Type.FUNCTION, constructs=Type.object("Map"), signature=...)
    INT32_ARRAY = Builtin("Int32Array", Type.FUNCTION, constructs=Type.object("TypedArray"))
    MATH = Builtin("Math", Type.object("Math"))
    NAN = Builtin("NaN", Type.NUMBER)
    J = Builtin("J", Type.object("J"))           # the harness's Java probe object (JavaProbe.java)
    PAUSE = Builtin("__pause", Type.FUNCTION, calls=Type.ANY)   # continuations (Host.java:83)
    EVAL = Builtin("eval", Type.FUNCTION, calls=Type.ANY)
TYPED_ARRAY_CONSTRUCTORS = (...)       # the twelve present in the table
ERROR_CONSTRUCTORS = (...)

@dataclass(frozen=True)
class Key:                             # a property name with a class, so generators ask for kinds of keys
    name: str
    kind: KeyKind                      # INDEX, LENGTH, PROTO, WELL_KNOWN, ORDINARY
class Keys:  A, B, C, X, Y, LENGTH, PROTO, CONSTRUCTOR, TO_STRING, VALUE_OF, GET, SET, HAS, ...; INDEX_0, INDEX_1, INDEX_10
INTERESTING_KEYS: tuple[Key, ...]      # Fuzzilli's custom names (a..f, "!", "42") + ours

@dataclass(frozen=True)
class Method:                          # a method on a receiver group, from the probe's prototype table
    receiver: str                      # group name ("Array", "String", "Map", "Math", "J", ...)
    name: str
    arity: int
    params: tuple[Type, ...] = ()      # hand-typed overlay where it matters
    returns: Type = Type.ANY
class Methods:  ARRAY_PUSH, ARRAY_MAP, STRING_CHAR_CODE_AT, MATH_IMUL, REFLECT_GET, J_ECHO, J_TAKES_INT, ...
```

Rules: the generators never name a builtin, key or method by string; the typer reads
`constructs`/`calls`/`returns` instead of keeping its own table; `oxpecker probe` refreshes the
table and the overlay is diffed against it by a test (a method in the overlay that the probe no
longer sees fails the test). Well-known symbols are `Methods`-style entries on `Builtins.SYMBOL`
(`Symbol.iterator` is `GetProperty(LoadBuiltin(SYMBOL), Keys.ITERATOR)`).

Java interop (`JavaProbe.java`): `Builtins.J` with a `Methods.J_*` entry per public method,
including the overloads of `over` and the typed `takes*` parameters; return values typed from the
Java signature (`long` beyond 2^53 is `Type.NUMBER`; `BigInteger` is `Type.BIGINT`; `List`/`Map`
are `Type.object("JavaList")` …). Every finding at this boundary is a `java-types` verdict in
`compare.py`, never discarded.

Value pools are data in `environment.py` too: `INTERESTING_INTEGERS` (Fuzzilli's list,
`JavaScriptEnvironment.swift:30-53`, plus Rhino's int32 and 2^53 boundaries from the old
`values.py`), `INTERESTING_FLOATS`, `INTERESTING_STRINGS` (characters only: `""`, `"0"`, `"-0"`,
`"abc"`, surrogate halves, `"length"`, …), `JS_TYPE_NAMES` (strings that `typeof` returns; they
are string *values*, not labels, and are compared with a `LoadString` as Fuzzilli does).

---

## 5. Types and analysis

### 5.1 Types

FuzzIL's `ILType` is a pair of bitsets (definite, possible) plus an extension carrying a group,
property and method sets, a signature, an element type (`TypeSystem.swift:85-99, 1534-1546`).
The possible set gives unions; the definite set gives "merged" types such as `jsString = string +
iterable + object(String)`. We keep the shape and drop the merges:

```python
class Kind(Flag):  UNDEFINED, NULL, BOOLEAN, INTEGER, FLOAT, BIGINT, STRING, SYMBOL, OBJECT, FUNCTION
@dataclass(frozen=True)
class Type:
    possible: Kind                     # union of kinds it may be; ANY = all kinds
    group: str | None = None           # object group ("Array", "Map", "TypedArray", "RegExp", "J", "_Object3")
    signature: Signature | None = None # for FUNCTION: params and return
    element: Type | None = None        # for iterables
    props: frozenset[Key] = ...        # known own keys (object literals, "length", …)
    def Is(self, other) / MayBe(self, other) / join(self, other)   # subsumes, intersects, union
NUMBER = INTEGER | FLOAT;  NULLISH = UNDEFINED | NULL;  PRIMITIVE = ...
```

`NULL` is its own kind (FuzzIL folds it into `undefined`, `JSTyper.swift:2040`; property access on
`null` and `undefined` throw the same way, but Rhino converts them differently at the Java
boundary). `INTEGER` versus `FLOAT` matters for the Java boundary and for Rhino's int
representation, so arithmetic results keep FuzzIL's split (`JSTyper.swift:1961-1988`): `ADD` →
`PRIMITIVE` (+`BIGINT` if an input may be), `SUB/MUL/DIV/MOD/EXP` → `NUMBER`, bitwise and
shifts → `INTEGER`, logical operators → the join of both inputs, `Compare`/`TestIn`/
`TestInstanceOf` → `BOOLEAN`, `TypeOf` → `STRING`. `Signature` is FuzzIL's: a parameter list of
`plain | opt | rest` parameters and a return type (`TypeSystem.swift:2610-2770`).

### 5.2 The typer

A single forward pass over the instructions, driven per instruction, as `JSTyper` is
(`JSTyper.swift:764-`), with a stack of branch states: a strong update inside a block, a union
at the join (`computeVariableTypes`, 3312-3345), an empty alternative for `if` without `else`,
for `switch` without `default`, and for every loop body (1674-1735). Function bodies are
analysed as "if called" (3219-3229). Return types are collected at the function end from every
`Return` plus `UNDEFINED` when a path falls off the end (1816-1923); inside its own body a
function's return type is `ANY`. Parameter types are declared by the generator that builds the
function (`setParameterTypesForNextSubroutine`, `ProgramBuilder.swift:7888`) and default to `ANY`.

Two changes to FuzzIL's pass, both from what the deleted D17 analysis found necessary:

1. **Loop bodies run twice.** FuzzIL reads loop-variable types at entry and never widens them
   for back-edge assignments (`JSTyper.swift:2614-2636`), so `var n = 1; while (…) { n = n + "" }`
   types `n` as INTEGER inside the body. The second pass starts from the join of entry and
   end-of-body states. Not a full fixpoint: two passes give the join of "zero or one iteration
   before", which is what a generator deciding on `n`'s type in the body needs.
2. **`try`/`catch`/`finally` join.** FuzzIL treats their bodies as unconditional (1880). A catch
   is entered from any point of the try, so the catch's entry state is the join of every state
   reached in the try; `finally` joins the try-exit and catch-exit states.

Everything else is FuzzIL's: `SetProperty` adds the key to the receiver variable's type
(2395-2402), object literals accumulate a program-local group (`_Object<n>`, 57-338), method
calls return the registry's result type, `GetElement` and computed access return `ANY`.

### 5.3 The Rhino model

The analysis predicts, from Rhino's own rules, which compiled-mode path a function and each of
its variables take. Generators that target the optimizer use these predictions to build the
shape they want; the activation mutator flips them; the inspector shows them (critique §3.3).
All rules are from `b95f75d`:

**A function needs an activation** (its locals are never optimizer-typed, `Optimizer.java:30-33`)
when its body: contains any nested function, arrows included (`IRFactory.java:1586-1588`:
"Functions containing other functions require activation objects"); contains a `catch` clause
(`IRFactory.java:2035`, inside the per-catch loop); uses `with` (2077); calls `eval` by that name
(`Parser.java:4852-4857`); reads `arguments` (4836-4841); contains `yield` (2432, generators);
uses `super` (`IRFactory.java:205`). Our predicate is computed from the IL: nested
`Begin*Function`, `BeginCatch`, `BeginWith`, `CallFunction(LoadBuiltin(EVAL))`, `LoadArguments`,
`Yield`, any `*Super*` op, in the function's own body.

**A local is a number variable** (`Block.java:65-110`, `Optimizer.java:109-`): the function has no
activation; the variable is a `var` local, not a parameter; every assignment's expression has
Rhino type `NumberType` under `findExpressionType` (`Block.java`): number literals; `SUB, MUL,
DIV, MOD, EXP`, the bitwise and shift operators, unary `-`, `+`, `~`, `++`, `--` are `NumberType`
whatever their operands (this is the BigInt bug: `-1n` is "number"); `ADD` is `NumberType` only
when both sides are; `?:`, `&&`, `||` join; a read of a number variable is `NumberType`; calls,
property reads, names, strings, `undefined`, BigInt literals, arrays, objects, templates are
`AnyType`. The var type is the join over all assignments, iterated to a fixpoint because an
assignment may read the variable (`x += 1`). The analysis exposes `rhino_expr_type(instr)` and
`number_var(v)`; a generator wanting the optimizer's double path keeps every assignment
`NumberType`; the "flip" mutators assign something `AnyType` once.

**A direct call** (`Codegen.java:383-400`, `OptTransformer.java:42-101`) links a call site to a
function when: the callee is a named *function statement at script level*; the call passes
exactly `paramCount` arguments (≤ 32); the callee needs no activation; the call is by that name
(`f(...)`, or `o.f(...)` with the same property name). Direct-call targets get the
"parameter in number context" treatment (`Optimizer.java:52-86`): parameters are passed as a
(Object, double) pair. The analysis reports `direct_call_target(f)` and `direct_call_site(i)`.

**Observed representations** (`int` vs `double`): not predicted statically; measured through
`J.typeOf` in the Java interop generators and the `java-types` verdict.

### 5.4 `check` and `verify`

`check(program)` is FuzzIL's static validity (`Code.swift:215-`): contiguous variable
numbering, inputs defined and visible, required context satisfied, blocks balanced and matched,
for-loop header consistency. `verify(program)` adds oxpecker's requirements (D7 kept): only
declared non-const variables are assigned; `Return` only in a subroutine, `Yield` only in a
generator function, `break`/`continue` targets exist; a `try` has a handler; spread calls only
when enabled; and the type requirements under the types at the point of use: a callee or
constructor `MayBe(FUNCTION)`, no property access on a definitely `NULLISH` receiver without a
guard or optional flag. Anything merely likely to throw is allowed: both modes must throw alike.

---

## 6. The builder

`Builder` is FuzzIL's `ProgramBuilder` without randomness: it emits, tracks scopes, context, the
typer and the Rhino model, and answers queries. It never calls `draw`.

```python
class Builder:
    # emitting
    def emit(self, op, inputs=()) -> Instruction          # allocates outputs, runs the analysers, checks context (ProgramBuilder.swift:2868-2902, 7870-7903)
    def load_int(self, v, radix=Radix.DEC) -> Var; load_float; load_bigint; load_string; load_bool; load_undefined; load_null; load_regexp
    def builtin(self, b: Builtin) -> Var; this(); arguments(); new_target()
    def declare(self, value, kind=DeclarationKind.VAR) -> Var
    def unary(op, x); binary(x, y, op); compare(x, y, cmp); ternary(c, a, b); typeof(x); void(x); test_in(k, o); test_instance_of(x, c)
    def reassign(target, value); update(target, value, op)
    def get_property(o, key: Key, optional=False) -> Var; set_property(o, key, v); update_property; delete_property; configure_property(o, key, flags, PropertyType, *fns)
    def get_element / set_element / ... (index: int);  get_computed / set_computed / ... (key: Var)
    def call_function(f, args, optional=False) -> Var; construct(c, args); call_method(o, m: Method, args, ...); call_computed_method(o, key, args)
    def create_array(elems, spreads=None) -> Var; create_template(parts: tuple[str,...], values) -> Var
    def destruct(source, pattern) -> tuple[Var, ...]; destruct_and_reassign(source, pattern, targets)
    def do_return(v=None); yield_(v=None) -> Var; yield_each(v); throw(v); directive_strict()
    def probe(label: ProbeLabel)
    # blocks (context managers; every Begin has its End emitted on exit)
    with b.plain_function(params: Parameters, name=False, strict=False) as (f, args):   # args are declared variables
    with b.arrow_function(params) as (f, args):  with b.generator_function(params) as (f, args):
    with b.object_literal() as obj:      obj.add_property(key, v); obj.add_element(i, v); obj.add_computed(k, v); obj.set_prototype(p); obj.copy_properties(o)
                                         with obj.method(key, params, generator=False) as (this, args): ...; obj.getter(key) as this; obj.setter(key) as (this, v); computed variants
    with b.if_(cond, inverted=False) as label: ...;  with b.else_(): ...
    with b.while_loop() as loop:  cond = ...; with loop.body(cond) as label: ...
    with b.do_while_loop() as loop: with loop.body() as label: ...; loop.condition(cond)
    with b.for_loop(initial_values) as loop:  (i,) = loop.condition_vars ... loop.condition(cond); loop.afterthought_vars ...; with loop.body() as (vars, label): ...
    with b.for_each(ForEachKind, iterable, pattern=None) as (vars, label): ...
    with b.repeat(n) as (i, label): ...
    with b.try_() as t: ...; with t.catch() as e: ...; with t.finally_(): ...
    with b.switch(v) as sw: with sw.case(value, falls_through=False): ...; with sw.default(): ...
    with b.block() as label: ...;  with b.with_(obj): ...;  with b.code_string() as src: ...   # src.result is the string variable
    b.break_(label=None); b.continue_(label=None); b.block_break(label); b.switch_break()
    # queries (pools are most-recent-first; the generator picks)
    def visible(self, *, assignable=None) -> list[Var]
    def pool(self, type: Type) -> Pool                     # Pool.exact, Pool.maybe, Pool.all (each a list, most recent first)
    def type_of(v) -> Type; def rhino(self) -> RhinoModel   # activation(f), number_var(v), direct_call_target(f)
    def context -> Context; def strict -> bool; def options -> GenOptions
```

Three things from FuzzIL's builder that we keep because they decide program shape:

- **Hidden variables** (`ProgramBuilder.swift:1444-1503`): a function's own variable is hidden
  inside its body (no trivial recursion unless a generator wants it), labels are hidden inside
  subroutines, object literals and switch blocks; `pool()` never returns hidden variables.
- **Scope ordering** (7916-7957): outputs go to the enclosing scope, inner outputs to the new one.
- **Recency.** FuzzIL's `findVariable` (1208-1249) prefers the last instruction's outputs with
  p=0.15 and an inner scope with a geometric bias of 1.25, then picks uniformly. Our pools are
  ordered most-recent-first and the generator indexes into them, so the shrinker pulls references
  towards recent values (proposal §3.8 rule 5) and the distribution is the strategy's.

What we do not take: `build(n:)`, `complete`, `assembleSyntheticGenerator`, the `ContextGraph`,
`buildPrefix`/`buildValues`, `findOrGenerateType`, splicing inside the builder and `adopt`.
Code generation is a strategy (§7); splicing and adoption are the splice mutator's (§9).

---

## 7. Generators as strategies

### 7.1 What a generator is

```python
@generator("binary_operation", family="operations", weight=40, inputs=(Type.ANY, Type.ANY),
           source="CodeGenerators.swift:2467")
@st.composite
def binary_operation(draw, b: Builder, lhs: Var, rhs: Var) -> Var:
    """`lhs op rhs` for a drawn binary operator."""
    return b.binary(lhs, rhs, draw(st.sampled_from(BinaryOperator)))
```

`@generator` only registers (D24 kept): name, family, weight, `context` (default
`Context.JAVASCRIPT`), `inputs` (types; the scheduler draws them from `b.pool`), `produces`,
`toggle` (`generators`, `destructuring`, `continuations`, …), `sloppy_only`, and `source` (the
Fuzzilli definition it follows, for the attribution and for review). It wraps the strategy in
one more composite that stamps `gen`/`origin` on what it emitted. A generator may be drawn by
another generator or tested with `@given` on its own.

Inputs follow Fuzzilli's modes (`CodeGenerator.swift:187-241`): `inputs=` is *loose* (the
scheduler draws from `pool(type).exact`, falling back to `maybe` then `all`), `required=` is
*strict* (the generator is skipped — falls back to the simplest generator — if `pool(type).exact`
is empty). Fuzzilli's `randomVariable(forUseAs:)` picks an exact match with p=0.8
(`ProgramBuilder.swift:1129, 1145-1171`); ours draws one boolean `maybe(0.8)` unconditionally
(rule 3: the draw must exist whether or not the exact pool is empty) and uses it only when it can.

### 7.2 Scheduling: nested blocks, per-context pools (the first open question)

Fuzzilli splits block-building generators into `Begin`/`End` stubs with context requirements and
`provides`, pushes the tail stubs on a `scheduled` stack, runs `buildRecursive` between them,
and when a drawn generator's context is not open, searches the `ContextGraph` for a chain of
generators that opens it and nests them (`ProgramBuilder.swift:2440-2766`). That machinery
exists so a flat, imperative builder with a single weighted list can produce nested structure.

Decision (G6): **no stubs, no context graph.** A generator that builds a block is one Python
function using the builder's context managers, and the code inside the block comes from
`draw(b.statements(...))`. That is what the proposal's `code(ctx)` recursion already was, and it
is the form Hypothesis shrinks well: every nested statement list is an `st.lists` span inside
the span of the generator that opened the block, so deleting a block deletes its body, and
deleting a body statement is one span.

**The trade-off, spelled out.** What Fuzzilli's stubs and context graph buy, and what each costs
or gains here:

| property | Fuzzilli (stubs + graph) | here (nested blocks) |
|---|---|---|
| nesting | the engine opens whatever context a drawn generator needs (`assembleSyntheticGenerator`), so a `super.x` generator never knows about object literals | a generator with a context requirement is either drawn inside that context or **scaffolds it itself**: `super_property` outside a method opens `with b.object_literal() as o, o.method(…)` around its one instruction. The scaffold is fixed code, so it costs the shrinker nothing (rule 12) and is deleted with the generator's span |
| frequency of rare contexts | one weighted list; a METHOD-context generator gets its full weight anywhere | with scaffolding, the same: its weight applies in any statement context. Without scaffolding its frequency would be the product of the opening probabilities, which is why scaffolding is required for every context-requiring generator |
| code between Begin and End | the engine's `buildRecursive` with an even budget split per yield point | the generator calls `draw(b.nested())` where it wants code; forgetting it gives an empty body, which the structure test for that generator catches (an object-literal generator must draw members; a function generator must draw a body) |
| meaning of a choice across contexts (rule 3) | one list; index k is the same generator everywhere | **one fixed list for all statement contexts** (javascript, subroutine, generator function, method, loop, switch case); a generator whose requirement the current context does not satisfy and that cannot scaffold (`return`, `yield`, `break`, `continue`, `switch_break`, `arguments`) falls back to the first applicable generator. Only **members** (object literal) and **cases** (switch block) have their own lists, because nothing in them is a statement |
| shrinking a block away but keeping its body | the minimizer's `BlockReducer` | Hypothesis's `pass_to_descendant` replaces a span by one of its descendant spans. With one global statement list, replacing a function generator's span by its body's `statements()` span re-reads the same indexes as the same generators: the block is removed, the body stays. That is the reason for the single list; per-context lists would re-mean the indexes and the shrink would fail |
| the codegen mutator | `build(n:)` at a point | `statements()` at a point with the point's context; same list |
| input feasibility (`createRequiredInputVariables`, `findGeneratorSequence`) | a search over generators' `produces` | value makers: `b.variable(t)` draws the maker for `t` when the pool is empty |
| what is lost | — | Fuzzilli's budget accounting (instructions generated per yield point); ours is statement counts per `nested()` call, which is coarser but what `st.lists` shrinks |

So: `POOL_STATEMENTS` (one fixed, ordered list, weights as repetition, simplest-first families),
`POOL_MEMBERS`, `POOL_CASES`. A toggle that is off, a sloppy-only generator in strict code, or an
inapplicable context-requiring generator falls back to the first applicable entry, never to a
re-numbered list. Swarm selection (D23) excludes by boolean per generator over the same fixed
list. The six context-requiring generators that cannot scaffold are the only ones with a
context-dependent fallback.

### 7.3 `statements()` and `nested()`

```python
@st.composite
def statements(draw, b, lo=None, hi=None):        # one st.lists draw; one span per statement (rule 1)
    gens = pools(b.options).for_context(b.context)         # the statement list, or members / cases (§7.2)
    def one(draw):
        g = gens[draw(st.integers(0, len(gens) - 1))]      # fixed list, pick by index
        if not g.applicable(b): g = fallback(gens, b)      # first applicable entry; a scaffolding generator is always applicable
        inputs = [draw(b.variable(t)) for t in g.inputs]    # loose inputs
        return draw(g.strategy(b, *inputs))
    draw(st.lists(st.composite(one)(), min_size=lo or b.options.min_statements, max_size=hi or b.options.max_statements))
```

`b.nested(n=…)` is `statements(0, n)` with the depth budget decremented; generators call it where
Fuzzilli calls `buildRecursive`. Depth is bounded by `options.max_depth`; at depth 0 only
non-block generators are applicable.

### 7.4 Value makers and the prefix

Fuzzilli's "value generators" (`useInPrefix`, 21 of them, `CodeGenerators.swift:271-280`) are
plain generators that can run with no visible variables and produce a statically typed value;
`buildPrefix` runs them until 10–15 variables exist. Ours: `@value_maker(Type.X)` marks a
generator as the fallback for `b.variable(Type.X)` when the pool is empty (the old `pick_or_make`,
D25), and a program's prefix is `draw(b.values(n))`, an `st.lists` over the value-maker pool.
Value makers build structure (brief §2.6): `a_map` constructs `Map` from an array of pairs,
`a_function` is a real `plain_function` block with a generated body, `an_object` is an object
literal with drawn members.

### 7.5 Rules applied

The proposal's §3.8 rules are in the plumbing, not in generator code (D22 kept): `st.lists` for
every repetition; `pick` for every context-dependent pool; fixed pools simplest-first (the family
order: values, operations, properties, containers, calls, control, compiler-targeted, java,
continuations, exotic, api); visible variables most-recent-first; `maybe()` shrinks to `False`;
every generator invocation is its own span; templates share one statement floor (D28); parts a
generator does not need for its point are drawn, not fixed (rule 13).

### 7.6 Translating Fuzzilli's generators

The inventory (182 generators in `CodeGenerators.swift`) sorts into:

- **translate as is — 97**: the value generators (integer, bigint, float, string, boolean,
  undefined, null, array, int array, float array, holey array, typed array, regexp, trivial
  function, object constructor function, builtin instance, iterator object), `this`/`arguments`/
  `new.target`, plain/strict/arrow/generator functions, return, yield, yield-each, object-literal
  members (property, element, computed, copy-properties, prototype, method, computed method,
  getter, setter, computed getter/setter), every property/element/computed get/set/update/
  delete/configure, type test, void, instanceof, in, method call, computed method call, function
  call, constructor call, unbound call/apply/bind, function bind, unary, binary, ternary, update,
  reassignment, chained reassignments, destruct (array/object, declare/reassign), comparison,
  the six super generators, if/else, compare-with-if-else, switch (block, case, default, break),
  while, do-while, simple for, complex for, for-in, for-of (plain, array-destructuring,
  object-destructuring), repeat loop, break, continue, try/catch/finally in three forms, throw,
  conditional throw, block statement, block break, with, eval (code string), well-known symbol
  load/store, prototype access/overwrite, callback property, call with different `this`,
  construct with different `new.target`, proxy, promise, length change, element kind change,
  number computation, fast-to-slow properties, object hierarchy, homomorphic objects, API
  constructor/method/function calls, template string, concatenated string, string normalize,
  builtin reference, named variable, builtin overwrite, object builder function, object integrity
  level, builtin prototype call, typed array from buffer, data view, typed array last index.
- **adapt — 9**: object-literal method generators (drop the 5 % async flag from
  `randomAsyncAndGeneratorFlags`, line 256); the four `…WithSpread` call generators (array spread
  stays, call spread off by default); `ImitationGenerator` (drop its class branch); `ProxyGenerator`
  (handler traps from a `Keys` tuple: Fuzzilli's list has `call`, which is not a trap);
  `BuiltinObjectPrototypeCallGenerator` (prototype list from the registry; no SharedArrayBuffer).
- **drop — 76**: everything class, private, async, `using`/disposable, bundle/module, wasm,
  Temporal, Intl, `Uint8Array` hex/base64, `Iterator.zip`, resizable/shared buffers, time zones.

Each translated file starts with the Apache-2.0 header naming the Swift source, and each
generator's `source=` names the line. The weights are Fuzzilli's (`CodeGeneratorWeights.swift`)
scaled to oxpecker's families; they decide list *frequency* (a generator appears `weight` times
in the fixed pool, so index choice stays uniform and context-independent).

### 7.7 oxpecker's own generators

Designed against the same builder; the deleted string versions are read for the intended shape
only. Each entry names the ops its structure test asserts (brief part 3, item 3).

**compiler-targeted** (the Rhino model, §5.3, drives these):

| generator | shape | must emit |
|---|---|---|
| `numeric_local_type_flip` | a plain function whose locals are all `number_var` (every assignment NumberType: arithmetic over literals, parameters and each other), then one local assigned an `AnyType` value late (a string, object, BigInt, `undefined`, a call result), then returns them; called with ints, then doubles, then a perturbing value, each call inside a generated top-level `try`/`catch` whose catch records the error name (top-level catches do not touch the function's activation) | `BeginPlainFunction`, `Declare(VAR)`, ≥1 `Reassign`/`Update`/increment whose RHS is NumberType, `Return`, `CallFunction` ×3 |
| `activation_pair` | the same generated numeric body twice: once plain, once with one activation trigger inserted (`LoadArguments`, a nested arrow, a `try`/`catch`, `with`, direct `eval` in sloppy mode); both called with the same arguments and the results collected | two `BeginPlainFunction` with identical bodies (`Builder.replay`, D22), one activation op |
| `direct_call_arity` | a named top-level function statement called with exact arity (a direct call), fewer, more, and none; optionally redefined by `Reassign` of the function name, then called again | named `BeginPlainFunction`, four `CallFunction`, optional `Reassign` |
| `callsite_relink` | one function with a single operator over its parameters, called in a loop with one type, then with another (int → double → string → array → object with `valueOf`) | `BeginRepeatLoop`, `CallFunction`, a `BinaryOperation`/`GetProperty` in the body |
| `property_site_shape_change` | a function reading `obj.k`, warmed in a loop, then the object loses the key, gains an accessor, changes prototype, or is replaced by an array | `GetProperty` in a function, `BeginRepeatLoop`, one of `DeleteProperty`/`ConfigureProperty`/`SetProperty(PROTO)`/`Reassign` |
| `string_concat_chain` | `s += x` in a bounded loop (ConsString), then a comparison, indexing, `charCodeAt`, `Number(s)` | `Update(ADD)` on a declared string in a loop, then one of `Compare`/`GetElement`/`CallMethod(STRING_CHAR_CODE_AT)`/`CallFunction(NUMBER)` |
| `int32_boundary_arith` | a number-var local initialised at an int32 boundary, one arithmetic step with a parameter, returned together with `x | 0`, `x >>> 0`, `x === (x | 0)` | `Declare(VAR)` of a boundary `LoadInteger`, `BinaryOperation(BIT_OR/U_RSHIFT)`, `Compare(STRICT_EQUAL)` |
| `super_in_object_literal` | an object literal with `__proto__` and a method using `super.p`, `++super.p`, `super[k] += 1`, `super.q = v`; the key may be an object whose `toString` swaps the prototype | `ObjectLiteralSetPrototype`, `BeginObjectLiteralMethod`, a `*SuperProperty` op |
| `const_site` | operations on literal constants only (`x + 1`, `x * 0`, `x / 0`, `-x`, `x | 0`, `typeof x`) | `BinaryOperation`/`UnaryOperation`/`TypeOf` whose inputs are all literal loads with `lift=INLINE` pinned |
| `negative_zero` | `n * 0`, `~n`, `-n` on top-level and local number vars, observed with `Object.is(v, -0)` | `UnaryOperation(MINUS, LoadInteger(0))`, `CallMethod(OBJECT_IS)` |

**java-interop** (`Builtins.J`, `Methods.J_*`):

| generator | shape | must emit |
|---|---|---|
| `java_typed_call` | `J.takesInt(x)` … `J.takesBigInteger(x)` with an edge number or a drawn value, inside a generated top-level `try`/`catch` that records the conversion error (recorded, not fatal) | `CallMethod(J_TAKES_*)` inside `BeginTry` |
| `java_echo` | `J.echo(x)`, `J.typeOf(x)`, `J.echo(x) === x` | `CallMethod(J_ECHO / J_TYPE_OF)` |
| `java_echo_arith` | the result of an arithmetic expression handed to `J.typeOf`/`J.echo` | `BinaryOperation`/`UnaryOperation` feeding `CallMethod(J_TYPE_OF)` |
| `java_overload` | `J.over(x)` | `CallMethod(J_OVER)` |
| `java_returned_value` | `J.aLong()`, `J.aFloat()`, `J.negZero()`, `J.bigInteger()`, `J.nothing()`, … used in arithmetic, comparison, concatenation | `CallMethod(J_A_*)` then an operator on the result |
| `java_array` | `J.intArray(n)` / `doubleArray` / `objectArray` read and written, `.length`, `J.sum(a)`, a JS array passed to `J.sum` | `CallMethod(J_INT_ARRAY …)`, `SetElement`, `GetProperty(LENGTH)` |
| `java_collections` | `J.list()`, `J.map()`: `size()`, `get()`, indexing, `for-in` | `CallMethod(J_LIST/J_MAP)`, `ForLoop(IN)` |
| `java_numeric_pipeline` | a number-var function whose intermediate values go to `J.typeOf` at several points | `BeginPlainFunction`, ≥2 `CallMethod(J_TYPE_OF)` on number vars |

**continuations** (toggle `continuations`; `Builtins.PAUSE`, `Builtins.CONTINUATION`):
`pause_in_function`, `pause_in_loop`, `pause_in_try_finally`, `pause_through_eval` (eval, `map`
callback, `call`, `new Function`, `apply` — all through registry entries), `native_continuation`.
Each must emit `CallFunction(LoadBuiltin(PAUSE))` (or `Construct(LoadBuiltin(CONTINUATION))`).

**values / exotic / control from the old families** that Fuzzilli lacks: boundary numbers and
int32 (value makers over `INTERESTING_INTEGERS` with radix/style draws), symbols (fresh,
registered, well-known), `sparse_array` (holes and a large `length`), `to_primitive_override`
(object-literal methods `valueOf`/`toString`/`[Symbol.toPrimitive]` used in arithmetic),
`getter_side_effect`, `direct_eval` (sloppy; `CallFunction(LoadBuiltin(EVAL), [code string])`),
`indirect_eval_function` (`Construct(LoadBuiltin(FUNCTION), …)` with a code string),
`promise_then`, `string_methods_unicode`, `finally_abrupt` (return/throw/break in `finally`),
`labelled_break`, `completion_value` (a code string with loops/try/switch, evaluated),
`let_const_block`, `tdz_access` (a function reading a `let` named variable declared after it,
through `CreateNamedVariable`), `generator_function` driven by `next`/`return`/`throw`, `for_of`
over Map/Set/string/custom iterable, `freeze_seal`, `proto_mutation`, `object_static`
(`Object.keys` … and JSON round-trips), `api_*` (methods/statics/constructors drawn from the
registry with arities, on matching and wrong receivers).

### 7.8 Templates

Strategies over the builder, each with the same statement floor (D28): `plain`,
`warmup_then_type_change`, `mutate_between_calls`, `deep_closures`, `strict_vs_sloppy_body`, and
the translations of `JIT1Function`, `JIT2Functions`, `JITTrickyFunction`, `JSONFuzzer`
(`ProgramTemplates.swift:299-566`; `Codegen50/100` are `plain` with a larger budget). A template
draws the prefix (`b.values`), its fixed structure, `statements()` where Fuzzilli calls
`build(n:)`, and places `Probe`s.

---

## 8. The lifter

### 8.1 Expression inlining: FuzzIL's policy, plus the per-instruction `lift` choice

Fuzzilli's `JavaScriptWriter` (`JavaScriptLifter.swift:2582-3062`) is the model, translated:

- Every value-producing instruction is turned into an `Expression` with a type from a fixed
  table of precedence, associativity and *characteristic* (`JSExpressions.swift:16-57`):
  identifiers, keywords and literals are **pure**; everything else, member access and all
  operators included, is **effectful**.
- A pure expression is inlined at every use and never declared (a literal with two uses appears
  twice). An effectful expression with exactly one use is held *pending* in evaluation order and
  inlined into its use only if its use retrieves the pending expressions as a suffix in order
  (`retrieve`, 2719-2794); any emitted statement or block boundary flushes the pending list as
  declarations or expression statements first (`emitPendingExpressions`, 2933; called from `emit`,
  `enterNewBlock`, `leaveCurrentBlock`). So inlining can never reorder two effects, and nothing
  effectful crosses a statement. This replaces D11's hand-made folding rules with the same
  guarantee and more inlining (`q.x = f() + o.p;` in one statement).
- An expression with two or more uses, or an instruction whose `lift` is `VAR | LET | CONST`,
  is declared with that keyword (`VAR` when `INLINE` was drawn but inlining is not legal).
  FuzzIL infers `const`/`let` by assignment count (2882-2889); here the keyword is a drawn,
  per-instruction choice (§3.6), because it is part of what Rhino is being tested on.
- Declared variables (§3.2) are always identifiers, never inlined; a `Reassign` with a single
  later use may be inlined as an assignment expression exactly as FuzzIL does it (2695-2711).
- Brackets come from the expression types alone (`Expression.needsBrackets`,
  `Expression.swift:69-80`): all binary operators share one precedence level with no
  associativity, so nested binaries are always bracketed; `**` with a unary or negative left
  operand is bracketed (1169-1173); a number literal as a member receiver is bracketed (`(3).x`).
- Loop headers and arrow bodies are lifted into temporary buffers and joined as comma
  expressions or an immediately invoked arrow when a header needs statements (2206-2252).
  Construct results are never inlined (1092-1097).

### 8.2 Spellings

- Numbers: `LoadInteger` by radix (`0x…`, `0o…`, `0b…`); `LoadFloat` by style (Python `repr`
  gives the shortest round-trip form; `inf`/`nan` never reach the lifter — they are builtins);
  negative values do not exist in literals (§3.5); `LoadBigInt` with `n`.
- Strings: full escaping (`json.dumps` semantics for `"`, `\`, control characters, lone
  surrogates as `\uXXXX`); FuzzIL escapes only `"` (366), which is a bug for us.
- Keys: dot form when the name is an identifier, `[n]` for indexes, `["…"]` otherwise
  (`liftMemberAccess`, 2264-2270); object-literal keys the same way; `__proto__:` for
  `ObjectLiteralSetPrototype`.
- Code strings: FuzzIL lifts the nested body inside a template literal with `2^n - 1`
  backslashes per nesting level (1656-1673), which breaks on a backtick or `${` in the body.
  Ours lifts the nested program to source with the same lifter and emits it as a properly
  escaped string literal.
- Functions are declarations (`function f1(a2, a3) {`) at top level or directly in a function
  body, and `var f1 = function (…) {…};` elsewhere (D14, Annex B). Arrows are expressions,
  inlined into their single use when they do not refer to themselves (2356-2400).
- Names: `v<n>` for temporaries, `a<n>` parameters, `f<n>` functions, `e<n>` catch, `i<n>` loop
  variables, `L<n>` labels (2842-2898), with a type-based prefix for declared variables (`n`, `s`,
  `o`, …; D13) because the reader of a reproducer wants to know the kind at a glance.
- `do { … } while (c);` gets its semicolon (FuzzIL omits it, 1451); `switch` cases get
  `break;` unless `fallsThrough`; labels only when used.

### 8.3 Probes, guards, comments, strictness

- `Probe(label)` lifts to `__probe("label", v1, v2, …)` over the declared variables and kept
  temporaries visible at that point, most recent first, up to `options.probe_width`, and is
  omitted when it directly follows another probe at the same indentation (D9). The plain
  reproducer is the same program without probe statements; nothing else changes shape.
- `// [generator]` comments per top-level statement from provenance, and `// step k: <mutator>`
  on instructions a surviving mutation step touched.
- A strict program starts with `"use strict";`; a strict function gets `Directive`. The builder
  tracks strictness per frame (D29) so sloppy-only ops are never emitted into strict code.

### 8.4 Lifter guards

Every lifted program must parse in Rhino (the property test of D32: probed and plain forms,
strict and sloppy, every `Lift` keyword) and in node's parser; a golden set of twenty lifted
programs is committed and read once by a reviewer (critique §3.2).

---

## 9. Mutators and lineages

All strategies over a program, each re-verified (`reject()` on failure), simplest first in a
fixed list (D34 kept): operation, input, declaration-kind, reassign, codegen, splice,
activation, number-flip.

| mutator | from | what it changes |
|---|---|---|
| operation | `OperationMutator.swift` (table at 65-345) | one parameter of one instruction: a literal's value within its pool (never the current value), an operator within its group, a key within its kind, a method on the same receiver group, a loop count, a property flag set, `inverted` on an `if`, a receiver/call-optional flag with p=0.1 (996-1002); or one `lift` choice |
| declaration-kind | ours (user review) | flips the kind of one declaration: `Declare`, a named variable, a for-header or for-each variable, or a lifted temporary's keyword; `var ↔ let` always, `→ const` only for a never-assigned variable; the ordered pool is `VAR, LET, CONST` so shrinking moves towards `var` |
| input | `InputMutator.swift` (aware mode) | one input rewired to another visible variable the slot accepts under the types at that point; function slots to functions, assignable slots to assignable variables |
| reassign | ours (critique §2) | inserts `Reassign(v, w)` for a declared `v` and a `w` of a different type class at a drawn point |
| codegen | `CodeGenMutator.swift` | `statements()` at a drawn point with the pool of that point's context (plus lifting choices for the new instructions) |
| splice | `SpliceMutator.swift`, `ProgramBuilder.swift:1735-2050` | a donor instruction's backward slice, blocks whole, variables renumbered; with p=0.10 an output and p=0.75 an inner output remapped to a host variable of the same type, instructions that may mutate a required variable included with p=0.5. Donors are drawn in the same test case (another `programs()` draw; corpus donors with the fuzz loop) |
| combine | `CombineMutator.swift` | a whole donor program appended at a point |
| activation | ours | inserts or removes one activation trigger in a function the Rhino model says is optimizer-typed (or not): `LoadArguments`, an empty nested arrow, an empty `try`/`catch` |
| number-flip | ours | for a `number_var`, inserts one assignment whose RHS is `AnyType` (a string, `undefined`, a BigInt, a call) at a drawn point; the exact shape of the recorded compiled-mode bugs |

Not ported: the runtime-assisted mutators (`Exploration`, `Probing`, `Fixup`) need the engine to
report back through an output channel; the harness could, but it is a later step. Fuzzilli's
minimizer is not ported: Hypothesis shrinks; its passes (`Minimization/*.swift`) inform the
ordering rules in §7.5.

Lineages are the proposal's `evolved()`: a base `programs()` draw, then a loop over a boolean
drawing one mutation per step; corpus entries, extension by `fuzz_one_input`, and tiered
compaction with the probe sets from `9ed5a22` are the fuzz-loop step (task #4), designed after
this layer exists.

---

## 10. Tests and guards

Written with the first operations, before any generator (brief part 3):

1. **No text-carrying op exists**: a test enumerates `ops.py` and asserts no operation has a
   `str` field other than `LoadString.value`, `LoadRegExp.pattern`, `CreateTemplateString.parts`,
   `Directive`, `Builtin.name`, `Key.name`, `Method.name`, `ProbeLabel`; and that every program
   drawn from every generator contains only those.
2. **The lint**: an `ast` walk over `oxpecker/il/generators/**`, `templates.py`, `strategies.py`
   fails on any string constant containing a JavaScript token (`{ } ( ) ; => . + - * / % < > = ! & | ^ ~ ?`,
   or a keyword: `function new typeof instanceof var let const return if for while this delete void in`)
   outside a docstring. The allowlist is empty.
3. **Structure test per generator**, from its docstring and the `must emit` column: fifty draws
   alone, every program `verify`s, and the required ops appear in every draw (or in ≥ 90 % where
   the shape is drawn, stated per generator).
4. **Text share is zero**: the §1.3 measurement from the post-mortem as a standing test —
   instructions per generator by class; the text class does not exist, so the test asserts the
   enumeration of op classes used.
5. **Lifter**: parse property tests (§8.4), the golden twenty, evaluation-order tests
   (`test_folding_never_reorders_effects` from the deleted suite, re-derived), `lift` round trips.
6. **Analysis**: the Rhino model against Rhino's own classification where observable: a function
   predicted optimizer-typed must show `Integer`/`Double` differences at `J.typeOf` that an
   activation-forced copy does not (a harness test, marked slow).
7. **Registry**: the overlay agrees with the probe table.
8. **Mutators**: children differ from parents, < 35 % rejected, locality (median ≥ 90 % kept),
   lineages shrink to the needed step (D34's tests, re-derived).
9. **Choice budget** (`scripts/choice_budget.py`, D35) re-run per family.
10. **Completion rate**: with no guards (§3.3), a standing harness test measures the share of
    generated programs that run to their end in interpreted mode (status `ok`), per family,
    and fails below a threshold set from the first measurement; a family whose programs mostly
    throw early is a generator problem, not a Rhino finding.

Delegated work gets this document, `ops.py`, the registries and the examples in the brief; no
escape hatch; the Apache-2.0 header in every Fuzzilli-derived file; and every file is read
before it is committed.

---

## 11. Order of work

1. `ops.py`, `program.py` (`check`), `types.py`, `environment.py` (registries from the table +
   overlay), with their tests and guard tests 1, 2, 7.
2. `analysis.py`: scopes, contexts, def-use, typer, Rhino model, `verify`; tests incl. 6.
3. `builder.py` and `strategies.py` (`pick`, `maybe`, `statements`, registry, lifting choices,
   `programs()`); the value makers; the first ten Fuzzilli translations (values, operations) and
   the structure tests; `lift.py` with the parse property test and the golden set.
4. The remaining Fuzzilli translations by family (delegable once 1–3 exist: Sonnet workers with
   the spec, the inventory and the attribution requirement; every file read before commit), then
   oxpecker's own families, then templates.
5. `mutate.py`, `evolved()`, the mutator tests; `oxpecker gen`/`list`/`export` commands.
6. The fuzz loop (task #4), then wider coverage (task #8).

---

## 12. Review outcome (2026-10-26)

The six questions put to the user and what was decided:

1. **`Declare` and two variable classes**: agreed. `let`/`const` are modelled explicitly in
   generation and mutation (§3.2, the `declaration-kind` mutator in §9).
2. **No stubs, no context graph**: accepted after the trade-off in §7.2, which also refined the
   design: one fixed statement list, self-scaffolding for context-requiring generators, separate
   member and case lists.
3. **Spread calls**: supported and on by default with a low weight; weights are configuration.
4. **Guards**: none. Exceptions end the program; probes observe; a generated `try`/`catch` is
   available where a shape needs it (§3.3, §8.3, §10 item 10).
5. **Lifting keyword**: per instruction (`Lift.INLINE | VAR | LET | CONST`, §3.6).
6. **`b95f75d`** is the Rhino commit this project pins (`oxpecker-project-decisions`: Rhino
   `origin/master` on 2026-10-18, 2.0.0-SNAPSHOT; `harness-java/gradle.properties`
   `rhinoVersion`). It locks in nothing beyond that pin: the Rhino model's predicates were read
   from that version's source and the capability table was probed from it. The model records
   `RHINO_MODEL_SOURCE = "b95f75d"`, `oxpecker probe` stores the Rhino version it probed, and
   the registry test warns when the two differ, so a Rhino upgrade re-checks three predicates
   (activation triggers, `findExpressionType`, direct-call conditions) and the slow harness test
   (§10 item 6) confirms them.

---
---

# Part II — Implementation

Conventions for all of Part II: Python 3.11+, `from __future__ import annotations`, frozen
dataclasses for anything stored in a program, `Enum`/`Flag` for every closed vocabulary, no
`str` field anywhere except the ones §10 item 1 enumerates. Public names are the ones generators
and tests use; everything else is private to its module. Every file translated from Fuzzilli
starts with:

```python
# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/<path>.swift at commit be2811d00282320381fc8007d64873745772011d.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
```

## 13. `il/ops.py`

### 13.1 Base classes

```python
class Attr(Flag):
    MUTABLE = auto(); CALL = auto(); BLOCK_START = auto(); BLOCK_END = auto(); JUMP = auto()
    VARIADIC = auto(); SINGULAR = auto(); PROPAGATES_CONTEXT = auto(); RESUMES_CONTEXT = auto()

class Context(Flag):
    EMPTY = 0
    JAVASCRIPT = auto(); SUBROUTINE = auto(); GENERATOR_FUNCTION = auto(); METHOD = auto()
    LOOP = auto(); OBJECT_LITERAL = auto(); SWITCH_BLOCK = auto(); SWITCH_CASE = auto(); WITH = auto()

@dataclass(frozen=True)
class Operation:
    """Base of every operation. Subclasses are frozen dataclasses whose *fields* are the
    operation's parameters; the class attributes below describe inputs, outputs and context."""
    num_inputs: ClassVar[int] = 0
    num_outputs: ClassVar[int] = 0
    num_inner_outputs: ClassVar[int] = 0
    first_variadic: ClassVar[int | None] = None     # index of the first variadic input, if VARIADIC
    attrs: ClassVar[Attr] = Attr(0)
    required_context: ClassVar[Context] = Context.JAVASCRIPT
    context_opened: ClassVar[Context] = Context.EMPTY

    # instance-level overrides for ops whose arity depends on a parameter (variadic calls, Parameters)
    def inputs(self) -> int: return self.num_inputs
    def outputs(self) -> int: return self.num_outputs
    def inner_outputs(self) -> int: return self.num_inner_outputs
    @property
    def is_block_start(self) -> bool: ...   # and is_block_end, is_jump, is_variadic, is_call, is_singular
```

Variadic and parameterised ops override the three methods. `END_OF: dict[type[Operation],
tuple[type[Operation], ...]]` is the translation of `isMatchingEnd` (`Semantics.swift:62-`):
`BeginIf: (BeginElse, EndIf)`, `BeginTry: (BeginCatch, BeginFinally)`, `BeginWhileLoopHeader:
(BeginWhileLoopBody,)`, `BeginForLoopInitializer: (BeginForLoopCondition,)`, …; `check()` reads
it.

### 13.2 Declaring an operation (five representative ones)

```python
@dataclass(frozen=True)
class LoadInteger(Operation):                       # JsOperations.swift:70
    value: int
    radix: Radix = Radix.DEC
    num_outputs = 1; attrs = Attr.MUTABLE

@dataclass(frozen=True)
class BinaryOperation(Operation):                   # 2054
    op: BinaryOperator
    num_inputs = 2; num_outputs = 1; attrs = Attr.MUTABLE

@dataclass(frozen=True)
class CallMethod(Operation):                        # 1799
    method: Method                                   # registry entry, not a string
    num_arguments: int
    receiver_optional: bool = False
    call_optional: bool = False
    num_outputs = 1; first_variadic = 1; attrs = Attr.MUTABLE | Attr.VARIADIC | Attr.CALL
    def inputs(self) -> int: return 1 + self.num_arguments   # receiver, then arguments

@dataclass(frozen=True)
class BeginPlainFunction(Operation):                # 1504 (BeginAnyNamedFunction 1485, BeginAnyFunction 1472)
    parameters: Parameters
    named: bool = False                              # a function *statement* with a name; needed for direct calls (§5.3)
    strict: bool = False                             # the lifter emits the directive; the builder tracks strictness
    num_outputs = 1; attrs = Attr.BLOCK_START
    context_opened = Context.JAVASCRIPT | Context.SUBROUTINE
    def inputs(self) -> int: return self.parameters.num_defaults           # default-value inputs, as FuzzIL
    def inner_outputs(self) -> int: return self.parameters.num_bindings   # parameters, after destructuring

@dataclass(frozen=True)
class BeginForLoopCondition(Operation):             # 2624
    num_loop_variables: int
    attrs = Attr.BLOCK_START | Attr.BLOCK_END | Attr.PROPAGATES_CONTEXT
    context_opened = Context.JAVASCRIPT
    def inputs(self) -> int: return self.num_loop_variables          # initial values
    def inner_outputs(self) -> int: return self.num_loop_variables   # the loop variables, declared, assignable
```

`Parameters` is FuzzIL's struct (`JsOperations.swift:1382-1431`): `count`, `has_rest`,
`default_indices: tuple[int, ...]`, `destructuring: Mapping[int, DestructuringPattern]`, with
`num_defaults` and `num_bindings`. `DestructuringPattern` is FuzzIL's enum (3571-3648) as frozen
dataclasses `ObjectPattern(properties, has_rest)`, `ArrayPattern(elements, rest)` with targets
`Binding | Property(key) | Element(i) | Computed | Nested(pattern)`; `num_extra_inputs` and
`num_bindings` as in the Swift.

The object-literal method begins carry `this` as inner output 0, as FuzzIL (489-613); the lifter
binds it to `this`, the typer gives it the literal's growing group type.

`ProbeLabel` is an `Enum` (`END, WARM, CHANGED, GUARD, STEP, …`) so a probe label is not free
text; a template that wants a numbered probe uses `Probe(label, n)`.

### 13.3 Text fields

The complete list of `str` fields in `ops.py`, asserted by guard test 1: `LoadString.value`,
`LoadRegExp.pattern`, `CreateTemplateString.parts: tuple[str, ...]`, `CreateNamedVariable.name`
(an identifier drawn from `Keys`' ordinary names; the op stores the `Key`, so even this is a
`Key`, not a `str`), `Directive.content` (fixed to `USE_STRICT`, an enum member). So the
*effective* list is two: a string literal's characters and a regexp pattern.

## 14. `il/program.py`

```python
Var = NewType("Var", int)

@dataclass(frozen=True)
class Instruction:
    op: Operation
    inouts: tuple[Var, ...]                 # inputs, outputs, inner outputs (Instruction.swift layout)
    index: int = -1                         # set by Program/Builder
    gen: str = ""; origin: int = 0; step: int = 0; lift: Lift | None = None   # metadata (§3.6)
    @property inputs / outputs / inner_outputs / all_outputs / variadic_inputs / output (asserts one)

@dataclass(frozen=True)
class Program:
    code: tuple[Instruction, ...]
    options: GenOptions
    template: str = ""
    # derived, cached: analysis (§17), source (probed), plain_source, signature()

def check(code) -> None                     # raises InvalidProgram: Code.swift:215-321 translated
def find_block_end(code, head) / find_block_begin(code, tail) / block_groups(code)   # Code.swift:346-512
def renumber(code) -> tuple[Instruction, ...]   # Code.swift:166-181; after any insertion
def signature(program) -> str               # structural hash over ops, inouts and lift; ignores gen/origin/step
```

`check` errors are typed (`InvalidProgram.kind ∈ {numbering, undefined, invisible, context,
block_mismatch, for_header}`) so tests assert the kind.

## 15. `il/types.py`

```python
class Kind(Flag): UNDEFINED, NULL, BOOLEAN, INTEGER, FLOAT, BIGINT, STRING, SYMBOL, OBJECT, FUNCTION
@dataclass(frozen=True)
class Param: type: Type; mode: ParamMode (PLAIN | OPT | REST)
@dataclass(frozen=True)
class Signature: params: tuple[Param, ...]; returns: Type
@dataclass(frozen=True)
class Type:
    possible: Kind
    group: str | None = None
    signature: Signature | None = None
    element: Type | None = None
    props: frozenset[Key] = frozenset()
    def Is(self, t) -> bool         # self.possible ⊆ t.possible and (t.group is None or ==) and props ⊇ t.props
    def MayBe(self, t) -> bool      # self.possible & t.possible
    def join(self, t) -> Type       # union of kinds; group kept if equal; props intersected; signature kept if equal
    def with_prop(self, k) / without_prop(self, k) / with_signature(self, s)
    @staticmethod object(group=None, props=()) / function(sig=None)
ANY, NOTHING, UNDEFINED, NULL, NULLISH, BOOLEAN, INTEGER, FLOAT, NUMBER, BIGINT, STRING, SYMBOL,
PRIMITIVE (= boolean|integer|float|string), OBJECT, FUNCTION, ARRAY (= object("Array")), ...
```

`join` is FuzzIL's `|` (`TypeSystem.swift:920-1098`) without the definite set; `Is`/`MayBe` are
`subsumes`/intersection (507-517) restricted to what the verifier and the pools use.

## 16. `il/environment.py`

Loading: at import, `rhino-capabilities.json` is read; `Builtins` is built as an `Enum`-like
namespace (a class with one `Builtin` attribute per global not in `EXCLUDED_GLOBALS`, plus the
host entries `J`, `PAUSE`, `PROBE` is not exposed), `Methods` from `protos` and `statics` (name,
arity), `Keys` from a fixed list plus every property name the probe saw on prototypes. The
overlay `TYPES: dict[str, Type | Signature]` keyed `"Map"`, `"Map.prototype.get"`,
`"Math.imul"`, … adds result types and parameter types for what generators use; the typer falls
back to `ANY` for untyped methods. `test_environment_overlay_matches_probe` fails on an overlay
key the table does not contain.

Pools: `INTERESTING_INTEGERS: tuple[int, ...]` (Fuzzilli's 100 plus int32/2^53 boundaries),
`INTERESTING_FLOATS`, `INTERESTING_STRINGS`, `JS_TYPE_NAMES`, `INTERESTING_KEYS`,
`REGEXP_PATTERNS: tuple[tuple[str, RegExpFlag], ...]` (pattern, incompatible flags), ordered
simplest-first (`0`, `1`, `-1`… ; `""`, `"a"`…) because the shrinker lowers indexes.

## 17. `il/analysis.py`

```python
@dataclass
class Analysis:
    visible_at: list[tuple[Var, ...]]          # per instruction index: visible, non-hidden variables, most recent first
    context_at: list[Context]
    storage: dict[Var, Storage]                # declared variables only (§3.2)
    assignable: frozenset[Var]
    defs: dict[Var, int]; uses: dict[Var, list[int]]; assignments: dict[Var, list[int]]   # DefUseAnalyzer
    def type_at(self, i: int, v: Var) -> Type  # flow-sensitive
    types: dict[Var, Type]                      # join over the program (for names)
    returns: dict[Var, Type]                    # function variable -> return type
    rhino: RhinoModel
@dataclass
class RhinoModel:
    activation: dict[Var, tuple[ActivationReason, ...]]   # per function variable; empty tuple = optimizer runs
    number_var: frozenset[Var]
    direct_call_target: frozenset[Var]; direct_call_site: frozenset[int]
    expr_type: dict[int, RhinoType]              # NUMBER | ANY per value-producing instruction
def analyse(program) -> Analysis
def verify(program) -> Analysis                  # analyse + the checks of §5.4; raises InvalidProgram
```

Typer implementation: translate `JSTyper`'s `AnalyzerState` (`JSTyper.swift:3022-3371`):
a stack of levels, each a list of branch `State(types: dict[Var, Type])`; `update(v, t)` stores
the old type in the parent on first change then sets; `start_group()`, `enter_branch()`,
`end_group()` merging by `join` and joining with the parent type when not every branch set `v`.
Hooks per op class in a dispatch table `TYPE_RULES: dict[type[Operation], Callable]` returning
output types; the block rules of §5.2 (two-pass loops, joining try/catch) in `SCOPE_RULES`.
Complexity: linear in instructions times the number of loop passes; the second loop pass
re-runs only the loop's body.

Rhino model implementation: `rhino_expr_type` is a function over `(instr, number_vars)` with the
table of §5.3; `number_vars(fn)` iterates `expr_type` over the function's assignments until
stable (at most |assignments| iterations); `activation(fn)` scans the function's own body
(nested function bodies excluded) for the trigger ops; `direct_call_target` scans top-level named
`BeginPlainFunction`s and the `CallFunction` sites whose callee input is that variable with
`num_arguments == parameters.count`.

## 18. `il/builder.py`

State (translated from `ProgramBuilder.swift:58-238`): `code: list[Instruction]`,
`next_var: int`, `scopes: list[list[Var]]`, `hidden: set[Var]`, `hidden_by_scope`, a
`ContextAnalyzer`, the incremental `Typer` (same class as the analysis's, fed per emit as FuzzIL
does, `7893-7903`), `rhino: RhinoModel` recomputed lazily, `object_literals: list[ObjectLiteralState]`,
`switches: list[SwitchState]`, `functions: list[FunctionState]` (for strictness, context, the
function variable), `labels`, `options: GenOptions`, `depth: int` (nesting budget),
`current_gen: str`, `origin: int`.

```python
class Builder:
    def __init__(self, options: GenOptions): ...
    @classmethod
    def at(cls, program: Program, pos: int) -> Builder      # for the codegen mutator: replays program[:pos] through the analysers, then emits into a buffer that the mutator splices in
    def emit(self, op: Operation, inputs: Sequence[Var] = ()) -> Instruction
    def finish(self) -> tuple[Instruction, ...]              # asserts no open block; runs check()
    def replay(self, start: int, end: int, var_map: dict[Var, Var]) -> dict[Var, Var]   # re-emits code[start:end] with fresh variables (D22's "same body twice")
    # pools
    def visible(self, *, assignable: bool | None = None, kind: Kind | None = None) -> list[Var]
    def pool(self, t: Type) -> Pool                            # Pool(exact, maybe, all), each most-recent-first, hidden excluded
    def type_of(self, v: Var) -> Type
    @property context / strict / depth / rhino
    # block context managers return small state objects (see §6 for the list)
```

Each context manager emits the Begin on `__enter__`, pushes the frame, and emits the End on
`__exit__` *only* on normal exit (an exception propagates; the builder is then unusable, which is
what a Hypothesis `reject()` wants). `FunctionFrame` gives `(f, args)`; `ObjectLiteralFrame` has
`add_property(key: Key, v)`, `add_element(i: int, v)`, `add_computed(k: Var, v)`,
`set_prototype(p)`, `copy_properties(o)`, `method(key, params, generator=False)`,
`computed_method(k, params)`, `getter(key)`, `setter(key)`, `computed_getter(k)`,
`computed_setter(k)`, `result` (set on exit); `TryFrame` has `catch()` and `finally_()`;
`SwitchFrame` has `case(v, falls_through=False)` and `default(falls_through=False)` (the default
is singular: a second one is a builder error); `ForLoopFrame` exposes `condition_vars`,
`condition(cond)`, `afterthought_vars`, `body()`; `WhileFrame` has `body(cond)`; `DoWhileFrame`
has `body()` and `condition(cond)`.

Hiding follows `ProgramBuilder.swift:7920-7954`: entering a subroutine, object literal or switch
hides labels; `plain_function` hides its own variable inside its body unless
`recursive=True`.

## 19. `il/strategies.py`

```python
def pick(draw, xs: Sequence[T]) -> T               # xs[draw(st.integers(0, len(xs)-1))]; asserts xs
def maybe(p: float = 0.5) -> st.SearchStrategy[bool]   # st.booleans() biased; shrinks to False
@dataclass(frozen=True)
class GenOptions: min_statements=3; max_statements=12; max_depth=3; probe_every=1; probe_width=6
                  strict=False; language_version=200; toggles=frozenset(...)
                  families: frozenset[str] | None = None; generators: frozenset[str] | None = None
                  weights: Mapping[str, float] = {}        # generator or family name -> multiplier (0 removes); e.g. {"spread-calls": 0.5}

@dataclass(frozen=True)
class GeneratorSpec:
    name: str; family: str; weight: int; context: Context; inputs: tuple[Type, ...]; required: tuple[Type, ...]
    produces: Type | None; toggle: str | None; sloppy_only: bool; source: str; strategy: Callable; doc: str
    def applicable(self, b: Builder) -> bool     # context ⊆ b.context, toggle on, not (sloppy_only and b.strict), depth ok, required pools non-empty
def generator(name, *, family, weight=1, context=Context.JAVASCRIPT, inputs=(), required=(), produces=None,
              toggle=None, sloppy_only=False, source="") -> decorator     # registers; wraps the composite to stamp provenance
def value_maker(t: Type) -> decorator;  def template(name, weight) -> decorator
REGISTRY: dict[str, GeneratorSpec]; FAMILY_ORDER: tuple[str, ...]
def pools(options) -> Pools                        # Pools.statements, Pools.members, Pools.cases (§7.2): fixed, ordered by (family order, name), each spec repeated round(weight * options.weights[...]) times; options.families/generators filter (filtering removes, never reorders)
def fallback(pool, b) -> GeneratorSpec             # the first applicable entry of the pool for this builder state

@st.composite
def variable(draw, b, t: Type) -> Var              # §7.1: maybe(0.8) drawn unconditionally; exact → maybe → all → value maker for t
@st.composite
def values(draw, b, n: tuple[int, int]) -> list[Var]        # prefix: st.lists over value makers (fixed order)
@st.composite
def statements(draw, b, lo=None, hi=None, kinds: tuple[str, ...] | None = None) -> int   # §7.3
@st.composite
def nested(draw, b, n: int = 2) -> int              # statements(0, n) with depth-1
@st.composite
def lifting_choices(draw, code) -> tuple[Instruction, ...]   # for every value-producing instruction with lift=None: draw(st.sampled_from(Lift)) with INLINE first
@st.composite
def programs(draw, options=GenOptions()) -> Program            # swarm mask → template pick → builder → verify → lifting choices → Program
@st.composite
def mutation(draw, program) -> tuple[str, Program]
@st.composite
def evolved(draw, options, max_steps=10_000) -> Lineage        # proposal §3.5
```

`programs()` asserts `verify` (a failure is a generator bug, never a filter) and never calls
`assume`.

## 20. Translation recipe: Fuzzilli Swift → oxpecker Python

This is the table the delegated translators follow. Anything not in the table is a design
question, not an invitation to improvise.

| Fuzzilli | oxpecker | note |
|---|---|---|
| `CodeGenerator("XGenerator", inputs: .two) { b, a, c in … }` | `@generator("x", family=…, weight=…, inputs=(Type.ANY, Type.ANY), source="CodeGenerators.swift:<line>")` + `@st.composite def x(draw, b, a, c)` | name in snake case without "Generator" |
| `inputs: .preferred(.object())` / `.required(.string)` | `inputs=(Type.OBJECT,)` / `required=(Type.STRING,)` | loose vs strict (§7.1) |
| `inContext: .single(.objectLiteral)` | `context=Context.OBJECT_LITERAL` | pools are per context |
| multi-part `[GeneratorStub(Begin…), GeneratorStub(End…)]` with `runtimeData.push/pop` | one function: `with b.plain_function(...) as (f, args): draw(b.nested()); …` | §7.2; shared values are Python locals |
| `b.buildRecursive(n: k)` / `b.build(n: k)` | `draw(b.nested(k))` / `draw(b.statements(0, k))` | |
| `probability(p)` / `withProbability(p) {…}` | `draw(maybe(p))` | unconditional draw, then branch |
| `withEqualProbability({…}, {…})` | `draw(st.sampled_from(FORMS))(b, …)` where `FORMS` is a module-level tuple of small builder functions | brief §2.5: data, not labels |
| `chooseUniform(from: X.allCases)` | `draw(st.sampled_from(X))` for an enum; `pick(draw, xs)` for anything context-dependent | |
| `Int.random(in: a...b)` / `Int64.random` | `draw(st.integers(a, b))` | |
| `b.randomInt()` / `randomFloat()` / `randomString()` | `draw(st.sampled_from(INTERESTING_INTEGERS))` etc. (value makers add the ranges) | |
| `b.randomJsVariable()` | `pick(draw, b.visible())` | most recent first |
| `b.randomVariable(forUseAs: T)` / `(ofType: T)` | `draw(b.variable(T))` / `pick(draw, b.pool(T).exact)` (guard non-empty) | |
| `b.randomVariable(forUseAsGuarded:)` → `(v, matches)` | `v = draw(b.variable(T)); matches = b.type_of(v).Is(T)` | |
| `b.randomArguments(forCalling: f)` | `draw(b.arguments_for(f))`: a strategy drawing one `variable(p.type)` per declared parameter, `st.integers(0,5)` extra for rest | |
| `b.randomPropertyName()` / `randomCustomPropertyName()` / `randomMethodName()` | `draw(st.sampled_from(INTERESTING_KEYS))` / `ORDINARY_KEYS` / `pick(draw, methods_of(b.type_of(o)))` | registry only |
| `b.randomBuiltin()` | `draw(st.sampled_from(BUILTIN_POOL))` | |
| `b.loadInt(v)` etc. | `b.load_int(v)` | builder has the same constructors |
| `b.reassign(variable: v, value: w)` | `b.reassign(v, w)` where `v` from `b.visible(assignable=True)`; if that pool is empty, `v = b.declare(pick(draw, b.visible()))` first | G4 |
| `b.hide(v)` / `unhide` | `b.hide(v)` / `b.unhide(v)` (same semantics) | |
| `guard: needGuard` / `randomVariable(forUseAsGuarded:)` | no guards (§3.3): emit the op unguarded; prefer `b.pool(T).exact` so the type is right more often; a generator that must continue past a throw builds `with b.try_() as t:` with a catch body that declares the error | |
| `b.setParameterTypesForNextSubroutine(types)` + `emit(BeginPlainFunction…)` | `with b.plain_function(params, types=types)` | |
| `b.randomParameters()` | `draw(b.parameters())`: count by visible-variable count as `ProgramBuilder.swift:635-701`, types from the pool, rest with p=0.2 | |
| `b.createNamedVariable(forBuiltin: name)` | `b.builtin(Builtins.NAME)` | |
| `b.eval("…")` / `Eval` | does not exist; design question | |
| `b.buildTryCatchFinally(tryBody:catchBody:)` | `with b.try_() as t: …; with t.catch() as e: …` | |
| `isAsync`, class ops, `using`, wasm | dropped (§3.3) | |

Idioms the translator must not use: f-strings or string concatenation that produce code; string
constants other than string-literal *contents*; `getattr`/dict lookups keyed by code-like
strings; `sampled_from` over a pool whose size depends on the builder's state (use `pick`);
counts drawn then looped (use `st.lists`); hidden draws inside the builder.

## 21. `il/lift.py`

```python
class Characteristic(Enum): PURE, EFFECTFUL
@dataclass(frozen=True)
class ExprType: precedence: int; associativity: Assoc; characteristic: Characteristic
IDENTIFIER = ExprType(20, NONE, PURE); LITERAL = …; CALL = ExprType(19, LEFT, EFFECTFUL); MEMBER = ExprType(19, LEFT, EFFECTFUL)
NEW = (19, NONE, EFFECTFUL); NUMBER_LITERAL = (17, NONE, PURE); STRING_LITERAL = (17, …, PURE); TEMPLATE/OBJECT/ARRAY_LITERAL = (17, …, EFFECTFUL)
POSTFIX = 16; UNARY = (15, RIGHT, EFFECTFUL); BINARY = (14, NONE, EFFECTFUL); TERNARY = (4, NONE, …); ASSIGNMENT = (3, RIGHT, …); ARROW = (3, RIGHT, …); YIELD = (2, RIGHT, …); SPREAD = 2; COMMA = (1, LEFT, …)
@dataclass(frozen=True)
class Expr: type: ExprType; text: str; parts: int
    def needs_brackets(self, outer: Expr, is_lhs: bool) -> bool     # Expression.swift:69-80
    def __add__(self, other: Expr | str) -> Expr                      # brackets only when an Expr is added

class Writer:            # JavaScriptWriter, JavaScriptLifter.swift:2582-3062
    exprs: dict[Var, Expr]; pending: list[Var]; names: dict[Var, str]; buffers: list[ScriptWriter]
    def assign(self, e: Expr, v: Var, *, allow_inline=True, lift: Lift)   # 2649-2676 + the per-instruction choice
    def retrieve(self, vs: Sequence[Var]) -> list[Expr]                   # 2719-2794 suffix rule
    def as_identifier(self, e: Expr, v: Var) -> Expr                      # 2799-2819
    def reassign(self, v: Var, e: Expr)                                   # 2695-2711
    def emit(self, line: str); enter_block(); leave_block(); flush()      # every one flushes pending first
    def push_buffer(indent) / pop_buffer() -> str                         # loop headers, arrow bodies
@dataclass(frozen=True)
class LiftOptions: probes: bool = True; comments: bool = True; strict: bool = False
RHINO_MODEL_SOURCE = "b95f75d"                                            # §12 item 6; also in analysis.py
def lift(program: Program, options: LiftOptions) -> str
```

`lift` is one pass over the instructions with a dispatch table `LIFT_RULES: dict[type[Operation],
Callable[[LiftState, Instruction], None]]`, translated op by op from the Swift `switch`
(`JavaScriptLifter.swift:341-2171`). The number of rules equals the number of op classes; a
missing rule is a test failure (`test_every_op_has_a_lift_rule`). Rhino-specific rules are the
ones in §8.2; everything else is the Swift behaviour.

## 22. `il/mutate.py`

```python
@st.composite def operation_mutation(draw, program, an) -> Program
@st.composite def input_mutation(draw, program, an) -> Program
@st.composite def declaration_kind_mutation(draw, program, an) -> Program   # Declare.kind, NamedVariableMode, loop-variable kinds, or a temporary's Lift keyword
@st.composite def reassign_mutation(draw, program, an) -> Program
@st.composite def codegen_mutation(draw, program, an) -> Program      # Builder.at(program, pos); statements(); splice back; renumber
@st.composite def splice_mutation(draw, program, an, donor: Program) -> Program   # ProgramBuilder.swift:1735-2050 translated
@st.composite def combine_mutation(draw, program, an, donor) -> Program
@st.composite def activation_mutation(draw, program, an) -> Program
@st.composite def number_flip_mutation(draw, program, an) -> Program
MUTATORS: tuple[tuple[str, strategy, applicable], ...]       # fixed order (§9); fallback codegen
def insertion_points(program, an) -> list[int]               # not in dead code after a jump; context allows statements
```

Each mutator returns a new `Program` after `renumber` and `lifting_choices` for new instructions;
`mutation()` verifies and `reject()`s.

## 23. Tests

```
tests/il/test_ops.py            attributes, END_OF table, text-field enumeration (guard 1), every op constructible
tests/il/test_program.py        check() kinds, block finders, renumber, signature stability under metadata changes
tests/il/test_types.py          Is/MayBe/join laws (reflexive, join is an upper bound, ANY top, NOTHING bottom)
tests/il/test_environment.py    registry built from the table, overlay diff (guard 7), pools ordered simplest-first
tests/il/test_analysis.py       scopes, def-use, typer per op rule, joins, two-pass loops, try/catch join, Rhino model
                                predicates on hand-built programs, verify() rejections by kind
tests/il/test_builder.py        every block helper emits a balanced, checkable group; hiding; at(); replay
tests/il/test_strategies.py     pick/maybe/statements spans, pool fixedness (same index → same generator across contexts
                                and masks), programs() verify, lifting choices, swarm
tests/il/test_generators.py     parametrised over REGISTRY: 50 draws alone, verify, required ops (guard 3), op-class
                                enumeration (guard 4), docstring present
tests/il/test_lint.py           the ast lint over generators/, templates.py, strategies.py (guard 2)
tests/il/test_lift.py           per-op lift rule exists; inlining examples (a), (b), (c) of the lifter notes; evaluation
                                order property; every Lift choice; spellings; code strings round trip; golden twenty
tests/il/test_lift_rhino.py     parse property test via the harness (slow)
tests/il/test_rhino_model.py    predictions vs J.typeOf observations via the harness (slow)
tests/il/test_mutate.py         per mutator: differs, <35% rejected, locality, specific effect; lineages shrink
scripts/choice_budget.py        restored (D35)
```

The lint's token list and keyword list are module constants in the test; the allowlist is an
empty tuple and a comment says it stays empty.

## 24. Delegation brief

A delegated translator (Sonnet) receives: this document; `ops.py`, `types.py`,
`environment.py`, `builder.py`, `strategies.py` as committed; the inventory of the Swift
generators it is assigned (`CodeGenerators.swift` line ranges); the translation recipe (§20);
the attribution header; the two worked examples below; and the acceptance list: the lint passes,
the structure tests it wrote for its generators pass, each generator has a docstring naming its
Fuzzilli source, no new string constants beyond string-literal contents, and the reviewer has
read every file before it is committed. It gets no escape hatch: a construct the builder cannot
express is reported back as a design question with the Swift line, and the generator is left out
until the question is answered.

## 25. Worked examples

A Fuzzilli translation (`CodeGenerators.swift:2808-2829`, `WhileLoopGenerator`):

```python
@generator("while_loop", family="control", weight=15, source="CodeGenerators.swift:2808")
@st.composite
def while_loop(draw, b: Builder) -> None:
    """A counted while loop: `var i = 0; while (i < n) { ...; i++ }` with a generated body."""
    i = b.declare(b.load_int(0))
    with b.while_loop() as loop:
        cond = b.compare(i, b.load_int(draw(st.integers(0, 10))), Comparator.LESS)
        with loop.body(cond):
            draw(b.nested())
            b.unary(UnaryOperator.POST_INC, i)
```

An oxpecker generator against the Rhino model (`numeric_local_type_flip`):

```python
NUMERIC_STEPS = (                        # every entry's RHS is Rhino NumberType (§5.3) when its inputs are
    lambda b, t, x, y: b.reassign(t, b.binary(x, y, BinaryOperator.ADD)),
    lambda b, t, x, y: b.reassign(t, b.binary(x, y, BinaryOperator.MUL)),
    lambda b, t, x, y: b.unary(UnaryOperator.POST_INC, t),
    lambda b, t, x, y: b.update(t, x, BinaryOperator.ADD),
    lambda b, t, x, y: b.reassign(t, b.binary(x, b.load_int(0), BinaryOperator.BIT_OR)),
    lambda b, t, x, y: b.reassign(t, b.unary(UnaryOperator.MINUS, x)),
    lambda b, t, x, y: b.reassign(t, b.call_method(b.builtin(Builtins.MATH), Methods.MATH_IMUL, [x, y])),
)

@generator("numeric_local_type_flip", family="compiler-targeted", weight=6, produces=Type.FUNCTION)
@st.composite
def numeric_local_type_flip(draw, b: Builder) -> Var:
    """Number-var locals (every assignment NumberType to Rhino's optimizer), one flipped to a
    non-number late; called with ints, doubles and a perturbing value."""
    with b.plain_function(Parameters(2), types=(NUMBER, NUMBER)) as (f, (p0, p1)):
        locals_ = [b.declare(b.load_int(draw(st.sampled_from(INTERESTING_INTEGERS))))
                   for _ in range(draw(st.integers(1, 3)))]
        pool = locals_ + [p0, p1]
        for step in draw(st.lists(st.sampled_from(NUMERIC_STEPS), max_size=5)):
            step(b, pick(draw, locals_), pick(draw, pool), pick(draw, pool))
        assert all(b.rhino.number_var(v) for v in locals_), "shape bug: a step is not NumberType"
        if draw(maybe(0.8)):
            victim = pick(draw, locals_)
            b.reassign(victim, draw(b.variable(draw(st.sampled_from(PERTURBING_TYPES)))))   # STRING, OBJECT, BIGINT, UNDEFINED
            b.reassign(pick(draw, locals_), b.binary(victim, pick(draw, pool), BinaryOperator.ADD))
        b.do_return(b.create_array(locals_))
    acc = b.declare(b.create_array([]))
    for args in draw(st.lists(b.arguments_for(f), min_size=1, max_size=3)):
        with b.try_() as t:
            b.call_method(acc, Methods.ARRAY_PUSH, [b.call_function(f, args)])
            with t.catch() as e:
                b.call_method(acc, Methods.ARRAY_PUSH, [b.get_property(e, Keys.NAME)])
    return f
```

Its structure test asserts, over fifty draws: `BeginPlainFunction`, at least one `Declare`, at
least one assignment whose `rhino.expr_type` is `NUMBER`, a `Return`, a `CallFunction` inside a
`BeginTry`; and in at least 70 % of draws a `Reassign` whose RHS type is not a number.
