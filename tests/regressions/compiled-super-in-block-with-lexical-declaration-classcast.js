// Rhino b95f75d: in compiled mode an object-literal method that refers to `super` (a property
// write, read or method call) inside a block that also declares a `let` or `const` crashes at
// the super reference with java.lang.ClassCastException "LocalScope cannot be cast to
// NativeCall"; the interpreter runs it normally. Both parts must share the block: `var`
// instead of `let`/`const`, the declaration in a sibling block, the super reference outside
// the block, and the same method body without the block all agree. Class methods agree in
// both modes, so only object-literal methods are affected; strict mode and whether the
// method sits at top level or inside a function make no difference.
//   interpreted: "1" | "2" | "string"     compiled: crash (ClassCastException) at the first case
// Found by `oxpecker fuzz` (hour-long campaign, a super property assignment in a for loop whose
// body also declared a `const`), 2026-10-28.
// Replay: oxpecker diff tests/regressions/compiled-super-in-block-with-lexical-declaration-classcast.js
var w = { m() { { super.a = 1; const n = 0.1; return this.a; } } };
var r = { __proto__: { a: 2 }, m() { { let n = 0; return super.a; } } };
var c = { m() { { const n = 0; return typeof super.toString(); } } };
__probe("step", w.m(), r.m(), c.m());
