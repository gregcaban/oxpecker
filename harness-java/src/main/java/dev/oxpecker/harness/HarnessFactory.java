package dev.oxpecker.harness;

import org.mozilla.javascript.Context;
import org.mozilla.javascript.ContextFactory;

/** ContextFactory that enforces the per-run {@link Budget} through the instruction observer. */
final class HarnessFactory extends ContextFactory {
    static final Object BUDGET_KEY = new Object();

    @Override
    protected void observeInstructionCount(Context cx, int instructionCount) {
        Budget b = (Budget) cx.getThreadLocal(BUDGET_KEY);
        if (b == null) return;
        String reason = b.consume(instructionCount);
        if (reason != null) throw new Budget.Exceeded(reason);
    }

    @Override
    protected boolean hasFeature(Context cx, int featureIndex) {
        // Keep Rhino's defaults; the point is only that both modes see the same answers.
        return super.hasFeature(cx, featureIndex);
    }
}
