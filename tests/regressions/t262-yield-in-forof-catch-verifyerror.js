// Rhino b95f75d: a generator with a `for-of` loop whose body's `try/catch` yields from the
// `catch` block compiles to bytecode the JVM verifier rejects, but only once enough other
// top-level declarations precede it to shift local-variable slot numbering (the exact slot the
// verifier complains about is index 10 here; trimming the leading `Err` declaration changes which
// slot is bad or makes it disappear). Reproduced standalone, no test262 harness:
//   interpreted: ok
//   compiled:    java.lang.IllegalStateException: bad local variable type: 0 at index: 10
// Compiled-only crash.
// Originating tests (same root cause/signature, `yield`/`yield *` from inside a for-of's catch):
//   language/statements/for-of/yield-from-catch.js
//   language/statements/for-of/yield-star-from-catch.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-yield-in-forof-catch-verifyerror.js --language-version 250
function Err(message) {
  if (!(this instanceof Err)) return new Err(message);
  this.message = message;
}
function* values() { yield 1; yield 1; }
var dataIterator = values();
var controlIterator = (function* () {
  for (var x of dataIterator) {
    try {
      throw new Error();
      throw new Err('unreachable');
    } catch (err) {
      yield;
    }
  }
})();
controlIterator.next();
controlIterator.next();
controlIterator.next();
