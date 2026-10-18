// Rhino b95f75d: update expressions through a computed super reference are wrong in the
// interpreter. `++super[k]` evaluates to the *key* ("p") instead of 2, and `super[k]--` leaves an
// own property "0" with value "p" on the receiver. Compiled mode is correct. Same area as the
// test262 case Rhino tags interpreted-only (prop-expr-getsuperbase-before-topropertykey-...).
//   interpreted: ["p", ...own "0": "p"]      compiled: [2, ...]
// Found by the inspector export (super_in_object_literal), minimised by hand, 2026-10-18.
// Replay: oxpecker diff tests/regressions/interpreted-computed-super-update.js
var proto = {p: 1};
var o = {
  __proto__: proto,
  inc(k) { return ++super[k]; },
  dec(k) { return super[k]--; }
};
var r1 = o.inc("p");
var r2 = o.dec("p");
__probe("p", r1, r2, Object.getOwnPropertyNames(o), proto.p, o.p);
[r1, r2];
