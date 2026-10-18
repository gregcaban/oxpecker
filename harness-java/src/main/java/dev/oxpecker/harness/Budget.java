package dev.oxpecker.harness;

/** Per-run instruction and wall-clock budget, consulted from the instruction observer. */
final class Budget {
    private final long instructionLimit;
    private final long deadlineNanos;
    private long consumed;

    Budget(long instructionLimit, long wallMs) {
        this.instructionLimit = instructionLimit;
        this.deadlineNanos = System.nanoTime() + wallMs * 1_000_000L;
    }

    /** Returns the reason the budget is exhausted, or null. */
    String consume(int instructions) {
        consumed += instructions;
        if (consumed > instructionLimit) return "instructions";
        if (System.nanoTime() > deadlineNanos) return "wall";
        return null;
    }

    long consumed() {
        return consumed;
    }

    /**
     * Thrown to abort a script. It is an Error (not a RuntimeException) so that neither JS
     * catch nor finally blocks run after it, which is what Rhino's interpreter does for Errors.
     */
    static final class Exceeded extends Error {
        final String reason;

        Exceeded(String reason) {
            super("budget exceeded: " + reason, null, false, false);
            this.reason = reason;
        }
    }
}
