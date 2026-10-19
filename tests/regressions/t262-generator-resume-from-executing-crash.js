// Rhino b95f75d: a generator that reenters itself (calls `next`/`return`/`throw` on its own
// iterator object from inside its own body, while it is still "executing") should get a plain
// TypeError per GeneratorValidate. The first, reentrant call does throw that TypeError correctly,
// but it leaves the interpreter's saved call frame un-frozen; the *next* outer `.next()` call then
// tries to clone that frame to resume it and hits an internal assertion:
//   interpreted: java.lang.IllegalStateException: FAILED ASSERTION
//     (Kit.codeBug -> ACallFrame.<init> -> CallFrame.shallowCloneFrozen -> Interpreter.resumeGenerator)
//   compiled:    ok (throws the correct TypeError both times)
// Interpreted-only crash.
// Originating tests (same root cause, one per generator method):
//   built-ins/GeneratorPrototype/next/from-state-executing.js
//   built-ins/GeneratorPrototype/return/from-state-executing.js
//   built-ins/GeneratorPrototype/throw/from-state-executing.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-generator-resume-from-executing-crash.js --language-version 250
var iter;
function* g() { iter.next(); }
iter = g();
try { iter.next(); } catch (e) {}
iter.next();
