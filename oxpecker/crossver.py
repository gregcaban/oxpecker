"""Rhino's own last release as the oracle: every program runs on two targets, the candidate (the
process's, OXPECKER_RHINO, usually master) and a baseline (OXPECKER_RHINO_BASELINE, usually the
last release), each in both modes. A mode whose outcome changed between the two is a potential
regression, whether or not the candidate's two modes agree with each other; the candidate's own
mode comparison still runs, so a bug both versions share is reported as before.

A change is a fix rather than a regression when the baseline's modes disagreed and the
candidate's changed mode now does what the baseline's other mode did. The generators draw from
the builtins and syntax both targets have (environment.intersect), so a builtin only one of them
has is not a difference.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

from . import targets
from .harness.client import HarnessClient, Outcome, RunResult
from .harness.compare import DISCARD, EQUAL, HARD, JAVA_TYPES, SKIP, SOFT, Verdict, compare, compare_run

BASELINE_ENV = "OXPECKER_RHINO_BASELINE"
FIXED = "fixed"          # the candidate fixed a mode difference the baseline has: counted, not recorded
_ORDER = {HARD: 0, JAVA_TYPES: 1, SOFT: 2, DISCARD: 3, SKIP: 4, FIXED: 5, EQUAL: 6}
_COVERAGE_ARGS = ("coverage_bucket", "coverage_new_ids", "coverage_require", "coverage_hits")


def baseline() -> str | None:
    """The target this process compares against, or None: OXPECKER_RHINO_BASELINE."""
    return os.environ.get(BASELINE_ENV) or None


@dataclass
class CrossRun:
    """One program's runs on both targets. `results` holds the candidate's outcomes under their
    mode names and the baseline's as BASELINE/MODE, which is what findings and reproducers print."""
    base_target: str
    cand_target: str
    base: RunResult
    cand: RunResult

    @property
    def id(self) -> str:
        return self.cand.id

    @property
    def restarted(self) -> bool:
        return self.base.restarted or self.cand.restarted

    @property
    def results(self) -> dict[str, Any]:
        return {**self.cand.results, **{f"{self.base_target}/{m}": o for m, o in self.base.results.items()}}

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "results": {m: o.to_json() for m, o in self.results.items()}, "restarted": self.restarted}

    def expectations(self) -> dict[str, str]:
        """The verdict each target gives on its own (two modes): a reproducer's expectation headers."""
        return {self.base_target: compare_run(self.base).kind, self.cand_target: compare_run(self.cand).kind}


# Differences between Rhino versions that are deliberate or implementation-defined, rewritten the
# same way on both sides before a cross-version comparison (the mode comparison sees them as is).
# Each was a cross-version "finding" of the first campaign, 2026-11-11.
_NAMESPACES = ("Math", "JSON", "Reflect", "Atomics", "Intl")
VERSION_CANON: list[tuple[str, re.Pattern[str], str]] = [
    # function source text: decompiled differently by each version (master prints a method's
    # name, `m() {}`, where 1.9.2 printed `() {}`, as the spec wants); the snapshot's src= field
    ("function source text", re.compile(r'src="(?:[^"\\]|\\.)*"'), "src=…"),
    # 1.9.2 ends a native function's source with a newline; master, as the spec's NativeFunction, does not
    # (a probe's string argument holds the newlines escaped, a completion value raw)
    ("native function trailing newline", re.compile(r"\[native code\](\n|\\n)\}(\n|\\n)"), r"[native code]\1}"),
    # Number.MIN_VALUE: 1.9.2 prints 4.9e-324; master, as Number::toString's shortest digits, 5e-324
    ("Number.MIN_VALUE digits", re.compile(r"(?<![\d.])4\.9e-324"), "5e-324"),
]


def _sort_namespace_members(s: str) -> str:
    """The own properties of Math, JSON, ... are created in an order the spec leaves to the
    implementation, which master changed (`toSource` moved): sort them inside their snapshot."""
    out, i = [], 0
    for m in re.finditer(r"@\d+(?:%s)<[^>]*>\{" % "|".join(_NAMESPACES), s):
        if m.start() < i:
            continue                                         # nested in one already sorted
        j, depth, parts, start, quoted = m.end(), 1, [], m.end(), False
        while j < len(s) and depth:
            ch = s[j]
            if quoted:
                if ch == "\\":
                    j += 1
                elif ch == '"':
                    quoted = False
            elif ch == '"':
                quoted = True
            elif ch in "{[(":
                depth += 1
            elif ch in "}])":
                depth -= 1
            elif ch == ";" and depth == 1:
                parts.append(s[start:j])
                start = j + 1
            j += 1
        if depth:
            break                                            # truncated snapshot: leave the rest
        parts.append(s[start:j - 1])
        out += [s[i:m.end()], ";".join(sorted(p for p in parts if p)), "}"]
        i = j
    return "".join(out) + s[i:]


def version_canon(o: Outcome) -> Outcome:
    def c(s: str | None) -> str | None:
        if s is None:
            return None
        for _, pattern, repl in VERSION_CANON:
            s = pattern.sub(repl, s)
        return _sort_namespace_members(s)
    thrown = dict(o.thrown, value=c(o.thrown.get("value"))) if o.thrown else o.thrown
    return replace(o, value=c(o.value), trace=[c(t) for t in o.trace], output=[c(t) for t in o.output], thrown=thrown)


def cross_verdict(run: CrossRun) -> Verdict:
    """The worst of: a mode that changed between the targets (hard, or java-types/soft with a
    prefixed reason), and the candidate's own mode comparison. FIXED when every hard change is
    a fix of a baseline mode difference and nothing else is wrong."""
    b, c = run.base_target, run.cand_target
    regressions: list[Verdict] = []
    lesser: list[Verdict] = []
    fixed = False
    base_modes_agree = compare_run(run.base).kind != HARD
    for mode, co in run.cand.results.items():
        bo = run.base.results.get(mode)
        if bo is None:
            continue
        v = compare(replace(version_canon(bo), mode=b), replace(version_canon(co), mode=c))
        prefix = f"{b}->{c} {mode}: "
        v = Verdict(v.kind, [prefix + r for r in v.reasons], [prefix + j for j in v.java_type_diff], v.first_trace_diff)
        if v.kind != HARD:
            lesser.append(v)
            continue
        others = [o for m, o in run.base.results.items() if m != mode]
        if not base_modes_agree and any(compare(version_canon(o), version_canon(co)).kind != HARD for o in others):
            fixed = True                      # now does what the baseline's other mode did
        else:
            regressions.append(v)
    if regressions:
        worst = regressions[0]
        for v in regressions[1:]:
            worst.reasons.extend(r for r in v.reasons if r not in worst.reasons)
        return worst
    own = compare_run(run.cand)
    candidates = [own] + lesser
    if fixed:
        candidates.append(Verdict(FIXED, [f"fixed on {c}: " + "; ".join(compare_run(run.base).reasons[:1])]))
    return min(candidates, key=lambda v: _ORDER[v.kind])     # ties: the candidate's own verdict, listed first


class CrossClient:
    """A HarnessClient look-alike over two JVMs: `run` runs the candidate (which keeps the
    coverage) and the baseline side by side and returns a CrossRun; everything else, the
    coverage calls included, goes to the candidate."""

    def __init__(self, candidate: HarnessClient, base_target: str):
        self.candidate = candidate
        self.base_target = base_target
        self.cand_target = candidate.target or targets.current()
        self.baseline = HarnessClient(quiet=True, target=base_target)
        self._pool = ThreadPoolExecutor(1)

    def __enter__(self) -> "CrossClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def start(self) -> None:
        self.baseline.start()

    def stop(self) -> None:
        self.baseline.stop()
        self._pool.shutdown()

    @property
    def coverage_checkpoint(self) -> str | None:
        return self.candidate.coverage_checkpoint

    @coverage_checkpoint.setter
    def coverage_checkpoint(self, value: str | None) -> None:
        self.candidate.coverage_checkpoint = value

    def __getattr__(self, name: str) -> Any:
        return getattr(self.candidate, name)

    def run(self, source: str, **kwargs: Any) -> CrossRun:
        plain = {k: v for k, v in kwargs.items() if k not in _COVERAGE_ARGS}
        base = self._pool.submit(self.baseline.run, source, **plain)     # its JVM works while the candidate's does
        cand = self.candidate.run(source, **kwargs)
        return CrossRun(self.base_target, self.cand_target, base.result(), cand)
