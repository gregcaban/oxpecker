package dev.oxpecker.harness;

import java.util.ArrayList;
import java.util.List;

/** Result of running one case in one mode. Serialised as-is by Gson. */
public final class Outcome {
    public static final String OK = "ok";
    public static final String THROW = "throw";
    public static final String SYNTAX = "syntax";
    public static final String TIMEOUT = "timeout";
    public static final String RESOURCE = "resource";
    public static final String FALLBACK = "fallback";
    public static final String CRASH = "crash";

    public String mode;
    /** Whether the script actually ran through the bytecode compiler (see ModeRunner#wasCompiled). */
    public Boolean compiledActually;
    public String status;
    /** Canonical form of the script's completion value (status ok only). */
    public String value;
    /** Java runtime types of the leaves of {@link #value}, e.g. "Integer" vs "Double". */
    public String valueJavaTypes;
    public Thrown thrown;
    /** Canonical probe snapshots, in order. */
    public List<String> trace = new ArrayList<>();
    /** Java runtime types for each probe snapshot, parallel to {@link #trace}. */
    public List<String> traceJavaTypes = new ArrayList<>();
    /** What Java received/returned at each J.* call: values (compared strictly). */
    public List<String> javaTrace = new ArrayList<>();
    /** Runtime classes seen at each J.* call, parallel to {@link #javaTrace}. */
    public List<String> javaTraceTypes = new ArrayList<>();
    /** Error messages encountered during canonicalisation (compared softly). */
    public List<String> soft = new ArrayList<>();
    /** Captured print() output. */
    public List<String> output = new ArrayList<>();
    public EngineException engineException;
    /** Why a timeout/resource status was reported: instructions, wall, watchdog, stack. */
    public String reason;
    public long ms;

    public static final class Thrown {
        /** "js-value" for a JS throw, "ecma-error" for a Rhino runtime error, "evaluator" for EvaluatorException. */
        public String kind;
        public String name;
        public String message;
        public String value;
        public String valueJavaTypes;
    }

    public static final class EngineException {
        public String clazz;
        public String message;
        public String stack;
    }

    static Outcome of(Mode mode, String status) {
        Outcome o = new Outcome();
        o.mode = mode.wire;
        o.status = status;
        return o;
    }

    static EngineException describe(Throwable t) {
        EngineException e = new EngineException();
        e.clazz = t.getClass().getName();
        e.message = t.getMessage();
        StringBuilder sb = new StringBuilder();
        StackTraceElement[] frames = t.getStackTrace();
        for (int i = 0; i < Math.min(frames.length, 25); i++) {
            sb.append(frames[i]).append('\n');
        }
        if (t.getCause() != null && t.getCause() != t) {
            sb.append("Caused by: ").append(t.getCause()).append('\n');
        }
        e.stack = sb.toString();
        return e;
    }
}
