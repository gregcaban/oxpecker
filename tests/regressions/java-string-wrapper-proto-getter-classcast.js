// oxpecker-expect[master]: equal
// Rhino 1.9.2: reading `__proto__` from the LiveConnect wrapper of a Java string a Java method
// returned crashes both modes with java.lang.ClassCastException "NativeJavaObject cannot be cast
// to ScriptableObject", which the script cannot catch. No prototype has to be set first, where
// java-array-set-prototype-classcast.js needs a write (or Object.setPrototypeOf) before the
// read crashes. Same cast: the `__proto__` accessor on Object.prototype takes its receiver for
// a ScriptableObject, which the Java wrappers are not. Fixed on master 4c3d79b.
//   interpreted: crash (ClassCastException)     compiled: crash (ClassCastException)
// Found by `oxpecker fuzz` on 1.9.2 (hour-long campaign with generator processes, 2026-11-17),
// shrunk by `oxpecker shrink` to eight lines, reduced by hand.
// Replay: oxpecker diff tests/regressions/java-string-wrapper-proto-getter-classcast.js
J.echo("").__proto__;
