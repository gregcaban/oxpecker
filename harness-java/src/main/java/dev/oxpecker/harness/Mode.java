package dev.oxpecker.harness;

import org.mozilla.javascript.Context;

/** The Rhino execution configurations under test. */
public enum Mode {
    INTERPRETED("interpreted", true),
    COMPILED("compiled", false),
    /** Interpreter with a no-op Debugger attached, as in Rhino's own test262 runner. */
    DEBUGGER("debugger", true);

    public final String wire;
    public final boolean interpreted;

    Mode(String wire, boolean interpreted) {
        this.wire = wire;
        this.interpreted = interpreted;
    }

    public static Mode fromWire(String s) {
        for (Mode m : values()) {
            if (m.wire.equals(s)) return m;
        }
        throw new IllegalArgumentException("unknown mode: " + s);
    }

    void configure(Context cx) {
        RhinoCompat.setInterpreted(cx, interpreted);
        if (this == DEBUGGER) cx.setDebugger(new NoOpDebugger(), null);
    }
}
