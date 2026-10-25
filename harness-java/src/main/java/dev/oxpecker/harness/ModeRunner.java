package dev.oxpecker.harness;

import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.atomic.AtomicReference;
import org.mozilla.javascript.Context;
import org.mozilla.javascript.EcmaError;
import org.mozilla.javascript.EvaluatorException;
import org.mozilla.javascript.JavaScriptException;
import org.mozilla.javascript.RhinoException;
import org.mozilla.javascript.Script;
import org.mozilla.javascript.ScriptableObject;
import org.mozilla.javascript.TopLevel;
import org.mozilla.javascript.Undefined;
import org.mozilla.javascript.WrappedException;

/** Runs one case in one mode, on a dedicated thread with a fixed stack size and a watchdog. */
final class ModeRunner {
    /** Same stack for every mode so deep recursion is at least comparable. */
    static final long STACK_BYTES = 64L * 1024 * 1024;
    /** Grace beyond the soft wall limit before the watchdog declares the thread stuck. */
    static final long WATCHDOG_GRACE_MS = 3_000;
    /** Frame limit for the interpreter's heap-allocated stack. */
    static final int INTERPRETER_STACK_DEPTH = 20_000;

    private final HarnessFactory factory = new HarnessFactory();
    final Coverage coverage = new Coverage();
    /** Set when a worker thread could not be stopped; the JVM must be recycled. */
    private volatile boolean poisoned;

    boolean poisoned() {
        return poisoned;
    }

    Outcome run(Request req, Mode mode) {
        AtomicReference<Outcome> result = new AtomicReference<>();
        Thread t = new Thread(null, () -> result.set(runOnThisThread(req, mode)),
                "oxpecker-" + mode.wire, STACK_BYTES);
        t.setDaemon(true);
        long start = System.nanoTime();
        t.start();
        try {
            t.join(req.wallMs() + WATCHDOG_GRACE_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        Outcome o = result.get();
        if (o == null) {
            // The instruction observer did not get a chance to fire (e.g. a long-running
            // native builtin). Report, and let the client restart us.
            poisoned = true;
            o = Outcome.of(mode, Outcome.TIMEOUT);
            o.reason = "watchdog";
        }
        o.ms = (System.nanoTime() - start) / 1_000_000;
        if (coverage.enabled()) {
            String bucket = (req.coverageBucket == null ? "*" : req.coverageBucket) + "|" + mode.wire;
            Coverage.Drained d = coverage.drain(bucket, Boolean.TRUE.equals(req.coverageNewIds), req.coverageRequire);
            o.newProbes = d.fresh;
            o.newProbeIds = d.newIds;
            o.requiredReached = d.requiredReached;
        }
        return o;
    }

    private Outcome runOnThisThread(Request req, Mode mode) {
        Outcome o = Outcome.of(mode, Outcome.OK);
        Host host = new Host();
        try (Context cx = factory.enterContext()) {
            mode.configure(cx);
            cx.setLanguageVersion(req.languageVersion());
            cx.setLocale(Locale.ROOT);
            cx.setTimeZone(TimeZone.getTimeZone("UTC"));
            // Enabled in all modes so the compiler emits the same observer code either way.
            // A budget <= 0 disables the observer entirely (changes compiled codegen; useful to
            // check whether a finding depends on it).
            if (req.instructionBudget() > 0) cx.setInstructionObserverThreshold(10_000);
            // The interpreter keeps its frames on the heap and would otherwise recurse until
            // the JVM runs out of memory; compiled code hits the Java stack instead.
            if (mode.interpreted) RhinoCompat.setInterpreterStackDepth(cx, INTERPRETER_STACK_DEPTH);
            cx.putThreadLocal(HarnessFactory.BUDGET_KEY, new Budget(req.instructionBudget(), req.wallMs()));

            // No LiveConnect, no shared state: a fresh scope per case and per mode.
            TopLevel scope = cx.initSafeStandardObjects(new TopLevel());
            ScriptableObject global = scope.getGlobalThis();
            host.install(cx, scope, req.seed());

            String src = req.source;
            int line = 1;
            if (req.strict()) {
                src = "\"use strict\";\n" + src;
                line = 0;
            }

            Script script = null;
            try {
                try {
                    script = cx.compileString(src, req.sourceName(), line, null);
                } catch (RhinoException e) {
                    o.status = Outcome.SYNTAX;
                    o.thrown = new Outcome.Thrown();
                    o.thrown.kind = "syntax";
                    o.thrown.name = e instanceof EcmaError ee ? ee.getName() : "SyntaxError";
                    o.thrown.message = e.details();
                    return o;
                }
                o.compiledActually = wasCompiled(script, mode);
                if (mode == Mode.COMPILED && Boolean.FALSE.equals(o.compiledActually)) {
                    o.status = Outcome.FALLBACK;
                    return o;
                }

                Object r = execute(cx, scope, global, script, req, host, o);
                RhinoCompat.drainMicrotasks(cx);
                Host.Canonical k = host.canon(cx, r);
                o.value = k.text();
                o.valueJavaTypes = k.javaTypes();
                if (!k.soft().isEmpty()) host.soft.add(k.soft());
                return o;
            } catch (JavaScriptException e) {
                return thrown(o, host, cx, "js-value", e.getValue(), null, e.details());
            } catch (EcmaError e) {
                return thrown(o, host, cx, "ecma-error", e.getErrorObject(), e.getName(), e.getErrorMessage());
            } catch (Budget.Exceeded e) {
                o.status = Outcome.TIMEOUT;
                o.reason = e.reason;
                return o;
            } catch (StackOverflowError e) {
                return resource(o, "stack");
            } catch (WrappedException e) {
                Throwable cause = e.getWrappedException();
                if (cause instanceof StackOverflowError) return resource(o, "stack");
                return crash(o, cause != null ? cause : e);
            } catch (EvaluatorException e) {
                String msg = e.details();
                if (msg != null && (msg.contains("too much recursion") || msg.contains("stack"))) {
                    return resource(o, "stack");
                }
                return thrown(o, host, cx, "evaluator", null, "InternalError", msg);
            } catch (OutOfMemoryError e) {
                poisoned = true;
                return crash(o, e);
            } catch (IllegalStateException e) {
                // Rhino refuses continuation capture outside the interpreter / outside
                // executeScriptWithContinuations: a by-design mode difference, not a crash.
                String msg = String.valueOf(e.getMessage());
                if (msg.contains("continuation") || msg.contains("Interpreter frames")) {
                    o.status = Outcome.UNSUPPORTED;
                    o.reason = "continuations";
                    o.engineException = Outcome.describe(e);
                    return o;
                }
                return crash(o, e);
            } catch (RuntimeException | LinkageError | AssertionError e) {
                // Anything else out of Rhino is an engine bug (VerifyError, NPE in codegen, ...).
                return crash(o, e);
            } finally {
                o.trace = host.trace;
                o.traceJavaTypes = host.traceJavaTypes;
                o.output = host.output;
                o.soft = host.soft;
                o.continuations = host.continuations;
                o.javaTrace = host.java.trace;
                o.javaTraceTypes = host.java.types;
                if (o.compiledActually == null && script != null) o.compiledActually = wasCompiled(script, mode);
            }
        } catch (RuntimeException | LinkageError | AssertionError e) {
            // Failure while setting up the scope or the host: a harness bug rather than a finding,
            // but reported through the same channel so it is never silent.
            o.status = Outcome.CRASH;
            o.engineException = Outcome.describe(e);
            o.reason = "setup";
            return o;
        }
    }

    /**
     * Runs the script; with continuations requested and an interpreted script, runs it through
     * executeScriptWithContinuations and resumes each captured continuation with the paused
     * value, logging every round. A compiled script runs plainly: its __pause() calls fail
     * inside Rhino, which the normal exception classification reports.
     */
    private static Object execute(Context cx, TopLevel scope, ScriptableObject global, Script script,
            Request req, Host host, Outcome o) {
        if (!req.continuations() || !Boolean.FALSE.equals(o.compiledActually)) {
            return script.exec(cx, scope, global);
        }
        org.mozilla.javascript.ContinuationPending pending;
        try {
            return cx.executeScriptWithContinuations(script, scope);
        } catch (org.mozilla.javascript.ContinuationPending p) {
            pending = p;
        }
        for (int round = 0; ; round++) {
            Object paused = pending.getApplicationState();
            Host.Canonical k = host.canon(cx, paused);
            host.continuations.add("pause(" + k.text() + ")");
            if (round >= req.maxResumes()) {
                host.continuations.add("abandoned");
                return Undefined.instance;
            }
            // Resume with the paused value itself, so `var x = __pause(v)` sees x === v.
            try {
                Object r = cx.resumeContinuation(pending.getContinuation(), scope, paused);
                host.continuations.add("end");
                return r;
            } catch (org.mozilla.javascript.ContinuationPending p) {
                pending = p;
            }
        }
    }

    private static Outcome resource(Outcome o, String reason) {
        o.status = Outcome.RESOURCE;
        o.reason = reason;
        return o;
    }

    private static Outcome crash(Outcome o, Throwable t) {
        o.status = Outcome.CRASH;
        o.engineException = Outcome.describe(t);
        return o;
    }

    private static Outcome thrown(Outcome o, Host host, Context cx, String kind, Object value,
            String name, String message) {
        o.status = Outcome.THROW;
        Outcome.Thrown t = new Outcome.Thrown();
        t.kind = kind;
        t.message = message;
        t.name = name;
        if (value != null) {
            try {
                Host.Canonical k = host.canon(cx, value);
                t.value = k.text();
                t.valueJavaTypes = k.javaTypes();
                if (!k.soft().isEmpty()) host.soft.add(k.soft());
                if (t.name == null) t.name = host.errName(cx, value);
            } catch (RuntimeException | StackOverflowError e) {
                t.value = "!canon: " + e;
                if (t.name == null) t.name = "?";
            }
        }
        o.thrown = t;
        return o;
    }

    /** Delegates to RhinoCompat; falls back to the mode's nominal answer when Rhino can't tell. */
    static Boolean wasCompiled(Script script, Mode mode) {
        Boolean b = RhinoCompat.wasCompiled(script);
        return b != null ? b : !mode.interpreted;
    }
}
