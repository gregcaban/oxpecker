// Rhino b95f75d (master, 2.0.0-SNAPSHOT): a direct call to a top-level function that
// recurses returns the wrong value in compiled mode.
//   interpreted: 3      compiled: 1
// Replay: bin/harness --replay tests/regressions/direct-recursive-call-completion.js
// Disappears when the call is not a direct call (var r = function…, or via a local alias).
// Found by hand while smoke-testing the harness, 2026-10-18.
function r(n) { return n === 0 ? 0 : r(n - 1) + 1; }
r(3);
