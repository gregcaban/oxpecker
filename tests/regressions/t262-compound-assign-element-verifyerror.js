// Rhino b95f75d: the short-circuit compound assignment operators (`&&=`, `||=`, `??=`) all
// compile to invalid bytecode when the assignment target is a *computed element* access on a
// local object (`obj[p] &&= value` etc., inside a function so the operand types are locals, not
// globals):
//   interpreted: ok
//   compiled:    java.lang.VerifyError: Bad type on operand stack
// Global-variable, local-variable, property, and super-property/element targets for the same
// operators are unaffected (checked individually); only the plain element-access target crashes,
// for all three operators.
// Compiled-only crash.
// Originating test: staging/sm/expressions/short-circuit-compound-assignment.js
//   (the `testAnd_Element`/`testOr_Element`/`testCoalesce_Element` cases).
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-compound-assign-element-verifyerror.js --language-version 250
function f() {
  let p = 123;
  let obj = { [p]: 1 };
  return [(obj[p] &&= 2), obj[p]];
}
f();
