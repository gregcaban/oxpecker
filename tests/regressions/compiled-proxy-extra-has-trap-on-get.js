// Rhino b95f75d: a named property read through a Proxy (`p.x`, `p.length`) invokes the `has`
// trap before `get` in compiled mode; the interpreter (and the spec) call only `get`.
// Indexed reads (`p[1]`) agree. The compiled property-get linker apparently probes
// [[HasProperty]] first.
//   interpreted: ["get:x"]      compiled: ["has:x", "get:x"]
// Found by the jit templates run, confirmed with variants in runs/proxy, 2026-10-18.
// Replay: oxpecker diff tests/regressions/compiled-proxy-extra-has-trap-on-get.js
var log = [];
var p = new Proxy({}, {
  get(t, k, r) { log.push("get:" + String(k)); return Reflect.get(t, k, r); },
  has(t, k) { log.push("has:" + String(k)); return k in t; }
});
var x = p.x;
__probe("p", log);
log;
