// Rhino b95f75d: `Number(bigIntValue)` for a BigInt that overflows to Infinity (e.g. `2n**1024n`,
// which is above Number.MAX_VALUE) works correctly when the BigInt is used directly, but once the
// BigInt is first stored in a local `var` the compiled-mode path mishandles the conversion:
//   interpreted: ok, Number(x) == Infinity
//   compiled:    TypeError: Cannot convert BigInt to a number
// (inlining `Number(2n ** 1024n)` directly, or at top level, does not reproduce it — it needs the
// intermediate local variable inside a function, suggesting the compiler's type inference wrongly
// assumes the variable is already a primitive number and skips the BigInt-aware conversion).
// Compiled-only throw; interpreted does not throw.
// Originating test: staging/sm/BigInt/Number-conversion-rounding.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-bigint-to-number-overflow-throws.js --language-version 250
function f() {
  var x = 2n ** 1024n;
  return Number(x);
}
f();
