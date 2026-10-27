"""Comparison rules (brief §7): decide whether two modes' outcomes constitute a finding."""

from __future__ import annotations

import re

from dataclasses import replace, dataclass, field

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
        if self.kind == JAVA_TYPES:
            first = (self.java_type_diff or self.reasons or [""])[0]
        else:
            first = (self.reasons or self.java_type_diff or [""])[0]
        first = re.sub(r"trace\[\d+\]", "trace[]", first)
        first = re.sub(r"arg\d+", "arg", first)
        first = re.sub(r"java\[\d+\]", "java[]", first)
        first = re.sub(r"@\d+", "@", first)
        first = re.sub(r'"-?\d+"', '"#"', first)          # numeric property keys
        # The same divergence surfaces as `value` (completion) or `trace[] arg` (a probe): one class.
        first = re.sub(r"^(value|trace\[\] arg) ", "diff ", first)
        # Numbers become '#', except zero: -0 vs 0 is its own class of bug.
        first = re.sub(r"(?<![A-Za-z_#])(-?)(\d+(\.\d+)?(e[+-]?\d+)?)",
                       lambda m: m.group(1) + "0" if float(m.group(2)) == 0 else "#", first)
        return f"{self.kind}:{first}"

    def to_json(self) -> dict:
        return {"kind": self.kind, "reasons": self.reasons, "javaTypeDiff": self.java_type_diff,
                "firstTraceDiff": self.first_trace_diff, "signature": self.signature()}

    def coarse_signature(self) -> str:
        """kind + the *type* of the first difference, without any values: what a shrinker should
        hold constant (values and lengths change while shrinking; the bug class should not)."""
        import re
        first = (self.reasons or self.java_type_diff or [""])[0]
        m = re.match(r"(status \S+ vs \S+|thrown name|thrown value|value|trace\[\d+\]( arg\d+)?|trace length|output differs"
                     r"|java\[\d+\]|java trace length|continuations|(\w+) crashed: [\w.$]+|java types differ|error messages differ)", first)
        head = m.group(0) if m else first[:20]
        head = re.sub(r"\[\d+\]", "[]", head)
        head = re.sub(r"arg\d+", "arg", head)
        return f"{self.kind}:{head}"

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


_IDENTITY_HASH = re.compile(r"((?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*)@[0-9a-f]{1,8}\b")


def canon_identity(s: str | None) -> str | None:
    """`dev.oxpecker.harness.JavaProbe@3bde59e5` -> `...JavaProbe@`: a Java object's default
    `toString` carries its identity hash, which differs between any two runs."""
    return None if s is None else _IDENTITY_HASH.sub(r"\1@", s)


def compare(a: Outcome, b: Outcome, *, strict_java_types: bool = False, strict_messages: bool = False) -> Verdict:
    """Compares the outcome of one mode against another (a is the reference, usually interpreted)."""
    a = replace(a, value=canon_identity(a.value), trace=[canon_identity(t) for t in a.trace],
                output=[canon_identity(t) for t in a.output], javaTrace=[canon_identity(t) for t in a.javaTrace])
    b = replace(b, value=canon_identity(b.value), trace=[canon_identity(t) for t in b.trace],
                output=[canon_identity(t) for t in b.output], javaTrace=[canon_identity(t) for t in b.javaTrace])
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
    if a.status == "unsupported" and b.status == "unsupported":
        # neither mode ran the program to its end (a continuation captured across a non-JavaScript
        # frame, or captured at all in the compiled mode): the partial traces differ by construction
        return Verdict(SKIP, [f"both unsupported ({a.reason} / {b.reason})"])
    paused = {o.mode: bool(o.continuations) for o in (a, b)}
    if paused.get("interpreted") and not paused.get("compiled") and "unsupported" not in (a.status, b.status):
        # the interpreter paused; the compiled mode recorded no continuation and did not report the
        # refusal, so the program's own try/catch swallowed it (G42): everything after that point
        # differs by construction, like the both-unsupported case
        return Verdict(SKIP, ["interpreted paused, compiled refusal caught by the program (continuations)"])
    if a.status in slow or b.status in slow:
        o = a if a.status in slow else b
        return Verdict(SOFT, [f"{o.mode} {o.status} ({o.reason}) while {(b if o is a else a).mode} {(b if o is a else a).status}"])

    if a.status != b.status:
        def st(o: Outcome) -> str:
            return o.status + (f"({o.reason})" if o.status == "unsupported" else "")
        reasons.append(f"status {a.mode}={st(a)} vs {b.mode}={st(b)}")
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
                reasons.append(f"trace[{i}] {_arg_diff(x, y)}")
                break
        else:
            first_diff = min(len(a.trace), len(b.trace))
            reasons.append(f"trace length {len(a.trace)} vs {len(b.trace)}")
    if a.output != b.output:
        reasons.append(f"output differs: {_short(str(a.output))} vs {_short(str(b.output))}")
    if a.continuations != b.continuations:
        reasons.append(f"continuations {_short(str(a.continuations))} vs {_short(str(b.continuations))}")
    if a.javaTrace != b.javaTrace:
        for i, (x, y) in enumerate(zip(a.javaTrace, b.javaTrace)):
            if x != y:
                reasons.append(f"java[{i}] {_short(x)} vs {_short(y)}")
                break
        else:
            reasons.append(f"java trace length {len(a.javaTrace)} vs {len(b.javaTrace)}")
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
    if a.javaTraceTypes != b.javaTraceTypes:
        for i, (x, y) in enumerate(zip(a.javaTraceTypes, b.javaTraceTypes)):
            if x != y:
                jdiff.append(f"java[{i}] {_short(x, 60)} vs {_short(y, 60)}")
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


_shell_client = None


def run_and_compare(source: str, *, modes: tuple[str, ...] = ("interpreted", "compiled"), strict: bool = False,
                    quiet: bool = False, **run_kwargs) -> tuple[Verdict, RunResult]:
    """Run JavaScript source through the harness (one long-lived client per process) and compare
    the modes; prints the verdict and outcomes unless `quiet`. For the shell and the tests."""
    global _shell_client
    from .client import HarnessClient

    if _shell_client is None:
        _shell_client = HarnessClient(quiet=True)
        _shell_client.start()
    run = _shell_client.run(source, modes=modes, strict=strict, **run_kwargs)
    verdict = compare_run(run)
    if not quiet:
        print(verdict.pretty())
        for mode, o in run.results.items():
            print(f"  {mode:12} {o.status:10} value={o.value!r}" + (f" thrown={o.thrown}" if o.thrown else ""))
            for t in o.trace:
                print(f"      trace: {t}")
    return verdict, run


def _arg_diff(x: str, y: str) -> str:
    """The first probe argument that differs, so reports and signatures point at the value rather
    than the whole snapshot."""
    xs, ys = x.split(" | "), y.split(" | ")
    for j, (p, q) in enumerate(zip(xs, ys)):
        if p != q:
            k = next((c for c, (u, v) in enumerate(zip(p, q)) if u != v), min(len(p), len(q)))
            lo = max(0, k - 30)
            pre = "…" if lo else ""
            return f"arg{j} {pre}{_short(p[lo:], 70)} vs {pre}{_short(q[lo:], 70)}"
    return f"arity {len(xs)} vs {len(ys)}"


def _short(s: str | None, n: int = 80) -> str:
    if s is None:
        return "None"
    return s if len(s) <= n else s[: n - 1] + "…"
