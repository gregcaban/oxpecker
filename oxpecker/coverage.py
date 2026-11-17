"""How much of Rhino a fuzzing run reached, and whether it was still finding new code when it
stopped: the numbers a run reports so its length can be judged.

Two sources:
- **JaCoCo's analysis of the Rhino jar at the end of a run** (Coverage.report in the harness):
  lines, branches and methods covered, per package and for all of org.mozilla.javascript. The
  denominators are every class in the jar, loaded or not.
- **The coverage curve** (corpus/coverage.jsonl, a row every CURVE_EVERY_S seconds): the probes
  covered so far. Probes are JaCoCo's per-branch-edge flags, so the curve follows branch
  coverage. Its last quarter says whether the run had plateaued: a run still gaining fast would
  have covered more had it run longer.

Coverage is cumulative over a corpus: a resumed or seeded run starts from what its checkpoint
(coverage.exec) already covers.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

CURVE = "coverage.jsonl"
CURVE_EVERY_S = 30.0
# What running twice as long would add, as a share of the probes covered: below PLATEAU the run
# had plateaued, above GROWING it was still climbing.
PLATEAU, GROWING = 0.01, 0.05


def append_row(corpus_dir: Path, t0: float, runs: int, corpus: int, covered: int, findings: int,
               *, start: bool = False) -> dict[str, Any]:
    row = {"time": round(time.time(), 1), "t": round(time.time() - t0, 1), "runs": runs, "corpus": corpus,
           "covered": covered, "findings": findings, **({"start": True} if start else {})}
    with (corpus_dir / CURVE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def read_curve(corpus_dir: Path) -> list[dict[str, Any]]:
    """The rows of the latest run in the corpus (a resumed corpus appends one curve per run)."""
    path = corpus_dir / CURVE
    if not path.exists():
        return []
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    start = max((i for i, r in enumerate(rows) if r.get("start")), default=0)
    return rows[start:]


_PROGRESS = re.compile(r"^\s*(\d+) runs\s+([\d.]+)/s\s+corpus (\d+)\s+new probes (\d+)\s+findings (\d+)")


def curve_from_log(log: Path) -> list[dict[str, Any]]:
    """An approximate curve for runs from before coverage.jsonl, from fuzz.log's progress lines:
    time from runs / rate, probes as the new probes of corpus entries (shrinking runs and
    timed-out ones are missed), relative to the start."""
    rows: list[dict[str, Any]] = []
    for ln in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _PROGRESS.match(ln)
        if not m:
            continue
        runs, rate, corpus, new, found = int(m[1]), float(m[2]), int(m[3]), int(m[4]), int(m[5])
        if rows and runs < rows[-1]["runs"]:
            rows = []                          # a later run appended to the same log: keep the last
        rows.append({"t": round(runs / rate, 1) if rate else 0.0, "runs": runs, "corpus": corpus,
                     "covered": new, "findings": found})
    if rows:
        rows.insert(0, {"t": 0.0, "runs": 0, "corpus": 0, "covered": 0, "findings": 0})
    return rows


def _at(rows: list[dict[str, Any]], t: float) -> int:
    return max((r["covered"] for r in rows if r["t"] <= t), default=rows[0]["covered"])


def saturation(rows: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    """Whether the run was long enough. Fuzzing coverage grows about logarithmically in time, so
    the curve is fitted as covered = a + b ln t (past its first 5 %, the start-up burst) and the
    answer is what doubling the run would add, b ln 2, as a share of the probes covered (`total`
    when the rows are relative, as a curve rebuilt from fuzz.log is)."""
    if len(rows) < 4 or rows[-1]["t"] < 120:
        return {"verdict": "too short to tell", "points": len(rows)}
    end_t, end, start = rows[-1]["t"], rows[-1]["covered"], rows[0]["covered"]
    covered = total or end
    pts = [(math.log(r["t"]), r["covered"]) for r in rows if r["t"] >= 0.05 * end_t and r["t"] > 0]
    if len(pts) < 3:
        return {"verdict": "too short to tell", "points": len(rows)}
    mx, my = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    b = sum((x - mx) * (y - my) for x, y in pts) / sxx if sxx else 0.0
    doubling = max(0.0, b * math.log(2))
    share = doubling / covered if covered else 0.0
    if share < PLATEAU:
        verdict = "plateaued: a longer run with these generators is unlikely to reach much new Rhino code"
    elif share < GROWING:
        verdict = "slowing down: running twice as long would add a little"
    else:
        verdict = "still growing: a longer run would likely cover more"
    q = 0.25 * end_t / 60
    return {"verdict": verdict, "points": len(rows), "seconds": end_t, "start": start, "end": end,
            "covered": covered, "gained": end - start,
            "per_minute_first_quarter": round((_at(rows, 0.25 * end_t) - start) / q, 1),
            "per_minute_last_quarter": round((end - _at(rows, 0.75 * end_t)) / q, 1),
            "doubling_adds": round(doubling), "doubling_share": round(share, 4)}


# Rhino packages no program the harness runs can reach: CommonJS module loading (no `require`),
# serialization of scopes, the debugger API (no debugger mode). Left out of the `reachable` figure.
UNREACHABLE = ("org.mozilla.javascript.commonjs", "org.mozilla.javascript.serialize", "org.mozilla.javascript.debug")


def jar_coverage(report: dict[str, Any]) -> dict[str, Any]:
    """JaCoCo's counters for the Rhino jar from Coverage.report's cumulative bucket: overall, for
    the packages the harness can reach, and per package, each [covered, total]."""
    bucket = report.get("buckets", {}).get("*cumulative*", {}) or {}
    packages = bucket.get("packages", {})
    out: dict[str, Any] = {"lines": [0, 0], "branches": [0, 0], "methods": [0, 0],
                           "reachable": {"lines": [0, 0], "branches": [0, 0], "methods": [0, 0]}, "packages": {},
                           # the classes with the most lines never run, nested classes folded in
                           "missed": sorted(([cls, l_cov, l_tot, b_cov, b_tot] for cls, (l_cov, l_tot, b_cov, b_tot)
                                             in bucket.get("classLines", {}).items() if l_tot > l_cov),
                                            key=lambda c: c[1] - c[2])[:40]}
    for pkg, (m_cov, m_tot, b_cov, b_tot, l_cov, l_tot) in sorted(packages.items()):
        out["packages"][pkg] = {"lines": [l_cov, l_tot], "branches": [b_cov, b_tot], "methods": [m_cov, m_tot]}
        sums = [out] + ([] if pkg.startswith(UNREACHABLE) else [out["reachable"]])
        for key, c, t in (("lines", l_cov, l_tot), ("branches", b_cov, b_tot), ("methods", m_cov, m_tot)):
            for s in sums:
                s[key][0] += c
                s[key][1] += t
    return out


def summarize(report: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {**jar_coverage(report), "probes": report.get("coveredProbes"), "curve": saturation(rows)}


def _pct(pair: list[int]) -> str:
    c, t = pair
    return f"{100 * c / t:5.1f}% ({c}/{t})" if t else "  n/a"


def format_summary(cov: dict[str, Any], rows: list[dict[str, Any]] | None = None, *, packages: int = 8,
                   approximate: bool = False, missed: int = 0) -> str:
    lines = []
    if cov.get("lines", [0, 0])[1]:
        lines.append(f"Rhino coverage: lines {_pct(cov['lines'])}, branches {_pct(cov['branches'])}, "
                     f"methods {_pct(cov['methods'])}")
        if cov.get("reachable"):
            r = cov["reachable"]
            lines.append(f"  without {', '.join(u.rsplit('.', 1)[1] for u in UNREACHABLE)} (unreachable from the harness): "
                         f"lines {_pct(r['lines'])}, branches {_pct(r['branches'])}")
        ranked = sorted(cov["packages"].items(), key=lambda kv: -kv[1]["lines"][1])[:packages]
        for pkg, p in ranked:
            lines.append(f"  {pkg:42} lines {_pct(p['lines'])}  branches {_pct(p['branches'])}")
    if missed and cov.get("missed"):
        lines.append(f"  most lines never run:")
        for cls, l_cov, l_tot, b_cov, b_tot in cov["missed"][:missed]:
            lines.append(f"    {l_tot - l_cov:5} of {l_tot:5} lines  {cls.replace('org.mozilla.javascript.', '')}")
    s = cov.get("curve") or {}
    if s.get("verdict"):
        if "seconds" in s:
            lines.append(f"coverage curve{' (approximate, from fuzz.log)' if approximate else ''}: "
                         f"{s['gained']} probes gained in {s['seconds'] / 60:.0f} min, {s['covered']} covered; "
                         f"{s['per_minute_first_quarter']}/min in the first quarter, {s['per_minute_last_quarter']}/min in the last; "
                         f"running twice as long would add about {s['doubling_adds']} ({100 * s['doubling_share']:.1f}%)")
        lines.append(f"  -> {s['verdict']}")
    if rows:
        lines.append("  min  runs   corpus  probes  findings")
        step = max(1, len(rows) // 10)
        for r in rows[::step] + ([rows[-1]] if (len(rows) - 1) % step else []):
            lines.append(f"  {r['t'] / 60:4.0f}  {r['runs']:6}  {r['corpus']:6}  {r['covered']:6}  {r['findings']:6}")
    return "\n".join(lines)
