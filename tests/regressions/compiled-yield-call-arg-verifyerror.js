// Rhino b95f75d: inside a JS 1.7 generator function (implicit, from a bare `yield` in the body),
// using a parenthesized `yield` expression as a call argument (`z((yield 3))`) parses fine but
// the bytecode generator emits a stack frame the JVM verifier rejects:
//   interpreted: ok (generator created normally)
//   compiled:    java.lang.VerifyError: Bad type on operand stack
// Only the parenthesized form triggers it; `z(yield 3)` is a plain SyntaxError in both modes.
// Originating tests: js1_7/decompilation/regress-352026.js and
// js1_8_1/decompilation/regress-352026.js (both titled "decompilation of yield in argument
// lists", but the crash happens on ordinary compilation, no decompile/toString needed).
// Found by replaying Rhino's legacy Mozilla suite (oxpecker corpus rhino-mozilla), 2026-10-18.
// Replay: oxpecker diff tests/regressions/compiled-yield-call-arg-verifyerror.js --language-version 250
function g() { z((yield 3)); }
