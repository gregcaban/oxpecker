# What went wrong with the IR generators, and what we want instead

Status: post-mortem and specification, 2026-10-26. The generation layer it describes — the string
generator in `oxpecker/gen/` and the IR built on it in `oxpecker/ir/` — was deleted in commit
`796b11a`. A new generation layer is being designed from Fuzzilli (reference checkout
`~/Dev/Compilers/fuzzilli`, commit `be2811d`). This document is the brief for that design: part 1
says what happened and why, part 2 says, in detail and with examples, what the new layer must look
like, part 3 says how we keep it that way.

Copies of the deleted IR ports are kept for reference (not for copying) in
`runs/ir-ports-2026-10-25-reference/`; everything else is in git history before `796b11a`.

---

## Part 1 — What happened

### 1.1 The goal

`docs/proposal-hypothesis-ir.md` set it: generators written as composable Hypothesis strategies,
producing a FuzzIL-style IR (instructions over variables), which a separate pass — the lifter —
turns into JavaScript. Mutators edit the IR; Hypothesis shrinks everything. The point of an IR is
that **the program is structure**: every operation, every value, every block is something analysis
can see, mutators can change and the shrinker can simplify. JavaScript text exists in exactly one
place: the lifter's output.

### 1.2 What was built

The core went roughly to plan: operations, a flow-sensitive analysis, a verifier, a lifter with
safe expression folding, a builder, Hypothesis strategies, mutators, lineages, ~1,200 tests. Then
the ~130 string generators were ported to it — 30 by hand (values, operations, compiler-targeted,
five templates) and ~100 by seven parallel workers following a porting guide I wrote.

### 1.3 What came out

Measured on 2026-10-25 by drawing every generator alone 25 times and classifying each instruction
it emitted as structure, `Eval` (a string of JS code with input placeholders) or a `LoadLiteral`
whose "spelling" was really code:

- **105 of 133 generators emitted JavaScript text.** 19 had at least a quarter of their
  instructions as text; `function_literal` and `completion_value` were 100 % text.
- **Every family was affected**: all of `properties`, `exotic-arrays`, `api`, `toggled`; 12 of 14
  in `operations`; 10 of 13 in `objects`.
- Over 200 whole programs, 3.9 % of all instructions were `Eval` — a number that looked harmless
  and hid the breadth. `json_fuzzer` alone emitted 401 of them.

The IR carried the *skeleton* of programs — statements, blocks, which variable flows where — while
a large share of the *leaves* were text blobs. Mutators cannot reach inside a blob, the analysis
cannot see what it does, and the shrinker can only keep or drop it whole. That defeats the reason
for having an IR.

### 1.4 The five ways text got in

**(a) An escape hatch that was cheaper than doing it right.** The IR had an `Eval` op: code text
with `{0}`, `{1}` placeholders for inputs. It was introduced (decision D4) "for constructs not
modelled yet" with the promise that it would shrink. It grew instead, because adding an op is work
and writing a string is not. A real example, the ported `proxy` generator:

```python
p = b.declare(b.eval(
    "new Proxy({0}, {"
    " get(t, k, r) { {1}.push(\"get:\" + String(k)); return Reflect.get(t, k, r); },"
    " set(t, k, v, r) { {1}.push(\"set:\" + String(k)); return Reflect.set(t, k, v, r); },"
    " has(t, k) { {1}.push(\"has:\" + String(k)); return k in t; } })", tgt, log, out=T.OBJECT))
```

Three methods, their bodies, a constructor call — one opaque string. And `object_accessors`:

```python
code = "({ get %s() { return {0}; }, set %s(v) { {0} = v; }, %s: {1} })" % (key, key, other)
return b.declare(b.eval(code, backing, n, out=T.OBJECT))
```

**(b) `Eval` used where an op already existed.** The escape hatch was used even when the op was
there — the strongest sign that it was a default, not an exception. The ported `try_catch`:

```python
if throw:
    b.eval("throw {0}", thrown)                       # comment said "no Throw op" -- there was one
elif null_access:
    b.eval("{0}.x", null)                             # GetProperty exists
...
v = b.eval("{0} instanceof TypeError", e, out=T.BOOLEAN, pure=True)   # Binary("instanceof") exists
```

**(c) "Literals" that were code.** My own decision D3 kept the string generator's literal pools and
called their entries *spellings*. For primitives that was defensible; for everything else it
smuggled code in under the name "literal" — and because value makers are called from inside every
generator that needs an operand, the text spread to generators that never wrote a string
themselves (`binary_arith` was 11 % text: its operands were spelled objects):

```python
SPELLED = {
    T.FUNCTION:   ["function (x) { return x; }", "(x) => x", "function () { return arguments.length; }"],
    T.MAP:        ["new Map()", "new Map([[1, 2]])", 'new Map([["a", 1], [NaN, 2]])'],
    T.OBJECT:     ["{}", "{0: 1, 1: 2, length: 2}", "{valueOf() { return 7; }}"],
    T.TYPEDARRAY: ["new Int32Array(4)", "new Float64Array([0.5, 1.5])"],
}
SYMBOLS = ['Symbol("s")', 'Symbol()', 'Symbol.iterator', 'Symbol.for("k")']
```

Even the *number* pool hid expressions: `"1/3"` is a division, `"-1"`, `"-0"` and `"-Infinity"` are
unary minus applied to a literal (JavaScript has no negative literals). The pool made them look
like atoms.

**(d) JavaScript as branch labels.** Generators chose *which code to build* by drawing a string
that looked like the code and matching on it:

```python
form = draw(st.sampled_from(["|0", ">>>0", "~~", ">>0", "&0xffffffff"]))
if form == "~~":
    ...
op = {"|0": "|", ">>>0": ">>>", ">>0": ">>", "&0xffffffff": "&"}[form]

cond = draw(st.sampled_from(["j == 1", "i == j", "acc > 15"]))
form = draw(st.sampled_from(["e", "e instanceof TypeError", "typeof e", "e && e.name", "String(e)"]))
```

These are not emitted, but they are JavaScript in generator code: they read as code, they invite
copy-paste into `Eval`, a typo silently selects nothing, and the real decision (which operator,
which operands) is hidden behind a lookup.

**(e) Bare strings for closed vocabularies.** Operators, declaration kinds, loop kinds, builtins
and keys were all strings — in the IR itself (`Binary(op="|")`, `DeclareVar(kind="var")`,
`BeginForEach(kind="of")`) and in generator tables:

```python
BINOPS = ["+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>", ">>>", "<", "===", "=="]
TYPED_ARRAYS = ["Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", ...]
KEYS = ["a", "b", "x", "y", "length", "0", "1", "10", "constructor", "toString", "__proto__", "foo"]
```

Nothing checks a misspelt operator; mutators cannot enumerate "the other operators of this group"
from data that knows what a group is; the analysis kept its own second table of builtin names
(`_CONSTRUCTED = {"Map": T.MAP, ...}`) that had to agree with the generators' strings.

### 1.5 Why it happened — the process

1. **I wrote the instructions that produced it.** The porting guide's translation table had rows
   "JS syntax with no op → `b.eval(...)`" and "literal value from a pool → keep the pools". The
   workers followed it faithfully. The guide also said "keep semantics and JS shapes as close as
   the IR allows", which, read literally, says *copy the old strings*.
2. **Porting from string code can only produce strings.** The source of truth was the string
   generator. A "faithful port" of `f"{x} | 0"` is a string with a hole. The right source was the
   design (FuzzIL), not the old code.
3. **Ops were added one complaint at a time** instead of from a principled inventory. Each missing
   op was a reason to reach for `Eval`, so the IR never caught up with what generators needed.
4. **The tests checked the wrong property.** "Verifies, lifts, parses in node and Rhino, runs in
   both modes" — code-as-strings passes all of it. Nothing measured *how much of the program is
   structure*. 1,200 green tests said nothing about the one thing that mattered.
5. **I accepted the work on its test results without reading it.** Seven worker reports listed
   their `b.eval` uses honestly; I summarised them as "missing ops for later" and called the
   families ported. The user found the problem by reading the code.

---

## Part 2 — What we want

### 2.1 The one rule

**The generation layer contains no JavaScript.** Not emitted, not as labels, not as data that is
secretly code. JavaScript text is produced by the lifter and only by the lifter. Everything a
generator decides is a choice among *typed values*: operators, builtins, keys, numbers, nested
blocks — and every construct a generator wants has an operation.

What counts as JavaScript text, i.e. what must never appear in generator code:

| forbidden | why | instead |
|---|---|---|
| code with placeholders (`"throw {0}"`, `"new Proxy({0}, {...})"`) | opaque to everything | the operations that build it |
| a "literal" that is an expression (`"1/3"`, `"-1"`, `"new Map()"`, `'Symbol("s")'`, `"function (x) {...}"`) | an expression hiding as an atom | `Binary(Div, 1, 3)`, `Unary(Minus, 1)`, `Construct(Builtin.MAP)`, `CallFunction(Builtin.SYMBOL, ["s"])`, a `BeginFunction` block |
| operator/keyword spellings (`"|"`, `"instanceof"`, `"typeof"`, `"var"`, `"of"`) | untyped closed vocabulary | enums (§2.2) |
| code-like branch labels (`"j == 1"`, `"|0"`, `"e instanceof TypeError"`) | code as control data | draw the data itself (§2.5) |
| builtin names as strings (`"Int32Array"`, `"Math"`) | an open vocabulary with types attached | the builtin registry (§2.3) |

What *is* allowed as a Python `str` in generator code: the **contents** of a JavaScript string
value (`b.load_string("get:")` — the characters, not the quotes; the lifter escapes and quotes
them), and a property key obtained from the key registry (§2.3). Nothing else.

### 2.2 Closed vocabularies are enums

Every vocabulary the IR interprets is an `Enum`, defined once next to the operations, as FuzzIL
does (`Sources/Fuzzilli/FuzzIL/JsOperations.swift`: `BinaryOperator`, `UnaryOperator`,
`Comparator`, …). Only the lifter knows how they are spelled.

```python
class BinaryOperator(Enum):              # FuzzIL: BinaryOperator
    ADD = "+";  SUB = "-";  MUL = "*";  DIV = "/";  MOD = "%";  EXP = "**"
    BIT_AND = "&";  BIT_OR = "|";  XOR = "^";  LSHIFT = "<<";  RSHIFT = ">>";  U_RSHIFT = ">>>"
    LOGIC_AND = "&&";  LOGIC_OR = "||";  NULL_COALESCE = "??"

class Comparator(Enum):                  # FuzzIL keeps comparisons separate from arithmetic
    EQUAL = "=="; STRICT_EQUAL = "==="; NOT_EQUAL = "!="; STRICT_NOT_EQUAL = "!=="
    LESS = "<"; LESS_EQUAL = "<="; GREATER = ">"; GREATER_EQUAL = ">="

class UnaryOperator(Enum):              # FuzzIL also puts ++/-- here (PreInc, PostInc, ...);
    MINUS = "-"; PLUS = "+"; LOGICAL_NOT = "!"; BITWISE_NOT = "~"   # typeof, void, instanceof are
                                         # their own operations there (TypeOf, Void, TestInstanceOf)

class DeclarationKind(Enum):  VAR = "var"; LET = "let"; CONST = "const"
class ForEachKind(Enum):      OF = "of"; IN = "in"
```

Groups are data too, so a mutator changes `+` into another *arithmetic* operator without a string
table: `ARITHMETIC = frozenset({ADD, SUB, MUL, DIV, MOD, EXP})`, `BITWISE = ...`, `LOGICAL = ...`.

### 2.3 Open vocabularies are typed registries

Builtins and property keys are open-ended — JavaScript names them with strings — but each one
carries facts the generators, the typer and the mutators need. They live in registries, built from
the Rhino capability probe (`oxpecker/environment/rhino-capabilities.json`) where possible, the way
FuzzIL's `JavaScriptEnvironment.swift` declares builtins with types.

```python
@dataclass(frozen=True)
class Builtin:
    name: str                       # the global binding, spelled by the lifter
    type: Type                      # what loading it gives
    constructs: Type | None = None  # what `new` of it gives (Map -> Type.MAP), if constructible
    callable_as: Type | None = None # what calling it gives (String(x) -> Type.STRING)

class Builtins:                     # generated from the environment model; one entry per global
    MAP = Builtin("Map", Type.FUNCTION, constructs=Type.MAP)
    INT32_ARRAY = Builtin("Int32Array", Type.FUNCTION, constructs=Type.TYPED_ARRAY)
    STRING = Builtin("String", Type.FUNCTION, callable_as=Type.STRING)
    REFLECT = Builtin("Reflect", Type.OBJECT)
    ...
TYPED_ARRAYS = (Builtins.INT8_ARRAY, Builtins.UINT8_ARRAY, ..., Builtins.FLOAT64_ARRAY)
```

The typer reads `constructs`/`callable_as` instead of keeping its own name table.

Property keys are a value type with classes, so generators can ask for *kinds* of keys:

```python
@dataclass(frozen=True)
class Key:
    name: str
    kind: KeyKind                   # INDEX ("0", "10"), LENGTH, PROTO ("__proto__"), WELL_KNOWN
                                    # ("constructor", "toString", "valueOf"), ORDINARY ("a", "x")
INTERESTING_KEYS: tuple[Key, ...]   # from the environment model plus Fuzzilli's custom names
```

Method names on builtin prototypes come from the same environment table, so a generator calling
`arr.push(x)` names `Methods.ARRAY_PUSH`, which knows its receiver type and arity.

### 2.4 Every construct is an operation; there is no escape hatch

The operation inventory is FuzzIL's (`Sources/Fuzzilli/FuzzIL/JsOperations.swift`), restricted to
what Rhino supports (the capability probe says which syntax parses) — decided up front, not grown
on demand. There is **no `Eval` op** in the generation layer. FuzzIL does have one (`final class
Eval`, a code string with `%@` placeholders), but its code generators never use it: it appears only
in engine profiles, for V8 intrinsics such as `%LeakHole()` and `%GetBytecode(%@)`. Fuzzilli's own
source states the cost — `Profiles/V8SandboxProfile.swift`: *"Because Fuzzilli treats `eval` strings
as opaque, the mutation engine cannot genetically evolve [them] … Implementing native Instruction
classes … would allow Fuzzilli to structurally mutate these."* We take the stricter rule. If a generator needs a construct the
IR cannot express, that is a design task: add the operation (with analysis, lifting, mutation
support and tests) or drop the idea. It is never a string.

The inventory the deleted generators needed and did not have, every item of which FuzzIL has:

| needed for | FuzzIL operations |
|---|---|
| object literals with methods, getters, setters, computed keys, `__proto__`, spread | `BeginObjectLiteral`, `ObjectLiteralAddProperty`, `…AddElement`, `…AddComputedProperty`, `…SetPrototype`, `…CopyProperties`, `Begin/EndObjectLiteralMethod`, `…Getter`, `…Setter`, `EndObjectLiteral` |
| classes, `super` | `BeginClassDefinition`, `BeginClassConstructor`, `BeginClassMethod`, …, `CallSuperConstructor`, `GetSuperProperty`, `SetSuperProperty`, `UpdateSuperProperty` |
| `this`, `arguments`, `new.target` | `LoadThis`, `LoadArguments`, `LoadNewTarget` |
| `o.k += v`, `o[k]++`, `delete o[k]` | `UpdateProperty`, `UpdateElement`, `UpdateComputedProperty`, `DeleteElement`, `DeleteComputedProperty` |
| default and rest parameters | `Parameters` (with `hasRestParameter`) and default-value inputs on `BeginPlainFunction` |
| destructuring | `Destruct`, `DestructAndReassign` |
| template literals | `CreateTemplateString` |
| `typeof`, `instanceof` | `TypeOf`, `TestInstanceOf` |
| computed method calls, spread calls | `CallComputedMethod`, `CallFunctionWithSpread`, `CallMethodWithSpread`, `ConstructWithSpread`, `CreateArrayWithSpread` |
| named (undeclared) globals, `with` bodies | `CreateNamedVariable` |
| `eval` of a program | a nested IR sub-program, lifted into the string argument by the lifter — never handwritten text (an oxpecker addition; FuzzIL has no equivalent) |
| compound `for` headers | `BeginForLoopInitializer` / `…Condition` / `…Afterthought` / `…Body` |
| typed literal values | `LoadInteger`, `LoadFloat`, `LoadBigInt`, `LoadString`, `LoadBoolean`, `LoadUndefined`, `LoadNull`, `LoadRegExp(pattern, flags)` |

Literals hold **values**, not spellings. Where Rhino parses spellings differently (hex vs decimal,
exponent forms), the spelling is a typed field the lifter honours, not a string:

```python
LoadInteger(value=2147483647, radix=Radix.HEX)      # lifts as 0x7fffffff
LoadFloat(value=1e21, style=FloatStyle.EXPONENT)     # lifts as 1e21
Unary(UnaryOperator.MINUS, LoadInteger(0))           # -0: JavaScript has no negative literals
Binary(BinaryOperator.DIV, LoadInteger(1), LoadInteger(3))   # the old "1/3"
LoadRegExp(pattern="(a)(b)?", flags=frozenset({RegExpFlag.GLOBAL}))
```

### 2.5 Generators draw data, not labels

When a generator chooses among forms, it draws the thing it will build — an operator, a builtin, a
small function that builds a form — never a string that looks like the form.

`int32_coerce`, before (port of the string generator):

```python
form = draw(st.sampled_from(["|0", ">>>0", "~~", ">>0", "&0xffffffff"]))
if form == "~~":
    return b.unary("~", b.unary("~", x))
op = {"|0": "|", ">>>0": ">>>", ">>0": ">>", "&0xffffffff": "&"}[form]
return b.binary(op, x, b.const(T.INT32, "0xffffffff" if op == "&" else "0"))
```

After:

```python
INT32_COERCIONS = (                          # (operator, right operand): x | 0, x >>> 0, x >> 0, x & 0xffffffff
    (BinaryOperator.BIT_OR, Int(0)), (BinaryOperator.U_RSHIFT, Int(0)), (BinaryOperator.RSHIFT, Int(0)),
    (BinaryOperator.BIT_AND, Int(0xFFFF_FFFF, Radix.HEX)),
)

@generator("int32_coerce", family="operations", weight=2, produces=Type.INT32)
@st.composite
def int32_coerce(draw, b: Builder):
    """`x | 0`, `x >>> 0`, `~~x` on numbers near the int32 boundaries."""
    x = draw(b.variable(Type.NUMBER))
    if draw(maybe(0.2)):
        return b.unary(UnaryOperator.BITWISE_NOT, b.unary(UnaryOperator.BITWISE_NOT, x))
    op, k = draw(st.sampled_from(INT32_COERCIONS))
    return b.binary(x, b.load(k), op)
```

`try_catch`, before (the deleted port), then after:

```python
form = draw(st.sampled_from(["e", "e instanceof TypeError", "typeof e", "e && e.name", "String(e)"]))
...
    b.eval("throw {0}", thrown)
    v = b.eval("{0} instanceof TypeError", e, out=T.BOOLEAN, pure=True)
```

```python
# How the catch block looks at the error: each entry builds it from the caught value.
INSPECT_ERROR = (
    lambda b, e: e,
    lambda b, e: b.test_instance_of(e, b.builtin(Builtins.TYPE_ERROR)),       # FuzzIL: TestInstanceOf
    lambda b, e: b.typeof(e),                                                 # FuzzIL: TypeOf
    lambda b, e: b.call_function(b.builtin(Builtins.STRING), [e]),
)

@generator("try_catch", family="control", weight=3)
@st.composite
def try_catch(draw, b: Builder):
    """try/catch around nested statements and a possible throw; the catch inspects the error."""
    out = b.declare(b.load_int(0))
    thrown = draw(maybe(0.5)) and draw(b.variable(draw(st.sampled_from(THROWABLE_TYPES))))
    inspect = draw(st.sampled_from(INSPECT_ERROR))
    with b.try_() as t:
        draw(b.nested())
        if thrown:
            b.throw(thrown)
        with t.catch() as e:
            b.reassign(out, inspect(b, e))
```

`labelled_break`'s condition, the same way:

```python
JUMP_CONDITIONS = (
    lambda b, i, j, acc: b.compare(j, b.load_int(1), Comparator.EQUAL),
    lambda b, i, j, acc: b.compare(i, j, Comparator.EQUAL),
    lambda b, i, j, acc: b.compare(acc, b.load_int(15), Comparator.GREATER),
)
```

### 2.6 Values are built, not spelled

Value makers produce structure from the same operations as everything else. Before (`values.py`
via the string generator's pools):

```python
T.MAP: ["new Map()", "new Map([[1, 2]])", 'new Map([["a", 1], [NaN, 2]])'],
T.FUNCTION: ["function (x) { return x; }", "(x) => x", "Math.max"],
T.OBJECT: ["{}", "{0: 1, 1: 2, length: 2}", "{valueOf() { return 7; }}"],
```

After:

```python
@value_maker(Type.MAP)
@st.composite
def a_map(draw, b):
    entries = draw(st.lists(st.tuples(b.variable(), b.variable()), max_size=3))
    pairs = b.create_array([b.create_array([k, v]) for k, v in entries])
    return b.construct(b.builtin(Builtins.MAP), [pairs] if entries or draw(maybe()) else [])

@value_maker(Type.FUNCTION)
@st.composite
def a_function(draw, b):
    with b.plain_function(draw(st.integers(0, 2))) as (f, params):    # a real block, generated body
        draw(b.nested(1))
        b.do_return(draw(b.variable()))
    return f

@value_maker(Type.OBJECT)
@st.composite
def an_object(draw, b):
    with b.object_literal() as obj:
        for key, value in draw(st.lists(st.tuples(st.sampled_from(INTERESTING_KEYS), b.variable()), max_size=3)):
            obj.add_property(key, value)
        if draw(maybe(0.2)):                                           # the old {valueOf() { return 7; }}
            with obj.method(Keys.VALUE_OF, params=0):
                b.do_return(b.load_int(7))
    return obj.result
```

### 2.7 The hard cases, done right

`proxy` — handler object with traps whose bodies are generated IR (FuzzIL:
`BeginObjectLiteralMethod`, `CallMethod`, `Construct`):

```python
@generator("proxy", family="exotic", weight=2, produces=Type.OBJECT)
@st.composite
def proxy(draw, b: Builder):
    """A Proxy with get/set/has traps that log into an array, then used like an object."""
    target = draw(b.variable(Type.OBJECT))
    log = b.create_array([])

    def trap(obj, name, arity, forward):
        with obj.method(name, params=arity) as params:
            label = b.binary(b.load_string(name.name + ":"),
                             b.call_function(b.builtin(Builtins.STRING), [params[1]]), BinaryOperator.ADD)
            b.call_method(log, Methods.ARRAY_PUSH, [label])
            b.do_return(forward(params))

    with b.object_literal() as handler:
        trap(handler, Keys.GET, 3, lambda p: b.call_method(b.builtin(Builtins.REFLECT), Methods.REFLECT_GET, list(p)))
        trap(handler, Keys.SET, 4, lambda p: b.call_method(b.builtin(Builtins.REFLECT), Methods.REFLECT_SET, list(p)))
        trap(handler, Keys.HAS, 2, lambda p: b.test_in(p[1], p[0]))            # FuzzIL: TestIn
    p = b.construct(b.builtin(Builtins.PROXY), [target, handler.result])
    draw(b.use_as_object(p))                    # a property read, an `in` test, Object.keys(p), ...
    return p
```

Now the input mutator can rewire `target`, the operation mutator can change which `Reflect` method
a trap forwards to, codegen can insert code into a trap body, and the shrinker can delete one trap.

`object_accessors` (FuzzIL: `BeginObjectLiteralGetter` / `…Setter`):

```python
with b.object_literal() as obj:
    with obj.getter(key):
        b.do_return(backing)
    with obj.setter(key) as v:
        b.reassign(backing, v)
    obj.add_property(other_key, n)
```

`completion_value` — the eval'd programs as IR, lifted into the string by the lifter:

```python
with b.eval_program() as sub:          # a nested program; its own scope, lifted to a string argument
    draw(b.statements(1, 3, kinds=COMPLETION_STATEMENTS))    # try/finally, loops, switch, labelled blocks
b.call_function(b.builtin(Builtins.EVAL), [sub.source])        # the lifter spells direct `eval(...)`
```

### 2.8 Generators follow Fuzzilli's code, translated, not oxpecker's old strings

The source of truth for each generator is `CodeGenerators.swift` / `ProgramTemplates.swift`, read
and translated to the builder; oxpecker's own Rhino-specific generators (compiler-targeted
numeric locals, Java interop through `J`, continuations through `__pause`) are designed against
the same builder, with the old string versions read only as a description of what program shape
they were after. Fuzzilli's generators are already structural — for example:

```swift
CodeGenerator("BinaryOperationGenerator", inputs: .two) { b, lhs, rhs in
    b.binary(lhs, rhs, with: chooseUniform(from: BinaryOperator.allCases))
},
CodeGenerator("UpdateGenerator", inputs: .one) { b, v in
    let newValue = b.randomVariable(forUseAs: b.type(of: v))
    b.reassign(variable: v, value: newValue, with: chooseUniform(from: BinaryOperator.allCases))
},
CodeGenerator("TypeTestGenerator", inputs: .one) { b, val in
    let type = b.typeof(val)
    let rhs = b.loadString(chooseUniform(from: JavaScriptEnvironment.jsTypeNames))
    b.compare(type, with: rhs, using: .strictEqual)
},
```

translate to:

```python
@generator("binary_operation", family="operations", inputs=2)
@st.composite
def binary_operation(draw, b, lhs, rhs):
    return b.binary(lhs, rhs, draw(st.sampled_from(BinaryOperator)))

@generator("update", family="operations", inputs=1)
@st.composite
def update(draw, b, v):
    b.reassign(v, draw(b.variable_for_use_as(b.type_of(v))), draw(st.sampled_from(BinaryOperator)))

@generator("type_test", family="operations", inputs=1)
@st.composite
def type_test(draw, b, val):
    t = b.typeof(val)
    return b.compare(t, b.load_string(draw(st.sampled_from(JS_TYPE_NAMES))), Comparator.STRICT_EQUAL)
```

Each such file starts with the Fuzzilli attribution header (Apache-2.0, naming the Swift source
it follows), as `THIRD_PARTY_NOTICES.md` requires.

Fuzzilli also splits block-building generators into `Begin`/`End` stubs with *context
requirements* — `ObjectLiteralPropertyGenerator` runs `inContext: .single(.objectLiteral)` — so
other generators (and mutators) can add members to an open object literal or statements to an
open function. Whether the new builder adopts that scheduling, or keeps generators as nested
Python blocks, is the first design decision of the new layer; either way the result is IR.

### 2.9 What stays from the deleted work

The ideas that survived review, to be re-introduced deliberately (not copied) into the new design:
generators, templates, value makers and mutators as plain Hypothesis strategies (`@st.composite`,
every choice an explicit `draw`); the strategy rules of proposal §3.8 (lists not counts, `pick` for
context-dependent pools, fixed simplest-first pools, recent-first variables, equal template floors,
deletable scaffolding); declared variables vs temporaries with flow-sensitive types and per-function
activation; a verifier run after every mutation; a lifter whose folding never reorders effects and
whose lifting choices are part of the IR; lineages shrunk by Hypothesis; per-run probe sets from the
harness (kept: `oxpecker/harness`, `harness-java`).

---

## Part 3 — How we keep it this way

1. **No `Eval` operation exists in the generation layer**, so it cannot be called. A test imports
   every generator module and asserts the IR has no text-carrying operation, and that no
   generated program contains one.
2. **A lint test reads the generator sources** (Python `ast`) and fails on any string constant
   that contains JavaScript syntax — operator tokens, braces, parentheses, `=>`, `;`, keywords such
   as `function`, `new`, `typeof`, `instanceof`, `var`, `let`, `return`. String contents meant for
   JavaScript string values go through `b.load_string(...)`; keys and builtins come from the
   registries. The allowlist is empty and stays empty.
3. **A structure test per generator**: drawing it alone produces only typed operations, and the
   operations it is *for* (a `try_catch` that never emits `Throw` across 50 draws is wrong; a
   `proxy` without `BeginObjectLiteralMethod` is wrong). These assertions are written with the
   generator, from its docstring.
4. **The op inventory comes first.** Operations, enums, registries, typer, lifter and builder API
   are designed and tested before any generator is written. Generators are written against a
   finished vocabulary.
5. **Delegated work gets this document, the inventory and the examples — and no escape hatch.**
   Acceptance is: the lint test, the structure tests, the Fuzzilli attribution headers, and my
   reading of every file before it is committed. Test results alone are never "done".
6. **Measure what matters.** The per-generator text share (§1.3) was the measurement that exposed
   the problem; it becomes a standing check, and it must read zero.
