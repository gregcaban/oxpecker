// Rhino 1.9.2: a `with` statement whose object is a LiveConnect wrapper of a Java object (the
// harness's `J`, a Java array such as `J.intArray(2)`, a Java string returned by a Java method)
// crashes the compiled mode at the first unqualified name read inside the block, whether the
// name is a member of the Java object (`sum`), a global (`Math`, `undefined`) or a `const` of
// the script: java.lang.ClassCastException "NativeJavaObject cannot be cast to
// ScriptableObject" (NativeJavaArray for arrays), thrown from the invokedynamic linker that
// folds `const` reads (ConstAwareLinker.getConstValue steps from the NativeWith to its prototype
// and casts it to ScriptableObject; the Java wrappers implement Scriptable directly). The script
// cannot catch it. The interpreter resolves the names normally. `typeof name` inside the block,
// a write (`name = 1`), a call (`f()` looks the callee up on another path) and an empty block
// agree; with an array the callee lookup throws TypeError in both modes before any argument is
// read, so `var x = length` is the array form. Not checked on master b95f75d.
//   interpreted: "function" | 1     compiled: crash (ClassCastException in ConstAwareLinker)
// Found by `oxpecker fuzz` on 1.9.2 (hour-long campaign, seed 15: `with (J) { v36 in Math; }`
// after lineage and source shrinking), 2026-10-28.
// Replay: oxpecker diff tests/regressions/compiled-with-java-object-name-lookup-classcast.js
with (J) {
    __probe("step", typeof sum, Math.abs(-1));
}
