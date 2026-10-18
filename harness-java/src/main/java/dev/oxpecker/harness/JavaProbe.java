package dev.oxpecker.harness;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The Java side of the JS/Java boundary, exposed to scripts as the global {@code J} through
 * Rhino's LiveConnect wrapping. Every call records what Java actually received (runtime class
 * and value) so that interpreter/compiler differences in how values cross into Java are visible.
 *
 * <p>Values go to {@code javaTrace} (compared strictly) and runtime classes to
 * {@code javaTraceTypes} (the java-types verdict).
 */
public final class JavaProbe {
    final List<String> trace = new ArrayList<>();
    final List<String> types = new ArrayList<>();

    private <T> T rec(String method, Object received, T ret) {
        trace.add(method + "(" + show(received) + ") -> " + show(ret));
        types.add(method + "(" + cls(received) + ") -> " + cls(ret));
        return ret;
    }

    static String cls(Object o) {
        if (o == null) return "null";
        Class<?> c = o.getClass();
        return c.isArray() ? c.getComponentType().getSimpleName() + "[]" : c.getSimpleName();
    }

    static String show(Object o) {
        return show(o, 0);
    }

    // Arrays and collections can contain themselves (a JS script can store x2 into x2[0]).
    private static String show(Object o, int depth) {
        if (o == null) return "null";
        if (depth > 3) return "…";
        // JS objects implement java.util.Map/List; their contents are the probes' business.
        if (o instanceof org.mozilla.javascript.Scriptable) return "<" + cls(o) + ">";
        if (o instanceof Double d) {
            if (d.isNaN()) return "NaN";
            if (d == 0 && 1 / d < 0) return "-0.0";
            return d.toString();
        }
        if (o instanceof Float f) return f.toString() + "f";
        if (o instanceof Number || o instanceof Boolean) return o.toString();
        if (o instanceof CharSequence s) return "\"" + s + "\"";
        if (o instanceof Character c) return "'" + c + "'";
        if (o instanceof int[] a) return java.util.Arrays.toString(a);
        if (o instanceof double[] a) return java.util.Arrays.toString(a);
        if (o instanceof Object[] a) {
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < a.length; i++) sb.append(i > 0 ? "," : "").append(show(a[i], depth + 1));
            return sb.append("]").toString();
        }
        if (o instanceof List<?> l) {
            StringBuilder sb = new StringBuilder("List[");
            for (int i = 0; i < l.size(); i++) sb.append(i > 0 ? "," : "").append(show(l.get(i), depth + 1));
            return sb.append("]").toString();
        }
        if (o instanceof Map<?, ?> m) {
            StringBuilder sb = new StringBuilder("Map{");
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                sb.append(first ? "" : ",").append(show(e.getKey(), depth + 1)).append("=").append(show(e.getValue(), depth + 1));
                first = false;
            }
            return sb.append("}").toString();
        }
        // JS objects (Scriptable) and anything else: only the class, never user-visible toString.
        return "<" + cls(o) + ">";
    }

    // ---- typed parameters: Rhino converts the JS value before the call ------------------------
    public int takesInt(int v) { return rec("takesInt", v, v); }
    public long takesLong(long v) { return rec("takesLong", v, v); }
    public short takesShort(short v) { return rec("takesShort", v, v); }
    public byte takesByte(byte v) { return rec("takesByte", v, v); }
    public char takesChar(char v) { return rec("takesChar", v, v); }
    public float takesFloat(float v) { return rec("takesFloat", v, v); }
    public double takesDouble(double v) { return rec("takesDouble", v, v); }
    public boolean takesBoolean(boolean v) { return rec("takesBoolean", v, v); }
    public String takesString(String v) { return rec("takesString", v, v); }
    public Integer takesInteger(Integer v) { return rec("takesInteger", v, v); }
    public Number takesNumber(Number v) { return rec("takesNumber", v, v); }
    public BigInteger takesBigInteger(BigInteger v) { return rec("takesBigInteger", v, v); }
    public CharSequence takesCharSequence(CharSequence v) { return rec("takesCharSequence", v, v); }

    /** Untyped: records the runtime class Rhino chose to pass (Integer vs Double, ConsString...). */
    public Object echo(Object v) { return rec("echo", v, v); }
    public String typeOf(Object v) { return rec("typeOf", v, cls(v)); }

    // ---- overload resolution depends on the runtime class of the argument --------------------
    public String over(int v) { return rec("over", v, "int"); }
    public String over(long v) { return rec("over", v, "long"); }
    public String over(double v) { return rec("over", v, "double"); }
    public String over(String v) { return rec("over", v, "String"); }
    public String over(Object v) { return rec("over", v, "Object"); }
    public String over(boolean v) { return rec("over", v, "boolean"); }

    // ---- values coming back into JS ---------------------------------------------------------
    public int anInt() { return rec("anInt", null, 7); }
    public long aLong() { return rec("aLong", null, 9007199254740993L); }   // 2^53 + 1: not a safe JS integer
    public long maxLong() { return rec("maxLong", null, Long.MAX_VALUE); }
    public float aFloat() { return rec("aFloat", null, 0.1f); }
    public double aDouble() { return rec("aDouble", null, 0.1); }
    public double negZero() { return rec("negZero", null, -0.0); }
    public double nan() { return rec("nan", null, Double.NaN); }
    public short aShort() { return rec("aShort", null, (short) -3); }
    public byte aByte() { return rec("aByte", null, (byte) 200); }
    public char aChar() { return rec("aChar", null, 'x'); }
    public Integer boxedInt() { return rec("boxedInt", null, 42); }
    public Long boxedLong() { return rec("boxedLong", null, 1L << 40); }
    public BigInteger bigInteger() { return rec("bigInteger", null, new BigInteger("123456789012345678901234567890")); }
    public String aString() { return rec("aString", null, "java"); }
    public Object nothing() { return rec("nothing", null, null); }
    public int[] intArray(int n) { return rec("intArray", n, new int[Math.max(0, Math.min(n, 8))]); }
    public double[] doubleArray(int n) { return rec("doubleArray", n, new double[Math.max(0, Math.min(n, 8))]); }
    public Object[] objectArray(int n) { return rec("objectArray", n, new Object[Math.max(0, Math.min(n, 8))]); }
    public String[] stringArray() { return rec("stringArray", null, new String[] {"a", "b"}); }
    public List<Object> list() { return rec("list", null, new ArrayList<>(List.of(1, 2.5, "s"))); }
    public Map<String, Object> map() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("k", 1);
        m.put("d", 2.5);
        return rec("map", null, m);
    }
    public int sum(int[] a) {
        int s = 0;
        for (int x : a) s += x;
        return rec("sum", a, s);
    }
    public double sum(double[] a) {
        double s = 0;
        for (double x : a) s += x;
        return rec("sumD", a, s);
    }
    public int size(List<?> l) { return rec("size", l, l.size()); }
    public Object get(Map<?, ?> m, Object k) { return rec("get", k, m.get(k)); }

    public int field = 5;
    public static final double PI_ISH = 3.0;
    public static int staticCounter = 0;
    public static int bump() { return ++staticCounter; }
}
