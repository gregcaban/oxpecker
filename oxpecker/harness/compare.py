"""Comparison rules (brief §7): decide whether two modes' outcomes constitute a finding."""

from __future__ import annotations

from dataclasses import dataclass, field

from .client import Outcome, RunResult

HARD = "hard"
JAVA_TYPES = "java-types"   # same JS behaviour, different Java representation at the host boundary
SOFT = "soft"
EQUAL = "equal"
SKIP = "skip"          # fallback: excluded from comparison
DISCARD = "discard"    # both timeout/resource: a generator problem


@dataclass
class Verdict:
    kind: str                                   # equal | hard | java-types | soft | skip | discard
    reasons: list[str] = field(default_factory=list)
    java_type_diff: list[str] = field(default_factory=list)   # Integer-vs-Double style differences
    first_trace_diff: int | None = None

    @property
    def is_finding(self) -> bool:
        return self.kind in (HARD, JAVA_TYPES, SOFT)

    def signature(self) -> str:
        """Dedup key: the kind and the first reason / first Java-type difference, with probe
        indices and variable names stripped so the same divergence groups across programs."""
        import re
        first = (self.reasons or self.java_type_diff or [""])[0]
        first = re.sub(r"trace\[\d+\]", "trace[]", first)
        first = re.sub(r"arg\d+", "arg", first)
        return f"{self.kind}:{first}"

    def to_json(self) -> dict:
        return {"kind": self.kind, "reasons": self.reasons, "javaTypeDiff": self.java_type_diff,
                "firstTraceDiff": self.first_trace_diff, "signature": self.signature()}

    def pretty(self) -> str:
        s = self.kind.upper()
        if self.reasons:
            s += ": " + "; ".join(self.reasons)
        if self.java_type_diff:
            s += " [java types: " + "; ".join(self.java_type_diff) + "]"
        return s


def _thrown_sig(o: Outcome) -> tuple[str | None, str | None]:
    if not o.thrown:
        return (None, None)
    return (o.thrown.get("name"), o.thrown.get("value"))


def compare(a: Outcome, b: Outcome, *, strict_java_types: bool = False, strict_messages: bool = False) -> Verdict:
    """Compares the outcome of one mode against another (a is the reference, usually interpreted)."""
    reasons: list[str] = []
    jdiff: list[str] = []
    first_diff: int | None = None

    if a.status == "fallback" or b.status == "fallback":
        return Verdict(SKIP, [f"{'/'.join(o.mode for o in (a, b) if o.status == 'fallback')} fell back to the interpreter"])
    if a.status == "crash" or b.status == "crash":
        for o in (a, b):
            if o.status == "crash":
                ee = o.engineException or {}
                reasons.append(f"{o.mode} crashed: {ee.get('clazz')}: {str(ee.get('message'))[:120]}")
        return Verdict(HARD, reasons)

    slow = {"timeout", "resource"}
    if a.status in slow and b.status in slow:
        return Verdict(DISCARD, [f"both {a.status}/{b.status}"])
    if a.status in slow or b.status in slow:
        o = a if a.status in slow else b
        return Verdict(SOFT, [f"{o.mode} {o.status} ({o.reason}) while {(b if o is a else a).mode} {(b if o is a else a).status}"])

    if a.status != b.status:
        reasons.append(f"status {a.mode}={a.status} vs {b.mode}={b.status}")
        if a.thrown or b.thrown:
            reasons.append(f"thrown {_thrown_sig(a)[0]} vs {_thrown_sig(b)[0]}")
        return Verdict(HARD, reasons)

    # Same status: ok / throw / syntax.
    if a.status == "throw" and _thrown_sig(a) != _thrown_sig(b):
        na, va = _thrown_sig(a)
        nb, vb = _thrown_sig(b)
        if na != nb:
            reasons.append(f"thrown name {na} vs {nb}")
        if va != vb:
            reasons.append(f"thrown value {_short(va)} vs {_short(vb)}")
    if a.status == "ok" and a.value != b.value:
        reasons.append(f"value {_short(a.value)} vs {_short(b.value)}")
    if a.trace != b.trace:
        for i, (x, y) in enumerate(zip(a.trace, b.trace)):
            if x != y:
                first_diff = i
                reasons.append(f"trace[{i}] {_short(x)} vs {_short(y)}")
                break
        else:
            first_diff = min(len(a.trace), len(b.trace))
            reasons.append(f"trace length {len(a.trace)} vs {len(b.trace)}")
    if a.output != b.output:
        reasons.append(f"output differs: {_short(str(a.output))} vs {_short(str(b.output))}")
    if reasons:
        return Verdict(HARD, reasons, first_trace_diff=first_diff)

    # Soft: messages and Java representation types.
    if a.valueJavaTypes != b.valueJavaTypes:
        jdiff.append(f"value {_short(a.valueJavaTypes)} vs {_short(b.valueJavaTypes)}")
    if a.traceJavaTypes != b.traceJavaTypes:
        for i, (x, y) in enumerate(zip(a.traceJavaTypes, b.traceJavaTypes)):
            if x != y:
                # Report only the first probe argument whose Java types differ.
                xs, ys = x.split(" | "), y.split(" | ")
                for j, (xa, ya) in enumerate(zip(xs, ys)):
                    if xa != ya:
                        jdiff.append(f"trace[{i}] arg{j} {_short(xa, 60)} vs {_short(ya, 60)}")
                        break
                else:
                    jdiff.append(f"trace[{i}] arity {len(xs)} vs {len(ys)}")
                break
    ta, tb = a.thrown or {}, b.thrown or {}
    if ta.get("valueJavaTypes") != tb.get("valueJavaTypes"):
        jdiff.append(f"thrown {ta.get('valueJavaTypes')} vs {tb.get('valueJavaTypes')}")
    soft_reasons: list[str] = []
    if a.soft != b.soft or (ta.get("message") != tb.get("message")):
        soft_reasons.append("error messages differ")
    if jdiff:
        # Always reported, never folded into "equal": embedders see these at the Java boundary.
        return Verdict(HARD if strict_java_types else JAVA_TYPES, ["java types differ"] + soft_reasons, jdiff)
    if soft_reasons:
        return Verdict(HARD if strict_messages else SOFT, soft_reasons, jdiff)
    return Verdict(EQUAL, [], [])


def compare_run(run: RunResult, *, reference: str = "interpreted", strict_java_types: bool = False,
                strict_messages: bool = False) -> Verdict:
    """Compares every other mode against the reference; the worst verdict wins."""
    ref = run.results.get(reference)
    if ref is None:
        return Verdict(SKIP, [f"no {reference} result"])
    order = {HARD: 0, JAVA_TYPES: 1, SOFT: 2, DISCARD: 3, SKIP: 4, EQUAL: 5}
    worst: Verdict | None = None
    for mode, o in run.results.items():
        if mode == reference:
            continue
        v = compare(ref, o, strict_java_types=strict_java_types, strict_messages=strict_messages)
        if worst is None or order[v.kind] < order[worst.kind]:
            worst = v
        elif v.kind == worst.kind:
            worst.reasons.extend(r for r in v.reasons if r not in worst.reasons)
            worst.java_type_diff.extend(j for j in v.java_type_diff if j not in worst.java_type_diff)
    return worst or Verdict(EQUAL)


def _short(s: str | None, n: int = 80) -> str:
    if s is None:
        return "None"
    return s if len(s) <= n else s[: n - 1] + "…"
