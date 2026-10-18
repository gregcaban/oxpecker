"""Command-line interface.

  oxpecker gen -n 50 -o out/           write 50 generated programs (case-0001.js ...) for eyeballing
  oxpecker gen -n 50 -o out/ --run     ... and run them through the harness, writing verdicts
  oxpecker run out/ tests/regressions/ run existing .js files through the harness
  oxpecker list                        generators and templates
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, Phase, given, seed as hyp_seed, settings

from .gen.builder import GenOptions
from .gen.registry import GENERATORS, TEMPLATES, available_generators, catalogue, load_all
from .gen.strategies import Program, programs


# ----------------------------------------------------------------------------- generation
def generate_programs(opts: GenOptions, count: int, seed: int) -> list[Program]:
    """Draws `count` programs with Hypothesis (generate phase only, deterministic for a seed)."""
    out: list[Program] = []
    attempts = 0
    while len(out) < count and attempts < 5:
        attempts += 1
        want = count - len(out)

        @hyp_seed(seed + attempts - 1)
        @settings(max_examples=want, phases=[Phase.generate], database=None, deadline=None,
                  suppress_health_check=list(HealthCheck), derandomize=False)
        @given(programs(opts))
        def collect(p: Program) -> None:
            if len(out) < count:
                out.append(p)

        collect()
    return out


def options_from_args(args: argparse.Namespace) -> GenOptions:
    return GenOptions(
        min_statements=args.min_statements, max_statements=args.max_statements,
        probes=not args.no_probes, probe_every=args.probe_every, comments=not args.no_comments,
        strict=args.strict, toggles=frozenset(args.toggle or ()),
        generators=tuple(args.generator) if args.generator else None,
        templates=tuple(args.template) if args.template else None,
        swarm=not args.no_swarm, max_depth=args.max_depth,
    )


def cmd_gen(args: argparse.Namespace) -> int:
    load_all()
    opts = options_from_args(args)
    bad = [g for g in (args.generator or []) if g not in GENERATORS]
    bad += [t for t in (args.template or []) if t not in TEMPLATES]
    if bad:
        print(f"unknown generator/template: {', '.join(bad)} (see `oxpecker list`)", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    progs = generate_programs(opts, args.count, args.seed)
    index: list[dict[str, Any]] = []
    for i, p in enumerate(progs, 1):
        case_id = f"case-{i:04d}"
        header = f"// oxpecker {case_id}  seed={args.seed}  (plain reproducer: {case_id}.plain.js)\n"
        (out_dir / f"{case_id}.js").write_text(header + p.source, encoding="utf-8")
        (out_dir / f"{case_id}.plain.js").write_text(p.source_plain, encoding="utf-8")
        meta = {"id": case_id, "seed": args.seed, **p.meta()}
        for span in meta["generatorTrace"]:
            span["lines"] = [span["lines"][0] + 1, span["lines"][1] + 1]   # the header line above
        (out_dir / f"{case_id}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        index.append({"id": case_id, "template": p.template, "stats": p.stats,
                      "generators": sorted(p.generator_usage)})
    (out_dir / "catalogue.json").write_text(json.dumps(catalogue(), indent=2), encoding="utf-8")
    elapsed = time.time() - t0
    print(f"wrote {len(progs)} programs to {out_dir}/ in {elapsed:.1f}s "
          f"({_usage_summary(progs)})")

    if args.run:
        results = run_paths([out_dir], args, index_by_id={e['id']: e for e in index})
        for e in index:
            e["verdict"] = results.get(e["id"], {}).get("verdict")
    (out_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return 0


def _usage_summary(progs: list[Program]) -> str:
    usage: Counter[str] = Counter()
    for p in progs:
        usage.update(p.generator_usage)
    never = [g for g in GENERATORS if g not in usage]
    s = f"{len(usage)} distinct generators used"
    if never:
        s += f"; never fired: {', '.join(never[:8])}{'…' if len(never) > 8 else ''}"
    return s


# ----------------------------------------------------------------------------- running
def _js_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.glob("*.js") if not f.name.endswith(".plain.js")))
        elif p.suffix == ".js":
            files.append(p)
    return files


def run_paths(paths: list[Path], args: argparse.Namespace, *, index_by_id: dict[str, Any] | None = None) -> dict[str, Any]:
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
    with HarnessClient(quiet=True) as client:
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


def cmd_run(args: argparse.Namespace) -> int:
    results = run_paths([Path(p) for p in args.paths], args)
    hard = sum(1 for r in results.values() if r["verdict"]["kind"] == "hard")
    return 1 if hard else 0


# ----------------------------------------------------------------------------- listing
def cmd_list(args: argparse.Namespace) -> int:
    load_all()
    opts = GenOptions(toggles=frozenset(args.toggle or ()), strict=args.strict)
    enabled = {g.name for g in available_generators(opts)}
    print(f"{'generator':28} {'family':18} {'w':>2} {'toggle':14} {'inputs':22} description")
    for g in GENERATORS.values():
        flag = " " if g.name in enabled else "-"
        print(f"{flag}{g.name:27} {g.family:18} {g.weight:>2} {(g.toggle or ('sloppy' if g.sloppy_only else '')):14} "
              f"{','.join(t.value for t in g.inputs):22} {g.description.splitlines()[0] if g.description else ''}")
    print("\ntemplates:")
    for t in TEMPLATES.values():
        print(f" {t.name:27} w={t.weight}  {t.description.splitlines()[0] if t.description else ''}")
    print(f"\n{len(enabled)}/{len(GENERATORS)} generators enabled with toggles={sorted(opts.toggles)} strict={opts.strict}"
          " ('-' = disabled)")
    return 0


# ----------------------------------------------------------------------------- main
def _add_gen_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--min-statements", type=int, default=3)
    p.add_argument("--max-statements", type=int, default=12)
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--probe-every", type=int, default=1, help="probe after every N top-level statements (0=only end)")
    p.add_argument("--no-probes", action="store_true")
    p.add_argument("--no-comments", action="store_true", help="no `// [generator]` markers")
    p.add_argument("--no-swarm", action="store_true", help="enable all generators in every program")
    p.add_argument("--toggle", action="append", metavar="NAME",
                   help="enable partial-support features: let, generators, for-of, destructuring, spread")
    p.add_argument("--generator", action="append", metavar="NAME", help="only these generators (repeatable)")
    p.add_argument("--template", action="append", metavar="NAME", help="only these templates (repeatable)")


def _add_run_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--modes", default="interpreted,compiled")
    p.add_argument("--language-version", type=int, default=200, help="Rhino language version: 200=ES6, 250=ECMASCRIPT")
    p.add_argument("--wall-ms", type=int, default=5000)
    p.add_argument("--strict-java-types", action="store_true",
                   help="treat Integer-vs-Double style Java representation differences as hard findings")
    p.add_argument("-v", "--verbose", action="store_true", help="print every case, not only findings")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oxpecker", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="generate programs into a directory")
    g.add_argument("-n", "--count", type=int, default=20)
    g.add_argument("-o", "--out", required=True)
    g.add_argument("--strict", action="store_true", help="generate strict-mode programs (no with/eval)")
    g.add_argument("--run", action="store_true", help="also run them through the harness")
    _add_gen_options(g)
    _add_run_options(g)
    g.set_defaults(fn=cmd_gen)

    r = sub.add_parser("run", help="run .js files or directories through the harness")
    r.add_argument("paths", nargs="+")
    r.add_argument("--strict", action="store_true", help="prepend \"use strict\"")
    _add_run_options(r)
    r.set_defaults(fn=cmd_run)

    ls = sub.add_parser("list", help="list generators and templates")
    ls.add_argument("--toggle", action="append")
    ls.add_argument("--strict", action="store_true")
    ls.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
