// Rhino b95f75d: calling a static-looking Array method with no arguments (`Array.find()` — in
// Rhino Array.prototype methods are also reachable as Array.<name> generics) throws a raw Java
// ArrayIndexOutOfBoundsException past JS try/catch, in both modes, instead of a TypeError.
// Found by the jit1_function template run (templates_fz.py), 2026-10-18.
// Replay: oxpecker diff tests/regressions/array-find-no-args-aioobe-both-modes.js
try {
  Array.find();
} catch (e) {
  print("caught " + e);
}
