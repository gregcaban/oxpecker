// Rhino b95f75d: ++ and -- wrap at the int32 boundary in compiled mode when the target is not an
// optimizer-typed function local: top-level variables, locals of functions that need an activation
// (eval / arguments / with), object properties and array elements.
//   var n = 2147483647; n++;         interpreted: 2147483648     compiled: -2147483648
//   var n = -2147483648; n--;        interpreted: -2147483649    compiled: 2147483647
//   function f() { eval("1"); var n = 2147483647; return n++ + n; }   4294967295 vs -1
// `n += 1` and `n = n + 1` are correct; so is n++ inside a plain function (optimizer path).
// Found by the inspector export (activation_pair), reduced with variants in runs/inc, 2026-10-18.
// Replay: oxpecker diff tests/regressions/compiled-increment-int32-overflow.js
var n = 2147483647;
n++;
var m = -2147483648;
m--;
var o = {p: 2147483647};
o.p++;
__probe("p", n, m, o.p);
[n, m, o.p];
