// Rhino b95f75d: calling a function that declares a (here unused) formal parameter, passing a
// relational-comparison expression (`1 < 2`) as the argument, from inside another function
// (not from top-level script code), makes the bytecode generator mis-track the operand stack
// depth: the JVM class verifier then rejects the generated method.
//   interpreted: ok (returns normally)
//   compiled:    java.lang.IllegalStateException: Stack underflow: -1
// Originating test: js1_8_1/trace/trace-test.js (`nestedExit`/`nestedExitLoop`, part of a large
// JIT-tracing mini-testsuite; `nestedExitLoop`'s call `nestedExit(j < 7)` is what crashes - the
// surrounding loops and the `if`/`++q` body are incidental).
// Found by replaying Rhino's legacy Mozilla suite (oxpecker corpus rhino-mozilla), 2026-10-18.
// Replay: oxpecker diff tests/regressions/compiled-relop-arg-stack-underflow.js --language-version 250
function a(x) {} function b() { a(1 < 2); }
