// Rhino b95f75d: `super.x++` where the inherited value is a string does string concatenation in
// the interpreter ("a" -> "a1") instead of ToNumber ("a" -> NaN, as compiled mode does).
// Related to tests/regressions/interpreted-computed-super-update.js (++super[k] returns the key).
//   interpreted: "a1"      compiled: NaN
// Found by the property test (tests/test_differential.py), 2026-10-18.
// Replay: oxpecker diff tests/regressions/interpreted-super-increment-string-concat.js
var proto = {x: "a"};
var o = {
  __proto__: proto,
  upd() { super.x++; return this.x; }
};
var r = o.upd();
__probe("p", r, o.x, proto.x);
r;
