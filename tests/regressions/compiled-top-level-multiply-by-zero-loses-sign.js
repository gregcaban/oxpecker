// Rhino b95f75d: at script top level the compiled mode computes `x * 0` as +0 for a negative
// `x` held in a variable; the interpreter gives -0 (IEEE 754, and what the spec requires).
// Inside a function (`function f(p) { var y = p * 0; ... }`) both modes give -0, and a literal
// `-3 * 0` is folded to -0 by both. `Object.is(y, -0)` and `1 / y` show it.
//   interpreted: Object.is(y, -0) true,  1 / y -Infinity
//   compiled:    Object.is(y, -0) false, 1 / y Infinity
// Found by `oxpecker fuzz` (first ten-minute campaign, negative_zero / number_computation), 2026-10-26.
// Replay: oxpecker diff tests/regressions/compiled-top-level-multiply-by-zero-loses-sign.js
var x = -3;
var y = x * 0;
__probe("step", Object.is(y, -0), 1 / y, Object.is(x * 0, -0));
