"""engine262 as a by-the-spec judge of findings: when Rhino's modes disagree (or a mode changed
between two Rhino versions, crossver), run the program once more on engine262, an implementation
of ECMA-262 that follows the spec's algorithms step by step, and say which side agrees with it.

Only findings are judged, never every program: engine262 is far slower than Rhino. The host
(judge/host.mjs, `npm install --prefix judge`) runs the harness's own canon.js and prelude.js
inside engine262, so outcomes compare with compare.compare like two Rhino modes. Before comparing,
both sides drop what Rhino renders differently by design or that the spec leaves open: function
source text (Rhino decompiles), Rhino's own `arity` property on functions, and crossver's
VERSION_CANON. Programs that need Java (`J`), continuations or other Rhino-only host features
are not judged.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import ROOT
from .harness.client import Outcome

JUDGE_DIR = ROOT / "judge"
HOST = JUDGE_DIR / "host.mjs"
ENGINE = JUDGE_DIR / "node_modules" / "@engine262" / "engine262"
TIMEOUT_S = 60.0

# What engine262 cannot run: the Java boundary, continuations, Rhino's shell and LiveConnect.
_UNSUPPORTED = [("Java (J)", re.compile(r"(?<![\w$.])J(?![\w$])")), ("continuations", re.compile(r"__pause|\bContinuation\b")),
                ("LiveConnect", re.compile(r"\b(Packages|java|javax|JavaAdapter|JavaImporter)\b"))]
# Rhino-only renderings, dropped on both sides
_RHINO_ONLY = [re.compile(r'"arity"\{[-a-z]*\}:\d+;?')]


def available() -> bool:
    return shutil.which("node") is not None and ENGINE.exists()


@dataclass
class Judgement:
    """`label` is the short answer, `agrees` says per Rhino outcome (mode, or BASE/MODE for a
    cross-version run) whether it agrees with engine262; `spec` is engine262's outcome."""
    label: str
    summary: str
    agrees: dict[str, bool] = field(default_factory=dict)
    spec: Outcome | None = None

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "summary": self.summary, "agrees": self.agrees,
                "engine262": self.spec.to_json() if self.spec else None}


def unsupported_reason(source: str) -> str | None:
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)     # a reproducer's header names Java classes
    for why, pattern in _UNSUPPORTED:
        if pattern.search(code):
            return why
    return None


def run_engine262(source: str, *, strict: bool = False, seed: int = 1, timeout_s: float = TIMEOUT_S) -> Outcome:
    req = json.dumps({"source": source, "strict": strict, "seed": seed})
    try:
        p = subprocess.run(["node", str(HOST)], input=req, capture_output=True, text=True, timeout=timeout_s,
                           env=dict(os.environ, TZ="UTC"), cwd=JUDGE_DIR)
    except subprocess.TimeoutExpired:
        return Outcome("engine262", "timeout", reason=f"wall {timeout_s:.0f}s")
    if p.returncode != 0 or not p.stdout.strip():
        return Outcome("engine262", "crash", engineException={"clazz": "host", "message": (p.stderr or "")[-400:]})
    return Outcome.from_json(json.loads(p.stdout.strip().splitlines()[-1]))


def _canon(o: Outcome) -> Outcome:
    from .crossver import version_canon

    def c(s: str | None) -> str | None:
        if s is None:
            return None
        for pattern in _RHINO_ONLY:
            s = pattern.sub("", s)
        return s.replace(";}", "}")
    o = version_canon(o)
    thrown = dict(o.thrown, value=c(o.thrown.get("value"))) if o.thrown else o.thrown
    # Java types and the Java trace do not exist on engine262: not compared
    return replace(o, value=c(o.value), trace=[c(t) for t in o.trace], output=[c(t) for t in o.output], thrown=thrown,
                   valueJavaTypes=None, traceJavaTypes=[], javaTrace=[], javaTraceTypes=[])


def observations(o: Outcome) -> dict[str, str | None]:
    """An outcome as named observations: status (with the error name), the thrown or completion
    value, each probe argument, each output line. A judgement looks only at the ones where the
    two sides of a finding differ, so what Rhino does differently everywhere (own `arguments` on
    every function, ...) and both sides share does not count."""
    o = _canon(o)
    thrown = o.thrown or {}
    obs: dict[str, str | None] = {"status": f"throw {thrown.get('name')}" if o.status == "throw" else o.status}
    if o.status == "throw":
        obs["thrown"] = thrown.get("value")
    if o.status == "ok":
        obs["value"] = o.value
    for i, tr in enumerate(o.trace):
        for j, arg in enumerate(tr.split(" | ")):
            obs[f"trace[{i}] arg{j}"] = arg
    obs["trace length"] = str(len(o.trace))
    for i, line in enumerate(o.output):
        obs[f"output[{i}]"] = line
    return obs


def _closer(x: str | None, y: str | None, s: str | None) -> str | None:
    """When neither side's observation is engine262's exactly: at each place x and y differ,
    which one's text, with its surroundings, engine262's contains. 'a', 'b' or None."""
    if x is None or y is None or s is None:
        return None
    votes = {"a": 0, "b": 0}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        for n in (24, 12, 6, 3):          # the widest context that tells them apart (Rhino-only text may be near)
            in_a = (x[max(0, i1 - n):i1] + x[i1:i2] + x[i2:i2 + n]) in s
            in_b = (y[max(0, j1 - n):j1] + y[j1:j2] + y[j2:j2 + n]) in s
            if in_a != in_b:
                votes["a" if in_a else "b"] += 1
                break
    if votes["a"] == votes["b"]:
        return None
    return "a" if votes["a"] > votes["b"] else "b"


def sides(a: Outcome, b: Outcome, spec: Outcome) -> tuple[bool, bool, list[str]] | None:
    """(a agrees with engine262, b agrees, the observations where a and b differ), judged only
    on those; None when a and b do not differ in anything the judge compares."""
    oa, ob, os_ = observations(a), observations(b), observations(spec)
    keys = [k for k in dict.fromkeys([*oa, *ob]) if oa.get(k) != ob.get(k)]
    if not keys:
        return None
    a_right = b_right = True
    for k in keys:
        x, y, s = oa.get(k), ob.get(k), os_.get(k)
        side = "a" if x == s else "b" if y == s else _closer(x, y, s)
        if side != "a":
            a_right = False
        if side != "b":
            b_right = False
    return a_right, b_right, keys


def judge(source: str, run: Any, *, strict: bool = False, seed: int = 1, timeout_s: float = TIMEOUT_S) -> Judgement:
    """Judges a finding: `run` is the RunResult (mode finding) or crossver.CrossRun it came from."""
    why = unsupported_reason(source)
    if why:
        return Judgement("not-judged", f"not judged: uses {why}, which engine262 does not have")
    if not available():
        return Judgement("not-judged", "not judged: engine262 is not installed (npm install --prefix judge)")
    spec = run_engine262(source, strict=strict, seed=seed, timeout_s=timeout_s)
    if spec.status in ("timeout", "crash", "resource"):
        return Judgement("not-judged", f"not judged: engine262 {spec.status} "
                         f"({spec.reason or (spec.engineException or {}).get('message', '')[:80]})", spec=spec)
    if spec.status == "syntax" and not any(o.status == "syntax" for o in run.results.values()):
        return Judgement("rhino-extension", "engine262 rejects the program as a syntax error: "
                         "Rhino-only syntax, or every mode wrong", spec=spec)
    if spec.status == "throw" and (spec.thrown or {}).get("name") == "ReferenceError" and not any(
            (o.thrown or {}).get("name") == "ReferenceError" for o in run.results.values()):
        return Judgement("rhino-extension", "engine262 throws a ReferenceError no Rhino mode does: "
                         "the program uses a Rhino-only global (CallSite, toSource, ...)", spec=spec)
    if getattr(run, "base_target", None) is not None:
        j = _versions(run, spec)
        if j is not None:
            return j
        run = run.cand                    # no mode changed between versions: judge the candidate's modes
    return _modes(run, spec)


def _modes(run: Any, spec: Outcome) -> Judgement:
    i, c = run.results.get("interpreted"), run.results.get("compiled")
    s = sides(i, c, spec) if i is not None and c is not None else None
    if s is None:
        # the modes agree with each other (both crash alike, say): only their status can be judged,
        # everything else would weigh what Rhino does differently everywhere
        mine, its = observations(i or c)["status"], observations(spec)["status"]
        if mine != its:
            return Judgement("all-wrong", f"both modes {mine}, engine262 {its}", {"interpreted": False, "compiled": False}, spec)
        return Judgement("inconclusive", "the modes do not differ in anything the judge compares", spec=spec)
    i_ok, c_ok, keys = s
    agrees = {"interpreted": i_ok, "compiled": c_ok}
    at = f" (at {', '.join(keys[:3])})"
    if i_ok and not c_ok:
        return Judgement("compiled-wrong", "compiled wrong: interpreted agrees with engine262" + at, agrees, spec)
    if c_ok and not i_ok:
        return Judgement("interpreted-wrong", "interpreted wrong: compiled agrees with engine262" + at, agrees, spec)
    return Judgement("all-wrong", "neither mode agrees with engine262" + at, agrees, spec)


def _versions(run: Any, spec: Outcome) -> Judgement | None:
    """Per mode that changed between the versions: the baseline agreed with engine262 and the
    candidate does not, a regression; the other way round, a fix."""
    b, c = run.base_target, run.cand_target
    regressed, fixed, both_wrong, agrees = [], [], [], {}
    for mode, co in run.cand.results.items():
        bo = run.base.results.get(mode)
        s = sides(bo, co, spec) if bo is not None else None
        if s is None:
            continue
        base_ok, cand_ok, _ = s
        agrees[f"{b}/{mode}"], agrees[mode] = base_ok, cand_ok
        (regressed if base_ok and not cand_ok else fixed if cand_ok and not base_ok else both_wrong).append(mode)
    if not agrees:
        return None
    if regressed:
        return Judgement("regression", f"regression on {c}: {b} agrees with engine262 in {', '.join(regressed)}, {c} does not", agrees, spec)
    if fixed and not both_wrong:
        return Judgement("fix", f"fixed on {c}: {c} agrees with engine262 in {', '.join(fixed)}, {b} did not", agrees, spec)
    return Judgement("all-wrong", f"neither version agrees with engine262 in {', '.join(both_wrong)}", agrees, spec)
