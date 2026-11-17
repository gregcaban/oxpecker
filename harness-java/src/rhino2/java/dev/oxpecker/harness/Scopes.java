package dev.oxpecker.harness;

import org.mozilla.javascript.Callable;
import org.mozilla.javascript.Context;
import org.mozilla.javascript.LambdaFunction;
import org.mozilla.javascript.Script;
import org.mozilla.javascript.VarScope;
import org.mozilla.javascript.ScriptableObject;
import org.mozilla.javascript.SerializableCallable;
import org.mozilla.javascript.TopLevel;

/**
 * Every call that takes a scope, for the Rhino 2.0 API (master since 04729d7e2), where a scope
 * is a VarScope and no longer an object: TopLevel holds the global object separately
 * (getGlobalThis). The same class in src/rhino1 is the 1.x one; build.gradle compiles one of
 * the two (rhinoApi). The rest of the harness holds the scope as an opaque Object.
 */
final class Scopes {
    private Scopes() {}

    /** A fresh top-level scope with the standard objects and no LiveConnect. */
    static Object newTopLevel(Context cx) {
        return cx.initSafeStandardObjects(new TopLevel());
    }

    /** The object top-level {@code this} and global declarations land on. */
    static ScriptableObject globalThis(Object scope) {
        return ((TopLevel) scope).getGlobalThis();
    }

    static Object exec(Script script, Context cx, Object scope, Object thisObj) {
        return script.exec(cx, (VarScope) scope, thisObj);
    }

    static Object call(Callable f, Context cx, Object scope, Object thisObj, Object[] args) {
        return f.call(cx, (VarScope) scope, thisObj, args);
    }

    static LambdaFunction lambda(Object scope, String name, int length, SerializableCallable target) {
        return new LambdaFunction((VarScope) scope, name, length, target);
    }

    static Object javaToJS(Object value, Object scope, Context cx) {
        return Context.javaToJS(value, (VarScope) scope, cx);
    }

    static Object executeWithContinuations(Context cx, Script script, Object scope) {
        return cx.executeScriptWithContinuations(script, (VarScope) scope);
    }

    static Object resumeContinuation(Context cx, Object continuation, Object scope, Object result) {
        return cx.resumeContinuation(continuation, (VarScope) scope, result);
    }
}
