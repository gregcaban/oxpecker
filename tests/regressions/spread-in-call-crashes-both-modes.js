// Rhino b95f75d: spread in a call expression is not rejected by the parser but neither evaluator
// can compile it. Both modes throw a raw Java RuntimeException out of compileString (not a JS
// SyntaxError), which a JS try/catch cannot intercept:
//   interpreted: java.lang.RuntimeException: 190   (CodeGenerator.badTree)
//   compiled:    java.lang.RuntimeException: Unexpected node type ...
// Array spread (`[...a]`) works. Found by the capability probe (oxpecker probe), 2026-10-18.
// Replay: oxpecker diff tests/regressions/spread-in-call-crashes-both-modes.js
Math.max(...[1, 2]);
