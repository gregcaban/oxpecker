package dev.oxpecker.harness;

import org.mozilla.javascript.Callable;
import org.mozilla.javascript.Context;
import org.mozilla.javascript.LambdaFunction;
import org.mozilla.javascript.Script;
import org.mozilla.javascript.Scriptable;
import org.mozilla.javascript.ScriptableObject;
import org.mozilla.javascript.SerializableCallable;
import org.mozilla.javascript.TopLevel;

/**
 * Every call that takes a scope, for the Rhino 1.x API, where a scope is a Scriptable. The
 * same class in src/rhino2 types it as the 2.0 line's VarScope; build.gradle compiles one
 * of the two (rhinoApi). The rest of the harness holds the scope as an opaque Object.
 */
final class Scopes {
    private Scopes() {}

    /** A fresh top-level scope with the standard objects and no LiveConnect. */
    static Object newTopLevel(Context cx) {
        return cx.initSafeStandardObjects(new TopLevel());
    }

    /** The object top-level {@code this} and global declarations land on: the scope itself. */
    static ScriptableObject globalThis(Object scope) {
        return (ScriptableObject) scope;
    }

    static Object exec(Script script, Context cx, Object scope, Object thisObj) {
        return script.exec(cx, (Scriptable) scope, (Scriptable) thisObj);
    }

    static Object call(Callable f, Context cx, Object scope, Object thisObj, Object[] args) {
        return f.call(cx, (Scriptable) scope, (Scriptable) thisObj, args);
    }

    static LambdaFunction lambda(Object scope, String name, int length, SerializableCallable target) {
        return new LambdaFunction((Scriptable) scope, name, length, target);
    }

    static Object javaToJS(Object value, Object scope, Context cx) {
        return Context.javaToJS(value, (Scriptable) scope, cx);
    }

    static Object executeWithContinuations(Context cx, Script script, Object scope) {
        return cx.executeScriptWithContinuations(script, (Scriptable) scope);
    }

    static Object resumeContinuation(Context cx, Object continuation, Object scope, Object result) {
        return cx.resumeContinuation(continuation, (Scriptable) scope, result);
    }
}
