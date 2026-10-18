package dev.oxpecker.harness;

import java.util.List;

/** One JSON-lines request. Fields mirror the wire protocol; Gson fills them. */
public final class Request {
    /** Optional command: "run" (default), "info", "exit". */
    public String cmd;

    public String id;
    public String source;
    public String sourceName;
    public List<String> modes;
    /** Rhino language version, e.g. 200 (ES6) or 250 (ECMASCRIPT). */
    public Integer languageVersion;
    public Boolean strict;
    /** Approximate number of interpreted/compiled instructions allowed. */
    public Long instructionBudget;
    /** Soft wall-clock limit enforced via the instruction observer. */
    public Long wallMs;
    /** Seed for the prelude's Math.random replacement. */
    public Long seed;

    public static final int DEFAULT_LANGUAGE_VERSION = 200;
    public static final long DEFAULT_INSTRUCTION_BUDGET = 5_000_000L;
    public static final long DEFAULT_WALL_MS = 5_000L;

    public int languageVersion() {
        return languageVersion == null ? DEFAULT_LANGUAGE_VERSION : languageVersion;
    }

    public boolean strict() {
        return strict != null && strict;
    }

    public long instructionBudget() {
        return instructionBudget == null ? DEFAULT_INSTRUCTION_BUDGET : instructionBudget;
    }

    public long wallMs() {
        return wallMs == null ? DEFAULT_WALL_MS : wallMs;
    }

    public long seed() {
        return seed == null ? 1L : seed;
    }

    public String sourceName() {
        return sourceName == null ? "case.js" : sourceName;
    }

    public List<Mode> modeList() {
        if (modes == null || modes.isEmpty()) return List.of(Mode.INTERPRETED, Mode.COMPILED);
        return modes.stream().map(Mode::fromWire).toList();
    }
}
