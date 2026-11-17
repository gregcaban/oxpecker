"""Which checked-in regressions a new finding is probably connected to.

Signatures only deduplicate exact repeats: the same Rhino bug reached with other values, another
probe or another symptom (throw in one mode instead of a value difference) gets a new signature
and is recorded as a new finding. Rather than merge such findings away (a looser match would also
hide new bugs that share a symptom class), each new finding is labelled with the regressions it
shares evidence with, so triage starts from the likely duplicate:

  - the Rhino frames an engine crash was thrown from (strongest: same code path),
  - an error message, digits masked (`Cannot convert BigInt to a number`),
  - the source constructs both programs use, weighted by how rare they are among the regressions (`super`, `__proto__`, `||=`, `...` count; `var`, `function`
    do not; `<<=` and `+=` also count as a compound assignment),
  - the coarse signature (kind of difference only; weak on its own).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .harness.client import RunResult
from .harness.compare import Verdict, compare_run

REGRESSIONS = Path(__file__).resolve().parents[1] / "tests" / "regressions"
SOURCE_SCALE = 8.0     # shared rarity mass at which the source evidence reaches 1 - 1/e

# Names the generators give variables, parameters and loop indices (v12, n3, arr4, big0, f18, p19,
# i48, e13, s2, b6, o5): they say nothing about the construct, only about the generator.
_GENERATED_NAME = re.compile(r"(?:v|n|o|arr|f|p|i|e|s|big|b)\d+")
_TOKEN = re.compile(r"\.\.\.|>>>=|\*\*=|&&=|\|\|=|\?\?=|<<=|>>=|>>>|\?\?|\?\.|\*\*|=>|[A-Za-z_$][\w$]*|[-+*/%&|^]=")
_NOISE = {"__probe", "var", "let", "const", "function", "return", "true", "false", "undefined", "null"}
_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`')
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_RHINO_FRAME = re.compile(r"^(org\.mozilla\.javascript\.(?!gen\.)[\w.$]+)\(")


_TYPED_ARRAY = re.compile(r"(?:Big)?(?:Int|Uint|Float)\d+(?:Clamped)?Array")


def _classes(token: str) -> tuple[str, ...]:
    """Class tokens beside the spelled one, so `super.x <<= 0` and `super.x += 1` share the
    construct (a compound assignment) and Float64Array and Int8Array share TypedArray."""
    if token in ("&&=", "||=", "??="):
        return ("<logical-assign>",)
    if len(token) >= 2 and token.endswith("=") and token not in ("=>",):
        return ("<compound-assign>",)
    if _TYPED_ARRAY.fullmatch(token):
        return ("<TypedArray>",)
    return ()


def source_tokens(source: str) -> frozenset[str]:
    """The identifiers and operators of a program, without comments, string contents, numbers,
    generated names, single-letter names (hand-written regressions use them like the generator
    uses v12) and the most common keywords; plus class tokens (`_classes`)."""
    src = _STRING.sub('""', _COMMENT.sub(" ", source))
    out: set[str] = set()
    for t in _TOKEN.findall(src):
        if t in _NOISE or _GENERATED_NAME.fullmatch(t) or (len(t) == 1 and t.islower()):
            continue
        out.add(t)
        out.update(_classes(t))
    return frozenset(out)


def _mask(text: str) -> str:
    return re.sub(r"\d+", "#", text).strip()


def run_messages(run: RunResult) -> frozenset[str]:
    """Thrown error name+message and engine exception class+message per mode, digits masked."""
    out = set()
    for o in run.results.values():
        if o.thrown and o.thrown.get("message"):
            out.add(_mask(f"{o.thrown.get('name')}: {o.thrown['message']}")[:160])
        if o.engineException:
            out.add(_mask(f"{o.engineException.get('clazz')}: {o.engineException.get('message')}")[:160])
    return frozenset(out)


def run_frames(run: RunResult, depth: int = 2) -> frozenset[str]:
    """The top Rhino frames (method names, no line numbers) each mode's engine exception came from."""
    out = set()
    for o in run.results.values():
        stack = (o.engineException or {}).get("stack") or ""
        frames = [m.group(1) for ln in stack.splitlines() if (m := _RHINO_FRAME.match(ln))]
        out.update(frames[:depth])
    return frozenset(out)


@dataclass(frozen=True)
class Fingerprint:
    name: str
    signature: str = ""
    coarse: str = ""
    messages: frozenset[str] = frozenset()
    frames: frozenset[str] = frozenset()
    tokens: frozenset[str] = frozenset()

    @classmethod
    def of(cls, name: str, source: str, run: RunResult | None, verdict: Verdict | None) -> "Fingerprint":
        finding = verdict is not None and verdict.is_finding
        return cls(name=name,
                   signature=verdict.signature() if finding else "",
                   coarse=verdict.coarse_signature() if finding else "",
                   messages=run_messages(run) if run is not None and finding else frozenset(),
                   frames=run_frames(run) if run is not None and finding else frozenset(),
                   tokens=source_tokens(source))


@dataclass(frozen=True)
class Related:
    name: str
    score: float
    shared: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.name} ({self.score:.2f}: {'; '.join(self.shared)})"


@dataclass
class KnownIndex:
    """The exact signatures to skip plus a fingerprint per regression to relate new findings to."""
    signatures: set[str] = field(default_factory=set)
    entries: list[Fingerprint] = field(default_factory=list)

    def __post_init__(self) -> None:
        df: dict[str, int] = {}
        for e in self.entries:
            for t in e.tokens:
                df[t] = df.get(t, 0) + 1
        n = max(len(self.entries), 1)
        self._idf = {t: math.log((n + 1) / c) for t, c in df.items()}

    def related(self, fp: Fingerprint, *, limit: int = 3, threshold: float = 0.5) -> list[Related]:
        """Regressions ranked by evidence: frame and message 1.0 each, source up to 1.0, coarse
        0.2. Source-only scores below ~0.7 are usually shared incidental syntax, not a link."""
        scored = []
        for e in self.entries:
            score, shared = 0.0, []
            if fp.frames & e.frames:
                score += 1.0
                shared.append("frame " + min(fp.frames & e.frames).removeprefix("org.mozilla.javascript."))
            if fp.messages & e.messages:
                score += 1.0
                shared.append(f"message {min(fp.messages & e.messages)[:70]!r}")
            # The rarity-weighted mass of the constructs both use, saturating: a ratio fails both
            # ways (over the union, a long finding drowns a minimal regression; over the
            # regression alone, a regression that is little more than `for` matches every loop).
            both = fp.tokens & e.tokens
            if both:
                overlap = 1 - math.exp(-sum(self._idf.get(t, 0.0) for t in both) / SOURCE_SCALE)
                score += overlap
                rare = sorted(both, key=lambda t: -self._idf.get(t, 0.0))[:4]
                shared.append(f"source {overlap:.2f} ({' '.join(rare)})")
            if fp.coarse and fp.coarse == e.coarse:
                score += 0.2
                shared.append(f"coarse {e.coarse}")
            if score >= threshold:
                scored.append(Related(e.name, round(score, 2), tuple(shared)))
        scored.sort(key=lambda r: -r.score)
        return scored[:limit]


def known_index(client: Any, regressions: Path = REGRESSIONS, verdict: Any = compare_run) -> KnownIndex:
    """Runs every regression (tests/regressions/*.js) through the harness: their signatures are
    known, and with tests/known_signatures.txt they are what the loop does not record as new.
    `verdict` judges each run as the loop will (crossver.cross_verdict with a CrossClient); with
    that, the known version changes (tests/version-changes/, master fixes) are known too.
    Regressions in subdirectories are related by source only."""
    index = KnownIndex()
    for path in sorted(regressions.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        run = client.run(source, source_name=path.name)
        v = verdict(run)
        index.signatures.add(v.signature())
        index.entries.append(Fingerprint.of(path.name, source, run, v))
    for path in sorted(regressions.glob("*/*.js")):
        index.entries.append(Fingerprint.of(f"{path.parent.name}/{path.name}", path.read_text(encoding="utf-8"), None, None))
    if verdict is not compare_run:
        for path in sorted((regressions.parent / "version-changes").glob("*.js")):
            index.signatures.add(verdict(client.run(path.read_text(encoding="utf-8"), source_name=path.name)).signature())
    extra = regressions.parent / "known_signatures.txt"
    if extra.exists():
        index.signatures |= {ln.split("#", 1)[0].strip() for ln in extra.read_text().splitlines() if ln.split("#", 1)[0].strip()}
    index.__post_init__()
    return index
