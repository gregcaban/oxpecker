// Rhino b95f75d: per spec, a `yield` expression inside a parameter default initializer of a
// function that is itself a generator (or an arrow/nested function whose parameters belong to a
// generator context) is an early SyntaxError. Rhino does not enforce that early error: it parses
// the default-parameter `yield` and only fails during compiled-mode bytecode generation:
//   interpreted: ok (no error at all)
//   compiled:    org.mozilla.classfile.ClassFileWriter$ClassFileFormatException: out of range variable
// Compiled-only crash.
// Originating tests (same root cause/signature, `x = yield` as a parameter default):
//   language/expressions/arrow-function/param-dflt-yield-expr.js
//   language/expressions/object/method-definition/generator-param-init-yield.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-yield-in-default-param-classfile-crash.js --language-version 250
function* g() {
  (x = yield) => {};
}
