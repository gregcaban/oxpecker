// Rhino 1.9.2: `new Date(year, month)` with both arguments -2147483648 crashes both modes with
// java.lang.IllegalStateException "FAILED ASSERTION" (a Kit.codeBug), which the script cannot
// catch. Either argument alone with an ordinary value for the other (`new Date(-2147483648, 0)`,
// `new Date(0, -2147483648)`) gives an Invalid Date in both modes, which is what the spec asks
// for here too: MakeDay returns NaN for a year and month out of range. The fuzzer's form was
// `2147483648 | 0` passed twice to the Date constructor. Master 4c3d79b crashes the same way.
//   interpreted: crash (IllegalStateException)     compiled: crash (IllegalStateException)
// Found by `oxpecker fuzz` on 1.9.2 (hour-long campaign with generator processes, 2026-11-17),
// shrunk by `oxpecker shrink` to four lines, reduced by hand.
// Replay: oxpecker diff tests/regressions/date-two-int-min-args-failed-assertion-both-modes.js
new Date(-2147483648, -2147483648);
