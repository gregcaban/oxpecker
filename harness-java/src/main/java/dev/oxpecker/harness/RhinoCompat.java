package dev.oxpecker.harness;

import java.lang.reflect.Method;
import org.mozilla.javascript.Context;
import org.mozilla.javascript.Script;

/**
 * Every call that depends on a particular Rhino version lives here, so that bumping
 * {@code rhinoVersion} in gradle.properties touches one file. Optional APIs are reached
 * reflectively and degrade to "unknown" rather than failing to compile.
 */
final class RhinoCompat {
    private RhinoCompat() {}

    private static final Method SET_EVALUATION_METHOD = find(Context.class, "setEvaluationMethod", evaluationMethodClass());
    private static final Method SET_OPTIMIZATION_LEVEL = find(Context.class, "setOptimizationLevel", int.class);
    private static final Method SET_INTERPRETED_MODE = find(Context.class, "setInterpretedMode", boolean.class);
    private static final Method PROCESS_MICROTASKS = find(Context.class, "processMicrotasks");
    private static final Method SET_MAX_INTERPRETER_STACK_DEPTH = find(Context.class, "setMaximumInterpreterStackDepth", int.class);

    /** Selects the interpreter or the bytecode compiler, whichever API this Rhino offers. */
    static void setInterpreted(Context cx, boolean interpreted) {
        try {
            if (SET_EVALUATION_METHOD != null) {
                Class<?> em = evaluationMethodClass();
                Object value = Enum.valueOf(em.asSubclass(Enum.class), interpreted ? "Interpreter" : "Compiler");
                SET_EVALUATION_METHOD.invoke(cx, value);
            } else if (SET_INTERPRETED_MODE != null) {
                SET_INTERPRETED_MODE.invoke(cx, interpreted);
            } else if (SET_OPTIMIZATION_LEVEL != null) {
                SET_OPTIMIZATION_LEVEL.invoke(cx, interpreted ? -1 : 9);
            } else {
                throw new IllegalStateException("no API to select the evaluation method on this Rhino");
            }
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Runs queued Promise jobs; a no-op on Rhino versions without an explicit microtask queue. */
    static void drainMicrotasks(Context cx) {
        if (PROCESS_MICROTASKS == null) return;
        try {
            PROCESS_MICROTASKS.invoke(cx);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Caps the interpreter's heap-allocated frame stack, where supported. */
    static void setInterpreterStackDepth(Context cx, int frames) {
        if (SET_MAX_INTERPRETER_STACK_DEPTH == null) return;
        try {
            SET_MAX_INTERPRETER_STACK_DEPTH.invoke(cx, frames);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    /**
     * Whether a compiled Script object really came from the bytecode compiler, or null if
     * this Rhino gives no way to tell. Rhino silently falls back to the interpreter when
     * codegen hits a class-file limit, so the mode cannot simply be assumed.
     *
     * <p>Strategies, most specific first: the 2.0 descriptor's code object is an
     * InterpreterData for interpreted scripts; older Rhinos return an InterpretedFunction
     * for interpreted scripts and an instance of a generated {@code org.mozilla.javascript.gen.*}
     * class for compiled ones.
     */
    static Boolean wasCompiled(Script script) {
        try {
            Method getDescriptor = find(script.getClass(), "getDescriptor");
            if (getDescriptor != null) {
                Object descriptor = getDescriptor.invoke(script);
                if (descriptor != null) {
                    Method getCode = find(descriptor.getClass(), "getCode");
                    if (getCode != null) {
                        Object code = getCode.invoke(descriptor);
                        if (code != null) return !code.getClass().getSimpleName().equals("InterpreterData");
                    }
                }
            }
        } catch (ReflectiveOperationException | RuntimeException ignored) {
            // fall through to the class-name heuristic
        }
        String cls = script.getClass().getName();
        if (cls.startsWith("org.mozilla.javascript.gen.")) return true;
        if (cls.equals("org.mozilla.javascript.InterpretedFunction")) return false;
        return null;
    }

    private static Class<?> evaluationMethodClass() {
        try {
            return Class.forName("org.mozilla.javascript.Context$EvaluationMethod");
        } catch (ClassNotFoundException e) {
            return null;
        }
    }

    private static Method find(Class<?> cls, String name, Class<?>... params) {
        if (cls == null) return null;
        for (Class<?> p : params) if (p == null) return null;
        try {
            Method m = cls.getMethod(name, params);
            m.setAccessible(true);
            return m;
        } catch (NoSuchMethodException | RuntimeException e) {
            return null;
        }
    }
}
