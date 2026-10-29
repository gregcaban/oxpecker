package dev.oxpecker.harness;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;
import java.util.jar.JarEntry;
import java.util.jar.JarFile;
import org.jacoco.core.analysis.Analyzer;
import org.jacoco.core.analysis.CoverageBuilder;
import org.jacoco.core.analysis.IClassCoverage;
import org.jacoco.core.analysis.ICounter;
import org.jacoco.core.data.ExecutionData;
import org.jacoco.core.data.ExecutionDataReader;
import org.jacoco.core.data.ExecutionDataStore;
import org.jacoco.core.data.ExecutionDataWriter;
import org.jacoco.core.data.SessionInfoStore;
import java.io.FileInputStream;
import java.io.FileOutputStream;

/**
 * Coverage of Rhino itself, through the JaCoCo agent attached by bin/harness (OXPECKER_COVERAGE=1).
 *
 * <p>Every run can name a bucket (e.g. the generator the program came from). After each mode the
 * agent's execution data is drained, merged into the bucket's store (keyed bucket|mode) and into a
 * cumulative store; the number of probes hit for the first time is returned with the outcome, which
 * is the signal Hypothesis {@code target()} can steer on. {@code report()} analyses every bucket
 * against the Rhino classes once, at the end.
 */
final class Coverage {
    private final Object agent;              // org.jacoco.agent.rt.IAgent, or null when not attached
    private final ExecutionDataStore cumulative = new ExecutionDataStore();
    private final Map<String, ExecutionDataStore> buckets = new TreeMap<>();
    private long totalProbes;

    Coverage() {
        Object a = null;
        try {
            a = org.jacoco.agent.rt.RT.getAgent();
        } catch (Throwable ignored) {
            // agent not attached
        }
        agent = a;
    }

    boolean enabled() {
        return agent != null;
    }

    /** What one drain found: probes new to this JVM, and optionally which ones, and which of a
     *  required set this run reached. Probe sets are keyed by class name (stable across JVMs). */
    static final class Drained {
        int fresh;
        Map<String, List<Integer>> newIds;          // only when requested
        Map<String, List<Integer>> requiredReached; // only when a required set was sent
    }

    /** Drains the agent and merges into {@code bucket}; returns probes newly covered overall. */
    synchronized int drain(String bucket) {
        return drain(bucket, false, null).fresh;
    }

    /**
     * Drains the agent (resetting it, so the data is exactly what this run hit), merges into
     * {@code bucket} and the cumulative store, and reports the probes hit for the first time in
     * this JVM -- with {@code wantIds}, also which ones -- and, for {@code required}, the subset of
     * it this run reached. Shrinking a corpus entry needs the latter: on a re-run its probes are no
     * longer new, so "new probes > 0" cannot be the property; "reaches P" can.
     */
    synchronized Drained drain(String bucket, boolean wantIds, Map<String, List<Integer>> required) {
        Drained d = new Drained();
        if (wantIds) d.newIds = new TreeMap<>();
        if (required != null) d.requiredReached = new TreeMap<>();
        if (agent == null) return d;
        byte[] data = ((org.jacoco.agent.rt.IAgent) agent).getExecutionData(true);
        ExecutionDataStore store = buckets.computeIfAbsent(bucket, k -> new ExecutionDataStore());
        ExecutionDataReader reader = new ExecutionDataReader(new ByteArrayInputStream(data));
        reader.setSessionInfoVisitor(new SessionInfoStore());
        reader.setExecutionDataVisitor(ed -> {
            ExecutionData prev = cumulative.get(ed.getId());
            boolean[] probes = ed.getProbes();
            boolean[] old = prev == null ? new boolean[0] : prev.getProbes();
            if (prev == null) totalProbes += probes.length;
            List<Integer> ids = null;
            for (int i = 0; i < probes.length; i++) {
                if (probes[i] && (i >= old.length || !old[i])) {
                    d.fresh++;
                    if (wantIds) {
                        if (ids == null) ids = new ArrayList<>();
                        ids.add(i);
                    }
                }
            }
            if (ids != null) d.newIds.put(ed.getName(), ids);
            if (required != null) {
                List<Integer> want = required.get(ed.getName());
                if (want != null) {
                    List<Integer> got = new ArrayList<>();
                    for (int i : want) if (i < probes.length && probes[i]) got.add(i);
                    if (!got.isEmpty()) d.requiredReached.put(ed.getName(), got);
                }
            }
            cumulative.put(copy(ed));
            store.put(copy(ed));
        });
        try {
            reader.read();
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        return d;
    }

    private static ExecutionData copy(ExecutionData ed) {
        return new ExecutionData(ed.getId(), ed.getName(), ed.getProbes().clone());
    }

    /** Persists the cumulative store (JaCoCo .exec format) so a later JVM resumes the frontier. */
    synchronized void save(String path) throws IOException {
        try (FileOutputStream out = new FileOutputStream(path)) {
            ExecutionDataWriter w = new ExecutionDataWriter(out);
            new SessionInfoStore().accept(w);
            cumulative.accept(w);
        }
    }

    /** Merges a saved .exec file into the cumulative store (not into any bucket). */
    synchronized void load(String path) throws IOException {
        try (FileInputStream in = new FileInputStream(path)) {
            ExecutionDataReader r = new ExecutionDataReader(in);
            r.setSessionInfoVisitor(new SessionInfoStore());
            r.setExecutionDataVisitor(ed -> {
                if (cumulative.get(ed.getId()) == null) totalProbes += ed.getProbes().length;
                cumulative.put(copy(ed));
            });
            r.read();
        }
    }

    synchronized long coveredProbes() {
        long n = 0;
        for (ExecutionData ed : cumulative.getContents()) for (boolean p : ed.getProbes()) if (p) n++;
        return n;
    }

    synchronized long totalProbes() {
        return totalProbes;
    }

    /**
     * Per bucket: per-package method/branch/line counters, plus per-class method counters for the
     * optimizer package and the interpreter/runtime classes of interest.
     */
    synchronized Map<String, Object> report() throws IOException {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("enabled", agent != null);
        out.put("coveredProbes", coveredProbes());
        out.put("totalProbes", totalProbes);
        Map<String, Object> perBucket = new LinkedHashMap<>();
        for (Map.Entry<String, ExecutionDataStore> e : buckets.entrySet()) {
            perBucket.put(e.getKey(), analyse(e.getValue()));
        }
        perBucket.put("*cumulative*", analyse(cumulative));
        out.put("buckets", perBucket);
        return out;
    }

    private static final String[] INTEREST = {"org.mozilla.javascript.Interpreter", "org.mozilla.javascript.ScriptRuntime",
            "org.mozilla.javascript.Context", "org.mozilla.javascript.CodeGenerator", "org.mozilla.javascript.NativeArray",
            "org.mozilla.javascript.NativeObject", "org.mozilla.javascript.ScriptableObject", "org.mozilla.javascript.NativeProxy",
            "org.mozilla.javascript.JSFunction", "org.mozilla.javascript.ConsString"};

    private static Map<String, Object> analyse(ExecutionDataStore store) throws IOException {
        CoverageBuilder builder = new CoverageBuilder();
        Analyzer analyzer = new Analyzer(store, builder);
        String jarPath = org.mozilla.javascript.Context.class.getProtectionDomain().getCodeSource().getLocation().getPath();
        try (JarFile jar = new JarFile(jarPath)) {
            Enumeration<JarEntry> entries = jar.entries();
            while (entries.hasMoreElements()) {
                JarEntry je = entries.nextElement();
                String n = je.getName();
                if (!n.startsWith("org/mozilla/javascript/") || !n.endsWith(".class") || n.startsWith("org/mozilla/javascript/gen/")) continue;
                try (InputStream in = jar.getInputStream(je)) {
                    analyzer.analyzeClass(in, n);
                }
            }
        }
        Map<String, int[]> packages = new TreeMap<>();   // pkg -> {mCov, mTot, bCov, bTot, lCov, lTot}
        Map<String, int[]> classes = new TreeMap<>();    // class -> {mCov, mTot}
        for (IClassCoverage cc : builder.getClasses()) {
            String name = cc.getName().replace('/', '.');
            String pkg = cc.getPackageName().replace('/', '.');
            int[] p = packages.computeIfAbsent(pkg, k -> new int[6]);
            add(p, 0, cc.getMethodCounter());
            add(p, 2, cc.getBranchCounter());
            add(p, 4, cc.getLineCounter());
            boolean interesting = pkg.equals("org.mozilla.javascript.optimizer");
            for (String s : INTEREST) if (name.equals(s) || name.startsWith(s + "$")) interesting = true;
            if (interesting && cc.getMethodCounter().getCoveredCount() > 0) {
                int[] c = classes.computeIfAbsent(name.replaceAll("\\$.*", ""), k -> new int[2]);
                c[0] += cc.getMethodCounter().getCoveredCount();
                c[1] += cc.getMethodCounter().getTotalCount();
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("packages", packages);
        out.put("classes", classes);
        return out;
    }

    private static void add(int[] acc, int at, ICounter c) {
        acc[at] += c.getCoveredCount();
        acc[at + 1] += c.getTotalCount();
    }
}
