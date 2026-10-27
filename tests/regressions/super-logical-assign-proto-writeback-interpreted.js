// Rhino b95f75d: a short-circuiting logical assignment to a super property (`||=`, `??=`) in an
// object-literal method writes the value it read back through the `super` reference in the
// interpreted mode. With `__proto__` (an accessor on Object.prototype) that write is observable:
// `super.__proto__` reads Array.prototype through `this`, and writing it back sets the object's
// prototype to Array.prototype. The compiled mode leaves the prototype alone, as the spec says
// (GetValue is truthy, no PutValue). `+=` assigns in both modes and agrees.
//   interpreted: proto === arr1 false, proto === Array.prototype true
//   compiled:    proto === arr1 true,  proto === Array.prototype false
// Found by `oxpecker fuzz` (first ten-minute campaign, super_property_update), 2026-10-26.
// Replay: oxpecker diff tests/regressions/super-logical-assign-proto-writeback-interpreted.js
var arr1 = [5];
var o = { __proto__: arr1, m() { super.__proto__ ||= 5; } };
o.m();
__probe("step", Object.getPrototypeOf(o) === arr1, Object.getPrototypeOf(o) === Object.prototype, Object.getPrototypeOf(o) === Array.prototype);
