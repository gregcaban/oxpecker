// Rhino b95f75d: when a Proxy's "getOwnPropertyDescriptor" trap explicitly returns `undefined`
// and the target does not have the property either, NativeProxy.getOwnPropertyDescriptor calls
// `target.getOwnPropertyDescriptor(cx, name)`, which returns plain Java `null` (not
// Undefined.instance/SCRIPTABLE_UNDEFINED) when the property is absent. The subsequent
// `Undefined.isUndefined(targetDesc)` check only recognises the two JS-undefined sentinels, so
// it is false for a Java `null`, and execution falls through to `targetDesc.isConfigurable(false)`:
//   both modes: java.lang.NullPointerException: Cannot invoke
//     "ScriptableObject$DescriptorInfo.isConfigurable(boolean)" because "targetDesc" is null
// Crashes in both interpreted and compiled mode (the bug is in NativeProxy/NativeObject, not in
// either evaluator).
// Originating tests (same root cause, via Object.getOwnPropertyDescriptor(s) on such a Proxy):
//   built-ins/Object/getOwnPropertyDescriptors/proxy-undefined-descriptor.js
//   built-ins/Proxy/getOwnPropertyDescriptor/result-is-undefined-targetdesc-is-undefined.js
//   built-ins/Proxy/getOwnPropertyDescriptor/trap-is-null-target-is-proxy.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-proxy-gopd-undefined-npe.js --language-version 250
var p = new Proxy({}, { getOwnPropertyDescriptor(t, k) { return undefined; } });
Object.getOwnPropertyDescriptor(p, "x");
