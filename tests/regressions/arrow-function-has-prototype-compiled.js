// Rhino b95f75d: arrow functions get a `prototype` property in compiled mode. Per spec (and in
// the interpreter) arrows have none.
//   interpreted: "undefined"      compiled: "object"
// Found by replaying Rhino's own tests/testsrc/jstests/arrowfn.jstest (oxpecker corpus
// rhino-jstests), bisected to `assertEq(f.prototype, undefined)`, 2026-10-18.
// Replay: oxpecker diff tests/regressions/arrow-function-has-prototype-compiled.js
var f = () => 10;
__probe("p", typeof f.prototype, Object.getOwnPropertyNames(f));
typeof f.prototype;
