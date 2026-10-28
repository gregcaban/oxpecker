// Rhino b95f75d: object-rest destructuring (`var { [sym]: _, ...rest } = proxy`) over a Proxy
// whose `ownKeys` trap returns a Symbol copies data properties via CopyDataProperties, which must
// call the `getOwnPropertyDescriptor` trap once per *non-excluded* key:
//   interpreted: throws TypeError: "The object is not a string" (tries to coerce the Symbol key
//     to a string somewhere in the exclusion check) — the trap is never reached for any key.
//   compiled:    runs to completion, but the gOPD trap is *also* never invoked for the included
//     key ("a") — `log` stays empty instead of recording it.
// Both modes diverge from the spec (and from each other); neither calls the trap for the
// included key, but they fail in different ways.
// Originating test: language/expressions/object/dstr/object-rest-proxy-gopd-not-called-on-excluded-keys.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-rest-destructure-proxy-symbol-gopd.js --language-version 250
var sym = Symbol("s");
var log = [];
var proxy = new Proxy({}, {
  getOwnPropertyDescriptor: function (t, k) { log.push(k); },
  ownKeys: function () { return [sym, "a"]; }
});
var { [sym]: _, ...rest } = proxy;
__probe("p", log);
