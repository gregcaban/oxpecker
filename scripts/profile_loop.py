"""Where the fuzz loop's time goes. Three measurements, each a subcommand:

    scripts/profile_loop.py loop OUT [--seconds 180] [--seed-corpus runs/X/corpus] [--jfr]
        runs oxpecker.fuzz.fuzz() as `oxpecker fuzz` does, with wall-clock timers around its
        phases (generation, harness round trip, Rhino's own time, verdict, shrinking, checkpoint)
    scripts/profile_loop.py gen OUT [-n 150]
        cProfile over drawing fresh lineages and extensions only (no harness); writes the programs
        drawn to OUT/programs.json for `harness`
    scripts/profile_loop.py harness OUT [--passes 3] [--jfr]
        replays OUT/programs.json through the harness alone: the executions/s the JVM side gives
        with and without the coverage agent, and the fixed cost of an empty program

    scripts/profile_loop.py jfr OUT/harness.jfr
        the harness JVM's sampled CPU per phase of a case, from a --jfr recording

Each writes OUT/<subcommand>.json and prints a table. --jfr records a Java Flight Recorder
profile of the harness JVM into OUT/ (read with `jfr print --events jdk.ExecutionSample`).
"""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import shutil
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from random import Random

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oxpecker import fuzz as fz                       # noqa: E402
from oxpecker.harness import client as hc             # noqa: E402
from oxpecker.harness.compare import compare_run      # noqa: E402
from oxpecker.il.options import GenOptions            # noqa: E402


# ------------------------------------------------------------------------------- exclusive timers
class Timers:
    """Exclusive wall time per phase: time inside a nested phase is not counted in its parent.
    A phase entered under "shrink" is recorded as "shrink/<phase>"."""

    def __init__(self) -> None:
        self.total: dict[str, float] = defaultdict(float)
        self.calls: Counter[str] = Counter()
        self._local = threading.local()  # a stack per thread: the pipelined loop runs programs on a second one,
                                         # so the phases can add up to more than the wall

    @property
    def stack(self) -> list[list]:       # [name, started, child time]
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    def wrap(self, name: str, fn):
        def timed(*a, **kw):
            label = name
            if any(s[0].startswith("shrink") for s in self.stack) and not name.startswith("shrink"):
                label = "shrink/" + name
            frame = [label, time.perf_counter(), 0.0]
            self.stack.append(frame)
            try:
                return fn(*a, **kw)
            finally:
                self.stack.pop()
                spent = time.perf_counter() - frame[1]
                self.total[label] += spent - frame[2]
                self.calls[label] += 1
                if self.stack:
                    self.stack[-1][2] += spent
        return timed

    def add(self, name: str, seconds: float) -> None:
        """Move `seconds` of the current phase's time to `name` (a sub-phase measured elsewhere)."""
        label = name
        if any(s[0].startswith("shrink") for s in self.stack):
            label = "shrink/" + name
        self.total[label] += seconds
        self.calls[label] += 1
        if self.stack:
            self.stack[-1][2] += seconds


def jfr_start(client, out: Path, name: str) -> None:
    """Start a Flight Recorder profile in the running harness JVM (bin/harness execs java, so the
    client's pid is the JVM's); it is written to OUT/name when the JVM exits. Started with jcmd
    rather than a JVM flag: the flag's startup line goes to stdout, the harness's reply channel."""
    import subprocess
    jcmd = Path(os.environ.get("OXPECKER_JAVA", "/usr/local/opt/openjdk@21/bin/java")).with_name("jcmd")
    subprocess.run([str(jcmd), str(client.proc.pid), "JFR.start", "settings=profile", f"filename={out / name}",
                    "dumponexit=true"], check=True, stdout=subprocess.DEVNULL)


def table(rows: list[tuple[str, float, int]], wall: float) -> None:
    print(f"{'phase':42} {'seconds':>9} {'share':>7} {'calls':>8} {'ms/call':>9}")
    for name, sec, n in sorted(rows, key=lambda r: -r[1]):
        print(f"{name:42} {sec:9.2f} {100 * sec / wall:6.1f}% {n:8} {1000 * sec / n if n else 0:9.2f}")


# ------------------------------------------------------------------------------- loop
def cmd_loop(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    corpus = out / "corpus"
    if corpus.exists():
        shutil.rmtree(corpus)
    if args.seed_corpus:
        shutil.copytree(args.seed_corpus, corpus)
    t = Timers()
    round_trips: list[tuple[float, float, str]] = []   # (round trip s, Rhino ms both modes, statuses)

    # generation
    fz.draw_lineage = t.wrap("gen: fresh lineage", fz.draw_lineage)
    fz.extend = t.wrap("gen: extend parent", fz.extend)
    fz.LineageCache.get = t.wrap("gen: parent lineage (cache/rebuild)", fz.LineageCache.get)
    fz.decode = t.wrap("gen: decode choices", fz.decode)
    fz.encode = t.wrap("corpus: encode choices", fz.encode)
    # bookkeeping
    fz.Corpus.add = t.wrap("corpus: append entry", fz.Corpus.add)
    fz.parent_weights = t.wrap("corpus: parent weights", fz.parent_weights)
    fz.findings.record = t.wrap("findings: record", fz.findings.record)
    fz.Shrinker.shrink = t.wrap("shrink: hypothesis", fz.Shrinker.shrink)
    fz.shrink_lifted = t.wrap("shrink: source", fz.shrink_lifted)
    verdict = t.wrap("verdict: compare modes", compare_run)

    # the harness: the pipe round trip, split into json, waiting, and (from the reply) Rhino's own ms
    real_request = hc.HarnessClient._request

    def request(self, req):
        cmd = req.get("cmd", "run")
        if cmd != "run":
            return t.wrap(f"harness: {cmd}", real_request)(self, req)
        started = time.perf_counter()
        resp = real_request(self, req)
        spent = time.perf_counter() - started
        results = resp.get("results", {})
        rhino = sum((o.get("ms") or 0) for o in results.values()) / 1000.0
        rhino = min(rhino, spent)
        t.add("harness: Rhino run (reported ms, 2 modes)", rhino)
        t.add("harness: round trip minus Rhino ms", spent - rhino)
        round_trips.append((spent, rhino, ",".join(o.get("status", "?") for o in results.values())))
        return resp

    hc.HarnessClient._request = request
    real_dumps, real_loads = hc.json.dumps, hc.json.loads

    class TimedJson:
        JSONDecodeError = json.JSONDecodeError
        dumps = staticmethod(t.wrap("harness: json encode request", real_dumps))
        loads = staticmethod(t.wrap("harness: json decode reply", real_loads))

    hc.json = TimedJson
    hc.Outcome.from_json = classmethod(t.wrap("harness: Outcome.from_json", hc.Outcome.from_json.__func__))

    from oxpecker.related import known_index
    own = hc.ModeClients(quiet=True, coverage=True) if args.mode_jvms else hc.HarnessClient(quiet=True, coverage=True)
    with own as client:
        assert client.info().get("coverage"), "coverage agent not attached"
        if args.jfr:
            if args.mode_jvms:
                for mode, c in client.clients.items():
                    jfr_start(c, out, f"loop-{mode}.jfr")
            else:
                jfr_start(client, out, "loop.jfr")
        index = known_index(client)
        # the loop's own wait for a program's run (on the run thread when pipelined): with a JVM per
        # mode the requests above are on two more threads, and this is the wall they cost together
        runner = t.wrap("run: the loop waiting for the JVMs (wall)", fz.harness_runner(client, 200, verdict))
        if args.shrink_process:
            verdict = compare_run                # goes to the shrink process: the timed wrapper does not pickle
            runner = t.wrap("run: the loop waiting for the JVMs (wall)", fz.harness_runner(client, 200, verdict))
        if args.workers or args.shrink_process:
            real_result = fz.ProcessPoolExecutor.submit

            def submit(self, fn, *a, **kw):
                future = real_result(self, fn, *a, **kw)
                label = ("shrink: the loop waiting for the shrink process (end of the run)" if fn is fz.shrink_finding
                         else "gen: the loop waiting for a generator process")
                future.result = t.wrap(label, future.result)
                return future
            fz.ProcessPoolExecutor.submit = submit
        t.total.clear(), t.calls.clear(), round_trips.clear()
        started = time.perf_counter()
        summary = fz.fuzz(corpus_dir=corpus, options=GenOptions(), seconds=args.seconds, seed=args.seed,
                          known=index.signatures, related_index=index, findings_dir=out / "findings",
                          client=client, runner=runner, quiet=True, verdict=verdict, judge_findings=False,
                          pipeline=not args.no_pipeline, generators=args.workers, shrink_process=args.shrink_process)
        wall = time.perf_counter() - started
    rows = [(k, v, t.calls[k]) for k, v in t.total.items()]
    accounted = sum(v for _, v, _ in rows)
    rows.append(("(loop itself, unaccounted)", wall - accounted, summary["runs"]))
    print(f"\n== loop: {summary['runs']} iterations in {wall:.1f} s = {summary['runs'] / wall:.2f}/s; "
          f"{len(round_trips)} harness runs = {len(round_trips) / wall:.2f}/s; corpus {summary['corpus']}; "
          f"seeded from {args.seed_corpus or 'nothing'}")
    table(rows, wall)
    trips = sorted(r[0] for r in round_trips)
    statuses = Counter(r[2] for r in round_trips)
    slow = [r for r in round_trips if r[0] > 1.0]
    print(f"\nharness round trip: median {1000 * statistics.median(trips):.1f} ms, mean {1000 * statistics.mean(trips):.1f} ms, "
          f"p90 {1000 * trips[int(.9 * len(trips))]:.1f} ms, p99 {1000 * trips[int(.99 * len(trips))]:.1f} ms, max {trips[-1]:.2f} s")
    print(f"runs over 1 s: {len(slow)} ({100 * len(slow) / len(trips):.1f} %) taking {sum(r[0] for r in slow):.1f} s "
          f"({100 * sum(r[0] for r in slow) / wall:.1f} % of the wall)")
    print("statuses (interpreted,compiled):", dict(statuses.most_common(8)))
    print("stats:", {k: v for k, v in summary["stats"].items() if not k.startswith("judged")})
    (out / "loop.json").write_text(json.dumps({
        "wall": wall, "iterations": summary["runs"], "harness_runs": len(round_trips), "seed_corpus": args.seed_corpus,
        "phases": {k: {"seconds": v, "calls": n} for k, v, n in rows}, "statuses": dict(statuses),
        "round_trip_ms": {"median": 1000 * statistics.median(trips), "mean": 1000 * statistics.mean(trips),
                          "p90": 1000 * trips[int(.9 * len(trips))], "p99": 1000 * trips[int(.99 * len(trips))]},
        "over_1s": {"runs": len(slow), "seconds": sum(r[0] for r in slow)}, "stats": summary["stats"]}, indent=1))


# ------------------------------------------------------------------------------- generation alone
def bucket(filename: str) -> str:
    f = filename.replace("\\", "/")
    if "/hypothesis/" in f:
        rest = f.split("/hypothesis/", 1)[1]
        return "hypothesis/" + ("internal/conjecture" if rest.startswith("internal/conjecture") else
                                "strategies" if rest.startswith("strategies") else "other")
    if "/oxpecker/" in f:
        return "oxpecker/" + f.split("/oxpecker/", 1)[1].removesuffix(".py")
    if f.startswith("<") or f.startswith("~"):
        return "builtins"
    return "stdlib/other"


def cmd_gen(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    options = GenOptions()
    rng = Random(args.seed)
    programs: list[dict] = []
    times = {"fresh": [], "extend": []}
    sizes: list[int] = []

    def work() -> None:
        for _ in range(args.n):
            started = time.perf_counter()
            drawn = fz.draw_lineage(options, 5, rng=rng)
            times["fresh"].append(time.perf_counter() - started)
            if drawn is None:
                continue
            lineage, choices = drawn
            programs.append({"source": lineage.source, "template": lineage.template})
            sizes.append(len(lineage.code))
            for _ in range(4):                       # p_new = 0.2: four extensions per fresh lineage
                started = time.perf_counter()
                child = fz.extend(choices, options, 5, rng, lineage)
                times["extend"].append(time.perf_counter() - started)
                if child is None:
                    break
                lineage, choices = child
                programs.append({"source": lineage.source, "template": lineage.template})
                sizes.append(len(lineage.code))

    work()                                           # warm: imports, strategy caches
    programs.clear(), sizes.clear()
    for v in times.values():
        v.clear()
    prof = cProfile.Profile()
    started = time.perf_counter()
    prof.enable()
    work()
    prof.disable()
    wall = time.perf_counter() - started
    stats = pstats.Stats(prof)
    by_bucket: dict[str, float] = defaultdict(float)
    for (filename, _line, _name), (_cc, _nc, tt, _ct, _callers) in stats.stats.items():
        by_bucket[bucket(filename)] += tt
    total = sum(by_bucket.values())
    print(f"\n== generation alone: {len(programs)} programs in {wall:.1f} s under cProfile "
          f"(cProfile roughly doubles the time; the per-draw times below are from the same run)")
    for kind, v in times.items():
        if v:
            print(f"{kind:8} n={len(v):4} mean {1000 * statistics.mean(v):7.1f} ms  median {1000 * statistics.median(v):7.1f} ms  "
                  f"p90 {1000 * sorted(v)[int(.9 * len(v))]:7.1f} ms")
    print(f"program size: mean {statistics.mean(sizes):.0f} instructions, "
          f"mean {statistics.mean(len(p['source'].splitlines()) for p in programs):.0f} source lines")
    print(f"\n{'own time by module':46} {'seconds':>8} {'share':>7}")
    for name, sec in sorted(by_bucket.items(), key=lambda kv: -kv[1])[:22]:
        print(f"{name:46} {sec:8.2f} {100 * sec / total:6.1f}%")
    print("\ntop functions by own time:")
    top = sorted(stats.stats.items(), key=lambda kv: -kv[1][2])[:30]
    for (filename, line, name), (_cc, nc, tt, ct, _callers) in top:
        print(f"{tt:7.2f} s own {ct:7.2f} s cum {nc:9} calls  {bucket(filename)}:{line} {name}")
    print("\ntop oxpecker functions by cumulative time:")
    ours = sorted(((k, v) for k, v in stats.stats.items() if "/oxpecker/" in k[0]), key=lambda kv: -kv[1][3])[:30]
    for (filename, line, name), (_cc, nc, tt, ct, _callers) in ours:
        print(f"{ct:7.2f} s cum {tt:7.2f} s own {nc:9} calls  {bucket(filename)}:{line} {name}")
    (out / "programs.json").write_text(json.dumps(programs))
    stats.dump_stats(str(out / "gen.prof"))
    (out / "gen.json").write_text(json.dumps({
        "programs": len(programs), "wall_profiled": wall, "by_module": dict(by_bucket),
        "ms": {k: {"mean": 1000 * statistics.mean(v), "median": 1000 * statistics.median(v)} for k, v in times.items() if v}},
        indent=1))

    # the same work without the profiler: the real generation rate
    programs_before = len(programs)
    started = time.perf_counter()
    work()
    plain = time.perf_counter() - started
    made = len(programs) - programs_before
    print(f"\nwithout cProfile: {made} programs in {plain:.1f} s = {made / plain:.1f} programs/s on one core "
          f"({1000 * plain / made:.0f} ms each)")
    del programs[programs_before:]


# ------------------------------------------------------------------------------- harness alone
def cmd_harness(args) -> None:
    out = Path(args.out)
    programs = json.loads((out / "programs.json").read_text())
    empty = [{"source": "1;\n", "template": "empty"}] * 300
    report: dict[str, dict] = {}

    def replay(label: str, cases: list[dict], *, coverage: bool, ids: bool, modes=hc.DEFAULT_MODES, jfr: str | None = None) -> None:
        with hc.HarnessClient(quiet=True, coverage=coverage) as client:
            client.info()
            if jfr:
                jfr_start(client, out, jfr)
            for n in range(args.passes):
                trips, rhino, statuses = [], [], Counter()
                started = time.perf_counter()
                for case in cases:
                    t0 = time.perf_counter()
                    res = client.run(case["source"], seed=1, modes=modes, coverage_bucket=case["template"] if coverage else None,
                                     coverage_new_ids=ids)
                    trips.append(time.perf_counter() - t0)
                    rhino.append(sum(o.ms or 0 for o in res.results.values()) / 1000.0)
                    statuses[",".join(o.status for o in res.results.values())] += 1
                wall = time.perf_counter() - started
            quick = [x for x in trips if x < 1.0]
            row = {"programs": len(cases), "per_s": len(cases) / wall, "median_ms": 1000 * statistics.median(trips),
                   "mean_ms": 1000 * statistics.mean(trips), "rhino_ms_mean": 1000 * statistics.mean(rhino),
                   "per_s_without_slow": len(quick) / sum(quick), "slow": len(trips) - len(quick),
                   "slow_seconds": sum(trips) - sum(quick), "restarts": client.restarts, "statuses": dict(statuses.most_common(6))}
            report[label] = row
            print(f"{label:50} {row['per_s']:7.1f}/s  median {row['median_ms']:6.1f} ms  mean {row['mean_ms']:7.1f} ms  "
                  f"(Rhino-reported {row['rhino_ms_mean']:6.1f} ms)  without the {row['slow']} runs over 1 s: "
                  f"{row['per_s_without_slow']:6.1f}/s  restarts {row['restarts']}")

    print(f"\n== harness alone, one JVM, pass {args.passes} of {args.passes} over {len(programs)} generated programs (earlier passes warm the JIT)")
    replay("generated, coverage + new ids (as the loop)", programs, coverage=True, ids=True, jfr="harness.jfr" if args.jfr else None)
    replay("generated, coverage, counts only", programs, coverage=True, ids=False)
    replay("generated, no coverage agent", programs, coverage=False, ids=False)
    replay("generated, no agent, interpreted only", programs, coverage=False, ids=False, modes=("interpreted",))
    replay("generated, no agent, compiled only", programs, coverage=False, ids=False, modes=("compiled",))
    replay("empty program `1;`, coverage + new ids", empty, coverage=True, ids=True)
    replay("empty program `1;`, no coverage agent", empty, coverage=False, ids=False)
    print("statuses of the generated programs:", report["generated, no coverage agent"]["statuses"])
    (out / "harness.json").write_text(json.dumps(report, indent=1))


# ------------------------------------------------------------------------------- JVM options
JVM_OPTIONS = (
    "",
    "-XX:+UseSerialGC",
    "-XX:+UseSerialGC -XX:CICompilerCount=2",
    "-XX:+UseSerialGC -XX:TieredStopAtLevel=1",
    "-XX:+UseParallelGC -XX:ParallelGCThreads=2 -XX:CICompilerCount=2",
)


def cmd_jvm(args) -> None:
    """What the harness JVM costs in CPU beyond running the cases: its compiler and collector
    threads. Per option set (JAVA_TOOL_OPTIONS), the programs replayed `--passes` times with
    coverage on: programs/s on the last pass, and the JVM's CPU seconds over all of them."""
    out = Path(args.out)
    programs = json.loads((out / "programs.json").read_text())
    report = {}
    print(f"\n== JVM options: {args.passes} passes over {len(programs)} programs, coverage on; CPU is the JVM's, all passes")
    for opts in (args.options or JVM_OPTIONS):
        if opts:
            os.environ["JAVA_TOOL_OPTIONS"] = opts
        else:
            os.environ.pop("JAVA_TOOL_OPTIONS", None)
        before = os.times()
        started = time.perf_counter()
        with hc.HarnessClient(quiet=True, coverage=True) as client:
            client.info()
            for n in range(args.passes):
                pass_started = time.perf_counter()
                rhino = 0.0
                for case in programs:
                    res = client.run(case["source"], seed=1, coverage_bucket=case["template"], coverage_new_ids=True)
                    rhino += sum(o.ms or 0 for o in res.results.values()) / 1000.0
                last = time.perf_counter() - pass_started
        wall = time.perf_counter() - started
        after = os.times()                                   # the JVM has exited and been waited for
        cpu = (after.children_user + after.children_system) - (before.children_user + before.children_system)
        runs = args.passes * len(programs)
        report[opts or "(default)"] = {"last_pass_per_s": len(programs) / last, "wall": wall, "jvm_cpu": cpu,
                                       "cpu_ms_per_program": 1000 * cpu / runs, "cores": cpu / wall}
        print(f"{opts or '(default)':66} {len(programs) / last:6.1f}/s last pass  wall {wall:6.1f} s  JVM CPU {cpu:6.1f} s "
              f"= {cpu / wall:4.2f} cores, {1000 * cpu / runs:5.1f} CPU-ms per program")
    os.environ.pop("JAVA_TOOL_OPTIONS", None)
    (out / "jvm.json").write_text(json.dumps(report, indent=1))


# ------------------------------------------------------------------------------- reading a JFR profile
JFR_PHASES = (          # first match wins, looked for anywhere in the sample's stack
    ("coverage drain (JaCoCo getExecutionData + diff)", ("harness.Coverage.", "org.jacoco.")),
    ("host install (canon.js, prelude.js, globals)", ("harness.Host.install",)),
    ("fresh scope (initStandardObjects)", ("harness.Scopes.newTopLevel", "initSafeStandardObjects", "initStandardObjects")),
    ("canonicalising values (canon.js, __probe)", ("harness.Host.canon", "harness.Host.lambda", "harness.JavaProbe", "harness.Host.")),
    ("compiling the case: to JVM bytecode", ("optimizer.Codegen", "optimizer.ClassCompiler", "DefiningClassLoader", "ClassLoader.defineClass")),
    ("compiling the case: parse + IR", ("Context.compileString", "Context.compileImpl", "javascript.Parser.")),
    ("running the case: interpreter", ("javascript.Interpreter.interpret",)),
    ("running the case: compiled code", ("javascript.gen.",)),
    ("json (Gson)", ("com.google.gson.",)),
)


def cmd_jfr(args) -> None:
    """Share of the harness JVM's sampled CPU per phase of a case."""
    import subprocess
    jfr = Path(os.environ.get("OXPECKER_JAVA", "/usr/local/opt/openjdk@21/bin/java")).with_name("jfr")
    raw = subprocess.run([str(jfr), "print", "--json", "--events", "jdk.ExecutionSample", "--stack-depth", "200", args.file],
                         check=True, capture_output=True, text=True).stdout
    events = json.loads(raw)["recording"]["events"]
    phases: Counter[str] = Counter()
    tops: Counter[str] = Counter()
    for e in events:
        frames = [f"{f['method']['type']['name']}.{f['method']['name']}".replace("/", ".") for f in (e["values"].get("stackTrace") or {}).get("frames", [])]
        if not frames:
            continue
        tops[frames[0]] += 1
        for name, needles in JFR_PHASES:
            if any(n in f for f in frames for n in needles):
                phases[name] += 1
                break
        else:
            phases["other"] += 1
    total = sum(phases.values())
    print(f"\n== {args.file}: {total} execution samples")
    for name, n in phases.most_common():
        print(f"{name:52} {100 * n / total:5.1f}%")
    print("\nhottest methods (top of stack):")
    for name, n in tops.most_common(15):
        print(f"{100 * n / total:5.1f}%  {name}")
    # Execution samples are of Java threads only. The JVM's own threads (JIT compiler, collector)
    # show in the per-thread CPU load events: each thread kind's share of the JVM's CPU.
    import re
    raw = subprocess.run([str(jfr), "print", "--json", "--events", "jdk.ThreadCPULoad", args.file],
                         check=True, capture_output=True, text=True).stdout
    load: Counter[str] = Counter()
    for e in json.loads(raw)["recording"]["events"]:
        v = e["values"]
        thread = (v.get("eventThread") or {}).get("javaName") or (v.get("eventThread") or {}).get("osName") or "?"
        load[re.sub(r"[#\d]+$", "", re.sub(r"\d+", "", thread)).strip() or thread] += (v.get("user") or 0) + (v.get("system") or 0)
    whole = sum(load.values())
    if whole:
        print("\nthe JVM's CPU by kind of thread (jdk.ThreadCPULoad):")
        for name, n in load.most_common(8):
            print(f"{100 * n / whole:5.1f}%  {name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("loop")
    lp.add_argument("out")
    lp.add_argument("--seconds", type=float, default=180)
    lp.add_argument("--seed", type=int, default=1)
    lp.add_argument("--seed-corpus")
    lp.add_argument("--jfr", action="store_true")
    lp.add_argument("--no-pipeline", action="store_true", help="draw and run in turn, as before pipelining")
    lp.add_argument("--workers", type=int, default=0, help="generator processes (oxpecker fuzz --workers)")
    lp.add_argument("--mode-jvms", action="store_true", help="one JVM per mode (oxpecker fuzz --mode-jvms)")
    lp.add_argument("--shrink-process", action="store_true", help="shrink findings in their own process (oxpecker fuzz --shrink-process)")
    lp.set_defaults(fn=cmd_loop)
    g = sub.add_parser("gen")
    g.add_argument("out")
    g.add_argument("-n", type=int, default=30, help="fresh lineages (each extended four times)")
    g.add_argument("--seed", type=int, default=1)
    g.set_defaults(fn=cmd_gen)
    h = sub.add_parser("harness")
    h.add_argument("out")
    h.add_argument("--passes", type=int, default=3)
    h.add_argument("--jfr", action="store_true")
    h.set_defaults(fn=cmd_harness)
    v = sub.add_parser("jvm")
    v.add_argument("out")
    v.add_argument("--passes", type=int, default=6)
    v.add_argument("--options", action="append", help="a JAVA_TOOL_OPTIONS string to measure (repeatable; default: a fixed set)")
    v.set_defaults(fn=cmd_jvm)
    j = sub.add_parser("jfr")
    j.add_argument("file")
    j.set_defaults(fn=cmd_jfr)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
