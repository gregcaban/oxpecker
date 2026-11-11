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


def record(prog: Any, run: RunResult, verdict: Verdict, *, out_dir: Path = DEFAULT_DIR,
           reproducer: bool = False, choices: list[int] | None = None, replay_cmd: str = "oxpecker diff",
           related: list[Any] | None = None) -> Path | None:
    """Appends to <kind>.jsonl once per signature. With `reproducer`, (over)writes
    <kind>-<sig>.js with the probed source (trace findings need the probes) and returns its path.
    `related` (oxpecker.related.Related) names the regressions the finding probably connects to."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / f"{verdict.kind}.jsonl"
    sig = verdict.signature()
    seen: set[str] = set()
    if log.exists():
        seen = {json.loads(ln)["signature"] for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()}
    if sig not in seen:
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"signature": sig, "rhino": targets.current(), "verdict": verdict.to_json(), "source": prog.source,
                                "template": prog.template, "choices": choices,
                                "related": [{"regression": r.name, "score": r.score, "shared": list(r.shared)}
                                            for r in related or ()],
                                "results": run.to_json()["results"]}) + "\n")
    if not reproducer:
        return None
    path = out_dir / f"{verdict.kind}-{short_sig(verdict)}.js"
    body = f"// oxpecker-expect: {verdict.kind}\n// " + verdict.pretty().replace("\n", " ") + "\n"
    body += "// " + json.dumps({m: {"status": o.status, "value": o.value} for m, o in run.results.items()}) + "\n"
    body += f"// Found on Rhino target {targets.current()} (oxpecker xver FILE: the other targets)\n"
    for r in related or ():
        body += f"// Related: {r}\n"
    body += f"// Replay: {replay_cmd} {path}\n" + prog.source
    path.write_text(body, encoding="utf-8")
    return path
