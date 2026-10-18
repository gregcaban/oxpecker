// Rhino b95f75d: compiled mode loses negative zero. A top-level variable holding a small
// integer is kept as an int by the optimizer, and `int * 0` cannot represent IEEE -0.
//   interpreted: -0 (spec)      compiled: 0        (Object.is(n * 0, -0): true vs false)
// Also: `var n = 0; n = ~n; n * 0`, `var n = 1; n = ~n; n * 0`. Agrees when n is a function
// local, when the value is constant-folded (`~0 * 0`), or when n comes from `-n - 1`.
// Found by the property test (tests/test_differential.py), shrunk by Hypothesis, 2026-10-18.
// Replay: oxpecker diff tests/regressions/compiled-negative-zero-lost-in-int-arith.js
var n = -1;
n * 0;
