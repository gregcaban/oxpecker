"""Command-line interface.

  oxpecker run out/ tests/regressions/ run existing .js files through the harness
  oxpecker diff case.js                where the two modes' outcomes first differ, per field
  oxpecker corpus test262|rhino-jstests|rhino-mozilla [--filter PREFIX] [--limit N]   replay a corpus
  oxpecker probe                       refresh the Rhino capability table

  oxpecker gen -o out/ -n 50 [--seed 1]  write N generated programs (.js, .plain.js, .il) to a directory
  oxpecker gen -o out/ -n 50 --run     ... and run them through the harness
  oxpecker gen -o out/ -n 50 --evolve 4  ... each case the end of a 4-step mutation lineage
  oxpecker fuzz -o runs/c1 --seconds 600  the loop: coverage corpus of lineages, findings shrunk and recorded
  oxpecker shrink finding.js           statement-level shrinking of a reproducer (writes finding.min.js)
  oxpecker export -o ui/data --per-generator 20   sample every generator/template for the UI; oxpecker metrics DIR
  oxpecker list [--family F] [--templates]   the registered generators (name, family, weight, doc)

"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------- running
def _js_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.glob("*.js") if not f.name.endswith(".plain.js")))
        elif p.suffix == ".js":
            files.append(p)
    return files


def run_paths(paths: list[Path], args: argparse.Namespace) -> dict[str, Any]:
    from .harness.client import HarnessClient
    from .harness.compare import compare_run

    files = _js_files(paths)
    if not files:
        print("no .js files found", file=sys.stderr)
        return {}
    modes = tuple(args.modes.split(","))
    counts: Counter[str] = Counter()
    status_counts: dict[str, Counter[str]] = {m: Counter() for m in modes}
    findings: list[tuple[Path, str]] = []
    results: dict[str, Any] = {}
    t0 = time.time()
    with HarnessClient(quiet=True, coverage=args.coverage) as client:
        for f in files:
            src = f.read_text(encoding="utf-8")
            run = client.run(src, modes=modes, language_version=args.language_version, strict=args.strict,
                             id=f.stem, source_name=f.name, wall_ms=args.wall_ms)
            v = compare_run(run, strict_java_types=args.strict_java_types)
            counts[v.kind] += 1
            for m, o in run.results.items():
                status_counts[m][o.status] += 1
            rec = {"id": f.stem, "file": str(f), "verdict": v.to_json(), "results": run.to_json()["results"]}
            results[f.stem] = rec
            f.with_name(f.stem + ".result.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
            line = f"{f.name:28} {v.pretty()}"
            if v.is_finding:
                findings.append((f, v.pretty()))
                print(line)
            elif args.verbose:
                print(line)
        if args.coverage:
            cov = client.coverage_report()
            out_dir = paths[0] if paths[0].is_dir() else paths[0].parent
            (out_dir / "coverage.json").write_text(json.dumps(cov, indent=1), encoding="utf-8")
            print_coverage(cov)
    elapsed = time.time() - t0
    print(f"\n{len(files)} cases in {elapsed:.1f}s ({len(files) / max(elapsed, 1e-9):.1f}/s), JVM restarts: {client.restarts}")
    print("verdicts: " + ", ".join(f"{k}={n}" for k, n in sorted(counts.items())))
    sigs: Counter[str] = Counter()
    for r in results.values():
        if r["verdict"]["kind"] in ("hard", "java-types"):
            sigs[r["verdict"]["signature"]] += 1
    if sigs:
        print("unique finding signatures:")
        for sig, n in sigs.most_common():
            print(f"  {n:4}  {sig}")
    for m in modes:
        print(f"  {m:12} " + ", ".join(f"{k}={n}" for k, n in sorted(status_counts[m].items())))
    return results


def print_coverage(cov: dict[str, Any]) -> None:
    if not cov.get("enabled"):
        print("coverage: agent not attached (is OXPECKER_COVERAGE=1 reaching bin/harness?)")
        return
    print(f"\nRhino coverage: {cov['coveredProbes']}/{cov['totalProbes']} probes")
    for bucket, rep in cov["buckets"].items():
        pk = rep["packages"]
        opt = pk.get("org.mozilla.javascript.optimizer", [0, 0, 0, 0, 0, 0])
        core = pk.get("org.mozilla.javascript", [0, 0, 0, 0, 0, 0])
        interp = rep["classes"].get("org.mozilla.javascript.Interpreter", [0, 0])
        print(f"  {bucket:40} optimizer methods {opt[0]:4}/{opt[1]:<4} branches {opt[2]:5}/{opt[3]:<5}  "
              f"core methods {core[0]:5}/{core[1]:<5}  Interpreter {interp[0]:3}/{interp[1]}")


def cmd_run(args: argparse.Namespace) -> int:
    results = run_paths([Path(p) for p in args.paths], args)
    hard = sum(1 for r in results.values() if r["verdict"]["kind"] == "hard")
    return 1 if hard else 0


# ----------------------------------------------------------------------------- corpora
def cmd_corpus(args: argparse.Namespace) -> int:
    from .corpora import test262
    from .harness.client import HarnessClient
    from .harness.compare import compare_run

    from .corpora import rhino_tests

    if args.known_divergences:
        for p, modes in sorted(test262.known_divergences().items()):
            print(f"{','.join(sorted(modes)):22} {p}")
        return 0

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    modes = tuple(args.modes.split(","))
    counts: Counter[str] = Counter()
    status_counts: dict[str, Counter[str]] = {m: Counter() for m in modes}
    sigs: Counter[str] = Counter()
    n = 0
    t0 = time.time()
    log = (out_dir / "results.jsonl").open("w", encoding="utf-8") if out_dir else None
    if args.corpus == "test262":
        it = test262.cases(filter=args.filter, limit=args.limit, strict_variants=not args.no_strict_variants,
                           only_known=args.only_known)
    elif args.corpus == "rhino-jstests":
        it = rhino_tests.jstests(filter=args.filter, limit=args.limit)
    else:
        it = rhino_tests.mozilla(filter=args.filter, limit=args.limit)
    with HarnessClient(quiet=True) as client:
        for case in it:
            n += 1
            run = client.run(case.source, modes=modes, language_version=args.language_version,
                             id=case.id, source_name=case.path, strict=case.strict and args.corpus != "test262",
                             wall_ms=args.wall_ms)
            v = compare_run(run, strict_java_types=args.strict_java_types)
            counts[v.kind] += 1
            for m, o in run.results.items():
                status_counts[m][o.status] += 1
            rec = {"id": case.id, "path": case.path, "strict": case.strict, "tags": case.tags,
                   "verdict": v.to_json(), "results": run.to_json()["results"]}
            if log:
                log.write(json.dumps(rec) + "\n")
            if v.kind in ("hard", "java-types"):
                sigs[v.signature()] += 1
            known = case.tags.get("knownDivergence")
            if v.is_finding or known or args.verbose:
                flag = f"  (rhino: fails in {known})" if known else ""
                print(f"{case.id:70} {v.pretty()[:110]}{flag}")
                if v.is_finding and out_dir:
                    name = case.path.replace("/", "__") + ("--strict" if case.strict else "") + ".js"
                    (out_dir / name).write_text(case.source, encoding="utf-8")
            if n % 500 == 0:
                print(f"... {n} cases, {n / (time.time() - t0):.1f}/s", file=sys.stderr)
    if log:
        log.close()
    elapsed = time.time() - t0
    print(f"\n{n} cases in {elapsed:.1f}s ({n / max(elapsed, 1e-9):.1f}/s), JVM restarts: {client.restarts}")
    print("verdicts: " + ", ".join(f"{k}={c}" for k, c in sorted(counts.items())))
    for m in modes:
        print(f"  {m:12} " + ", ".join(f"{k}={c}" for k, c in sorted(status_counts[m].items())))
    if sigs:
        print("unique finding signatures:")
        for sig, c in sigs.most_common(30):
            print(f"  {c:5}  {sig}")
    return 0


# ----------------------------------------------------------------------------- diff
def cmd_diff(args: argparse.Namespace) -> int:
    """Runs one file and shows, per field, where the two modes' outcomes first differ."""
    from .harness.client import HarnessClient
    from .harness.compare import compare_run

    path = Path(args.path)
    modes = tuple(args.modes.split(","))
    with HarnessClient(quiet=True) as client:
        run = client.run(path.read_text(encoding="utf-8"), modes=modes, language_version=args.language_version,
                         strict=args.strict, id=path.stem, source_name=path.name, wall_ms=args.wall_ms)
    a, b = run.results[modes[0]], run.results[modes[1]]
    print(f"verdict: {compare_run(run, strict_java_types=args.strict_java_types).pretty()}\n")
    for field in ("status", "compiledActually", "value", "valueJavaTypes", "thrown", "trace", "traceJavaTypes",
                  "javaTrace", "javaTraceTypes", "continuations", "output", "soft", "reason"):
        x, y = getattr(a, field), getattr(b, field)
        if x == y:
            continue
        if isinstance(x, list) and isinstance(y, list):
            for i, (p, q) in enumerate(zip(x, y)):
                if p != q:
                    _show_diff(f"{field}[{i}]", str(p), str(q), modes, ctx=args.context)
                    break
            else:
                print(f"{field}: length {len(x)} vs {len(y)}")
                extra = x[len(y):] or y[len(x):]
                print(f"  extra in {modes[0] if len(x) > len(y) else modes[1]}: {str(extra[0])[:200]}")
        else:
            _show_diff(field, json.dumps(x) if isinstance(x, dict) else str(x), json.dumps(y) if isinstance(y, dict) else str(y), modes, ctx=args.context)
    return 0


def _show_diff(label: str, x: str, y: str, modes: tuple[str, ...], ctx: int = 60) -> None:
    i = next((k for k, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
    lo = max(0, i - ctx)
    print(f"{label}: first difference at char {i}")
    print(f"  {modes[0]:12} …{x[lo:i]}▶{x[i:i + ctx]}…")
    print(f"  {modes[1]:12} …{y[lo:i]}▶{y[i:i + ctx]}…")


# ----------------------------------------------------------------------------- probe
def cmd_probe(args: argparse.Namespace) -> int:
    """Refreshes the Rhino capability table (builtins, per-snippet syntax support)."""
    from . import environment
    from .harness.client import HarnessClient

    with HarnessClient(quiet=True) as client:
        table = environment.run_probe(client, language_version=args.language_version)
    environment.save(table)
    protos = environment.method_table(table)
    statics = environment.static_table(table)
    print(f"rhino {table.get('rhino')}, language version {args.language_version}")
    print(f"{len(table['globals'])} globals, {len(protos)} prototypes with {sum(len(v) for v in protos.values())} methods, "
          f"{sum(len(v) for v in statics.values())} static functions on {len(statics)} owners")
    unsupported = sorted(k for k, v in table["syntax"].items() if not v["supported"])
    print("syntax unsupported: " + ", ".join(unsupported))
    for k, v in sorted(table["syntax"].items()):
        if v["verdict"] not in ("equal",) or "crash" in (v.get("interpreted"), v.get("compiled")):
            print(f"  !! {k:22} interpreted={v.get('interpreted')} compiled={v.get('compiled')}  {v['detail'][:120]}")
    print(f"written to {environment.CACHE}")
    return 0


# ----------------------------------------------------------------------------- main
# ----------------------------------------------------------------------------- generating
def _gen_options(args: argparse.Namespace):
    from .il.options import ALL_TOGGLES, GenOptions

    kw: dict[str, Any] = {"strict": args.strict}
    if args.max_statements is not None:
        kw["max_statements"] = args.max_statements       # min_statements follows it when smaller (GenOptions)
    if args.max_depth is not None:
        kw["max_depth"] = args.max_depth
    if args.family:
        kw["families"] = frozenset(args.family)
    if args.generator:
        kw["generators"] = frozenset(args.generator)
    if args.without:
        kw["toggles"] = ALL_TOGGLES - set(args.without)
    if args.language_version:
        kw["language_version"] = args.language_version
    return GenOptions(**kw)


def write_cases(out: Path, count: int, seed: int, options, *, template: str | None = None,
                evolve: int = 0) -> list[Path]:
    """Generate `count` programs from `seed` and write case_NNNN.js (probed), .plain.js, .il and
    .meta.json into `out`. Returns the .js paths. Case k is `generate(seed + k)`, or with
    `evolve` the end of `lineage(seed + k, steps=evolve)` (the steps are listed in the meta)."""
    import oxpecker.il.templates  # noqa: F401  (registration)
    from .il.mutate import evolved
    from .il.program import dump
    from .il.strategies import draw, programs

    strategy = programs(options, template) if not evolve else evolved(options, evolve, base=None)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for k in range(count):
        gen = draw(strategy, seed + k)
        stem = f"case_{k:04d}"
        js = out / f"{stem}.js"
        js.write_text(gen.source, encoding="utf-8")
        (out / f"{stem}.plain.js").write_text(gen.plain_source, encoding="utf-8")
        (out / f"{stem}.il").write_text(dump(gen.code) + "\n", encoding="utf-8")
        used = sorted({i.gen for i in gen.code if i.gen})
        steps = [s.name for s in getattr(gen, "steps", [])]
        meta = {"seed": seed + k, "template": gen.template, "generators": used, "instructions": len(gen.code),
                "mutations": steps,
                "options": {"strict": options.strict, "max_statements": options.max_statements, "max_depth": options.max_depth,
                            "toggles": sorted(options.toggles), "families": sorted(options.families or ()),
                            "generators": sorted(options.generators or ())}}
        (out / f"{stem}.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        paths.append(js)
    return paths


def cmd_gen(args: argparse.Namespace) -> int:
    options = _gen_options(args)
    out = Path(args.out)
    t0 = time.time()
    paths = write_cases(out, args.count, args.seed, options, template=args.template, evolve=args.evolve)
    print(f"{len(paths)} programs in {out} ({time.time() - t0:.1f}s), seeds {args.seed}..{args.seed + len(paths) - 1}")
    if not args.run:
        return 0
    results = run_paths([out], args)
    hard = sum(1 for r in results.values() if r["verdict"]["kind"] == "hard")
    return 1 if hard else 0


def cmd_list(args: argparse.Namespace) -> int:
    import oxpecker.il.templates  # noqa: F401
    from .il.strategies import FAMILY_ORDER, REGISTRY, TEMPLATES

    if args.templates:
        for name in sorted(TEMPLATES):
            t = TEMPLATES[name]
            print(f"{name:28} weight {t.weight:3}  {t.doc.strip().splitlines()[0] if t.doc else ''}")
        return 0
    specs = sorted(REGISTRY.values(), key=lambda s: (FAMILY_ORDER.index(s.family) if s.family in FAMILY_ORDER else 99, s.rank, s.name))
    family = None
    for spec in specs:
        if args.family and spec.family not in args.family:
            continue
        if spec.family != family:
            family = spec.family
            print(f"\n[{family}]")
        first = spec.doc.strip().splitlines()[0] if spec.doc else ""
        ctx = "" if spec.context.name == "JAVASCRIPT" else f" ctx={spec.context.name}"
        inputs = "" if not spec.inputs else " inputs=" + ",".join(str(t) for t in spec.inputs)
        print(f"  {spec.name:30} w{spec.weight:<3}{ctx}{inputs}  {first}")
    return 0


def cmd_shrink(args: argparse.Namespace) -> int:
    """Statement-level delta debugging of a reproducer while its coarse verdict signature holds."""
    from .harness.client import HarnessClient
    from .harness.compare import compare_run
    from .shrink import shrink_source

    path = Path(args.path)
    source = path.read_text(encoding="utf-8")
    with HarnessClient(quiet=True) as client:
        def verdict_of(src: str):
            return compare_run(client.run(src, language_version=args.language_version, strict=args.strict, seed=1,
                                          wall_ms=args.wall_ms))
        v = verdict_of(source)
        if not v.is_finding:
            print(f"not a finding: {v.pretty()}", file=sys.stderr)
            return 1
        coarse = v.coarse_signature()
        print(f"keeping: {coarse}", file=sys.stderr)
        small = shrink_source(source, lambda src: verdict_of(src).coarse_signature() == coarse, budget=args.budget)
        final = verdict_of(small)
    out = path.with_name(path.stem + ".min.js") if args.out is None else Path(args.out)
    out.write_text(small, encoding="utf-8")
    print(f"{len(source.splitlines())} -> {len(small.splitlines())} lines: {out}\n{final.pretty()}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from . import inspector

    options = _gen_options(args)
    out = inspector.export(Path(args.out), per_generator=args.per_generator, per_template=args.per_template,
                           seed=args.seed, options=options, language_version=args.language_version, coverage=args.coverage)
    print(f"exported to {out}\n")
    inspector.print_table(out / "metrics.json", worst=args.worst)
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    from . import inspector
    inspector.print_table(Path(args.path) / "metrics.json", worst=args.worst)
    return 0


def cmd_fuzz(args: argparse.Namespace) -> int:
    from . import findings
    from .fuzz import fuzz
    from .harness.client import HarnessClient

    options = _gen_options(args)
    with HarnessClient(quiet=True, coverage=True) as client:
        if not client.info().get("coverage"):
            print("coverage agent not attached: the harness build must include jacocoagent.jar", file=sys.stderr)
            return 2
        known = findings.known_signatures(client) if not args.ignore_known else set()
        summary = fuzz(corpus_dir=Path(args.corpus), options=options, iterations=args.iterations, seconds=args.seconds,
                       steps=args.steps, p_new=args.p_new, seed=args.seed, known=known,
                       findings_dir=Path(args.findings_dir) if args.findings_dir else findings.DEFAULT_DIR,
                       shrink_budget=args.shrink_budget, compact_budget=args.compact_budget,
                       source_shrink_budget=args.source_shrink_budget,
                       language_version=args.language_version, client=client, quiet=args.quiet,
                       max_depth=args.max_lineage_depth)
    print(json.dumps({k: v for k, v in summary.items() if k != "signatures"}, indent=1))
    if summary["signatures"]:
        print("hard signatures seen:")
        for sig, n in sorted(summary["signatures"].items(), key=lambda kv: -kv[1]):
            print(f"  {n:4}  {sig}")
    return 0


def _add_run_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--modes", default="interpreted,compiled")
    p.add_argument("--language-version", type=int, default=200, help="Rhino language version: 200=ES6, 250=ECMASCRIPT")
    p.add_argument("--wall-ms", type=int, default=5000)
    p.add_argument("--strict-java-types", action="store_true",
                   help="treat Integer-vs-Double style Java representation differences as hard findings")
    p.add_argument("-v", "--verbose", action="store_true", help="print every case, not only findings")
    p.add_argument("--coverage", action="store_true",
                   help="attach JaCoCo to the harness and report which Rhino code was reached (per template bucket)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oxpecker", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run .js files or directories through the harness")
    r.add_argument("paths", nargs="+")
    r.add_argument("--strict", action="store_true", help="prepend \"use strict\"")
    _add_run_options(r)
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("corpus", help="replay a corpus (test262) through the harness")
    c.add_argument("corpus", choices=["test262", "rhino-jstests", "rhino-mozilla"])
    c.add_argument("--filter", help="path prefix under test262/test, e.g. language/statements/try")
    c.add_argument("--limit", type=int, help="stop after N test files")
    c.add_argument("--only-known", action="store_true", help="only files test262.properties tags per mode")
    c.add_argument("--known-divergences", action="store_true", help="list those files and exit")
    c.add_argument("--no-strict-variants", action="store_true", help="run only the sloppy variant")
    c.add_argument("-o", "--out", help="directory for results.jsonl and finding sources")
    _add_run_options(c)
    c.set_defaults(fn=cmd_corpus)
    c.set_defaults(language_version=250, strict=False)

    d = sub.add_parser("diff", help="run one file and show where the modes first differ, per field")
    d.add_argument("path")
    d.add_argument("--strict", action="store_true")
    d.add_argument("--context", type=int, default=60, help="characters of context around the first difference")
    _add_run_options(d)
    d.set_defaults(fn=cmd_diff)

    g = sub.add_parser("gen", help="generate programs into a directory (and optionally run them)")
    g.add_argument("-o", "--out", required=True, help="output directory")
    g.add_argument("-n", "--count", type=int, default=20)
    g.add_argument("--seed", type=int, default=0, help="seed of the first case; case k uses seed+k")
    g.add_argument("--template", help="one template only (see `oxpecker list --templates`)")
    g.add_argument("--family", action="append", help="restrict the pools to a family (repeatable)")
    g.add_argument("--generator", action="append", help="restrict the pools to a generator (repeatable)")
    g.add_argument("--without", action="append", help="disable a toggle: let, generators, for-of, destructuring, spread, continuations, with, eval")
    g.add_argument("--max-statements", type=int)
    g.add_argument("--max-depth", type=int)
    g.add_argument("--strict", action="store_true", help="generate strict-mode programs")
    g.add_argument("--evolve", type=int, default=0, metavar="K", help="write the end of a K-step mutation lineage per case")
    g.add_argument("--run", action="store_true", help="run the written cases through the harness")
    _add_run_options(g)
    g.set_defaults(fn=cmd_gen)

    fz = sub.add_parser("fuzz", help="the differential-testing loop: lineages, a coverage corpus, shrunk findings")
    fz.add_argument("-o", "--corpus", required=True, help="corpus directory (corpus.jsonl, coverage.exec); resumed if present")
    fz.add_argument("-n", "--iterations", type=int, help="stop after N runs")
    fz.add_argument("--seconds", type=float, help="stop after this many seconds")
    fz.add_argument("--steps", type=int, default=5, help="mutation steps per round at most: a fresh lineage or one extension (Fuzzilli: 5)")
    fz.add_argument("--max-lineage-depth", type=int, default=20, help="safety cap on a lineage's depth (mutation steps) over all extension rounds")
    fz.add_argument("--p-new", type=float, default=0.2, help="probability of a fresh lineage instead of extending a corpus entry")
    fz.add_argument("--seed", type=int)
    fz.add_argument("--shrink-budget", type=int, default=150, help="harness runs Hypothesis may spend shrinking one finding")
    fz.add_argument("--compact-budget", type=int, default=0, help="runs spent shrinking each new corpus entry while it still reaches its probes (0 = off)")
    fz.add_argument("--source-shrink-budget", type=int, default=300, help="runs spent on statement-level shrinking of each finding after Hypothesis (0 = off)")
    fz.add_argument("--findings-dir", help="where findings are recorded (default tests/findings)")
    fz.add_argument("--ignore-known", action="store_true", help="shrink and record findings whose signature a regression already has")
    fz.add_argument("--family", action="append")
    fz.add_argument("--generator", action="append")
    fz.add_argument("--without", action="append", help="disable a toggle")
    fz.add_argument("--max-statements", type=int)
    fz.add_argument("--max-depth", type=int)
    fz.add_argument("--strict", action="store_true")
    fz.add_argument("--language-version", type=int, default=200)
    fz.add_argument("-q", "--quiet", action="store_true")
    fz.set_defaults(fn=cmd_fuzz)

    sh = sub.add_parser("shrink", help="statement-level shrinking of a reproducer while its coarse verdict holds")
    sh.add_argument("path")
    sh.add_argument("-o", "--out", help="output file (default <stem>.min.js next to the input)")
    sh.add_argument("--budget", type=int, default=300, help="harness runs to spend")
    sh.add_argument("--strict", action="store_true")
    sh.add_argument("--language-version", type=int, default=200)
    sh.add_argument("--wall-ms", type=int, default=5000)
    sh.set_defaults(fn=cmd_shrink)

    ex = sub.add_parser("export", help="sample every generator and template, run them, write a run directory for the UI")
    ex.add_argument("-o", "--out", default="ui/data", help="run directories go under here (default ui/data)")
    ex.add_argument("--per-generator", type=int, default=20)
    ex.add_argument("--per-template", type=int, default=20)
    ex.add_argument("--seed", type=int, default=0)
    ex.add_argument("--worst", type=int, help="print only the N worst rows of the quality table")
    ex.add_argument("--coverage", action="store_true", help="attach JaCoCo and write coverage.json per focus")
    ex.add_argument("--family", action="append")
    ex.add_argument("--generator", action="append")
    ex.add_argument("--without", action="append", help="disable a toggle")
    ex.add_argument("--max-statements", type=int)
    ex.add_argument("--max-depth", type=int)
    ex.add_argument("--strict", action="store_true")
    ex.add_argument("--language-version", type=int, default=200)
    ex.set_defaults(fn=cmd_export)

    me = sub.add_parser("metrics", help="print the quality table of an exported run")
    me.add_argument("path", help="the run directory (ui/data/<run-id>)")
    me.add_argument("--worst", type=int)
    me.set_defaults(fn=cmd_metrics)

    ls = sub.add_parser("list", help="list the registered generators (or templates)")
    ls.add_argument("--family", action="append")
    ls.add_argument("--templates", action="store_true")
    ls.set_defaults(fn=cmd_list)

    pr = sub.add_parser("probe", help="probe Rhino's builtins/syntax and refresh the environment model")
    pr.add_argument("--language-version", type=int, default=250)
    pr.set_defaults(fn=cmd_probe)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
