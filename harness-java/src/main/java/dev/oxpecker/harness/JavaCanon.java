package dev.oxpecker.harness;

import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import org.mozilla.javascript.Callable;
import org.mozilla.javascript.Context;
import org.mozilla.javascript.ContinuationPending;
import org.mozilla.javascript.EcmaError;
import org.mozilla.javascript.EvaluatorException;
import org.mozilla.javascript.JavaScriptException;
import org.mozilla.javascript.ScriptRuntime;
import org.mozilla.javascript.Scriptable;
import org.mozilla.javascript.ScriptableObject;
import org.mozilla.javascript.SerializableCallable;
import org.mozilla.javascript.Undefined;
import org.mozilla.javascript.Wrapper;

/**
 * Java port of canon.js (selected with OXPECKER_CANON=java|both). It renders the same text, Java
 * leaf types and soft messages for the same value graph, without interpreting JavaScript.
 *
 * <p>The structure of {@code walk}, {@code dataProp}, {@code descriptor} ... follows canon.js
 * statement by statement, including the order of every call that can reach user code, so that a
 * Proxy sees the same traps in the same order, a getter on Object.prototype is read where canon.js
 * reads it, and a poisoned toString runs where canon.js's string concatenation runs it. Like
 * canon.js, every builtin it calls is captured at install time (here: looked up in the fresh
 * scope before any user code ran) and called through the captured function object; only what
 * canon.js did with plain strings and arrays (join, slice, push, indexOf, ...) is done in Java.
 * The two things canon.js looks up when it runs, and so does this class, are the global
 * {@code Error} (isErrorLike) and the properties of {@code excludedErrorKeys} (an ordinary
 * object, so Object.prototype shows through).
 *
 * <p>The idea of porting canon.js to Java, and the helper shapes (errText, catchable, the
 * IdentityHashMap prototype table), come from the Java port in the aardvark179-java-version fork
 * (harness-rhino1.9/.../JavaCanon.java); this one was written again from the current canon.js.
 */
final class JavaCanon {
    private static final int MAX_DEPTH = 8, MAX_NODES = 600, MAX_ARRAY = 200, MAX_STRING = 400;
    private static final Object UNDEF = Undefined.SCRIPTABLE_UNDEFINED;
    private static final Object[] NO_ARGS = ScriptRuntime.emptyArgs;
    private static final String[] WELL_KNOWN = {
        "iterator", "asyncIterator", "hasInstance", "isConcatSpreadable", "match", "matchAll",
        "replace", "search", "species", "split", "toPrimitive", "toStringTag", "unscopables"
    };
    private static final String[] CTOR_NAMES = {
        "Object", "Array", "Function", "String", "Number", "Boolean", "Symbol", "BigInt",
        "Date", "RegExp", "Error", "TypeError", "RangeError", "SyntaxError", "ReferenceError", "EvalError",
        "URIError", "Map", "Set", "WeakMap", "WeakSet", "Promise", "ArrayBuffer", "DataView",
        "Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array", "Int32Array",
        "Uint32Array", "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array"
    };
    private static final String[] EXCLUDED_ERROR_KEYS = {
        "stack", "fileName", "lineNumber", "columnNumber", "rhinoException", "javaException"
    };

    private final Object scope;
    private final ScriptableObject global;
    private final Object proxyCtor;

    private final Callable ownKeys, gopd, gpo, isArray, isExtensible, jsonStringify, symKeyFor;
    private final Callable objToString, fnToString, symToString;
    private final Callable numberValueOf, stringValueOf, booleanValueOf, bigintValueOf;
    private final Callable dateGetTime, regexpToString, mapForEach, setForEach, mapSize, setSize;
    private final Callable taLength, symDesc;
    private final Scriptable excludedErrorKeys;

    private final List<Object> wellKnownSyms = new ArrayList<>();
    private final List<String> wellKnownNames = new ArrayList<>();
    /** Builtin prototypes and their names, first registration wins (canon.js scans in order). */
    private final IdentityHashMap<Object, String> protoNames = new IdentityHashMap<>();

    /**
     * Captures what canon.js captures. {@code genProto} is gpo(gpo(an instance of a generator
     * function)) or null; the caller obtains it (it needs a script) so that this class needs none.
     */
    JavaCanon(Context cx, Object scope, ScriptableObject global, Object genObject) {
        this.scope = scope;
        this.global = global;
        Object proxy = prop(global, "Proxy");
        this.proxyCtor = proxy instanceof Callable && "function".equals(ScriptRuntime.typeof(proxy)) ? proxy : null;

        Scriptable objectCtor = obj(global, "Object");
        Scriptable reflect = obj(global, "Reflect");
        Scriptable symbol = obj(global, "Symbol");
        ownKeys = fn(reflect, "ownKeys");
        gopd = fn(objectCtor, "getOwnPropertyDescriptor");
        gpo = fn(objectCtor, "getPrototypeOf");
        isExtensible = fn(objectCtor, "isExtensible");
        isArray = fn(obj(global, "Array"), "isArray");
        jsonStringify = fn(obj(global, "JSON"), "stringify");
        symKeyFor = fn(symbol, "keyFor");
        objToString = fn(proto("Object"), "toString");
        fnToString = fn(proto("Function"), "toString");
        symToString = fn(proto("Symbol"), "toString");
        numberValueOf = fn(proto("Number"), "valueOf");
        stringValueOf = fn(proto("String"), "valueOf");
        booleanValueOf = fn(proto("Boolean"), "valueOf");
        Object bigint = prop(global, "BigInt");
        bigintValueOf = "function".equals(ScriptRuntime.typeof(bigint)) ? fn(proto("BigInt"), "valueOf") : null;
        dateGetTime = fn(proto("Date"), "getTime");
        regexpToString = fn(proto("RegExp"), "toString");
        mapForEach = fn(proto("Map"), "forEach");
        setForEach = fn(proto("Set"), "forEach");
        mapSize = getter(cx, proto("Map"), "size");
        setSize = getter(cx, proto("Set"), "size");
        Object taProto = call(cx, gpo, UNDEF, proto("Int8Array"));
        Object taLengthDesc = truthy(taProto) ? call(cx, gopd, UNDEF, taProto, "length") : null;
        Object tl = truthy(taLengthDesc) ? prop((Scriptable) taLengthDesc, "get") : null;
        taLength = truthy(tl) && tl instanceof Callable c ? c : null;
        symDesc = getter(cx, proto("Symbol"), "description");

        for (String w : WELL_KNOWN) {
            Object s = prop(symbol, w);
            if ("symbol".equals(ScriptRuntime.typeof(s))) {
                wellKnownSyms.add(s);
                wellKnownNames.add("Symbol." + w);
            }
        }
        for (String name : CTOR_NAMES) {
            Object ctor = prop(global, name);
            if (ctor instanceof Scriptable cs && "function".equals(ScriptRuntime.typeof(ctor))) {
                Object p = prop(cs, "prototype");
                if (truthy(p)) protoNames.putIfAbsent(p, name + ".prototype");
            }
        }
        if (truthy(taProto)) protoNames.putIfAbsent(taProto, "TypedArray.prototype");
        if (genObject != null) {
            try {
                Object genProto = call(cx, gpo, UNDEF, call(cx, gpo, UNDEF, genObject));
                if (truthy(genProto)) protoNames.putIfAbsent(genProto, "Generator.prototype");
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
            }
        }
        excludedErrorKeys = (Scriptable) Scopes.newObject(cx, scope);
        for (String k : EXCLUDED_ERROR_KEYS) ScriptableObject.putProperty(excludedErrorKeys, k, 1);
    }

    // ---- errName --------------------------------------------------------------------------

    String errName(Context cx, Object v) {
        if (v == null || (!isTypeof(v, "object") && !isTypeof(v, "function"))) return ScriptRuntime.typeof(v);
        Object n = dataProp(cx, v, "name");
        return n instanceof CharSequence cs ? cs.toString() : "Object";
    }

    // ---- canon ----------------------------------------------------------------------------

    Host.Canonical canon(Context cx, Object root) {
        Walk w = new Walk(cx);
        String text = w.walk(root, 0);
        return new Host.Canonical(text, String.join(",", w.jtypes), String.join("\n", w.soft));
    }

    private final class Walk {
        final Context cx;
        final List<String> jtypes = new ArrayList<>();
        final List<String> soft = new ArrayList<>();
        /** First-visit id of each visited object (identity), and of each wrapped Java object. */
        final IdentityHashMap<Object, Integer> seen = new IdentityHashMap<>();
        final IdentityHashMap<Object, Integer> seenWrapped = new IdentityHashMap<>();
        int seenCount;
        int nodes;

        Walk(Context cx) {
            this.cx = cx;
        }

        /** Index of the first visited object === v, or null. (=== on two Java wrappers compares what they wrap.) */
        Integer seenId(Object v) {
            if (v instanceof Wrapper w) return seenWrapped.get(w.unwrap());
            return seen.get(v);
        }

        int addSeen(Object v) {
            int id = seenCount++;
            if (v instanceof Wrapper w) seenWrapped.putIfAbsent(w.unwrap(), id);
            seen.putIfAbsent(v, id);
            return id;
        }

        String number(Object v) {
            jtypes.add(Host.jtype(v));
            double d = ((Number) v).doubleValue();
            if (d != d) return "NaN";
            if (d == 0) return 1 / d < 0 ? "-0" : "0";
            return ScriptRuntime.toString(v);
        }

        String string(Object v) {
            jtypes.add(Host.jtype(v));
            CharSequence cs = (CharSequence) v;
            int len = cs.length();
            if (len > MAX_STRING) return json(cx, cs.subSequence(0, MAX_STRING).toString()) + "…[len=" + len + "]";
            return json(cx, v);
        }

        String prim(Object v) {
            switch (ScriptRuntime.typeof(v)) {
                case "undefined":
                    return "undefined";
                case "boolean":
                    return ScriptRuntime.toBoolean(v) ? "true" : "false";
                case "number":
                    return number(v);
                case "string":
                    return string(v);
                case "bigint":
                    jtypes.add(Host.jtype(v));
                    return ScriptRuntime.toString(v) + "n";
                case "symbol":
                    return symbolText(cx, v);
                default:
                    return v == null ? "null" : null;
            }
        }

        String protoText(Object v) {
            Object p;
            try {
                p = call(cx, gpo, UNDEF, v);
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
                return "?";
            }
            if (p == null) return "null";
            String n = protoNames.get(p);
            if (n != null) return n;
            Integer id = seenId(p);
            if (id != null) return "@" + id;
            return "{…}";
        }

        String descriptor(Object obj, Object key) {
            Object d;
            try {
                d = call(cx, gopd, UNDEF, obj, key);
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
                return "!" + errText(cx, e);
            }
            if (!truthy(d)) return "<missing>";
            Scriptable ds = (Scriptable) d;
            String flags = (truthy(prop(ds, "enumerable")) ? "e" : "-") + (truthy(prop(ds, "configurable")) ? "c" : "-");
            if (has(ds, "get") || has(ds, "set")) {
                return "{" + flags + (truthy(prop(ds, "get")) ? "g" : "-") + (truthy(prop(ds, "set")) ? "s" : "-") + "}";
            }
            return "{" + flags + (truthy(prop(ds, "writable")) ? "w" : "-") + "}";
        }

        String dataValue(Object obj, Object key, int depth) {
            Object d;
            try {
                d = call(cx, gopd, UNDEF, obj, key);
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
                return "!" + errText(cx, e);
            }
            if (!truthy(d) || !has((Scriptable) d, "value")) return "";
            return walk(prop((Scriptable) d, "value"), depth);
        }

        String walk(Object v, int depth) {
            String p = prim(v);
            if (p != null) return p;
            if (++nodes > MAX_NODES) return "…nodes";
            Integer prev = seenId(v);
            if (prev != null) return "@" + prev;
            int id = addSeen(v);
            String jt = Host.jtype(v);
            if (jt.equals("NativeJavaObject") || jt.equals("NativeJavaArray") || jt.equals("NativeJavaClass")) {
                return "@" + id + "[Java " + javaClass(v) + "]";
            }
            if (isProxy(v)) return "@" + id + "[Proxy]";
            if (ScriptRuntime.shallowEq(v, global)) return "@" + id + "[global]";
            String pn = protoNames.get(v);
            if (pn != null) return "@" + id + "[" + pn + "]";
            boolean isFunction = isTypeof(v, "function");
            if (isFunction) {
                String nsrc;
                try {
                    nsrc = ScriptRuntime.toString(call(cx, fnToString, v));
                } catch (RuntimeException e) {
                    if (!catchable(cx, e)) throw e;
                    nsrc = "";
                }
                if (nsrc.indexOf("[native code]") >= 0) {
                    Object nname = dataProp(cx, v, "name");
                    String left = "@" + id + "[native " + (nname instanceof CharSequence ? nname.toString() : "?") + "/";
                    return left + concat(cx, dataProp(cx, v, "length")) + "]";
                }
            }
            if (depth > MAX_DEPTH) return "@" + id + "…depth";
            String tag = tagOf(cx, v);
            String head = "@" + id + jsSlice(tag, 8, -1);
            List<String> parts = new ArrayList<>();
            boolean errorLike = isErrorLike(cx, v, tag);

            if (isFunction) {
                Object name = dataProp(cx, v, "name"), len = dataProp(cx, v, "length");
                head = "@" + id + "function";
                parts.add("name=" + (name instanceof CharSequence ? json(cx, name) : "?"));
                parts.add("length=" + (isTypeof(len, "number") ? ScriptRuntime.toString(len) : "?"));
                String src;
                try {
                    src = ScriptRuntime.toString(call(cx, fnToString, v));
                } catch (RuntimeException e) {
                    if (!catchable(cx, e)) throw e;
                    src = "!" + errText(cx, e);
                }
                parts.add("src=" + json(cx, src.length() > MAX_STRING ? src.substring(0, MAX_STRING) + "…" : src));
            } else if (tag.equals("[object Number]") || tag.equals("[object String]")
                    || tag.equals("[object Boolean]") || tag.equals("[object BigInt]")) {
                Callable vo = tag.equals("[object Number]") ? numberValueOf
                        : tag.equals("[object String]") ? stringValueOf
                        : tag.equals("[object Boolean]") ? booleanValueOf : bigintValueOf;
                try {
                    if (vo == null) throw ScriptRuntime.typeError("not a function");   // call.call(null, v)
                    parts.add("boxed=" + walk(call(cx, vo, v), depth + 1));
                } catch (RuntimeException e) {
                    if (!catchable(cx, e)) throw e;
                    parts.add("boxed=!");
                }
            } else if (tag.equals("[object Date]")) {
                try {
                    parts.add("time=" + walk(call(cx, dateGetTime, v), depth + 1));
                } catch (RuntimeException e) {
                    if (!catchable(cx, e)) throw e;
                    parts.add("time=!");
                }
            } else if (tag.equals("[object RegExp]")) {
                try {
                    parts.add("re=" + ScriptRuntime.toString(call(cx, regexpToString, v)));
                } catch (RuntimeException e) {
                    if (!catchable(cx, e)) throw e;
                    parts.add("re=!");
                }
            } else if (errorLike) {
                Object msg = dataProp(cx, v, "message");
                if (msg instanceof CharSequence) soft.add(msg.toString());
            }

            // Map / Set contents, found by brand check (the getters throw on non-instances).
            String mapLike = null;
            if (brand(mapSize, v)) mapLike = "map";
            if (mapLike == null && brand(setSize, v)) mapLike = "set";
            if (mapLike != null) {
                boolean isMap = mapLike.equals("map");
                List<String> ents = new ArrayList<>();
                SerializableCallable each = (c, s, t, args) -> {
                    Object val = args.length > 0 ? args[0] : Undefined.instance;
                    if (isMap) {
                        Object key = args.length > 1 ? args[1] : Undefined.instance;
                        String k = walk(key, depth + 1);
                        String x = walk(val, depth + 1);
                        ents.add(k + "=>" + x);
                    } else {
                        ents.add(walk(val, depth + 1));
                    }
                    return Undefined.instance;
                };
                try {
                    call(cx, isMap ? mapForEach : setForEach, v, Scopes.lambda(scope, "", 2, each));
                } catch (RuntimeException e) {
                    if (!catchable(cx, e)) throw e;
                    ents.add("!" + errText(cx, e));
                }
                parts.add((isMap ? "entries=[" : "values=[") + String.join(",", ents) + "]");
            }

            // Typed arrays: elements are not own-key enumerable in a useful way; render them explicitly.
            Object taLen = -1;
            if (taLength != null) {
                try {
                    taLen = call(cx, taLength, v);
                } catch (RuntimeException e) {
                    if (!catchable(cx, e)) throw e;
                    taLen = -1;
                }
            }
            if (isTypeof(taLen, "number") && ((Number) taLen).doubleValue() >= 0) {
                double tlen = ((Number) taLen).doubleValue();
                List<String> tel = new ArrayList<>();
                double tn = tlen > MAX_ARRAY ? MAX_ARRAY : tlen;
                for (int ti = 0; ti < tn; ti++) tel.add(walk(index(v, ti), depth + 1));
                if (tlen > tn) tel.add("…[len=" + ScriptRuntime.toString(taLen) + "]");
                parts.add("elements=[" + String.join(",", tel) + "]");
            }

            Object keysObj;
            try {
                keysObj = call(cx, ownKeys, UNDEF, v);
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
                keysObj = null;
            }
            if (keysObj == null) {
                parts.add("keys=!");
            } else if (ScriptRuntime.toBoolean(call(cx, isArray, UNDEF, v))) {
                Object[] keys = elements((Scriptable) keysObj);
                Object len = dataProp(cx, v, "length");
                boolean lenIsNumber = isTypeof(len, "number");
                double n = lenIsNumber ? ((Number) len).doubleValue() : 0;
                double shown = n > MAX_ARRAY ? MAX_ARRAY : n;
                List<String> els = new ArrayList<>();
                for (int ai = 0; ai < shown; ai++) {
                    Object d;
                    try {
                        d = call(cx, gopd, UNDEF, v, ai);
                    } catch (RuntimeException e) {
                        if (!catchable(cx, e)) throw e;
                        d = null;
                    }
                    if (!truthy(d)) {
                        els.add("<hole>");
                    } else {
                        Scriptable ds = (Scriptable) d;
                        if (has(ds, "get") || has(ds, "set")) {
                            els.add(descriptor(v, ai));
                        } else {
                            String ev = walk(prop(ds, "value"), depth + 1);
                            boolean plain = truthy(prop(ds, "writable")) && truthy(prop(ds, "enumerable"))
                                    && truthy(prop(ds, "configurable"));
                            els.add(ev + (plain ? "" : descriptor(v, ai)));
                        }
                    }
                }
                if (n > shown) els.add("…");
                parts.add("length=" + (lenIsNumber ? ScriptRuntime.toString(len) : "0"));
                parts.add("[" + String.join(",", els) + "]");
                for (Object key : keys) {
                    if (key instanceof CharSequence ks) {
                        String s = ks.toString();
                        if (s.equals("length")) continue;
                        double u = ScriptRuntime.toUint32(s);
                        if (ScriptRuntime.toString(u).equals(s) && u < shown) continue;
                    }
                    String kt = keyText(cx, key);
                    String dsc = descriptor(v, key);
                    parts.add(kt + dsc + ":" + dataValue(v, key, depth + 1));
                }
            } else {
                Object[] keys = elements((Scriptable) keysObj);
                for (Object key : keys) {
                    if (errorLike && key instanceof CharSequence && excluded(key)) continue;
                    if (errorLike && isStr(key, "message")) {
                        parts.add("\"message\"" + descriptor(v, key));
                        continue;
                    }
                    if (isFunction && (isStr(key, "arguments") || isStr(key, "caller"))) continue;
                    String kt = keyText(cx, key);
                    String dsc = descriptor(v, key);
                    parts.add(kt + dsc + ":" + dataValue(v, key, depth + 1));
                }
            }
            Object ext;
            try {
                ext = call(cx, isExtensible, UNDEF, v);
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
                ext = "?";
            }
            if (Boolean.FALSE.equals(ext)) parts.add("non-extensible");
            return head + "<" + protoText(v) + ">{" + String.join(";", parts) + "}";
        }

        boolean brand(Callable sizeGetter, Object v) {
            try {
                call(cx, sizeGetter, v);
                return true;
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
                return false;
            }
        }
    }

    // ---- helpers shared by canon and errName ----------------------------------------------

    private boolean isProxy(Object v) {
        return !ScriptRuntime.shallowEq(v, proxyCtor) && Host.isProxy(v);
    }

    /** excludedErrorKeys[key] === 1, looked up like JS does (Object.prototype shows through). */
    private boolean excluded(Object key) {
        Object r = key instanceof CharSequence cs ? ScriptableObject.getProperty(excludedErrorKeys, cs.toString()) : Scriptable.NOT_FOUND;
        return r != Scriptable.NOT_FOUND && ScriptRuntime.shallowEq(r, 1);
    }

    private String tagOf(Context cx, Object v) {
        try {
            return ScriptRuntime.toString(call(cx, objToString, v));
        } catch (RuntimeException e) {
            if (!catchable(cx, e)) throw e;
            return "[object ?]";
        }
    }

    private boolean isErrorLike(Context cx, Object v, String tag) {
        if (tag.equals("[object Error]")) return true;
        Object p = v;
        int guard = 0;
        try {
            while (truthy(p) && guard++ < 50) {
                if (ScriptRuntime.shallowEq(p, errorPrototype())) return true;
                p = call(cx, gpo, UNDEF, p);
            }
        } catch (RuntimeException e) {
            if (!catchable(cx, e)) throw e;
        }
        return false;
    }

    /** {@code Error.prototype}, looked up in the scope each time like canon.js does. */
    private Object errorPrototype() {
        Object err = ScriptableObject.getProperty(global, "Error");
        if (err == Scriptable.NOT_FOUND) throw ScriptRuntime.typeError("Error is not defined");
        if (err == null || Undefined.isUndefined(err)) throw ScriptRuntime.typeError("Cannot read property \"prototype\"");
        if (err instanceof Scriptable s) return prop(s, "prototype");
        return Undefined.instance;
    }

    /** Looks a property up along the prototype chain without invoking accessors. */
    private Object dataProp(Context cx, Object obj, String key) {
        Object p = obj;
        int guard = 0;
        try {
            while (p != null && !Undefined.isUndefined(p) && guard++ < 50) {
                if (isProxy(p)) return Undefined.instance;
                Object d = call(cx, gopd, UNDEF, p, key);
                if (truthy(d)) {
                    Scriptable ds = (Scriptable) d;
                    return has(ds, "value") ? prop(ds, "value") : Undefined.instance;
                }
                p = call(cx, gpo, UNDEF, p);
            }
        } catch (RuntimeException e) {
            if (!catchable(cx, e)) throw e;
        }
        return Undefined.instance;
    }

    private String keyText(Context cx, Object k) {
        if (isTypeof(k, "symbol")) return symbolText(cx, k);
        return json(cx, k);
    }

    private String symbolText(Context cx, Object s) {
        for (int i = 0; i < wellKnownSyms.size(); i++) {
            if (ScriptRuntime.shallowEq(wellKnownSyms.get(i), s)) return wellKnownNames.get(i);
        }
        Object reg;
        try {
            reg = call(cx, symKeyFor, UNDEF, s);
        } catch (RuntimeException e) {
            if (!catchable(cx, e)) throw e;
            reg = Undefined.instance;
        }
        if (!Undefined.isUndefined(reg)) return "Symbol.for(" + json(cx, reg) + ")";
        Object d;
        try {
            d = symDesc != null ? call(cx, symDesc, symbolThis(cx, s)) : Undefined.instance;
        } catch (RuntimeException e) {
            if (!catchable(cx, e)) throw e;
            d = Undefined.instance;
        }
        if (Undefined.isUndefined(d)) {
            try {
                return ScriptRuntime.toString(call(cx, symToString, symbolThis(cx, s)));
            } catch (RuntimeException e) {
                if (!catchable(cx, e)) throw e;
                return "Symbol(?)";
            }
        }
        return "Symbol(" + json(cx, d) + ")";
    }

    /** JSON.stringify(v) through the captured function; plain printable ASCII strings need no call. */
    private String json(Context cx, Object v) {
        if (v instanceof CharSequence cs) {
            int n = cs.length();
            boolean plain = true;
            for (int i = 0; i < n; i++) {
                char c = cs.charAt(i);
                if (c < 0x20 || c > 0x7e || c == '"' || c == '\\') {
                    plain = false;
                    break;
                }
            }
            if (plain) return "\"" + cs + "\"";
        }
        return ScriptRuntime.toString(call(cx, jsonStringify, UNDEF, v));
    }

    /** The {@code this} of a call on a symbol, as {@code f.call(sym)} passes it from a script: a symbol
     *  value is a Scriptable on Rhino 1.9.2 and a bare SymbolKey on master, where the builtins' own
     *  getters cast {@code this} to Scriptable (a ClassCastException, not a script error). */
    private Object symbolThis(Context cx, Object s) {
        return Scopes.symbolThis(cx, scope, s);
    }

    private Object call(Context cx, Callable f, Object thisObj, Object... args) {
        return Scopes.callThis(f, cx, scope, thisObj, args.length == 0 ? NO_ARGS : args);
    }

    /** {@code '' + v} (the left operand of the concatenation is already a string): ToPrimitive, then ToString. */
    private static String concat(Context cx, Object v) {
        return ScriptRuntime.toString(ScriptRuntime.add("", v, cx));
    }

    /** The string {@code '!' + e} gives in a JS catch block for this exception. */
    private String errText(Context cx, RuntimeException e) {
        return ScriptRuntime.toString(ScriptRuntime.add("!", Scopes.catchValue(e, cx, scope), cx));
    }

    /** Whether a JS {@code catch} in the interpreter catches e (Interpreter: only JS-visible exceptions). */
    private static boolean catchable(Context cx, RuntimeException e) {
        if (e instanceof JavaScriptException || e instanceof EcmaError || e instanceof EvaluatorException) return true;
        if (e instanceof ContinuationPending) return false;
        return cx.hasFeature(Context.FEATURE_ENHANCED_JAVA_ACCESS);
    }

    private static String javaClass(Object v) {
        if (v instanceof Wrapper w) return w.unwrap() == null ? "null" : w.unwrap().getClass().getName();
        return "?";
    }

    private static boolean isTypeof(Object v, String t) {
        return ScriptRuntime.typeof(v).equals(t);
    }

    private static boolean truthy(Object v) {
        return v != null && v != Scriptable.NOT_FOUND && ScriptRuntime.toBoolean(v);
    }

    private static boolean isStr(Object v, String s) {
        return v instanceof CharSequence cs && s.contentEquals(cs);
    }

    /** o[name], read with the prototype chain (and getters) like JS; absent is undefined. */
    private static Object prop(Scriptable o, String name) {
        Object r = ScriptableObject.getProperty(o, name);
        return r == Scriptable.NOT_FOUND ? Undefined.instance : r;
    }

    /** name in o */
    private static boolean has(Scriptable o, String name) {
        return ScriptableObject.hasProperty(o, name);
    }

    private static Object index(Object o, int i) {
        Object r = ScriptableObject.getProperty((Scriptable) o, i);
        return r == Scriptable.NOT_FOUND ? Undefined.instance : r;
    }

    /** JS {@code s.slice(start, end)} for a negative end. */
    private static String jsSlice(String s, int start, int end) {
        int len = s.length();
        int from = start < 0 ? Math.max(len + start, 0) : Math.min(start, len);
        int to = end < 0 ? Math.max(len + end, 0) : Math.min(end, len);
        return from < to ? s.substring(from, to) : "";
    }

    /** The elements of a fresh array (what Reflect.ownKeys returned). */
    private static Object[] elements(Scriptable arr) {
        long n = (long) ScriptRuntime.toNumber(ScriptableObject.getProperty(arr, "length"));
        Object[] out = new Object[(int) n];
        for (int i = 0; i < n; i++) {
            Object r = ScriptableObject.getProperty(arr, i);
            out[i] = r == Scriptable.NOT_FOUND ? Undefined.instance : r;
        }
        return out;
    }

    private static Scriptable obj(Scriptable o, String name) {
        return (Scriptable) ScriptableObject.getProperty(o, name);
    }

    private Scriptable proto(String ctor) {
        return (Scriptable) ScriptableObject.getProperty(obj(global, ctor), "prototype");
    }

    private static Callable fn(Scriptable o, String name) {
        return (Callable) ScriptableObject.getProperty(o, name);
    }

    /** gopd(o, name).get */
    private Callable getter(Context cx, Scriptable o, String name) {
        Object d = call(cx, gopd, UNDEF, o, name);
        if (!truthy(d)) return null;
        Object g = prop((Scriptable) d, "get");
        return g instanceof Callable c ? c : null;
    }
}
