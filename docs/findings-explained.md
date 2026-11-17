# The findings, explained

What oxpecker has found in Rhino `b95f75d` so far, one entry per root cause, with the program
that shows it, what each mode does, which mode is right, and what we know about why. Written
2026-10-27 from `tests/regressions/` (41 reproducers, 7 of them under `master-only/` because they
do not show on the 1.9.2 release the harness runs since 2026-10-28), `tests/seeds/` (2), the unsorted
`tests/findings/` (3 reduced files, 5 hard + 1 soft + 18 java-types records) and the two scratch
checks described in §6. Upstream status (issues, test262 expectations) is in
`docs/upstream-crosscheck.md`; this document does not repeat it beyond a pointer per family.

Every reproducer replays with `oxpecker diff <file>` (add `--language-version 250` where the
file header says so). §1 explains how to read what `diff` prints.

## 1. Reading a finding

A **finding** is a program on which Rhino's two execution modes, the interpreter and the
bytecode compiler, disagree, or on which either mode lets a raw Java exception escape. The
harness runs the same source in both modes in one JVM, records the completion value, every
`__probe(label, ...)` call as a canonical snapshot, the Java class of every number and string
the probes saw, and `compare.py` turns the two records into a **verdict**:

| kind | meaning |
|---|---|
| `hard` | a mode crashed with a Java exception, or status / value / probe snapshot / thrown error differ |
| `soft` | only error messages differ, or one mode timed out |
| `java-types` | JS behaviour is identical; only the Java class of a number or string differs (`Integer` vs `Double`, `ConsString` vs `String`) |
| `skip` | the compiled mode fell back to the interpreter (class-size limit), or both modes report `unsupported` |

`oxpecker diff` prints the verdict and then, for every recorded field that differs, the first
character at which the two modes diverge, with 60 characters of context each side and `▶` at
the divergence. Three things trip people up:

- **`compiledActually: False vs True` is noise.** It records whether the run really compiled.
  It is always `False` for the interpreted run and `True` for the compiled one, so it shows up on
  every finding and carries no information. (Worth skipping in `cmd_diff`.)
- **Probe arguments are numbered with the label as `arg0`.** In
  `__probe("p10", x2, f3, o2, o3, a1, a2, o4, x3)` the verdict's `arg6` is `a2`, the seventh
  value in the call, not the sixth.
- **`traceJavaTypes` repeats the same difference from the Java side.** Each `|` column lists the
  Java classes of the values inside that argument, so an array that gained an element in one
  mode also gains a type in that column. That is a consequence, not a second finding. The
  `java-types` verdict is reserved for runs where the JS values agree.

One classification gotcha: an error-message difference is `soft` when the error escapes as a
throw but `hard` when the program stringifies it (`e.message` into a probe makes it a value
difference). §5.7 has the example.

## 2. The map

Fifty entries (48 reproducers, two of them symptoms of one cause, plus the two seeds,
the continuation difference and the soft record), grouped by the part of Rhino that is wrong.
"Right" names the mode that follows the spec; "both wrong" means neither does.

| family | findings | right |
|---|---|---|
| 3. Compiled mode's integer typing | direct recursive call, `++`/`--` int32 wrap, `-0` lost (two forms), BigInt in a number-typed local (two forms), boxed `Double` identity, Java `Integer` vs `Double` | interpreted (Java types: neither, by design) |
| 4. Compiled mode and object shape | Proxy `has` before `get`, arrow `prototype`, generator-method `prototype` | interpreted (generator method: both wrong) |
| 5.1 Interpreter and `super` references | computed `super[k]` update, `super.x++` on a string, `super.p ||=` write-back, `super.p /=` receiver, GetSuperBase ordering (seed) | compiled |
| 5.2 Interpreter frames and argument arrays | `apply` with an array-like into a rest parameter, re-entrant generator `next()`, object-rest from a Proxy with a Symbol key | compiled (object-rest: both wrong) |
| 5.3 Compiled code the JVM rejects | relational argument stack underflow, `z((yield 3))`, `obj[p] &&= v`, `yield` in a default parameter, `yield` from a `catch` inside `for-of` | interpreted |
| 5.4 Compiled generators and `finally` | nested `finally`/`throw` escapes the `catch`, abrupt `finally` completion (seed) | interpreted |
| 5.5 Java exceptions escaping both modes | 17 reproducers, from `Array.find()` to `new Int8Array({length: 2**53})`, `new Date(-2**31, -2**31)` and a Java wrapper's `__proto__` | neither |
| 5.6 Expected differences | continuations | by design |
| 5.7 Soft | Symbol comparison message | neither |
| 5.8 Compiled `super` in a block with `let`/`const`, a `with` block or a `for (let ...)` body | `ClassCastException` at the super reference (two reproducers) | interpreted |
| 5.9 Compiled `with` over a Java object | `ClassCastException` at the first name read in the block | interpreted |
| 5.10 Compiled rest parameter of a direct-call target | `rest` is the first extra argument, not an array | interpreted |
| 5.11 Labelled `for` with a loop in a function in its head | `continue L`: compiled "unlocated label", interpreted skips the update | both wrong |

## 3. Compiled mode's integer typing

Rhino's compiler has an optimizer pass that types function locals and some expressions as
`int` or `double` and emits primitive JVM arithmetic for them, while the interpreter boxes every
number as a `java.lang.Double` and goes through `ScriptRuntime`. Most of the compiled-mode
value bugs are this pass choosing `int` where the spec needs a double, or choosing a number
where the value is not one. The upstream reading (issue #2407, aardvark179's comment) is that
the single-step IR to bytecode design makes this hard to fix piecemeal.

### 3.1 Direct recursive call with `=== 0` returns the wrong value

`tests/regressions/direct-recursive-call-completion.js`

```js
function r(n) { return n === 0 ? 0 : r(n - 1) + 1; }
r(3);               // interpreted 3, compiled 1
```

A top-level function that calls itself directly, with a strict-equality base case, returns as
if the recursion stopped one level in. `n == 0`, `n <= 0` and `n < 1` agree between modes, and
so does any non-direct call (a function expression, or calling through a local alias). The
compiler's "direct call" optimisation passes numeric arguments unboxed to a specialised entry
point, and the `===` on the unboxed value apparently compares against the wrong thing. This is
the one finding already filed upstream (#2407, open since 1.7.7.2).

The fuzz record `tests/findings/hard-4bb37440c8.js` (`f2(12)` is 8192 interpreted, 4
compiled) is this bug, not int32 wrap as `docs/upstream-crosscheck.md` §6.2 guessed: the same
function with `n == 0` instead of `n === 0` gives `EQUAL` (checked 2026-10-27).

### 3.2 `++` and `--` wrap at int32

`tests/regressions/compiled-increment-int32-overflow.js`

```js
var n = 2147483647; n++;     // interpreted 2147483648, compiled -2147483648
var m = -2147483648; m--;    // interpreted -2147483649, compiled 2147483647
var o = {p: 2147483647}; o.p++;   // same wrap on a property
```

Only the update operators, and only where the target is not an optimizer-typed local: top-level
variables, locals of functions that need an activation (`eval`, `arguments`, `with`), object
properties and array elements. `n += 1` and `n = n + 1` are correct, and `n++` inside a plain
function is correct because the optimizer gives it a `double` slot. The generic increment path
in compiled code evidently does its arithmetic on a Java `int` when the boxed value is an
`Integer`, which it is in compiled code (see §3.6) and never in the interpreter. The 2026-10-19
fuzz records `hard-07c088bb4e` and `hard-3bf16ac33e` (`2147549183 vs -2147418113`) were
attributed to this family by signature; they were not kept as files.

### 3.3 Negative zero lost

`tests/regressions/compiled-negative-zero-lost-in-int-arith.js` and
`compiled-top-level-multiply-by-zero-loses-sign.js`

```js
var n = -1; n * 0;           // interpreted -0, compiled 0
var x = -3; var y = x * 0;   // Object.is(y, -0): interpreted true, compiled false
```

Two reductions of one mechanism: a top-level variable holding a small integer is kept as an
`int` by the compiled code, and `int * 0` cannot represent IEEE `-0`. Inside a function both
modes give `-0`; a literal `-3 * 0` is constant-folded to `-0` by both. Hypothesis found the
first form on the first day; the fuzz loop found the second a week later through
`number_computation`, and six more array-shaped signatures (`[-0,-1,-2]` vs `[0,-1,-2]`) are
the same bug escaping the signature suppression. The scalar signatures are in
`tests/known_signatures.txt`.

### 3.4 BigInt in a number-typed local

`tests/regressions/compiled-bigint-in-number-typed-local.js` and
`t262-bigint-to-number-overflow-throws.js`

```js
function f() { var b = -1n; return b; } f();         // interpreted -1n, compiled TypeError
function f(a) { var n = a * a; return n; } f(10n);   // interpreted 100n, compiled TypeError
function f() { var x = 2n ** 1024n; return Number(x); } f();   // interpreted Infinity, compiled TypeError
```

The optimizer treats unary `-`, `~`, `*`, `%`, `**` and `++` as always numeric and gives the local a
`double` slot. When the operand is a BigInt the store into that slot throws
`TypeError: Cannot convert BigInt to a number`. `var b = 1n` (no operator), `return -1n` (no
local), top-level `var b = -1n` (no optimizer) and `n = a + a` (`+` may concatenate, so it is
not assumed numeric) all agree. The test262 `Number(2n ** 1024n)` finding was recorded first and
is a symptom of this one.

**Second form: a relational comparison with a number literal.**
`compiled-bigint-relop-number-literal-local.js` (found by the hour-long campaign of 2026-10-27):

```js
function lt(v) { return v < 5; } lt(1024n);            // interpreted false, compiled TypeError
function local() { var b = 1024n; return b < 5; }      // same
```

No numeric operator touches the variable; comparing a local or parameter with a number literal
(`<`, `<=`, `>`, `>=`, either operand order, `5.5` too) is enough for the same
`Cannot convert BigInt to a number`. The literal's side can also be a local the optimizer
types as a number (`const n = 5; v < n`), and the BigInt side can be a captured top-level
variable (`var top = 1024n; function f() { return top < 5; }`). `==`/`===`, a string literal,
an untyped operand (`v < five` with `five` a parameter), the top level, and a function with a
`try/catch` (which forces an activation frame) all agree.

### 3.5 One boxed `Double` per constant

`tests/regressions/compiled-boxed-number-constant-identity.js`

```js
function f1() { return J.echo(3 + 1e21); }
function f2() { return J.echo(3 + 1e21); }
f1() === f2();      // interpreted false, compiled true
```

Compiled code folds the constant and keeps a single boxed `java.lang.Double` for it in the
class's constant pool, so every evaluation hands Java the same object; the interpreter boxes a
fresh one each time. Pure JS cannot see this. It becomes visible through any host function that
returns its argument unchanged (`J.echo` in the harness, any identity-preserving embedder API),
because `===` on two host-returned wrappers compares Java references. Eight fuzz signatures of
the form `@N[Java java.lang.Double]` were this.

### 3.6 Java sees `Integer` from compiled code and `Double` from the interpreter

`tests/regressions/java-int-literal-double-vs-integer.js` and the 18 records in
`tests/findings/java-types.jsonl`

This is the one family where neither mode is "wrong" by the JS spec, and upstream treats it as
by design (#350, #1202, #1203), but an embedder that casts `(Integer) r` or switches on
`getClass()` sees the split, which is why the harness records it as its own verdict kind.

All 18 fuzz records reduce to the same rule, confirmed with one program on 2026-10-27:

```js
function f1(a0) { var n1 = 1; var n2 = ~n1; n2 += n2; return n1 + n2; }
function f2(a0) { var z = arguments.length; var n1 = 1; var n2 = ~n1; n2 += n2; return n1 + n2; }
var o = {a: 255};
var a = [1, 2];
__probe("p", f1(0), f2(0), o.a, a[0], 1e21 ? 2 : 3);
//  interpreted: Double | Double  | Double  | Integer | Double
//  compiled:    Double | Integer | Integer | Integer | Integer
```

- The interpreter boxes numeric literals as `Double` wherever they appear: top level, object
  literal values, conditional branches, function locals, arguments. Compiled code boxes
  int-valued literals as `Integer`, and `ScriptRuntime`'s generic arithmetic on two `Integer`s
  keeps the result an `Integer`.
- Optimizer-typed function locals come out as `Double` in both modes (`f1` above), because the
  `double` slot is boxed on return. A function that needs an activation (`f2`, through
  `arguments`) takes the generic path and returns `Integer` in compiled mode. So the direction
  is not "compiled is always `Integer`"; it depends on which path the value took.
- Dense array literal elements are `Integer` in both modes (`a[0]` above); the holey literal in
  `java-types[1]` (`[1, "a", {}, , 5]`) gave `Double` interpreted, so sparse storage differs.
- Results of `|`, `<<`, `%`, `-`, `+` and `*` on int-valued operands follow the operand boxing:
  the fuzz records are those operators in `callsite_relink`, `direct_call_arity`,
  `jit1_function` and `numeric_local_type_flip` bodies, plus `rest.length`, `arr.concat([1,2,3])`
  and property values read back from object literals.

The README's "Java types" section lists which embedder-facing APIs hand Java the raw object
(host `Callable`s, the completion value, `ScriptableObject.getProperty`) and which coerce first
(LiveConnect method calls, where every number becomes `Double`).

## 4. Compiled mode and object shape

### 4.1 Proxy `has` trap fires on a property read

`tests/regressions/compiled-proxy-extra-has-trap-on-get.js`

```js
var log = [];
var p = new Proxy({}, {
  get(t, k, r) { log.push("get:" + String(k)); return Reflect.get(t, k, r); },
  has(t, k)    { log.push("has:" + String(k)); return k in t; }
});
var x = p.x;    // interpreted ["get:x"], compiled ["has:x", "get:x"]
```

A named property read is a [[Get]]; a Proxy with no `get` trap forwards to the target and
never consults `has`. Compiled code probes [[HasProperty]] first. Indexed reads (`p[1]`) agree.
Reduced again on 2026-10-27 from `tests/findings/hard-ebd76c3473.js`: `typeof p.a` and bare
`p.a` both log `["a"]` with only a `has` trap installed, so it is the property-get path, not
`typeof`. Nothing upstream mentions it.

### 4.2 Arrow functions get a `prototype`

`tests/regressions/arrow-function-has-prototype-compiled.js`

```js
var f = () => 10;
typeof f.prototype;    // interpreted "undefined", compiled "object"
```

Arrows must not have a `prototype` property. Compiled code presumably creates function objects
through a path that installs one regardless of kind. Upstream fixed the same thing for methods
(PR #2031); arrows were missed. Found by replaying Rhino's own `arrowfn.jstest` corpus.

### 4.3 Generator methods: `prototype` wrong interpreted, absent compiled

`tests/regressions/t262-generator-method-prototype-chain.js`

```js
var method = { *method() {} }.method;
Object.getPrototypeOf(method.prototype) === %GeneratorPrototype%
// interpreted: false (prototype exists, chain is Object.prototype)
// compiled:    TypeError, method.prototype is undefined
```

Both wrong, differently: the spec wants a `prototype` whose [[Prototype]] is
%GeneratorPrototype%. The test262 file is listed as failing in all modes upstream, so the mode
difference is not recorded; #1904 is the same area.

## 5. The rest

### 5.1 Interpreter and `super` references

Four reproducers and one seed, all in the interpreter's handling of `super.p` / `super[k]` as
an assignment target in an object-literal method. The compiled mode is right in every case. The
test262 entry upstream tags as failing in `{interpreted,debugger}` only
(`prop-expr-getsuperbase-before-topropertykey-putvalue-increment.js`, reproduced as
`tests/seeds/test262-super-getsuperbase-before-topropertykey.js`) is the same code.

**Computed update returns the key.** `interpreted-computed-super-update.js`:

```js
var proto = {p: 1};
var o = { __proto__: proto, inc(k) { return ++super[k]; }, dec(k) { return super[k]--; } };
o.inc("p");    // interpreted "p", compiled 2
o.dec("p");    // interpreted leaves an own property "0" with value "p" on o
```

The symptom is consistent with the interpreter's icode for a computed super update swapping
key and value on its stack: the key ends up where the value should be and the old value
becomes the property name. (Mechanism inferred from the outputs, not read from the source.) The
fuzz records
`hard-49b2af89f8` (`"NaN": "p"` vs `"p": NaN`), `hard-c2a25a910c`, `hard-5147da45df` and the
fifth entry of `tests/findings/hard.jsonl` (`"-2": "p"` vs `"p": -2`) are all this: the own
property's name is the stringified old value and its value is the key.

**`super.x++` on a string concatenates.** `interpreted-super-increment-string-concat.js`:
`proto.x = "a"`, `super.x++` leaves `"a1"` interpreted and `NaN` compiled. The update
operator must apply ToNumber; the interpreter routes a super-property update through the
generic `+`.

**Short-circuit assignment writes the read value back.**
`super-logical-assign-proto-writeback-interpreted.js`:

```js
var arr1 = [5];
var o = { __proto__: arr1, m() { super.__proto__ ||= 5; } };
o.m();
Object.getPrototypeOf(o) === Array.prototype   // interpreted true, compiled false
```

`||=` with a truthy left side must not assign. The interpreter performs the PutValue anyway,
and because `__proto__` is an accessor on `Object.prototype`, the write-back of the value read
through `this` (Array.prototype) changes the object's prototype. `+=` assigns in both modes
and agrees, which is why only the short-circuit operators show it.

**Compound assignment reads with the wrong receiver.**
`super-compound-assign-wrong-receiver-interpreted.js`: a getter on the prototype that reports
its `this` sees the prototype under `super.g += ""` interpreted and the instance compiled; with a
typed array as the prototype, `super.length += 1` therefore runs interpreted and throws
`TypeError` compiled. A plain `super.g` read agrees, so the compound path alone passes the home
object instead of `this` to the getter. The form found on master on 2026-10-27, `super.__proto__
/= 10` with a `Date` prototype (interpreted `TypeError` from converting `Date.prototype` to a
number), does not show on 1.9.2; the getter and typed-array forms came from the first 1.9.2
campaign on 2026-10-28. The hour-long 1.9.2 campaign the same day added a form where the wrong
receiver shows as a missing error: with an array prototype whose `toString` is not callable,
`super.__proto__ **= 3` compiled reads the array through the instance and ToPrimitive throws
`TypeError` (correct), while the interpreter reads `Array.prototype` through the prototype and
converts it quietly.

### 5.2 Interpreter frames and argument arrays

**`apply` with an array-like into a rest parameter.** `t262-apply-rest-array-like-wrong.js`:

```js
function f(...args) { return args; }
f.apply(null, { length: 1, 0: 9 });    // interpreted [null], compiled [9]
```

The interpreter's args-array construction for `apply` handles real arrays and `arguments`
objects but hands a rest parameter `null` for the first slot and the array-like itself for
later slots. Upstream fixed three sibling bugs in the same code after the CallFrame refactor
(#2006, #2016, #2035); this instance is new.

**Re-entering a running generator.** `t262-generator-resume-from-executing-crash.js`:

```js
var iter;
function* g() { iter.next(); }
iter = g();
try { iter.next(); } catch (e) {}   // TypeError, correct in both
iter.next();                         // interpreted: IllegalStateException: FAILED ASSERTION
```

The first reentrant `next()` throws the right TypeError but leaves the interpreter's saved
frame in a state the next resume cannot clone (`CallFrame.shallowCloneFrozen` asserts).
Compiled mode throws the TypeError both times.

**Object-rest from a Proxy with a Symbol key.** `t262-rest-destructure-proxy-symbol-gopd.js`:
`var { [sym]: _, ...rest } = proxy` where `ownKeys` returns `[sym, "a"]`. The interpreter
throws `TypeError: The object is not a string` trying to coerce the Symbol during the exclusion
check; compiled mode completes but never calls the `getOwnPropertyDescriptor` trap for `"a"`
either. Both wrong.

### 5.3 Compiled code the JVM rejects

Six reproducers where `Codegen` emits a method the class verifier refuses, so the compiled
mode crashes with a Java error before running a line, while the interpreter runs the same
program normally. These are the easiest to file: the stack trace names the generated method.

| file | program shape | compiled error |
|---|---|---|
| `compiled-relop-arg-stack-underflow.js` | `function a(x) {} function b() { a(1 < 2); }` | `IllegalStateException: Stack underflow: -1` |
| `compiled-yield-call-arg-verifyerror.js` | JS 1.7 generator, `z((yield 3))` | `VerifyError: Bad type on operand stack` |
| `t262-compound-assign-element-verifyerror.js` | `obj[p] &&= 2` inside a function | `VerifyError: Bad type on operand stack` |
| `t262-yield-in-default-param-classfile-crash.js` | `function* g() { (x = yield) => {}; }` | `ClassFileFormatException: out of range variable` |
| `t262-yield-in-forof-catch-verifyerror.js` | `yield` from a `catch` inside `for-of` in a generator | `IllegalStateException: bad local variable type: 0 at index: 10` |
| `compiled-generator-let-in-conditional-block-before-yield.js` | `function* g(c) { if (c) { let x = 1; } yield 1; }` (1.9.2; `const` and `var` compile) | `IllegalStateException: bad local variable type: 0 at index: 7` |

Notes per row. The relational-argument case needs the call to be inside another function and
the callee to declare a parameter; `1 < 2` as a direct-call argument is tracked with the wrong
stack depth. Only the parenthesised `(yield 3)` form crashes, since `z(yield 3)` is a
SyntaxError in both modes. The `&&=` / `||=` / `??=` element case affects all three operators
and only the computed-element target; variable, property and super targets are fine. The
default-parameter `yield` should be an early SyntaxError per spec, which neither mode reports;
the interpreter runs it. The for-of case depends on local-slot numbering, so the leading `Err`
declaration in the file is load-bearing, and Rhino's own test262 run expects the two
originating tests to pass, which means its harness preamble probably hides it (unverified).

### 5.4 Compiled generators and `finally`

`t262-generator-nested-finally-throw-escapes.js`:

```js
function* f() {
  try { return 42; }
  finally { try { try { return 43; } finally { throw 9; } } catch (e) {} }
}
f().next();    // interpreted {value: 42}, compiled throws 9
```

The `throw 9` must be caught by the enclosing `catch (e) {}`; compiled generator code lets it
escape. A plain (non-generator) function behaves. Rhino's own expectation file tags
`completion-values-fn-finally-abrupt.js` as compiled-only failing (reproduced as
`tests/seeds/test262-completion-values-fn-finally-abrupt.js`), which is the same
`finally`-completion machinery, so these two are worth probing together.

### 5.5 Java exceptions escaping both modes

Seventeen reproducers where the bug is in a builtin, the parser or the front end shared by both
modes, and a raw Java exception comes out of the engine past any JS `try/catch`. They are not
mode divergences; the harness classes a crash as `hard` so they surface anyway. Grouped by what
should have happened instead.

**Should be a TypeError** (missing argument or receiver check):

| file | call | exception |
|---|---|---|
| `array-find-no-args-aioobe-both-modes.js` | `Array.find()` (Rhino's static generics) | `ArrayIndexOutOfBoundsException` |
| `typed-array-set-no-args-aioobe-both-modes.js` | `new Int16Array(4).set()` | `ArrayIndexOutOfBoundsException` |
| `callsite-tostring-npe-both-modes.js` | `String(new CallSite())` | `NullPointerException` (no stack element) |
| `error-tosource-null-npe-both-modes.js` | `Error.prototype.toSource.call(null)` | `NullPointerException` |
| `t262-string-iterator-prototype-confusion-npe.js` | `iter.next.call(%StringIteratorPrototype%)` | `NullPointerException` (`this.string`) |
| `t262-proxy-gopd-undefined-npe.js` | gOPD trap returns `undefined`, target lacks the key | `NullPointerException` (`targetDesc`) |

The first entry of `tests/findings/hard.jsonl`, `Array.indexOf()` crashing in
`NativeArray.forCtor`, is the `Array.find()` family: the Array generics presumably read
`args[0]` before checking the argument count.

**Should be a RangeError** (size or range check missing):

| file | call | exception |
|---|---|---|
| `t262-typedarray-excessive-length-crash.js` | `new Int8Array({length: 2**53})` | `IllegalArgumentException` |
| `t262-bigint-asintn-large-bits-crash.js` | `BigInt.asIntN(4294967295, 1n)` | `ArithmeticException: BigInteger would overflow` |
| `string-doubling-overflow-negative-array-size.js` | `s += s` until length is exactly 2^31 | `NegativeArraySizeException` |
| `typedarray-from-array-length-2-31-illegalstate-both-modes.js` | `a[2147483647] = 0; new Uint8ClampedArray(a)` | `IllegalStateException: list.length (2147483648) exceeds Integer.MAX_VALUE` |

The last row is the array form of the first: 1.9.2 throws the RangeError for the array-like
`{length: 2**53}` but not for a real array of length 2^31; master `4c3d79b` runs past the time
limit on it instead. `new Int8Array(2147483646)` is a third shape of the missing size check
(`OutOfMemoryError: Requested array size exceeds VM limit`, after which the harness restarts
its JVM); it has no regression file because the error depends on the JVM's limits.

**Should be an Invalid Date** (range check missing):

| file | call | exception |
|---|---|---|
| `date-two-int-min-args-failed-assertion-both-modes.js` | `new Date(-2147483648, -2147483648)` | `IllegalStateException: FAILED ASSERTION` |

Both the year and the month must be -2^31; either one with an ordinary value for the other
gives an Invalid Date in both modes. Master `4c3d79b` crashes the same way.

**Should be a SyntaxError or should compile** (parser or IR bug):

| file | program | exception |
|---|---|---|
| `spread-in-call-crashes-both-modes.js` | `Math.max(...[1, 2])` | `RuntimeException: 190` / `Unexpected node type` |
| `nested-array-comprehension-bad-node-195.js` | JS 1.7 `[[0 for (x in [])] for (y in [])]` | `RuntimeException: 195` (a stray RESETVAR node) |
| `let-genexpr-switch-case-null-tokenstream.js` | JS 1.7 `case (let (a) 0 for (c in 0)):` | `NullPointerException` in the parser |
| `t262-const-nested-array-dstr-npe.js` | `for (const [[x] = [1]] = []; …)` | `NullPointerException` (`VarScope.putConst`) |

Spread in call position is the one most likely to bite real code: array spread works, call
spread parses and then neither code generator knows the node. The two JS 1.7 forms are
non-Ecma syntax upstream has discussed removing (#1056).

**Should work or throw a catchable error** (LiveConnect wrapper cast):

| file | program | exception |
|---|---|---|
| `java-array-set-prototype-classcast.js` | `J.intArray(2).__proto__ = {}` | `ClassCastException: NativeJavaArray cannot be cast to ScriptableObject` (`NativeObject.js_protoSetter`) |
| `java-string-wrapper-proto-getter-classcast.js` | `J.echo("").__proto__` | `ClassCastException: NativeJavaObject cannot be cast to ScriptableObject` |

The second row needs no write first: reading `__proto__` from the wrapper of a Java string a
Java method returned is enough on 1.9.2 (fixed on master `4c3d79b`).

`Object.setPrototypeOf(a, {})` on the same array succeeds and the next read of `a.__proto__`
crashes in `js_protoGetter` instead; the wrapper of a plain Java object (`J.__proto__ = {}`)
throws a catchable `InternalError`. The `__proto__` accessor casts its receiver to
`ScriptableObject`, which the Java wrappers are not: the cast family of §5.9.

### 5.6 Expected differences

Continuations (`new Continuation()`, capture and resume) exist only in the interpreter; the
compiled mode reports `unsupported(continuations)` and the harness logs the pair as a known
signature rather than a finding (`tests/known_signatures.txt`, decision in
`project-continuations-in-scope`). The compiled-mode fallback to the interpreter for oversized
classes is `skip`, not a finding.

### 5.7 Soft: the Symbol comparison message

`tests/findings/soft.jsonl` has one record: `n1 < a0` with a Symbol operand throws
`TypeError: The object is not a number` interpreted and `TypeError: Symbol objects may not be
compared` compiled. Same error class, same place, different message, presumably because the interpreter's
generic comparison reaches ToNumber on the Symbol while compiled code checks for Symbols
first. Reduced on 2026-10-27:

```js
try { -1 < Symbol.iterator; } catch (e) { r = e.name + ": " + e.message; }
```

Note that this reduction is a `hard` verdict, not `soft`, because the message was captured into
a string value. A program that reports its own error messages turns every soft message
difference into a hard one; worth remembering when reading a campaign's hard list.

### 5.8 Compiled `super` inside a block that declares `let` or `const`

`compiled-super-in-block-with-lexical-declaration-classcast.js`

```js
var w = { m() { { super.a = 1; const n = 0.1; return this.a; } } };
w.m();   // interpreted 1, compiled ClassCastException: LocalScope cannot be cast to NativeCall
```

An object-literal method that refers to `super` (write, read or method call) inside a block
that also declares a `let` or `const` crashes in compiled mode at the super reference, with a
raw Java `ClassCastException` that no JS `catch` sees. The two parts must share the block:
`var` instead of `let`/`const`, the declaration in a sibling block, the super reference outside
the block, and the same body without the block all agree. Class methods agree in both modes.
Strict mode and nesting the method in a function make no difference. The cast names the
mechanism: the compiled super lookup walks to the enclosing activation and expects a
`NativeCall`, but a block with lexical declarations puts a `LocalScope` in front of it. Found
by `oxpecker fuzz` on 2026-10-28; the interpreter is right.

**Second form: a `with` block or a `for (let ...)` body.**
`compiled-super-in-with-or-for-let-classcast.js` (hour-long campaign of 2026-11-17):

```js
var w = { m() { with ({}) { super.x = 1; } return this.x; } };
var l = { m() { for (let i = 0; i < 1; i++) { super.x = 1; } return this.x; } };
w.m(); l.m();   // interpreted 1, compiled ClassCastException: NativeWith cannot be cast to NativeCall
```

No lexical declaration in a block is needed: any construct that puts a scope object in front
of the method's activation does it, and on 1.9.2 a super *write* is enough here, where the
block form needs a read. Master `4c3d79b` crashes the same way (`WithScope cannot be cast to
NativeCall`). The fuzzer's form was `super.get %= n` in a destructuring for-let loop.

### 5.9 Compiled `with` over a Java object

`compiled-with-java-object-name-lookup-classcast.js`

```js
with (J) { __probe("step", typeof sum, Math.abs(-1)); }
// interpreted "function" | 1, compiled ClassCastException: NativeJavaObject cannot be cast to ScriptableObject
```

A `with` statement whose object is a LiveConnect wrapper (the harness's `J`, a Java array from
`J.intArray(2)`, a Java string a Java method returned) crashes compiled code at the first
unqualified name read inside the block, whatever the name resolves to: a member of the Java
object, a global, a `const` of the script. The stack names the mechanism: compiled code links
name reads through `ConstAwareLinker`, which looks for a frozen `const` it can fold; given a
`NativeWith` it steps to the with-object and casts it to `ScriptableObject`, which
`NativeJavaObject` and `NativeJavaArray` are not (they implement `Scriptable` directly).
`typeof name`, writes and calls take other paths and agree; an empty block agrees. Found by
`oxpecker fuzz` on 1.9.2 (hour-long campaign, 2026-10-28), shrunk by the loop itself from a
45-line program to `with (J) { v36 in Math; }`; the interpreter is right. Not checked on master
`b95f75d`.

### 5.10 Compiled rest parameter of a direct-call target

`compiled-rest-param-direct-call-target.js`

```js
function f(a, ...rest) { return rest; }
function g() { return f(0, 7); }
f(0, 7);      // interpreted [7], compiled 7
f(0, 7, 8);   // interpreted [7, 8], compiled 7
```

Once a function with a rest parameter is called by name from inside another function, compiled
code binds `rest` to the first extra argument itself instead of an array of all of them, for
every call of the function: top-level calls, `f.call(...)`, calls through an alias or a
property. Extra arguments past the first are lost; an array argument arrives as the caller's
own array, so `rest.length = 0` empties it (the form the fuzzer saw). The same function with no
caller inside a function agrees, which points at the optimizer's direct-call entry point, where
a called-by-name function gets its parameters as JVM arguments and the rest array is
presumably never built. Found by `oxpecker fuzz` on 1.9.2 (three-hour campaign, 2026-10-29) as
a global array emptied after a warm-up loop; reduced by hand, the interpreter is right. Not
checked on master `b95f75d`.

### 5.11 Labelled `for` with a loop in a function in its head

`labelled-for-head-function-loop-continue.js`

```js
L: for (var a = function () { while (false) {} }, i = 0; i < 2; i++) {
    r.push(i);
    continue L;       // interpreted: never reaches i++; compiled: "unlocated label"
}
```

A labelled `for` or `for-in` whose head holds a function containing a loop, with `continue L`
in the body, is wrong in both modes. Compiled code does not compile: Codegen throws "unlocated
label" before a line runs. The interpreter runs, but `continue L` jumps past the update, so the
loop above never ends (`[0,0,0,0,0]` with a guard; the spec gives `[0,1]`). With `for (;;) {
break; }` as the inner loop the interpreter's CodeGenerator fails an assertion instead. The label, `continue L` (`break L` and a bare
`continue` compile), the function in the head and the loop inside it are all needed; the same
function in a `while` condition or in the body compiles. The inner loop apparently takes over
the label's continue target, which the parser assigns to the next loop it finishes after the
label. Found by `oxpecker fuzz` on 1.9.2 (seeded three-hour campaign, 2026-11-01: a
destructuring `for (let [a, b] = (() => { ... for (let i ...) {} })(); ...)` with `continue
L85`); the loop's shrinker left 24 lines, reduced by hand. Not checked on master `b95f75d`.

## 6. Where the findings came from, and what is left unsorted

| source | findings |
|---|---|
| hand smoke test of the harness | 3.1 |
| Hypothesis property test (`tests/test_differential.py`) | 3.3 first form, 5.1 string concat |
| inspector export of the generators | 3.2, 5.1 computed update |
| template runs (`jit*`, `api_*`) | 4.1, 5.5 `Array.find`, `CallSite` |
| capability probe | 5.5 spread |
| replaying Rhino's own suites (`rhino-jstests`, `rhino-mozilla`) | 4.2, 5.3 first two rows, 5.5 comprehension and string doubling |
| replaying test262 (incl. `staging/`, which Rhino never runs) | 4.3, 5.2, 5.3 last three rows, 5.4, six of 5.5 |
| `oxpecker fuzz`, 2026-10-19 | 3.4, 3.5, 5.5 `toSource` |
| completion-rate test of the generation layer | 5.5 `TypedArray.set` |
| `oxpecker fuzz`, 2026-10-26 and 27 campaigns | 3.3 second form, 3.4 second form, 5.1 write-back and receiver |
| `oxpecker fuzz`, 2026-10-28 campaign | 5.8, the wider triggers of 3.4's second form |
| `oxpecker fuzz` on 1.9.2, 2026-10-28 | 5.3 last row; hour-long campaign: 5.9, the ToPrimitive form of 5.1's receiver bug |
| `oxpecker fuzz` on 1.9.2, three-hour campaign, 2026-10-29 | 5.10, 5.5 Java array `__proto__` |
| `oxpecker fuzz` on 1.9.2, seeded three-hour campaign, 2026-11-01 | 5.11 |
| `oxpecker fuzz` on 1.9.2, hour-long campaign with generator processes, 2026-11-17 | 5.5 `Date`, typed array from a 2^31 array, Java string `__proto__`; 5.8 second form |

Everything still under `tests/findings/` maps to a family above:

| record | family |
|---|---|
| `hard-ebd76c3473.js`, `hard.jsonl` #3 | 4.1 Proxy `has` |
| `hard-49b2af89f8.js`, `hard.jsonl` #2 and #5 | 5.1 computed super update |
| `hard-4bb37440c8.js`, `hard.jsonl` #4 | 3.1 direct recursive call (corrected, see §3.1) |
| `hard.jsonl` #1 (`Array.indexOf()`) | 5.5 Array generics |
| `soft.jsonl` #1 | 5.7 |
| `java-types.jsonl`, all 18 | 3.6 |

Nothing in the unsorted set is a new root cause. The fuzz loop's signature normalisation is
what keeps re-surfacing the old ones in new array shapes (`docs/upstream-crosscheck.md` §6.4);
bucketing by the first differing leaf rather than the elided context would make a campaign's
hard list read as root causes instead of variants.

## 7. What an upstream reader would want first

In rough order of how clear-cut the report is:

1. The five JVM-verifier crashes (§5.3): deterministic, tiny, stack trace names the method.
2. Proxy `has` before `get` (§4.1) and arrow `prototype` (§4.2): spec-clear, one-line repro each.
3. The `super` family (§5.1): four interpreter bugs in one icode path; Rhino already tags the
   related test262 case as interpreter-only, so the maintainers know the area.
4. `++` int32 wrap and `-0` (§3.2, §3.3): wrong numbers from ordinary code at top level.
5. BigInt in a typed local (§3.4): any `var b = -1n` inside a function throws compiled.
6. The spread-in-call crash (§5.5): the most likely of the engine crashes to hit real code.
7. The both-mode `NullPointerException`s and range crashes (§5.5), one issue each, labelled
   "Java Exception Leaking".
8. §3.1 as a comment on the existing #2407, with the `==` / `===` characterisation.

The Java-type split (§3.6) and the boxed `Double` identity (§3.5) are embedder-facing and
upstream has called the former by design; they are worth a note on #350 rather than new
issues.

## 7. The hour-long campaigns

Five `oxpecker fuzz` campaigns of an hour or more so far, same settings (five mutation steps
per round, `--p-new 0.2`, shrink budget 150 runs), the first four from an empty corpus; from 2026-11-01
`scripts/campaign.sh start` seeds a campaign with the previous one's corpus after `oxpecker cmin`. The 1.9.2 campaign was the
first with the lineage shrinker firing in the loop (it had been silenced by a known-signature
check that ran before the shrink; fixed the same day).

| | 2026-10-27, master `b95f75d`, seed 11 | 2026-10-28, master, seed 12 | 2026-10-28, Rhino 1.9.2, seed 15 | 2026-10-29, 1.9.2, three hours, no seed | 2026-11-01, 1.9.2, three hours, seeded |
|---|---|---|---|---|---|
| runs (each in both modes) | 7 343 | 7 275 | 7 038 | 16 393 | 21 275 |
| corpus entries | 4 517 | 4 227 | 4 928 | 7 333 | 1 032 seeded, 16 085 at the end |
| probes covered / total | 18 081 / 46 447 | 14 717 / 45 562 | 14 138 / 42 935 | 17 484 / 43 543 | 12 712 / 42 300 (understated, see below) |
| hard verdicts (continuations) | 609 (582) | 426 (357) | 285 (258) | 979 (839) | 1 351 |
| lineage cache misses | 58 % | 56 % | 61 % | 74 % | 91 % (rebuilt from the parent) |
| reproducers written | 3 | 5 | 6 | 6 (5 files) | 12 (11 files) |
| new root causes | 3.4 second form | 5.8 | 5.9 | 5.10, 5.5 Java array `__proto__` | 5.11 |
| kept under | `runs/` (not kept) | `runs/fuzz-2026-10-28/` | `runs/fuzz-2026-10-28-r192-1h/` | `runs/fuzz-2026-10-29-r192-3h/` | `runs/fuzz-2026-11-01-r192-3h-seeded/` |

What the reproducers were, per campaign, after triage with `oxpecker shrink` on each one and
on the first program per coarse signature in `findings/hard.jsonl`:

- **Seed 11 (master):** the relop bug (3.4 second form) hid under the known coarse signature
  `ok vs throw`; the three reproducers written were the super write-back (5.1) under
  value-dependent signatures.
- **Seed 12 (master):** 5.8 written and shrunk by the loop; one `-0` lost (3.3) printing an
  array; two super write-backs (5.1); one relop case whose probe referenced names the source
  shrinker had deleted. The five coarse signatures all shrank to 3.3, 3.4 and 5.1.
- **Seed 15 (1.9.2):** 5.9 written and shrunk by the loop (45 lines to three); two super
  write-backs (5.1) under Object and Array prototype shapes; three harness artefacts, not Rhino
  bugs: `J + 0` and `J + "x"` print the Java probe object's identity hash with text glued on,
  which the comparator's mask (a word boundary after the hash) let through. `JavaProbe` now
  prints a fixed string and the mask also covers array descriptors (`[I@…`) and glued text.
  The coarse signatures: `true vs false` is 3.3 under `Object.is`; `NegativeArraySizeException`
  is the string-doubling crash of 5.5; `ok vs throw` is 5.1's wrong receiver in a form where it
  shows as a missing `TypeError` (now in that regression).
- **Three hours (1.9.2):** throughput fell from 3.5 to 1.5 runs a second as the corpus grew
  (lineage cache misses, each a full replay of the entry's history; a miss now rebuilds from
  the parent's lineage). Of the six new signatures, three were known bugs in new shapes:
  5.1's wrong receiver (a `TypeError` interpreted, from `super.__proto__ <<= 0` over a
  `Float64Array`), 5.1's write-back, 3.4's BigInt local (shown as a missing trace step). The new
  ones: the Java array `__proto__` crash (5.5, shrunk by the loop to four lines) and, from a
  global array emptied after a warm-up loop, 5.10 (reduced by hand). The known-signature
  entries without a reproducer replay and shrink to known regressions too (the generator crash
  of 5.3, 3.2, 3.3, the `NativeJavaArray` form of the new 5.5 row). Since then a new finding's
  reproducer names the regressions it shares evidence with (`// Related:`, `oxpecker related`).
- **Three hours, seeded (1.9.2):** started from the previous campaign's corpus minimised to
  1 032 entries; 2.0 runs a second at the end, 30 % more runs than the unseeded campaign. The
  corpus grew to 16 085 entries because 71 % of runs were added: a JVM restart (the watchdog
  killing a runaway program) lost the harness's cumulative coverage, so every probe the next
  runs hit was new again, and the final 12 712 probes count only what the last JVM saw. The
  same restarts probably inflated the earlier campaigns' corpora. A restarted JVM now reloads the
  corpus's `coverage.exec`, saved after every corpus addition. Of the eleven reproducers, ten
  are known bugs: 5.1's write-back three times (`super.__proto__ ??=`, `||=`, `&&=`), 5.1's
  wrong receiver twice (`super.__proto__ +=` over an `Int8Array`), 3.2, 3.3, 3.4 twice
  (`var m = ~p` with a BigInt argument; a captured relop), and 5.6's continuations (`new
  Continuation()` compiled). The new one is 5.11, which the shrinker left at 24 lines.

### The hour-long campaign of 2026-11-17 (generator processes, a JVM per mode)

The first campaign on the faster loop (`--workers 5`, one JVM per mode, findings shrunk in
their own process; run through `scripts/profile_loop.py`, so engine262 did not judge the
findings), Rhino 1.9.2, seed 11, seeded with the 462-entry corpus of `runs/judge-2026-11-16/`.
Kept under `runs/fuzz-2026-11-17-perf-60m/` (`min/` has every reproducer after `oxpecker
shrink --budget 400`, `reduced/` the hand reductions).

| | 2026-11-17, 1.9.2, one hour, seeded |
|---|---|
| runs (each in both modes) | 136 197 (37.8 a second, steady over the hour) |
| corpus entries | 462 seeded, 1 732 at the end |
| Rhino covered | 58.8 % of lines, 49.8 % of branches, 58.2 % of methods (three-hour fresh campaign of 2026-11-02: 56 % and 47 %) |
| probes covered | 36 870 at the start, 44 027 at the end (the two JVMs' counts added up); 22 a minute in the last quarter, doubling the run would add about 2.5 % |
| hard verdicts (continuations) | 7 337 (6 020) |
| new findings | 40 (37 shrunk, 3 recorded as found); 51 signatures in `hard.jsonl` |
| reproducers written | 33 |
| new root causes | 5.5 `Date` assertion, 5.5 typed array from a 2^31 array; new forms of 5.8 and of 5.5's Java wrapper `__proto__` |

What the 33 reproducers and the 16 signatures without one were, after `oxpecker shrink` on
each (the two continuation signatures aside):

- **Known bugs in new shapes, 33:** 5.1's write-back eight times (`super.__proto__ ??=` and
  `||=` over an Object, Array, Date, RegExp, Math, WeakSet, Int8Array and Float64Array
  prototype); 5.1's wrong receiver five times (`super.length ||=` and `<<=` over an
  Int8Array, `super.__proto__ <<=` over a BigInt64Array, and `super.__proto__ +=` over a Java
  array twice, where the interpreter's wrong receiver reaches 5.5's `__proto__` crash); 3.4
  eight times (`~p`, `-p`, `v % v`, `0 < p` and `v < 2` with a BigInt, shown as a `TypeError` name,
  a missing trace step or a later statement not run); 3.3 four times; 3.2 three times; 5.10
  three times; 5.9 twice (`with` over a Java array and over `J`).
- **Known crashes, 4:** the generator `let` before `yield` (5.3), `TypedArray.set()` with no
  arguments, the string-doubling `NegativeArraySizeException` and
  `new Int8Array(2147483646)` running the JVM out of memory (all 5.5).
- **Continuations in a `try`, 4:** `new Continuation()` throws a catchable error compiled, so
  the `catch` runs and a later probe differs (5.6). The known-signature check does not see
  these, since the status is `ok` in both modes.
- **New, 4:** `new Date(-2147483648, -2147483648)` and a typed array from an array of length
  2^31 (both 5.5), `super` in a `with` block or a for-let body (5.8, second form),
  `J.echo("").__proto__` (5.5, fixed on master).
- **Harness artefacts, 4 signatures, not Rhino bugs:**
  - Three are the overload list in a Java method's source text (`function sum() {/* int
    sum(int[]) double sum(double[]) */}`) coming out in a different order in each mode. With a
    JVM per mode the two lists come from two JVMs, and Java does not fix the order reflection
    returns methods in. The shrunk programs do not show it, because it depends on the JVM, not
    the program. The canonicaliser should sort or mask that list before `--mode-jvms` is used
    for a real campaign.
  - One is `J.size(null)`: the harness's own Java method throws a `NullPointerException`,
    which the harness reports as a crash when the script does not catch it. Inside
    `try`/`catch` both modes agree.

**Judged on engine262 afterwards** (`oxpecker judge` on the 49 shrunk programs; in `oxpecker
fuzz` the shrink process judges each finding itself, and `scripts/profile_loop.py` now does
too when `judge/` is installed):

| label | count | what they are |
|---|---|---|
| compiled-wrong | 18 | 3.4 seven times, 3.3 four, 3.2 three, 5.10 three, the generator `let` crash of 5.3 |
| interpreted-wrong | 10 | 5.1: the write-back eight times, the wrong receiver twice |
| all-wrong | 3 | both modes crash where engine262 runs or throws a `TypeError`: the `Date` assertion, `TypedArray.set()`, `new Int8Array(2147483646)` |
| not-judged | 16 | 13 use Java (`J`), one continuations, two engine262 could not run (the 2^31-length array timed out after 60 s, the doubled string is too long for it) |
| inconclusive | 2 | two of the overload-order artefacts: in one JVM the modes agree |

Every judged label agrees with the family's "right" column in §2. Of the new regressions,
the `Date` crash is all-wrong, `super` in a `with` block compiled-wrong, and the other two are
not judged (timeout, Java). One shrunk program, `min/hard-e662570ac4.js` (3.4, a BigInt stored
into a number-typed local), replays as equal although the 260-line original still shows the
difference: `oxpecker shrink` kept a candidate that no longer reproduces, which needs looking
at.

Two lessons that repeat: exact signatures are too fine when the probe prints an object (every
prototype shape is a new signature for one bug) and too coarse when only the status differs
(unrelated compiled-mode throws share one), so the first program per signature in `hard.jsonl`
is what to read after a campaign; and a reproducer that prints a host object is a harness
question before it is a Rhino one.
