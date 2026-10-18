// Rhino b95f75d: `CallSite` is a global constructor; an instance made by script has no stack
// element, and converting it to a string throws a Java NullPointerException out of the engine
// (NativeCallSite.js_toString -> ScriptStackElement.renderJavaStyle) in both modes, instead of a
// JS error. Found by the API generators (api_construct), 2026-10-18.
// Replay: oxpecker diff tests/regressions/callsite-tostring-npe-both-modes.js
var cs = new CallSite();
String(cs);
