// Rhino b95f75d: in compiled mode a function local that is assigned the result of an operator the
// optimizer treats as always-numeric (unary -, ~, *, ++) is typed as a double; when the operands
// are BigInts, storing the result throws instead of keeping the BigInt:
//   function f() { var b = -1n; return b; }       interpreted: -1n    compiled: TypeError
//   function f(a) { var n = a * a; return n; }    f(10n): 100n vs TypeError: Cannot convert BigInt to a number
// Also `let`, `var b = ~1n`, `var n = 1n; n++`. Agrees: `var b = 1n` (no operator), `return -1n`
// (no local), top-level `var b = -1n`, `n = a + a` (+ is not assumed numeric).
// Generalises t262-bigint-to-number-overflow-throws.js (same root cause).
// Found by `oxpecker fuzz` (runs/fuzz-2026-10-19, hard-ca304333ca / hard-e91776e34c), 2026-10-19.
// Replay: oxpecker diff tests/regressions/compiled-bigint-in-number-typed-local.js
function f() { var b = -1n; return b; }
f();
