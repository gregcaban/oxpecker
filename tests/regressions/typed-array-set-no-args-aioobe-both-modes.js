// Rhino b95f75d: `TypedArray.prototype.set()` called with no arguments throws a raw Java
// ArrayIndexOutOfBoundsException (NativeTypedArrayView.js_set, args[0] on an empty array) past
// JS try/catch, in both modes, instead of a TypeError.
// Found by the generation layer's completion-rate test (tests/il/test_completion_rhino.py,
// generate(1030, families={calls, values}), `computed_method_call`), 2026-10-26.
// Replay: oxpecker diff tests/regressions/typed-array-set-no-args-aioobe-both-modes.js
var a = new Int16Array(4);
try {
  a["set"]();
} catch (e) {
  print("caught " + e);
}
