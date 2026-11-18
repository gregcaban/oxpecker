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

    // NOTE: the three methods below are written from memory of the 2.0 API and were NOT compiled
    // against master (no master jar was available when they were added); fix them first if
    // `oxpecker build-harness master` fails here.

    /** Calls with an arbitrary JS value as {@code this} (2.0: any Object is accepted). */
    static Object callThis(Callable f, Context cx, Object scope, Object thisObj, Object[] args) {
        return f.call(cx, (VarScope) scope, thisObj, args);
    }

    /** The value a JS {@code catch (e)} binds for this exception (the interpreter's own conversion). */
    static Object catchValue(RuntimeException e, Context cx, Object scope) {
        if (e instanceof org.mozilla.javascript.JavaScriptException jse) return jse.getValue();
        Object cs = org.mozilla.javascript.ScriptRuntime.newCatchScope(e, null, "e", cx, (VarScope) scope);
        return ScriptableObject.getProperty((ScriptableObject) cs, "e");
    }

    /** A fresh plain object inheriting from the scope's Object.prototype. */
    static ScriptableObject newObject(Context cx, Object scope) {
        return (ScriptableObject) cx.newObject((VarScope) scope);
    }

    /** The {@code this} for a call on a symbol value: 2.0 symbol values are bare SymbolKeys, and the
     *  builtins' getters cast {@code this} to Scriptable, so it is boxed as a script's call would. */
    static Object symbolThis(Context cx, Object scope, Object symbol) {
        if (symbol instanceof org.mozilla.javascript.Scriptable) return symbol;
        return org.mozilla.javascript.ScriptRuntime.toObject(cx, (VarScope) scope, symbol);
    }

    static Object resumeContinuation(Context cx, Object continuation, Object scope, Object result) {
        return cx.resumeContinuation(continuation, (VarScope) scope, result);
    }
}
