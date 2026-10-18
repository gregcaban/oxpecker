// Adapted from test262 test/language/expressions/super/
// prop-expr-getsuperbase-before-topropertykey-putvalue-increment.js (BSD licence, Copyright (C)
// 2024 André Bargull). Rhino's test262.properties marks it as failing in interpreted and debugger
// modes only. GetSuperBase must happen before ToPropertyKey, so swapping the prototype inside the
// key's toString must not affect which object `super[key]` reads; expected result is 2.
var proto = { p: 1 };
var proto2 = { p: -1 };
var obj = {
  __proto__: proto,
  m() { return ++super[key]; }
};
var key = {
  toString() { Object.setPrototypeOf(obj, proto2); return "p"; }
};
var r = obj.m();
__probe("result", r, obj.p, proto.p, proto2.p);
r;
