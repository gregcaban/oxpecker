// Rhino 1.9.2 and master b95f75d: a compound assignment to a super property in an object-literal
// method reads the property with the wrong receiver in the interpreted mode: the getter runs
// with `this` = the prototype instead of the instance. The compiled mode passes the instance,
// as the spec requires (super property access uses the current `this` as receiver). A plain
// read (`super.g`) and an explicit `var x = super.g; x += 1` agree in both modes, so only the
// compound path is affected. With a typed array as the prototype, `super.length += 1` therefore
// runs in the interpreter and throws TypeError in compiled code (the accessor rejects a plain
// object as `this`). With `super.__proto__ **= 3` and an array prototype whose `toString` is
// not callable, the compiled mode reads the array through the instance and ToPrimitive throws
// (correct); the interpreter reads Array.prototype through the prototype and converts it
// quietly. The form found on master, `super.__proto__ /= 10` with a Date prototype
// (interpreted TypeError '"this" is not an instance of class NativeDate'), does not show on 1.9.2.
// Same family as super-logical-assign-proto-writeback-interpreted.js.
//   interpreted: "proto" | "1" | "1"     compiled: "instance" | "TypeError" | "TypeError"
// Found by `oxpecker fuzz` (hour-long campaign on master, super_property_update), 2026-10-27;
// the getter and typed-array forms from the first 1.9.2 campaign and the ToPrimitive form from
// the hour-long 1.9.2 campaign, 2026-10-28.
// Replay: oxpecker diff tests/regressions/super-compound-assign-wrong-receiver-interpreted.js
var p = { get g() { return this === o ? "instance" : this === p ? "proto" : "?"; } };
var o = { __proto__: p, m() { return super.g += ""; } };
var ta = new Int8Array(2);
var t = { __proto__: ta, m() { super.length += 1; return 1; } };
var a = []; a.toString = 0;
var n = { __proto__: a, m() { super.__proto__ **= 3; return 1; } };
function outcome(f) { try { return String(f()); } catch (e) { return e.name; } }
__probe("step", outcome(() => o.m()), outcome(() => t.m()), outcome(() => n.m()));
