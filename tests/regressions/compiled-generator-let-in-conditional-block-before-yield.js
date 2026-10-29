// Rhino 1.9.2: a generator function whose body declares a `let` inside a block that may or may
// not run (an `if` with a non-constant condition, or a `while` body) and then yields after the
// block fails to compile: the bytecode generator throws IllegalStateException "bad local
// variable type: 0 at index: 7" for the whole script, so compiled mode crashes before running a
// line while the interpreter runs the generator normally. `const` instead of `let`, `var`, a
// constant condition (`if (true)`, folded away), the `yield` inside the block, `return` instead
// of `yield` after it, and the same shape in a plain function all agree. Same family as
// master-only/t262-yield-in-forof-catch-verifyerror.js (a `yield` from a `catch` inside `for-of`),
// which 1.9.2 compiles; not checked on master b95f75d.
//   interpreted: "function" | 1 | true     compiled: crash (IllegalStateException in Codegen)
// Found by `oxpecker fuzz` on 1.9.2 (first campaign after the switch, seed 13: a nested generator
// with `if (!b) { let x = ...; } else {} yield v`), 2026-10-28.
// Replay: oxpecker diff tests/regressions/compiled-generator-let-in-conditional-block-before-yield.js
function* g(c) { if (c) { let x = 1; } yield 1; }
var it = g(true);
__probe("step", typeof g, it.next().value, it.next().done);
