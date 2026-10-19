// Rhino b95f75d: `Function.prototype.apply(thisArg, argsArray)` where `argsArray` is an
// array-like object (not a real Array/arguments object — e.g. a plain object or a TypedArray)
// feeds the wrong values into a rest-parameter in interpreted mode: it gets `null` for the first
// slot and the array-like object *itself* for subsequent slots, instead of expanding its indexed
// properties. Compiled mode expands it correctly.
//   interpreted: args == [null]          (for a length-1 array-like with element 0 === 9)
//   compiled:    args == [9]
// Behavioural difference (no crash); this is the root cause behind the "thrown name Object vs
// TypeError" findings below, where it corrupts `func.apply(null, typedArray)` results enough to
// fail a later assert in interpreted mode while compiled mode fails a different, unrelated assert.
// Originating test: built-ins/Function/prototype/apply/resizable-buffer.js
//   (also language/expressions/object/method-definition/generator-prototype-prop.js shares the
//   "thrown name Object vs TypeError" verdict shape but has an unrelated root cause, see
//   t262-generator-method-prototype-chain.js).
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-apply-rest-array-like-wrong.js --language-version 250
function f(...args) { return args; }
__probe("p", f.apply(null, { length: 1, 0: 9 }));
