package dev.oxpecker.harness;

import org.mozilla.javascript.Context;
import org.mozilla.javascript.debug.DebugFrame;
import org.mozilla.javascript.debug.DebuggableScript;
import org.mozilla.javascript.debug.Debugger;

/** Debugger that observes nothing; its presence alone switches Rhino onto the debug code path. */
final class NoOpDebugger implements Debugger {
    private static final DebugFrame FRAME = new DebugFrame() {};

    @Override
    public DebugFrame getFrame(Context cx, DebuggableScript fnOrScript) {
        return FRAME;
    }
}
