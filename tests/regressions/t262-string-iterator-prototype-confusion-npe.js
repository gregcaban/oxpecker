// Rhino b95f75d: calling a String Iterator's `next` method with `this` set to
// %StringIteratorPrototype% itself (rather than an actual StringIterator instance — a classic
// "prototype confusion" probe) should throw a TypeError, since the prototype object has no
// internal `[[IteratedString]]` slot. Rhino's implementation stores the iterated string in a
// Java field that is `null` on the prototype object and dereferences it unconditionally:
//   both modes: java.lang.NullPointerException: Cannot invoke "String.length()" because
//     "this.string" is null
// Crashes in both interpreted and compiled mode.
// Originating test: staging/sm/String/iterator_edge_cases.js (the
//   TestStringIteratorPrototypeConfusion case; TestStringIteratorWrappers, the other half of that
//   file, needs $262.createRealm() which oxpecker's shim does not support, and
//   staging/sm/String/replace-math.js is skipped as a resource-exhaustion OOM, not minimised here).
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-string-iterator-prototype-confusion-npe.js --language-version 250
var iter = ""[Symbol.iterator]();
iter.next.call(Object.getPrototypeOf(iter));
