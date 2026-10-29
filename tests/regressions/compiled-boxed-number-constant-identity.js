// Rhino b95f75d: compiled code passes the same boxed java.lang.Double instance for a numeric
// constant every time it is evaluated, the interpreter boxes a fresh one. A Java host function
// that returns its argument (J.echo, any identity-preserving embedder API) makes this visible to
// script as wrapper identity:
//   J.echo(3 + 1e21) === J.echo(3 + 1e21)    interpreted: false    compiled: true
// Java embedders comparing references (or using IdentityHashMap) see the same split.
// Found by `oxpecker fuzz` (runs/fuzz-2026-10-19, eight `@N[Java java.lang.Double]` signatures,
// e.g. hard-16b08c88ef), 2026-10-19.
// Replay: oxpecker diff tests/regressions/compiled-boxed-number-constant-identity.js
function f1() { return J.echo(3 + 1e21); }
function f2() { return J.echo(3 + 1e21); }
var a = f1(), b = f2();
__probe("p", a === b);
a === b;
