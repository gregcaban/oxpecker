// Rhino b95f75d: constructing a typed array from an array-like object whose `length` exceeds
// what a Java array/ArrayBuffer can represent (`{ length: 2**53 }`) should throw a spec RangeError
// during AllocateTypedArrayBuffer. Instead the allocation path raises a raw Java exception that
// escapes the JS try/catch:
//   both modes: java.lang.IllegalArgumentException: None
// Crashes in both interpreted and compiled mode.
// Originating tests (same root cause, regular and BigInt typed array constructors):
//   built-ins/TypedArrayConstructors/ctors/object-arg/length-excessive-throws.js
//   built-ins/TypedArrayConstructors/ctors-bigint/object-arg/length-excessive-throws.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-typedarray-excessive-length-crash.js --language-version 250
var obj = { length: Math.pow(2, 53) };
try { new Int8Array(obj); } catch (e) { print("caught " + e); }
