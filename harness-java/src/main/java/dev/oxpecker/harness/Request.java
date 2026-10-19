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
    /**
     * Run through Context.executeScriptWithContinuations where the script is interpreted, so
     * that __pause() can capture a continuation; the harness resumes it. Compiled scripts
     * cannot do this, which is itself a mode difference under test.
     */
    public Boolean continuations;
    /** Maximum number of pause/resume rounds per run. */
    public Integer maxResumes;
    /** Coverage bucket (e.g. the generator name) this case's execution data is merged into. */
    public String coverageBucket;
    /** For cmd coverage-save / coverage-load: the .exec file path. */
    public String path;

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

    public boolean continuations() {
        return continuations != null && continuations;
    }

    public int maxResumes() {
        return maxResumes == null ? 50 : maxResumes;
    }

    public String sourceName() {
        return sourceName == null ? "case.js" : sourceName;
    }

    public List<Mode> modeList() {
        if (modes == null || modes.isEmpty()) return List.of(Mode.INTERPRETED, Mode.COMPILED);
        return modes.stream().map(Mode::fromWire).toList();
    }
}
