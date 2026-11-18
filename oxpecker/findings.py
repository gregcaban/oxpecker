"""Recording findings: one jsonl log per verdict kind, deduplicated by signature, plus a `.js`
reproducer per hard signature. Shared by the property tests and `oxpecker fuzz`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import targets
from .harness.client import RunResult
from .harness.compare import Verdict

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "tests" / "findings"


def short_sig(verdict: Verdict) -> str:
    return hashlib.sha1(verdict.signature().encode()).hexdigest()[:10]


def known_signatures(client: Any, regressions: Path = DEFAULT_DIR.parent / "regressions") -> set[str]:
    """Signatures of the checked-in regressions (run through the harness) plus
    tests/known_signatures.txt."""
    from .related import known_index
    return known_index(client, regressions).signatures


_SEEN: dict[Path, tuple[int, set[str]]] = {}     # log -> (its size when read, the signatures in it)


def _seen(log: Path) -> set[str]:
    """The signatures already in a log. Read once and again only when the file's size is not what
    this process left it at (another process wrote to it, or it was removed): the loop records
    two runs in three, and reading the whole log each time grew with the campaign."""
    size = log.stat().st_size if log.exists() else 0
    cached = _SEEN.get(log)
    if cached is not None and cached[0] == size:
        return cached[1]
    seen: set[str] = set()
    if size:
        seen = {json.loads(ln)["signature"] for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()}
    _SEEN[log] = (size, seen)
    return seen


def record(prog: Any, run: RunResult, verdict: Verdict, *, out_dir: Path = DEFAULT_DIR,
           reproducer: bool = False, choices: list[int] | None = None, replay_cmd: str = "oxpecker diff",
           related: list[Any] | None = None, judgement: Any | None = None) -> Path | None:
    """Appends to <kind>.jsonl once per signature. With `reproducer`, (over)writes
    <kind>-<sig>.js with the probed source (trace findings need the probes) and returns its path.
    `related` (oxpecker.related.Related) names the regressions the finding probably connects to.
    A crossver.CrossRun records both targets, and its reproducer expects each one's own verdict.
    `judgement` (oxpecker.judge.Judgement) says which side engine262 agrees with."""
    base = getattr(run, "base_target", None)             # comparing two Rhinos (crossver)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / f"{verdict.kind}.jsonl"
    sig = verdict.signature()
    seen = _seen(log)
    if sig not in seen:
        seen.add(sig)
        # one write of the whole line, unbuffered: the loop and its shrink process both append here
        with log.open("ab", buffering=0) as f:
            f.write((json.dumps({"signature": sig, "rhino": targets.current(), **({"baseline": base} if base else {}), "verdict": verdict.to_json(), "source": prog.source,
                                "template": prog.template, "choices": choices,
                                "related": [{"regression": r.name, "score": r.score, "shared": list(r.shared)}
                                            for r in related or ()],
                                **({"judge": judgement.to_json()} if judgement is not None else {}),
                                "results": run.to_json()["results"]}) + "\n").encode("utf-8"))
        _SEEN[log] = (log.stat().st_size, seen)
    if not reproducer:
        return None
    path = out_dir / f"{verdict.kind}-{short_sig(verdict)}.js"
    if base:
        body = "".join(f"// oxpecker-expect[{t}]: {k}\n" for t, k in run.expectations().items())
    else:
        body = f"// oxpecker-expect: {verdict.kind}\n"
    body += "// " + verdict.pretty().replace("\n", " ") + "\n"
    body += "// " + json.dumps({m: {"status": o.status, "value": o.value} for m, o in run.results.items()}) + "\n"
    if base:
        body += f"// Found comparing Rhino {base} with {run.cand_target} (oxpecker xver --targets {base},{run.cand_target} FILE)\n"
        replay_cmd = f"oxpecker xver --targets {base},{run.cand_target}"
    else:
        body += f"// Found on Rhino target {targets.current()} (oxpecker xver FILE: the other targets)\n"
    if judgement is not None:
        body += f"// engine262: {judgement.label}: {judgement.summary}\n"
    for r in related or ():
        body += f"// Related: {r}\n"
    body += f"// Replay: {replay_cmd} {path}\n" + prog.source
    path.write_text(body, encoding="utf-8")
    return path
