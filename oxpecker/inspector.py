"""Inspector export (brief §12.2): samples programs per generator and per template, runs them
through the harness, and writes a run directory the UI reads:

    ui/data/<run-id>/catalogue.json   generators, templates (from the registry)
                     samples.jsonl    one program per line with per-mode outcomes and verdict
                     metrics.json     per generator/template quality aggregates
                     findings.json    hard/java-types/soft findings, deduplicated by signature

`oxpecker export -o ui/data --per-generator 20` also prints the quality table.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .cli import generate_programs
from .gen.builder import GenOptions
from .gen.registry import GENERATORS, TEMPLATES, catalogue, load_all
from .gen.strategies import Program
from .harness.client import HarnessClient
from .harness.compare import compare_run

STATUS_THROWN_EARLY = 2   # fewer probes than this before a throw counts as an early throw


def run_id() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = "nogit"
    return time.strftime("%Y-%m-%dT%H-%M") + "-" + (commit or "nogit")


def sample(opts: GenOptions, per_generator: int, per_template: int, seed: int) -> list[tuple[str, str, Program]]:
    """[(kind, focus, program)]: programs drawn with one generator or template emphasised."""
    load_all()
    out: list[tuple[str, str, Program]] = []
    all_toggles = frozenset({"let", "generators", "for-of", "destructuring", "spread", "continuations"})
    for name, g in GENERATORS.items():
        # The focus generator plus the value makers it may need, no swarm, plain template.
        o = GenOptions(**{**opts.__dict__, "generators": (name,), "templates": ("plain",), "swarm": False,
                          "toggles": opts.toggles | ({g.toggle} if g.toggle else frozenset())})
        for p in generate_programs(o, per_generator, seed):
            out.append(("generator", name, p))
    for name in TEMPLATES:
        o = GenOptions(**{**opts.__dict__, "templates": (name,), "toggles": opts.toggles | all_toggles})
        for p in generate_programs(o, per_template, seed):
            out.append(("template", name, p))
    return out


def export(out_root: Path, *, per_generator: int = 20, per_template: int = 20, seed: int = 0,
           opts: GenOptions = GenOptions(), language_version: int = 200, quiet: bool = False,
           coverage: bool = False) -> Path:
    rid = run_id()
    out = out_root / rid
    out.mkdir(parents=True, exist_ok=True)
    cat = catalogue()
    (out / "catalogue.json").write_text(json.dumps(cat, indent=1), encoding="utf-8")

    samples = sample(opts, per_generator, per_template, seed)
    metrics: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "samples": 0, "fired": 0, "syntaxBoth": 0, "earlyThrow": 0, "throwAny": 0, "timeout": 0, "resource": 0,
        "fallback": 0, "crash": 0, "hard": 0, "javaTypes": 0, "soft": 0, "equal": 0, "discard": 0,
        "probesBeforeThrow": [], "bytes": [], "statements": [], "exceptions": Counter(), "sources": set(),
    })
    usage: Counter[str] = Counter()
    findings: dict[str, dict[str, Any]] = {}
    t0 = time.time()
    with HarnessClient(quiet=True, coverage=coverage) as client, (out / "samples.jsonl").open("w", encoding="utf-8") as sf:
        for i, (kind, focus, p) in enumerate(samples):
            run = client.run(p.source, language_version=language_version, id=f"s-{i:06d}", seed=1,
                             coverage_bucket=focus if coverage else None)
            v = compare_run(run)
            rec = {"id": f"s-{i:06d}", "runId": rid, "kind": kind, "focus": focus, "source": p.source,
                   "sourcePlain": p.source_plain, "seed": seed, "template": p.template,
                   "enabledGenerators": p.enabled_generators, "generatorTrace": p.generator_spans,
                   "stats": p.stats, "results": run.to_json()["results"], "verdict": v.to_json()}
            sf.write(json.dumps(rec) + "\n")
            usage.update(p.generator_usage)
            m = metrics[focus]
            m["samples"] += 1
            if focus in p.generator_usage or kind == "template":
                m["fired"] += 1
            res = run.results
            statuses = {o.status for o in res.values()}
            if statuses == {"syntax"}:
                m["syntaxBoth"] += 1
            for o in res.values():
                if o.status == "throw":
                    m["throwAny"] += 1
                    m["exceptions"][(o.thrown or {}).get("name") or "?"] += 1
                    m["probesBeforeThrow"].append(len(o.trace))
                    if len(o.trace) < STATUS_THROWN_EARLY:
                        m["earlyThrow"] += 1
                    break
            for key in ("timeout", "resource", "fallback", "crash"):
                if key in statuses:
                    m[key] += 1
            m[{"hard": "hard", "java-types": "javaTypes", "soft": "soft", "equal": "equal", "discard": "discard",
               "skip": "discard"}[v.kind]] += 1
            m["bytes"].append(p.stats["bytes"])
            m["statements"].append(p.stats["statements"])
            m["sources"].add(p.source_plain)
            if v.is_finding:
                sig = v.signature()
                f = findings.setdefault(sig, {"signature": sig, "kind": v.kind, "count": 0, "focus": Counter(),
                                              "example": rec["id"], "verdict": v.to_json(), "source": p.source})
                f["count"] += 1
                f["focus"][focus] += 1
            if not quiet and (i + 1) % 200 == 0:
                print(f"... {i + 1}/{len(samples)} ({(i + 1) / (time.time() - t0):.1f}/s)", file=sys.stderr)
        if coverage:
            # coverage.json: {bucket "focus|mode": {packages, classes}} plus cumulative totals.
            (out / "coverage.json").write_text(json.dumps(client.coverage_report(), indent=1), encoding="utf-8")

    table = {}
    for focus, m in metrics.items():
        n = max(1, m["samples"])
        table[focus] = {
            "kind": "template" if focus in TEMPLATES else "generator",
            "samples": m["samples"], "fired": m["fired"], "usageTotal": usage.get(focus, 0),
            "syntaxBothRate": m["syntaxBoth"] / n, "earlyThrowRate": m["earlyThrow"] / n, "throwRate": m["throwAny"] / n,
            "meanProbesBeforeThrow": (sum(m["probesBeforeThrow"]) / len(m["probesBeforeThrow"])) if m["probesBeforeThrow"] else None,
            "timeouts": m["timeout"], "resource": m["resource"], "fallback": m["fallback"], "crash": m["crash"],
            "hard": m["hard"], "javaTypes": m["javaTypes"], "soft": m["soft"], "equal": m["equal"], "discard": m["discard"],
            "meanBytes": sum(m["bytes"]) / n, "meanStatements": sum(m["statements"]) / n,
            "duplicateRate": 1 - len(m["sources"]) / n,
            "exceptions": dict(m["exceptions"].most_common(6)),
        }
    (out / "metrics.json").write_text(json.dumps({"runId": rid, "languageVersion": language_version,
                                                  "samples": len(samples), "generatorUsage": dict(usage),
                                                  "perFocus": table}, indent=1), encoding="utf-8")
    fl = sorted(findings.values(), key=lambda f: (-f["count"], f["kind"]))
    for f in fl:
        f["focus"] = dict(f["focus"])
    (out / "findings.json").write_text(json.dumps(fl, indent=1), encoding="utf-8")
    return out


def print_table(metrics_path: Path, *, worst: int | None = None) -> None:
    m = json.loads(metrics_path.read_text(encoding="utf-8"))
    rows = sorted(m["perFocus"].items(),
                  key=lambda kv: (-(kv[1]["syntaxBothRate"] + kv[1]["earlyThrowRate"] + kv[1]["crash"]), kv[0]))
    if worst:
        rows = rows[:worst]
    print(f"{'focus':30} {'kind':9} {'n':>3} {'syntax':>7} {'early':>7} {'throw':>7} {'fallbk':>6} {'hard':>4} {'jtype':>5} "
          f"{'dup':>5} {'bytes':>6}  top exceptions")
    for name, r in rows:
        exc = ", ".join(f"{k}×{v}" for k, v in list(r["exceptions"].items())[:3])
        print(f"{name:30} {r['kind']:9} {r['samples']:>3} {r['syntaxBothRate']:>7.0%} {r['earlyThrowRate']:>7.0%} "
              f"{r['throwRate']:>7.0%} {r['fallback']:>6} {r['hard']:>4} {r['javaTypes']:>5} {r['duplicateRate']:>5.0%} "
              f"{r['meanBytes']:>6.0f}  {exc}")
    never = [g for g in GENERATORS if m["generatorUsage"].get(g, 0) == 0]
    if never:
        print(f"\nnever fired anywhere: {', '.join(never)}")
