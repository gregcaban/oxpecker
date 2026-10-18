// Rhino b95f75d (master, 2.0.0-SNAPSHOT): a direct call to a top-level function that recurses
// returns the wrong value in compiled mode when the base case uses strict equality.
//   interpreted: 3      compiled: 1
// Triggers with `n === 0`; `n == 0`, `n <= 0` and `n < 1` agree between modes. Disappears when
// the call is not a direct call (var r = function…, or via a local alias t = r).
// Found by hand while smoke-testing the harness, characterised with runs of variants, 2026-10-18.
// Replay: oxpecker diff tests/regressions/direct-recursive-call-completion.js
function r(n) { return n === 0 ? 0 : r(n - 1) + 1; }
r(3);
