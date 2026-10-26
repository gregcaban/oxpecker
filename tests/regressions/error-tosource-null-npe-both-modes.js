// Rhino b95f75d: `Error.prototype.toSource.call(null)` throws a raw Java NullPointerException
// ("Cannot invoke Scriptable.get(...) because obj is null") past JS try/catch in both modes,
// instead of a TypeError. `.call(undefined)` is fine.
// Found by `oxpecker fuzz` (runs/fuzz-2026-10-19, hard-9266ec7b04), 2026-10-19.
// Replay: oxpecker diff tests/regressions/error-tosource-null-npe-both-modes.js
try {
  Error.prototype.toSource.call(null);
} catch (e) {
  print("caught " + e);
}
