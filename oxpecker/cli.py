"""Command-line interface.

  oxpecker run out/ tests/regressions/ run existing .js files through the harness
  oxpecker diff case.js                where the two modes' outcomes first differ, per field
  oxpecker corpus test262|rhino-jstests|rhino-mozilla [--filter PREFIX] [--limit N]   replay a corpus
  oxpecker probe                       refresh the Rhino capability table

The generation commands (gen, fuzz, export, list) were removed on 2026-10-25 with the old generation
layer; they return with the FuzzIL-based one (docs/proposal-hypothesis-ir.md).
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

    pr = sub.add_parser("probe", help="probe Rhino's builtins/syntax and refresh the environment model")
    pr.add_argument("--language-version", type=int, default=250)
    pr.set_defaults(fn=cmd_probe)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
