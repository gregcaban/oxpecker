// Rhino 1.9.2 and master b95f75d: in compiled mode an object-literal method that refers to
// `super` inside a block that also declares a `let` or `const` crashes at the super reference
// with a java.lang.ClassCastException the script cannot catch; the interpreter runs it
// normally. On 1.9.2 the message is "NativeWith cannot be cast to NativeCall" and only a super
// property *read* in such a block crashes (the `r` case); a super write (`w`) and a super method
// call (`c`) run. On master b95f75d it is "LocalScope cannot be cast to NativeCall" and all three
// crash. Both parts must share the block: `var` instead of `let`/`const`, the declaration in a
// sibling block, the super reference outside the block, and the same body without the block all
// agree. Class methods agree in both modes; strict mode and nesting the method in a function
// make no difference.
//   interpreted: "1" | "2" | "string"     compiled: crash (ClassCastException)
// Found by `oxpecker fuzz` on master (hour-long campaign, a super property assignment in a for
// loop whose body also declared a `const`), 2026-10-28.
// Replay: oxpecker diff tests/regressions/compiled-super-in-block-with-lexical-declaration-classcast.js
var w = { m() { { super.a = 1; const n = 0.1; return this.a; } } };
var r = { __proto__: { a: 2 }, m() { { let n = 0; return super.a; } } };
var c = { m() { { const n = 0; return typeof super.toString(); } } };
__probe("step", w.m(), r.m(), c.m());
