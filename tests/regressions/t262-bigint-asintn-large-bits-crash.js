// Rhino b95f75d: `BigInt.asIntN(bits, value)` with a `bits` argument near UINT32_MAX should
// either compute correctly or throw a RangeError (per spec, bit counts that large are legal but
// an implementation may reasonably reject them with a RangeError). Rhino instead converts the bit
// count to a Java BigInteger operation that overflows its internal limits:
//   both modes: java.lang.ArithmeticException: BigInteger would overflow supported range
// Crashes in both interpreted and compiled mode.
// Originating test: staging/sm/BigInt/large-bit-length.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-bigint-asintn-large-bits-crash.js --language-version 250
BigInt.asIntN(4294967295, 1n);
