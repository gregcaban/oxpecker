package dev.oxpecker.harness;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.mozilla.javascript.Callable;
import org.mozilla.javascript.Context;
import org.mozilla.javascript.LambdaFunction;
import org.mozilla.javascript.NativeObject;
import org.mozilla.javascript.Script;
import org.mozilla.javascript.Scriptable;
import org.mozilla.javascript.ScriptableObject;
import org.mozilla.javascript.ScriptRuntime;
import org.mozilla.javascript.SerializableCallable;
import org.mozilla.javascript.TopLevel;
import org.mozilla.javascript.Undefined;
import org.mozilla.javascript.VarScope;
import org.mozilla.javascript.Wrapper;

/**
 * Per-run host state: the functions injected into the scope (print, __probe, …), the
 * canonicaliser, and the output/trace they collect.
 */
final class Host {
    private static final String CANON_SRC = resource("canon.js");
    private static final String PRELUDE_SRC = resource("prelude.js");

    final List<String> output = new ArrayList<>();
    final List<String> trace = new ArrayList<>();
    final List<String> traceJavaTypes = new ArrayList<>();
    final List<String> soft = new ArrayList<>();

    private Callable canon;
    private Callable errName;
    private TopLevel scope;

    /** Canonical rendering of a value: text, Java leaf types, soft messages. */
    record Canonical(String text, String javaTypes, String soft) {}

    /**
     * Installs the host functions into {@code scope}. Compiles canon.js and prelude.js in
     * interpreter mode whatever {@code cx}'s mode is, then restores the mode.
     */
    void install(Context cx, TopLevel scope, long seed) {
        this.scope = scope;
        boolean wasInterpreted = cx.isInterpretedMode();
        RhinoCompat.setInterpreted(cx, true);
        try {
            ScriptableObject global = scope.getGlobalThis();

            define(scope, "print", 1, (c, s, thisObj, args) -> {
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < args.length; i++) {
                    if (i > 0) sb.append(' ');
                    sb.append(ScriptRuntime.toString(args[i]));
                }
                output.add(sb.toString());
                return Undefined.instance;
            });
            define(scope, "__probe", 1, (c, s, thisObj, args) -> {
                // __probe(label, v0, v1, ...): one trace entry per call.
                StringBuilder text = new StringBuilder();
                StringBuilder jt = new StringBuilder();
                for (int i = 0; i < args.length; i++) {
                    Canonical k = canon(c, args[i]);
                    if (i > 0) {
                        text.append(" | ");
                        jt.append(" | ");
                    }
                    text.append(k.text());
                    jt.append(k.javaTypes());
                    if (!k.soft().isEmpty()) soft.add(k.soft());
                }
                trace.add(text.toString());
                traceJavaTypes.add(jt.toString());
                return Undefined.instance;
            });
            define(scope, "__isProxy", 1, (c, s, thisObj, args) -> args.length > 0 && isProxy(args[0]));
            define(scope, "__jtype", 1, (c, s, thisObj, args) -> args.length > 0 ? jtype(args[0]) : "void");

            NativeObject helpers = new NativeObject();
            ScriptableObject.putProperty(helpers, "jtype", ScriptableObject.getProperty(global, "__jtype"));
            ScriptableObject.putProperty(helpers, "isProxy", ScriptableObject.getProperty(global, "__isProxy"));
            ScriptableObject.putProperty(helpers, "javaClass", new LambdaFunction(scope, "javaClass", 1,
                    (SerializableCallable) (c, s, t, args) -> args.length > 0 && args[0] instanceof Wrapper w
                            ? (w.unwrap() == null ? "null" : w.unwrap().getClass().getName())
                            : "?"));
            ScriptableObject.putProperty(helpers, "global", global);

            Script canonScript = cx.compileString(CANON_SRC, "canon.js", 1, null);
            Callable factory = (Callable) canonScript.exec(cx, scope, global);
            Scriptable api = (Scriptable) factory.call(cx, scope, global, new Object[] {helpers});
            canon = (Callable) ScriptableObject.getProperty(api, "canon");
            errName = (Callable) ScriptableObject.getProperty(api, "errName");

            Script preludeScript = cx.compileString(PRELUDE_SRC, "prelude.js", 1, null);
            Callable prelude = (Callable) preludeScript.exec(cx, scope, global);
            prelude.call(cx, scope, global, new Object[] {(double) seed});
        } finally {
            RhinoCompat.setInterpreted(cx, wasInterpreted);
        }
    }

    Canonical canon(Context cx, Object value) {
        Object r = canon.call(cx, scope, Undefined.SCRIPTABLE_UNDEFINED, new Object[] {value});
        Scriptable arr = (Scriptable) r;
        return new Canonical(
                ScriptRuntime.toString(ScriptableObject.getProperty(arr, 0)),
                ScriptRuntime.toString(ScriptableObject.getProperty(arr, 1)),
                ScriptRuntime.toString(ScriptableObject.getProperty(arr, 2)));
    }

    String errName(Context cx, Object value) {
        Object r = errName.call(cx, scope, Undefined.SCRIPTABLE_UNDEFINED, new Object[] {value});
        return ScriptRuntime.toString(r);
    }

    static String jtype(Object v) {
        if (v == null) return "null";
        return v.getClass().getSimpleName();
    }

    static boolean isProxy(Object v) {
        // NativeProxy is package-private in Rhino, so match by name.
        return v != null && v.getClass().getName().startsWith("org.mozilla.javascript.NativeProxy");
    }

    private static void define(TopLevel scope, String name, int length, SerializableCallable target) {
        LambdaFunction f = new LambdaFunction(scope, name, length, target);
        ScriptableObject.putProperty(scope.getGlobalThis(), name, f);
    }

    private static String resource(String name) {
        try (InputStream in = Host.class.getResourceAsStream("/" + name)) {
            if (in == null) throw new IllegalStateException("missing resource " + name);
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }
}
