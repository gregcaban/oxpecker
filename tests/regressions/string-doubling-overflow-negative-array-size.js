// Rhino b95f75d: repeated self-concatenation (`s += s`) doubles a string's length every
// iteration; once the length hits exactly 2^31 the internal char-array allocation overflows
// to Integer.MIN_VALUE and both evaluators throw a raw Java NegativeArraySizeException instead
// of an `Error` (e.g. a RangeError or OutOfMemoryError) a script could catch:
//   interpreted: java.lang.NegativeArraySizeException: -2147483648
//   compiled:    java.lang.NegativeArraySizeException: -2147483648
// Originating test: js1_5/Regress/regress-3649-n.js (doubles a 4-char string 100000 times;
// here a 1-char seed and 31 doublings hit the exact 2^31 boundary much faster).
// Found by replaying Rhino's legacy Mozilla suite (oxpecker corpus rhino-mozilla), 2026-10-18.
// Replay: oxpecker diff tests/regressions/string-doubling-overflow-negative-array-size.js --language-version 250
var s = "a"; for (var i = 0; i < 31; i++) s += s;
