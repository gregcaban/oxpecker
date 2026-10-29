"""Inspector export: samples every generator and template, runs the samples through the harness
and writes a run directory the UI (`ui/`) reads:

    ui/data/<run-id>/catalogue.json   generators and templates from the registry
                     samples.jsonl    one program per line with per-mode outcomes and verdict
                     metrics.json     per generator/template quality aggregates
                     findings.json    hard/java-types/soft findings, deduplicated by signature
                     coverage.json    with --coverage: per-focus Rhino coverage

A generator sample is `run_generator(name, seed)`: a small prefix of values, then the generator
once, lifted with probes; a template sample is `generate(seed, template=name)`. The schema is
`ui/lib/types.ts` (the UI tolerates missing fields).

    oxpecker export -o ui/data --per-generator 20     also prints the quality table
    oxpecker metrics ui/data/<run-id>
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import oxpecker.il.templates  # noqa: F401  (registration)
from .harness.client import HarnessClient
from .harness.compare import compare_run
from .il import ops
from .il.lift import LiftOptions, lift
from .il.options import GenOptions
from .il.program import Instruction
from .il.strategies import REGISTRY, TEMPLATES, generate, run_generator

STATUS_THROWN_EARLY = 2   # fewer probes than this before a throw counts as an early throw


def run_id() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = "nogit"
    return time.strftime("%Y-%m-%dT%H-%M") + "-" + (commit or "nogit")


# ------------------------------------------------------------------------------- catalogue
def _defining_function(fn, depth: int = 0):
    """The function written in the repo behind an `st.composite` wrapper: the wrappers Hypothesis
    builds have no source file (`<string>`), the original sits in their closures."""
    if depth > 4 or not inspect.isfunction(fn):
        return None
    try:
        path = inspect.getsourcefile(fn)
    except (OSError, TypeError):
        path = None
    if path and path.endswith(".py") and "hypothesis" not in path:
        return fn
    for cell in fn.__closure__ or ():
        try:
            found = _defining_function(cell.cell_contents, depth + 1)
        except ValueError:
            continue
        if found is not None:
            return found
    wrapped = getattr(fn, "__wrapped__", None)
    return _defining_function(wrapped, depth + 1) if wrapped is not None else None


def _source_of(f) -> tuple[str, str]:
    """(path:line, source text) of a generator or template function, best effort."""
    inner = _defining_function(getattr(f, "inner", None) or f) or f
    try:
        path = inspect.getsourcefile(inner) or ""
        lines, start = inspect.getsourcelines(inner)
        root = Path(__file__).resolve().parents[1]
        rel = str(Path(path).resolve().relative_to(root)) if path else ""
        return f"{rel}:{start}", "".join(lines)
    except (OSError, TypeError, ValueError):
        return "", ""


def catalogue() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, spec in REGISTRY.items():
        path, snippet = _source_of(spec.strategy)
        out.append({"id": name, "kind": "generator", "family": spec.family, "weight": spec.weight,
                    "toggle": spec.toggle, "sloppyOnly": spec.sloppy_only,
                    "inputTypes": [str(t) for t in spec.inputs] + [f"required {t}" for t in spec.required],
                    "outputType": str(spec.produces) if spec.produces is not None else "",
                    "description": spec.doc, "context": spec.context.name, "block": spec.is_block,
                    "sourcePath": path, "sourceSnippet": snippet, "fuzzilli": spec.source})
    for name, t in TEMPLATES.items():
        path, snippet = _source_of(t.strategy)
        out.append({"id": name, "kind": "template", "family": "templates", "weight": t.weight, "toggle": None,
                    "sloppyOnly": False, "inputTypes": [], "outputType": "", "description": t.doc,
                    "sourcePath": path, "sourceSnippet": snippet})
    return out


# ------------------------------------------------------------------------------- samples
def generator_trace(source: str) -> list[dict[str, Any]]:
    """Inclusive 0-based line spans per generator, from the lifter's `// [name]` comments: a span
    runs from its comment to the line before the next comment or the end."""
    marks = [(i, ln.strip()[4:-1]) for i, ln in enumerate(source.splitlines()) if ln.strip().startswith("// [") and ln.strip().endswith("]")]
    total = len(source.splitlines())
    spans = []
    for k, (line, gen) in enumerate(marks):
        end = (marks[k + 1][0] - 1) if k + 1 < len(marks) else total - 1
        spans.append({"gen": gen, "lines": [line, max(line, end)]})
    return spans


def stats_of(code: tuple[Instruction, ...], source: str) -> dict[str, Any]:
    depth = max_depth = 0
    top = 0
    for instr in code:
        op = instr.op
        if op.is_block_end and not op.is_block_start:
            depth -= 1
        if depth == 0 and not isinstance(op, ops.Probe) and not (op.is_block_end and not op.is_block_start):
            top += 1
        if op.is_block_start and not op.is_block_end:
            depth += 1
            max_depth = max(max_depth, depth)
        elif op.is_block_start and op.is_block_end:      # a group middle (else, case, catch): same depth
            pass
    variables = {v for instr in code for v in instr.inouts}
    return {"statements": sum(1 for i in code if not isinstance(i.op, ops.Probe)), "topLevelStatements": top,
            "vars": len(variables), "maxDepth": max_depth, "probes": sum(1 for i in code if isinstance(i.op, ops.Probe)),
            "bytes": len(source.encode("utf-8"))}


def _lifted(code, options: GenOptions) -> tuple[str, str]:
    probed = lift(code, LiftOptions(probes=True, comments=True, strict=options.strict, probe_width=options.probe_width))
    plain = lift(code, LiftOptions(probes=False, comments=False, strict=options.strict))
    return probed, plain


def sample(options: GenOptions, per_generator: int, per_template: int, seed: int) -> list[dict[str, Any]]:
    """[{kind, focus, seed, template, code, source, sourcePlain, enabledGenerators, fired}]."""
    out: list[dict[str, Any]] = []
    enabled = sorted(n for n, s in REGISTRY.items() if s.toggle is None or s.toggle in options.toggles)
    opts = {k: getattr(options, k) for k in ("max_statements", "min_statements", "max_depth", "strict", "toggles")}
    for name, spec in REGISTRY.items():
        for k in range(per_generator):
            try:
                snippet = run_generator(name, seed + k, prefix_size=3, **opts)
            except Exception as e:                       # not applicable at top level, or a generator bug
                out.append({"kind": "generator", "focus": name, "seed": seed + k, "template": "snippet", "code": (),
                            "source": "", "sourcePlain": "", "enabledGenerators": [name], "fired": False,
                            "error": f"{type(e).__name__}: {e}"})
                continue
            probed, plain = _lifted(snippet.code, options)
            fired = any(i.gen == name for i in snippet.emitted)
            out.append({"kind": "generator", "focus": name, "seed": seed + k, "template": "snippet", "code": snippet.code,
                        "source": probed, "sourcePlain": plain, "enabledGenerators": [name], "fired": fired})
    for name in TEMPLATES:
        for k in range(per_template):
            gen = generate(seed + k, template=name, **opts)
            out.append({"kind": "template", "focus": name, "seed": seed + k, "template": name, "code": gen.code,
                        "source": gen.source, "sourcePlain": gen.plain_source, "enabledGenerators": enabled, "fired": True})
    return out


# ------------------------------------------------------------------------------- export
def export(out_root: Path, *, per_generator: int = 20, per_template: int = 20, seed: int = 0,
           options: GenOptions = GenOptions(), language_version: int = 200, quiet: bool = False,
           coverage: bool = False, client: HarnessClient | None = None) -> Path:
    rid = run_id()
    out = out_root / rid
    out.mkdir(parents=True, exist_ok=True)
    (out / "catalogue.json").write_text(json.dumps(catalogue(), indent=1), encoding="utf-8")

    samples = sample(options, per_generator, per_template, seed)
    metrics: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "samples": 0, "fired": 0, "syntaxBoth": 0, "earlyThrow": 0, "throwAny": 0, "timeout": 0, "resource": 0,
        "fallback": 0, "crash": 0, "hard": 0, "javaTypes": 0, "soft": 0, "equal": 0, "discard": 0,
        "probesBeforeThrow": [], "bytes": [], "statements": [], "exceptions": Counter(), "sources": set(),
    })
    usage: Counter[str] = Counter()
    findings: dict[str, dict[str, Any]] = {}
    t0 = time.time()
    own = client is None
    if own:
        client = HarnessClient(quiet=True, coverage=coverage)
        client.start()
    try:
        with (out / "samples.jsonl").open("w", encoding="utf-8") as sf:
            for i, s in enumerate(samples):
                focus = s["focus"]
                m = metrics[focus]
                m["samples"] += 1
                if not s["source"]:
                    continue
                run = client.run(s["source"], language_version=language_version, id=f"s-{i:06d}", seed=1,
                                 coverage_bucket=focus if coverage else None)
                v = compare_run(run)
                stats = stats_of(s["code"], s["source"])
                rec = {"id": f"s-{i:06d}", "runId": rid, "kind": s["kind"], "focus": focus, "source": s["source"],
                       "sourcePlain": s["sourcePlain"], "seed": s["seed"], "template": s["template"],
                       "enabledGenerators": s["enabledGenerators"], "generatorTrace": generator_trace(s["source"]),
                       "stats": stats, "results": run.to_json()["results"], "verdict": v.to_json()}
                sf.write(json.dumps(rec) + "\n")
                usage.update(i.gen for i in s["code"] if i.gen)
                if s["fired"]:
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
                m["bytes"].append(stats["bytes"])
                m["statements"].append(stats["statements"])
                m["sources"].add(s["sourcePlain"])
                if v.is_finding:
                    sig = v.signature()
                    f = findings.setdefault(sig, {"signature": sig, "kind": v.kind, "count": 0, "focus": Counter(),
                                                  "example": rec["id"], "verdict": v.to_json(), "source": s["source"]})
                    f["count"] += 1
                    f["focus"][focus] += 1
                if not quiet and (i + 1) % 200 == 0:
                    print(f"... {i + 1}/{len(samples)} ({(i + 1) / (time.time() - t0):.1f}/s)", file=sys.stderr)
        if coverage:
            (out / "coverage.json").write_text(json.dumps(client.coverage_report(), indent=1), encoding="utf-8")
    finally:
        if own:
            client.stop()

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
            "meanBytes": (sum(m["bytes"]) / len(m["bytes"])) if m["bytes"] else 0.0,
            "meanStatements": (sum(m["statements"]) / len(m["statements"])) if m["statements"] else 0.0,
            "duplicateRate": (1 - len(m["sources"]) / len(m["bytes"])) if m["bytes"] else 0.0,
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
    print(f"{'focus':30} {'kind':9} {'n':>3} {'fired':>5} {'syntax':>7} {'early':>7} {'throw':>7} {'hard':>4} {'jtype':>5} "
          f"{'dup':>5} {'bytes':>6}  top exceptions")
    for name, r in rows:
        exc = ", ".join(f"{k}x{v}" for k, v in list(r["exceptions"].items())[:3])
        print(f"{name:30} {r['kind']:9} {r['samples']:>3} {r['fired']:>5} {r['syntaxBothRate']:>7.0%} {r['earlyThrowRate']:>7.0%} "
              f"{r['throwRate']:>7.0%} {r['hard']:>4} {r['javaTypes']:>5} {r['duplicateRate']:>5.0%} "
              f"{r['meanBytes']:>6.0f}  {exc}")
    never = [g for g in REGISTRY if m["generatorUsage"].get(g, 0) == 0]
    if never:
        print(f"\nnever fired anywhere: {', '.join(never)}")
