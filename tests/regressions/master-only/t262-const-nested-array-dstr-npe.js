// Rhino b95f75d: a C-style `for` loop whose head is a `const` declaration with a *nested* array
// destructuring pattern that has a default (`const [[x] = [1]] = []`) crashes both evaluators
// while setting up the loop's per-iteration const bindings:
//   both modes: java.lang.NullPointerException: Cannot invoke
//     "VarScope.putConst(Context, String, Pod...)" because ... is null
// (the inner pattern's binder looks up a VarScope that was never installed for the nested
// destructuring target). Crashes in both interpreted and compiled mode.
// Originating tests (same root cause, `for (const [[...]] ...)`):
//   language/statements/for/dstr/const-ary-ptrn-elem-ary-elem-init.js
//   language/statements/for/dstr/const-ary-ptrn-elem-ary-elem-iter.js
//   language/statements/for/dstr/const-ary-ptrn-elem-ary-rest-init.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-const-nested-array-dstr-npe.js --language-version 250
var i = 0;
for (const [[x] = [1]] = []; i < 1; i++) { }
