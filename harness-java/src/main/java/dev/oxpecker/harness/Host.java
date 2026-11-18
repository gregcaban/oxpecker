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
import org.mozilla.javascript.Undefined;
import org.mozilla.javascript.Wrapper;

/**
 * Per-run host state: the functions injected into the scope (print, __probe, …), the
 * canonicaliser, and the output/trace they collect.
 */
final class Host {
    private static final String CANON_SRC = resource("canon.js");
    private static final String PRELUDE_SRC = resource("prelude.js");
    private static final String CANON_GEN_SRC = resource("canon-gen.js");
    private static final java.util.Map<String, Script> SCRIPTS = new java.util.concurrent.ConcurrentHashMap<>();

    /** OXPECKER_CANON: which canonicaliser renders values. js (default): canon.js; java: JavaCanon; both: compare. */
    enum CanonMode { JS, JAVA, BOTH }
    static final CanonMode CANON_MODE = canonMode(System.getenv("OXPECKER_CANON"));

    private static CanonMode canonMode(String v) {
        if (v == null || v.isEmpty() || v.equals("js")) return CanonMode.JS;
        if (v.equals("java")) return CanonMode.JAVA;
        if (v.equals("both")) return CanonMode.BOTH;
        throw new IllegalStateException("OXPECKER_CANON must be js, java or both, not " + v);
    }

    final List<String> output = new ArrayList<>();
    final List<String> trace = new ArrayList<>();
    final List<String> traceJavaTypes = new ArrayList<>();
    final List<String> soft = new ArrayList<>();

    final JavaProbe java = new JavaProbe();
    /** Pause/resume events: "pause(<canon value>)", "resume(<value>)". Part of the trace. */
    final List<String> continuations = new ArrayList<>();
    private Callable canon;
    private Callable errName;
    private JavaCanon javaCanon;
    private Object scope;                 // opaque: Scopes knows this Rhino's scope type

    /** Canonical rendering of a value: text, Java leaf types, soft messages. */
    record Canonical(String text, String javaTypes, String soft) {}

    /**
     * Installs the host functions into {@code scope}. Compiles canon.js and prelude.js in
     * interpreter mode whatever {@code cx}'s mode is, then restores the mode.
     */
    void install(Context cx, Object scope, long seed) {
        this.scope = scope;
        boolean wasInterpreted = cx.isInterpretedMode();
        RhinoCompat.setInterpreted(cx, true);
        try {
            ScriptableObject global = Scopes.globalThis(scope);

            define(scope, "print", 1, (c, s, thisObj, args) -> {
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < args.length; i++) {
                    if (i > 0) sb.append(' ');
                    sb.append(ScriptRuntime.toString(args[i]));
                }
                output.add(JavaCanon.sortOverloads(sb.toString()));   // a printed Java method: see canon.js
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
            define(scope, "__pause", 1, (c, s, thisObj, args) -> {
                // Captures the interpreter continuation and unwinds to ModeRunner, which records
                // the value and resumes. Outside executeScriptWithContinuations (compiled mode,
                // or called through eval/Java frames) Rhino throws IllegalStateException.
                Object v = args.length > 0 ? args[0] : Undefined.instance;
                org.mozilla.javascript.ContinuationPending pending = c.captureContinuation();
                pending.setApplicationState(v);
                throw pending;
            });
            define(scope, "__isProxy", 1, (c, s, thisObj, args) -> args.length > 0 && isProxy(args[0]));
            define(scope, "__jtype", 1, (c, s, thisObj, args) -> args.length > 0 ? jtype(args[0]) : "void");

            NativeObject helpers = new NativeObject();
            ScriptableObject.putProperty(helpers, "jtype", ScriptableObject.getProperty(global, "__jtype"));
            ScriptableObject.putProperty(helpers, "isProxy", ScriptableObject.getProperty(global, "__isProxy"));
            ScriptableObject.putProperty(helpers, "javaClass", Scopes.lambda(scope, "javaClass", 1,
                    (SerializableCallable) (c, s, t, args) -> args.length > 0 && args[0] instanceof Wrapper w
                            ? (w.unwrap() == null ? "null" : w.unwrap().getClass().getName())
                            : "?"));
            ScriptableObject.putProperty(helpers, "global", global);

            // The Java boundary (LiveConnect wrapping of a plain Java object; no Packages/reflection
            // beyond this one class). Static state is reset so modes don't see each other.
            JavaProbe.staticCounter = 0;
            ScriptableObject.putProperty(global, "J", Scopes.javaToJS(java, scope, cx));

            if (CANON_MODE != CanonMode.JAVA) {
                Script canonScript = compiled(cx, "canon.js", CANON_SRC);
                Callable factory = (Callable) Scopes.exec(canonScript, cx, scope, global);
                Scriptable api = (Scriptable) Scopes.call(factory, cx, scope, global, new Object[] {helpers});
                canon = (Callable) ScriptableObject.getProperty(api, "canon");
                errName = (Callable) ScriptableObject.getProperty(api, "errName");
            }
            if (CANON_MODE != CanonMode.JS) {
                // Captures the same builtins as canon.js does, before prelude.js and the program run.
                Object genObject = null;
                try {
                    genObject = Scopes.exec(compiled(cx, "canon-gen.js", CANON_GEN_SRC), cx, scope, global);
                } catch (RuntimeException e) {
                    // no generators: nothing to name
                }
                javaCanon = new JavaCanon(cx, scope, global, genObject);
            }

            Script preludeScript = compiled(cx, "prelude.js", PRELUDE_SRC);
            Callable prelude = (Callable) Scopes.exec(preludeScript, cx, scope, global);
            Scopes.call(prelude, cx, scope, global, new Object[] {(double) seed});
        } finally {
            RhinoCompat.setInterpreted(cx, wasInterpreted);
        }
    }

    Canonical canon(Context cx, Object value) {
        if (CANON_MODE == CanonMode.JAVA) return javaCanon.canon(cx, value);
        Canonical k = jsCanon(cx, value);
        if (CANON_MODE == CanonMode.BOTH) {
            // The JS rendering is the one reported. A user getter/trap with side effects runs twice.
            Canonical j = javaCanon.canon(cx, value);
            if (!k.equals(j)) mismatch("canon", k, j);
        }
        return k;
    }

    private Canonical jsCanon(Context cx, Object value) {
        Object r = Scopes.call(canon, cx, scope, Undefined.SCRIPTABLE_UNDEFINED, new Object[] {value});
        Scriptable arr = (Scriptable) r;
        return new Canonical(
                ScriptRuntime.toString(ScriptableObject.getProperty(arr, 0)),
                ScriptRuntime.toString(ScriptableObject.getProperty(arr, 1)),
                ScriptRuntime.toString(ScriptableObject.getProperty(arr, 2)));
    }

    String errName(Context cx, Object value) {
        if (CANON_MODE == CanonMode.JAVA) return javaCanon.errName(cx, value);
        Object r = Scopes.call(errName, cx, scope, Undefined.SCRIPTABLE_UNDEFINED, new Object[] {value});
        String js = ScriptRuntime.toString(r);
        if (CANON_MODE == CanonMode.BOTH) {
            String j = javaCanon.errName(cx, value);
            if (!js.equals(j)) {
                String msg = "canon-mismatch: errName: js=" + js + " java=" + j;
                soft.add(msg);
                System.err.println(msg);
            }
        }
        return js;
    }

    /** Records (in soft, and on stderr) that the two canonicalisers disagree: js is the reported one. */
    private void mismatch(String what, Canonical js, Canonical java) {
        StringBuilder sb = new StringBuilder("canon-mismatch: ").append(what);
        if (!js.text().equals(java.text())) sb.append(" text: js=").append(js.text()).append(" java=").append(java.text());
        if (!js.javaTypes().equals(java.javaTypes())) {
            sb.append(" javaTypes: js=").append(js.javaTypes()).append(" java=").append(java.javaTypes());
        }
        if (!js.soft().equals(java.soft())) sb.append(" soft: js=").append(js.soft()).append(" java=").append(java.soft());
        String msg = sb.toString();
        soft.add(msg);
        System.err.println(msg);
    }

    /**
     * The interpreter-mode Script for a bundled resource, compiled once per JVM for each setting
     * that can change the compilation (language version, whether the instruction observer is on).
     * A compiled Script does not depend on the scope it is later executed in.
     */
    private static Script compiled(Context cx, String name, String src) {
        String key = name + "|" + cx.getLanguageVersion() + "|" + (cx.getInstructionObserverThreshold() > 0);
        return SCRIPTS.computeIfAbsent(key, k -> cx.compileString(src, name, 1, null));
    }

    static String jtype(Object v) {
        if (v == null) return "null";
        return v.getClass().getSimpleName();
    }

    static boolean isProxy(Object v) {
        // NativeProxy is package-private in Rhino, so match by name.
        return v != null && v.getClass().getName().startsWith("org.mozilla.javascript.NativeProxy");
    }

    private static void define(Object scope, String name, int length, SerializableCallable target) {
        LambdaFunction f = Scopes.lambda(scope, name, length, target);
        ScriptableObject.putProperty(Scopes.globalThis(scope), name, f);
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
