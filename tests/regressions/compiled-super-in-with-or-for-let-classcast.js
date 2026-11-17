// Rhino 1.9.2: in compiled mode an object-literal method that refers to `super` inside a `with`
// block or inside the body of a `for (let ...)` loop crashes at the super reference with
// java.lang.ClassCastException "NativeWith cannot be cast to NativeCall", which the script
// cannot catch; the interpreter runs it normally. A super property *write* is enough, where
// compiled-super-in-block-with-lexical-declaration-classcast.js (a plain block declaring `let`
// or `const`) needs a read on 1.9.2. Same mechanism: the compiled super lookup takes the
// innermost scope for the method's activation. Master 4c3d79b crashes the same way ("WithScope
// cannot be cast to NativeCall").
//   interpreted: 1 | 1     compiled: crash (ClassCastException)
// Found by `oxpecker fuzz` on 1.9.2 (hour-long campaign with generator processes, 2026-11-17:
// `super.get %= n` in a destructuring for-let loop), shrunk by `oxpecker shrink` to 30 lines,
// reduced by hand.
// Replay: oxpecker diff tests/regressions/compiled-super-in-with-or-for-let-classcast.js
var w = { m() { with ({}) { super.x = 1; } return this.x; } };
var l = { m() { for (let i = 0; i < 1; i++) { super.x = 1; } return this.x; } };
__probe("with", w.m());
__probe("for-let", l.m());
