// Rhino b95f75d: inside a generator, a `try { return 42; } finally { ... }` whose finally block
// contains a nested `try { try { return 43; } finally { throw 9; } } catch (e) {}` should have the
// inner `throw 9` caught by the `catch (e) {}` and the generator should resume completing the
// outer `return 42`. In compiled mode the `throw 9` instead escapes the catch entirely and comes
// out of `.next()` as an uncaught value:
//   interpreted: ok, g.next().value === 42
//   compiled:    throws the plain value 9, uncaught
// Needs the surrounding function to be a generator (the same code in a plain function is fine,
// modulo an unrelated Double/Integer java-types difference).
// Compiled-only throw; interpreted does not throw.
// Originating test: staging/sm/generators/return-finally.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-generator-nested-finally-throw-escapes.js --language-version 250
function* f() {
  try {
    return 42;
  } finally {
    try {
      try {
        return 43;
      } finally {
        throw 9;
      }
    } catch (e) {}
  }
}
f().next();
