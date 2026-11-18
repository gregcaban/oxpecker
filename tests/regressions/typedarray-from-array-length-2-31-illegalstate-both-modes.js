// oxpecker-expect[master]: discard
// Rhino 1.9.2: a typed array built from a JS array whose length is 2^31 crashes both modes
// with java.lang.IllegalStateException "list.length (2147483648) exceeds Integer.MAX_VALUE",
// which the script cannot catch; the spec asks for a RangeError (the buffer cannot be
// allocated). The array needs no elements: setting index 2147483647 makes its length 2^31.
// The array-like form of the same missing check, `new Int8Array({length: 2**53})`
// (t262-typedarray-excessive-length-crash.js), throws a RangeError on 1.9.2. On master 4c3d79b
// both modes run past the time limit instead (discard).
//   interpreted: crash (IllegalStateException)     compiled: crash (IllegalStateException)
// Found by `oxpecker fuzz` on 1.9.2 (hour-long campaign with generator processes, 2026-11-17:
// `arr[2147483647] = 0; new Uint8ClampedArray(arr)` in a warmed-up function), shrunk by
// `oxpecker shrink`, reduced by hand.
// Replay: oxpecker diff tests/regressions/typedarray-from-array-length-2-31-illegalstate-both-modes.js
var a = [];
a[2147483647] = 0;
new Uint8ClampedArray(a);
