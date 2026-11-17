"""Parity of the Java canonicaliser (JavaCanon) with canon.js, and what it saves.

    python scripts/canon_parity.py [--generated N] [--seed S] [--keep N] [--no-timing]

1. One harness JVM with OXPECKER_CANON=both runs every tests/regressions, tests/seeds and
   tests/version-changes program and N generated ones (both modes). In `both` mode the harness
   renders each value with canon.js and with JavaCanon and, when text, javaTypes or soft differ,
   adds a "canon-mismatch: ..." line to the outcome's soft list. Every such case is reported with
   the first differing text. (A user getter or proxy trap with side effects runs twice in this
   mode: the second rendering sees the first one's effects; step 2 does not have that problem.)
2. The same programs run under OXPECKER_CANON=js and under =java, one JVM each, twice; the second
   pass is timed (programs per second), and the outcomes of the two are compared field by field
   (everything except timings and coverage), which catches a divergence in what user code observes.

Exit status 1 when anything differs.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from random import Random

from oxpecker.harness.client import HarnessClient, Outcome, RunResult

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
VOLATILE = {"ms", "newProbes", "newProbeIds", "requiredReached", "hitProbes"}


def fixed_cases() -> list[tuple[str, str]]:
    cases = []
    for sub in ("regressions", "seeds", "version-changes"):
        for p in sorted((TESTS / sub).rglob("*.js")):
            cases.append((str(p.relative_to(ROOT)), p.read_text(encoding="utf-8")))
    return cases


def generated_cases(n: int, seed: int) -> list[tuple[str, str]]:
    from oxpecker.fuzz import draw_lineage
    from oxpecker.il.options import GenOptions

    rng = Random(seed)
    cases: list[tuple[str, str]] = []
    options = GenOptions()
    while len(cases) < n:
        drawn = draw_lineage(options, 5, rng=rng)
        if drawn is None:
            continue
        cases.append((f"generated-{seed}-{len(cases)}", drawn[0].source))
    return cases


def run_all(canon: str, cases: list[tuple[str, str]]) -> tuple[list[RunResult], float]:
    """Runs the cases twice in a fresh JVM with OXPECKER_CANON=canon; returns the second pass and its time."""
    os.environ["OXPECKER_CANON"] = canon
    with HarnessClient(quiet=True, recycle_every=10 ** 9) as client:
        for name, src in cases:
            client.run(src, source_name=Path(name).name)
        t0 = time.perf_counter()
        results = [client.run(src, source_name=Path(name).name) for name, src in cases]
        elapsed = time.perf_counter() - t0
    return results, elapsed


def comparable(o: Outcome) -> dict:
    d = {k: v for k, v in o.to_json().items() if k not in VOLATILE}
    # Engine crashes carry Java stacks and generated class names (a per-JVM counter): compare the class only.
    if d.get("engineException"):
        d["engineException"] = d["engineException"].get("clazz")
    # Java identity hashes in messages ("org.mozilla.javascript.NativeArray@64cc7b09 is not a function").
    if d.get("thrown") and d["thrown"].get("message"):
        d["thrown"] = {**d["thrown"], "message": re.sub(r"@[0-9a-f]{3,8}\b", "@H", d["thrown"]["message"])}
    return d


def first_diff(a: str, b: str, width: int = 160) -> str:
    n = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    lo = max(0, n - 60)
    return f"at {n}: ...{a[lo:lo + width]!r} vs ...{b[lo:lo + width]!r}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generated", type=int, default=500, help="generated programs (default 500)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keep", type=int, default=20, help="how many differing cases to print in full")
    ap.add_argument("--no-timing", action="store_true", help="only the both-mode parity pass")
    args = ap.parse_args()

    cases = fixed_cases()
    n_fixed = len(cases)
    cases += generated_cases(args.generated, args.seed)
    print(f"{len(cases)} programs: {n_fixed} checked in, {len(cases) - n_fixed} generated (seed {args.seed})")

    # 1. both: the harness compares the two renderings of every value itself.
    os.environ["OXPECKER_CANON"] = "both"
    mismatching = 0
    shown = 0
    with HarnessClient(quiet=True, recycle_every=10 ** 9) as client:
        for name, src in cases:
            res = client.run(src, source_name=Path(name).name)
            lines = [(m, s) for m, o in res.results.items() for s in o.soft if s.startswith("canon-mismatch:")]
            if not lines:
                continue
            mismatching += 1
            if shown < args.keep:
                shown += 1
                m, s = lines[0]
                print(f"MISMATCH {name} [{m}] ({len(lines)} lines): {s[:600]}")
                # the first differing text, when it is a text mismatch
                if " text: js=" in s and " java=" in s:
                    js, java = s.split(" text: js=", 1)[1].split(" java=", 1)
                    print("   ", first_diff(js, java))
    print(f"both: {mismatching} of {len(cases)} programs with a canon-mismatch")
    failed = mismatching > 0
    if args.no_timing:
        return int(failed)

    # 2. js vs java: the same programs, timed, outcomes compared.
    js_results, js_s = run_all("js", cases)
    java_results, java_s = run_all("java", cases)
    differing = 0
    for (name, _), a, b in zip(cases, js_results, java_results):
        for m in a.results:
            ja, jb = comparable(a.results[m]), comparable(b.results[m])
            if ja != jb:
                differing += 1
                if differing <= args.keep:
                    key = next(k for k in ja if ja[k] != jb[k])
                    print(f"OUTCOME DIFFERS {name} [{m}] field {key}: {str(ja[key])[:200]!r} vs {str(jb[key])[:200]!r}")
    print(f"js vs java outcomes: {differing} differing (program, mode) pairs of {2 * len(cases)}")
    print(f"canon=js:   {len(cases) / js_s:7.1f} programs/s ({js_s:.1f} s for {len(cases)}, second pass)")
    print(f"canon=java: {len(cases) / java_s:7.1f} programs/s ({java_s:.1f} s for {len(cases)}, second pass)")
    return int(failed or differing > 0)


if __name__ == "__main__":
    sys.exit(main())
