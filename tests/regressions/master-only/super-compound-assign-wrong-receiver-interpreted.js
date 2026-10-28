// Rhino b95f75d: a compound assignment to a super property in an object-literal method reads
// the property with the wrong receiver in the interpreted mode. With `__proto__` (an accessor
// on Object.prototype) and a Date instance as the literal's prototype, `super.__proto__ /= 10`
// reads Date.prototype instead of the instance, and converting Date.prototype to a number throws
// TypeError ("this" is not an instance of class NativeDate). A plain read (`super.__proto__`)
// and an explicit `var x = super.__proto__; x /= 10` agree in both modes, so only the compound
// path is affected; the compiled mode runs it without throwing. The same family as
// super-logical-assign-proto-writeback-interpreted.js.
//   interpreted: TypeError    compiled: no exception    (the prototype stays the instance in both)
// Found by `oxpecker fuzz` (hour-long campaign, super_property_update), 2026-10-27.
// Replay: oxpecker diff tests/regressions/super-compound-assign-wrong-receiver-interpreted.js
var d = new Date(0);
var o = { __proto__: d, m() { super.__proto__ /= 10; } };
var threw = "no";
try { o.m(); } catch (e) { threw = e.name; }
__probe("step", threw, Object.getPrototypeOf(o) === d, Object.getPrototypeOf(o) === Date.prototype);
