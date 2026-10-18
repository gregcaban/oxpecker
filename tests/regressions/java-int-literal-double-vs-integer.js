// oxpecker-expect: java-types
// Rhino b95f75d: the same JS number reaches a Java host function as java.lang.Double from the
// interpreter but as java.lang.Integer from compiled code. Pure JS behaviour is identical; a Java
// embedder casting the result (e.g. `(Integer) r`) sees the difference.
//   interpreted trace javaTypes: String | Double | Double      compiled: String | Double | Integer
// Replay: bin/harness --replay tests/regressions/java-int-literal-double-vs-integer.js
function f(a) { var i = 0; i = a + 1; return i; }
__probe("p", f(1), 7);
f(2);
