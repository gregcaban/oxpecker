"""The differential-testing loop (`oxpecker fuzz`): proposal §3.6 and §5.3 on top of the
generation layer. A corpus entry is a lineage (a base program and its mutation steps) stored as
the Hypothesis choice sequence that draws it, so every entry can be replayed exactly, extended by
more mutation steps (the lineage's final "one more?" choice flipped to True, the rest drawn
fresh), and shrunk by Hypothesis's own shrinker.

    corpus   lineages that reached Rhino probes nothing before them had: `corpus.jsonl` plus the
             JaCoCo store `coverage.exec`, so a later run resumes the frontier
    loop     with p_new a fresh lineage, else a corpus entry extended by up to `steps` more
             mutation steps (per round, G49; `max_depth` bounds the whole lineage); the child's
             program runs in both Rhino modes and is compared
             new probes -> (optionally shrunk while it still reaches them) -> corpus
             a hard verdict with an unknown signature -> shrunk by Hypothesis -> recorded
    shrink   Hypothesis's shrinker (phases reuse + shrink from an in-memory example database
             holding the failing choices), with a call budget enforced from the test body

From a shell:

    >>> from oxpecker.fuzz import fuzz, Corpus, draw_lineage, extend, LineageCache
    >>> summary = fuzz(corpus_dir=Path("runs/c1"), iterations=200)
    >>> c = Corpus(Path("runs/c1")); c.entries[0].steps; print(c.replay(0))   # the lineage behind an entry
"""

from __future__ import annotations

import json
import os
import pickle
import random
import sys
import time
from collections import Counter, OrderedDict, deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any, Callable, Protocol

from hypothesis import HealthCheck, Phase, given, settings
from hypothesis.control import BuildContext, current_build_context
from hypothesis.database import InMemoryExampleDatabase, choices_from_bytes, choices_to_bytes
from hypothesis.errors import HypothesisException, StopTest, UnsatisfiedAssumption
from hypothesis.internal.conjecture.data import ConjectureData

import oxpecker.il.templates  # noqa: F401  (registration)
from . import coverage, findings
from .harness.compare import HARD, JAVA_TYPES, SOFT, Verdict, compare_run
from .il.mutate import Lineage, evolved
from .il.options import GenOptions
from .il.strategies import seeded_data

ProbeSet = dict[str, list[int]]
Choices = tuple[Any, ...]
MAX_DEPTH = 20            # safety cap on a lineage's depth across extension rounds (G49): the choice
                          # sequence grows by a few hundred bytes per step against Hypothesis's 8 KiB
WALL_MS = 1500            # a mode's time limit in the loop. A program takes 10 ms at the median and 100 at
                          # the 99th percentile; the 0.1 % that ran to the general 5 s limit and the 3 s
                          # watchdog after it took 29 % of a run's time, each holding up the only JVM
CHECKPOINT_SECONDS = 20.0 # the coverage store is saved for a restarted JVM at most this often
DATABASE_KEY = b"oxpecker.fuzz.lineage"     # the lineage test's database key for the shrink runs
SECONDARY_KEY = DATABASE_KEY + b".secondary"   # where the failing choices go: an example under the
                                                # primary key counts as already shrunk (engine.py, the
                                                # "reused previously shrunk" fast path); fuzz_one_input
                                                # saves to the secondary corpus for the same reason


# ------------------------------------------------------------------------------- drawing lineages
def draw_lineage(options: GenOptions, steps: int, *, prefix: Choices | None = None,
                 seed: int | None = None, rng: Random | None = None) -> tuple[Lineage, Choices] | None:
    """Draw a lineage: replaying `prefix` first (exactly), then fresh choices from `rng`/`seed`.
    Returns the lineage and the choices consumed, or None when the draw overran or rejected."""
    data = seeded_data(seed, rng=rng, prefix=prefix)
    try:
        with BuildContext(data, wrapped_test=lambda: None):
            lineage = data.draw(evolved(options, steps))
    except (StopTest, UnsatisfiedAssumption):
        return None
    data.freeze()
    return lineage, tuple(data.choices)


def replay(choices: Choices, options: GenOptions = GenOptions(), steps: int = 5) -> Lineage | None:
    """The lineage a choice sequence draws, or None if it no longer decodes (the strategies
    changed since it was recorded). `steps` must be the cap the sequence was drawn with (a corpus
    entry records it as `cap`): the "one more?" draws depend on it."""
    try:
        data = ConjectureData.for_choices(choices)
        with BuildContext(data, wrapped_test=lambda: None):
            return data.draw(evolved(options, steps))
    except (StopTest, UnsatisfiedAssumption, IndexError, ValueError):
        return None


def continue_lineage(parent: Lineage, parent_choices: Choices, choices: Choices, options: GenOptions,
                     cap: int) -> Lineage | None:
    """The lineage `choices` draws, given `parent`, the lineage `parent_choices` draws, when
    `choices` extends it the way `extend` does: only the child's own draws are replayed, not the
    whole history. None when `choices` does not continue `parent_choices` (a compacted entry) or no
    longer decodes; the caller falls back to `replay`."""
    kept = tuple(parent_choices) if parent.at_cap else tuple(parent_choices[:-1])
    if tuple(choices[:len(kept)]) != kept or len(choices) <= len(kept):
        return None
    try:
        data = ConjectureData.for_choices(tuple(choices[len(kept):]))
        with BuildContext(data, wrapped_test=lambda: None):
            return data.draw(evolved(options, cap, parent=parent))
    except (StopTest, UnsatisfiedAssumption, IndexError, ValueError):
        return None


def extend(choices: Choices, options: GenOptions, steps: int, rng: Random,
           parent: Lineage | None = None, *, cap: int | None = None,
           max_depth: int = MAX_DEPTH) -> tuple[Lineage, Choices] | None:
    """A parent lineage with up to `steps` more mutation steps, drawn fresh (proposal §3.5).
    `steps` is per round (G49): the child's cap is the parent's depth plus `steps`, bounded by
    `max_depth`, and `Lineage.cap` records it so the entry can be replayed and extended again.
    None when the parent is at `max_depth` or its choices no longer decode.

    Only the new steps are drawn (`evolved(parent=...)`): `parent` is the lineage `choices`
    draws, replayed here (with `cap`, its own cap) when not given; the loop keeps it in a
    `LineageCache`. The choices returned are the parent's, its stop flag (if it drew one)
    replaced by `True`, plus the new draws: the sequence a full replay with the child's cap
    consumes, so `replay` and the shrinker see no difference."""
    if parent is None:
        parent = replay(choices, options, cap if cap is not None else steps)
        if parent is None:
            return None
    if parent.depth >= max_depth or not choices:
        return None
    if parent.at_cap:
        kept = tuple(choices)                   # ended by its cap: no stop flag was drawn
    else:
        if choices[-1] is not False:
            return None
        kept = tuple(choices[:-1])
    new_cap = min(parent.depth + steps, max_depth)
    data = seeded_data(rng=rng, prefix=(True,))
    try:
        with BuildContext(data, wrapped_test=lambda: None):
            child = data.draw(evolved(options, new_cap, parent=parent))
    except (StopTest, UnsatisfiedAssumption):
        return None
    data.freeze()
    return child, kept + tuple(data.choices)


def encode(choices: Choices) -> str:
    return choices_to_bytes(choices).hex()


def decode(text: str) -> Choices | None:
    return choices_from_bytes(bytes.fromhex(text))


# ------------------------------------------------------------------------------- the corpus
@dataclass
class Entry:
    id: int
    choices: str                      # `encode(choices)`
    template: str
    steps: list[str]
    new_probes: int                   # how many probes were new when it was found
    probes: ProbeSet = field(default_factory=dict)   # the ones a re-run reaches again (D36)
    parent: int | None = None
    instructions: int = 0
    found: list[str] = field(default_factory=list)
    added_at: float = field(default_factory=time.time)
    cap: int | None = None            # the lineage's step cap (G49); None in corpora from before it
    ms: int = 0                       # run time when found (slowest mode); 0 in corpora from before it

    def to_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Corpus:
    """`corpus.jsonl` (one Entry per line, append-only) and `coverage.exec` in one directory."""

    def __init__(self, dir: Path):
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "corpus.jsonl"
        self.exec = self.dir / "coverage.exec"
        self.entries: list[Entry] = []
        if self.file.exists():
            for ln in self.file.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    d = json.loads(ln)
                    self.entries.append(Entry(**{k: d[k] for k in Entry.__dataclass_fields__ if k in d}))
        self.by_id: dict[int, Entry] = {e.id: e for e in self.entries}

    def add(self, e: Entry) -> None:
        self.entries.append(e)
        self.by_id[e.id] = e
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(e.to_json()) + "\n")

    def next_id(self) -> int:
        return max((e.id for e in self.entries), default=-1) + 1

    def replay(self, index: int, options: GenOptions = GenOptions(), steps: int = 5) -> Lineage | None:
        """The entry's lineage; `steps` stands in for the cap of an entry recorded without one."""
        e = self.entries[index]
        choices = decode(e.choices)
        return replay(choices, options, e.cap if e.cap is not None else steps) if choices is not None else None


class LineageCache:
    """The lineages behind corpus entries, by entry id, so extending an entry draws only its new
    steps instead of replaying the whole lineage (follow-up item 1). Entries the loop adds are
    put here as they are found; entries loaded from disk are replayed on first use. Least
    recently used entries are dropped past `capacity`; the loop keeps the capacity above the
    corpus's size (`fit`), since a dropped lineage costs a replay of its whole round (25 ms and
    more) the next time its entry is picked, and one kept costs a few kilobytes: a child shares
    its parent's steps and programs."""

    def __init__(self, capacity: int = 512):
        self.capacity = capacity
        self._lineages: OrderedDict[int, Lineage] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.incremental = 0           # misses (or their ancestors) rebuilt from a parent's lineage

    def fit(self, entries: int) -> None:
        """Room for a corpus of `entries` and the same again as it grows."""
        self.capacity = max(self.capacity, 2 * entries)

    def has(self, entry_id: int) -> bool:
        return entry_id in self._lineages

    def put(self, entry_id: int, lineage: Lineage) -> None:
        self._lineages[entry_id] = lineage
        self._lineages.move_to_end(entry_id)
        while len(self._lineages) > self.capacity:
            self._lineages.popitem(last=False)

    def get(self, entry: Entry, options: GenOptions, steps: int,
            by_id: dict[int, Entry] | None = None) -> Lineage | None:
        """The entry's lineage (None if it no longer decodes). On a miss it is rebuilt from its
        parent's lineage when `by_id` has the parent (itself cached, or rebuilt the same way), so
        only the entry's own round is replayed: a full replay costs about 0.23 s per mutation step,
        and the three-hour campaign's lineages reached 19 steps. Otherwise, or when the entry no
        longer continues its parent's choices, it is replayed from its choices."""
        cached = self._lineages.get(entry.id)
        if cached is not None:
            self.hits += 1
            self._lineages.move_to_end(entry.id)
            return cached
        self.misses += 1
        return self._rebuild(entry, options, steps, by_id or {})

    def _rebuild(self, entry: Entry, options: GenOptions, steps: int, by_id: dict[int, Entry]) -> Lineage | None:
        choices = decode(entry.choices)
        if choices is None:
            return None
        cap = entry.cap if entry.cap is not None else steps
        lineage = None
        parent_entry = by_id.get(entry.parent) if entry.parent is not None else None
        if parent_entry is not None and parent_entry.id != entry.id:
            parent = self._lineages.get(parent_entry.id)
            if parent is None:
                parent = self._rebuild(parent_entry, options, steps, by_id)
            parent_choices = decode(parent_entry.choices) if parent is not None else None
            if parent is not None and parent_choices is not None:
                lineage = continue_lineage(parent, parent_choices, choices, options, cap)
                if lineage is not None:
                    self.incremental += 1
        if lineage is None:
            lineage = replay(choices, options, cap)
        if lineage is not None:
            self.put(entry.id, lineage)
        return lineage


# ------------------------------------------------------------------------------- generator processes
@dataclass
class StepName:
    name: str


@dataclass
class Drawn:
    """A program drawn in a generator process (`fuzz(generators=N)`), as the loop needs it: what
    to run and what to record, without the lineage (which stays in the process that drew it; a
    lineage does not pickle, and the loop only needs one to extend it, which the generators do)."""
    choices: str                       # `encode(choices)`
    source: str
    template: str
    steps: list[StepName]
    instructions: int
    cap: int
    extended: bool                     # drawn from the parent asked for (else a fresh lineage)
    counts: dict[str, int]             # the loop's counters for this draw: extended, fresh, ...
    attempted: dict[str, int]          # mutation steps tried in this draw, per mutator
    rejected: dict[str, int]
    lineage: bytes = b""               # the lineage pickled: the loop keeps it, unopened, for an entry that
                                       # joins the corpus and hands it to the process asked to extend that entry

    @property
    def code(self) -> tuple[Any, ...]:
        """The instructions, replayed: only a finding's report and the tests' runner ask."""
        lineage = replay(decode(self.choices), _GEN["options"], self.cap) if _GEN else None
        return lineage.code if lineage is not None else ()


PICKLED_LINEAGES = 4096                # about 30 KB each; an older entry's lineage is replayed where it is needed
_GEN: dict[str, Any] = {}             # a generator process's state (and the options, in the loop's own process)


def _generator_init(options: GenOptions, steps: int, max_depth: int, corpus_file: str | None) -> None:
    """Runs once in each generator process: what every draw needs, the corpus file to read
    parents from, and this process's own cache of their lineages."""
    _GEN.update(options=options, steps=steps, max_depth=max_depth, file=corpus_file, offset=0, by_id={},
                lineages=LineageCache(4096))


def _generator_entry(entry_id: int) -> Entry | None:
    """A corpus entry by id, reading the lines the loop has appended to corpus.jsonl since this
    process last looked (the loop writes an entry before it asks for a child of it)."""
    by_id: dict[int, Entry] = _GEN["by_id"]
    if entry_id not in by_id and _GEN["file"] and os.path.exists(_GEN["file"]):
        with open(_GEN["file"], "rb") as f:
            f.seek(_GEN["offset"])
            data = f.read()
        end = data.rfind(b"\n") + 1
        _GEN["offset"] += end
        for ln in data[:end].decode("utf-8").splitlines():
            if ln.strip():
                d = json.loads(ln)
                by_id[d["id"]] = Entry(**{k: d[k] for k in Entry.__dataclass_fields__ if k in d})
        _GEN["lineages"].fit(len(by_id))
    return by_id.get(entry_id)


def generate(parent_id: int | None, seed: int, parent_pickle: bytes | None = None) -> Drawn | None:
    """One draw, in a generator process: the corpus entry `parent_id` extended, or (without one,
    or when it cannot be extended) a fresh lineage, from `seed` alone, so the loop's programs do
    not depend on which process drew them. None when the draw was rejected.

    The parent's lineage comes from this process's cache; when it is not there, from
    `parent_pickle` (the lineage as the process that drew it pickled it: 4 ms to load, where
    replaying its round costs 40 ms and its whole history 150), else it is replayed."""
    options, steps, max_depth = _GEN["options"], _GEN["steps"], _GEN["max_depth"]
    rng = Random(seed)
    counts: Counter[str] = Counter()
    drawn = None
    parent_lineage: Lineage | None = None
    if parent_id is not None:
        parent = _generator_entry(parent_id)
        parent_choices = decode(parent.choices) if parent is not None else None
        if parent_choices:
            cache: LineageCache = _GEN["lineages"]
            if parent_pickle and not cache.has(parent_id):
                cache.put(parent_id, pickle.loads(parent_pickle))
            parent_lineage = cache.get(parent, options, steps, _GEN["by_id"])
        if parent_lineage is not None and parent_lineage.depth >= max_depth:
            counts["at-max-depth"] += 1
        else:
            drawn = extend(parent_choices, options, steps, rng, parent_lineage, max_depth=max_depth) if parent_lineage else None
            counts["extended" if drawn else "extend-failed"] += 1
    extended = drawn is not None
    if drawn is None:
        parent_lineage = None
        drawn = draw_lineage(options, steps, rng=rng)
        counts["fresh"] += 1
    if drawn is None:
        return None
    lineage, choices = drawn
    base_attempted = parent_lineage.attempted_by if parent_lineage else {}
    base_rejected = parent_lineage.rejected_by if parent_lineage else {}
    return Drawn(encode(choices), lineage.source, lineage.template, [StepName(s.name) for s in lineage.steps],
                 len(lineage.code), lineage.cap, extended, dict(counts),
                 {n: k - base_attempted.get(n, 0) for n, k in lineage.attempted_by.items()},
                 {n: k - base_rejected.get(n, 0) for n, k in lineage.rejected_by.items()},
                 pickle.dumps(lineage, pickle.HIGHEST_PROTOCOL))


# ------------------------------------------------------------------------------- running one lineage
@dataclass
class Observation:
    """What one run of a lineage's final program showed."""
    verdict: Verdict
    run: Any                          # RunResult (or a stand-in with .results / .to_json)
    new_probes: int = 0
    new_probe_ids: ProbeSet = field(default_factory=dict)
    required_reached: ProbeSet = field(default_factory=dict)
    ms: int = 0                       # run time of the slowest mode
    timed_out: bool = False           # a mode hit the instruction budget, the watchdog or a resource limit


class Runner(Protocol):
    def __call__(self, lineage: Lineage, require: ProbeSet | None = None) -> Observation: ...


def harness_runner(client, language_version: int = 200, verdict: Callable[[Any], Verdict] = compare_run,
                   wall_ms: int = WALL_MS) -> Runner:
    """Runs a lineage's final program in both Rhino modes, asking for new probe ids. `verdict`
    judges the run: the mode comparison, or crossver.cross_verdict for a CrossClient's runs.
    `wall_ms` is the time a mode gets before it counts as timed out."""
    def run(lineage: Lineage, require: ProbeSet | None = None) -> Observation:
        res = client.run(lineage.source, language_version=language_version, seed=1, wall_ms=wall_ms,
                         coverage_bucket=lineage.template, coverage_new_ids=True, coverage_require=require)
        new_ids: ProbeSet = {}
        reached: ProbeSet = {}
        new = 0
        for o in res.results.values():
            new += o.newProbes or 0
            union(new_ids, o.newProbeIds or {})
            union(reached, o.requiredReached or {})
        return Observation(verdict(res), res, new, new_ids, reached,
                           ms=max((o.ms or 0 for o in res.results.values()), default=0),
                           timed_out=any(o.status in SLOW for o in res.results.values()))
    return run


SLOW = ("timeout", "resource")


def speed_weight(ms: int, mean_ms: float) -> float:
    """AFL's exec-time factor in calculate_score: an entry ten times slower than the corpus mean
    is picked a tenth as often, one four times faster three times as often. Entries without a
    recorded time (corpora from before it) count as average."""
    if ms <= 0 or mean_ms <= 0:
        return 1.0
    r = ms / mean_ms
    for over, w in ((10, 0.1), (4, 0.25), (2, 0.5), (1.33, 0.75)):
        if r > over:
            return w
    for under, w in ((0.25, 3.0), (0.33, 2.0), (0.5, 1.5)):
        if r < under:
            return w
    return 1.0


def parent_weights(entries: list[Entry], child_timeouts: Counter[int]) -> list[float]:
    """How likely each entry is to be extended: its speed factor, halved for every child of it
    that timed out this run (to 1/64). On the fresh three-hour campaign of 2026-11-02 the 7 % of
    entries that ran past the watchdog themselves had 84 % of their children time out, 97 % of
    the time spent on children (scripts/entry_speed.py)."""
    timed = [e.ms for e in entries if e.ms > 0]
    mean = sum(timed) / len(timed) if timed else 0.0
    return [speed_weight(e.ms, mean) * 0.5 ** min(child_timeouts[e.id], 6) for e in entries]


def union(into: ProbeSet, more: ProbeSet) -> None:
    for cls, ids in more.items():
        into[cls] = sorted(set(into.get(cls, ())) | set(ids))


def covers(reached: ProbeSet, required: ProbeSet) -> bool:
    return all(set(ids) <= set(reached.get(cls, ())) for cls, ids in required.items())


def probe_count(s: ProbeSet) -> int:
    return sum(len(v) for v in s.values())


# ------------------------------------------------------------------------------- interesting outcomes as exceptions
class FindingError(Exception):
    """Raised by the lineage test on a hard verdict; one subclass per signature so Hypothesis
    shrinks and reports distinct bugs separately (the exception type is the failure's identity)."""
    signature = ""


_FINDING_CLASSES: dict[str, type[FindingError]] = {}


def finding_class(signature: str) -> type[FindingError]:
    cls = _FINDING_CLASSES.get(signature)
    if cls is None:
        import hashlib
        short = hashlib.sha1(signature.encode()).hexdigest()[:10]
        cls = type(f"Finding_{short}", (FindingError,), {"signature": signature})
        _FINDING_CLASSES[signature] = cls
    return cls


class Exhausted(BaseException):
    """Raised by the lineage test once the shrink budget is spent. Not an Exception: Hypothesis
    catches those as test failures and keeps shrinking, this one leaves the engine at once."""


class Reached(Exception):
    """Raised by the compaction test while the lineage still reaches the required probes."""


@dataclass
class Shrinker:
    """Hypothesis's shrinker over a lineage: `shrink(choices)` saves the choices as a failing
    example (in the secondary corpus, so the engine does shrink it) and runs the lineage test
    with phases reuse + shrink; the test runs the program and
    raises the exception `interesting` names. After `budget` calls the test passes
    unconditionally so the run ends (Hypothesis has no public shrink budget); the smallest
    lineage that raised, with its choices and observation, is the result."""
    runner: Runner
    options: GenOptions
    steps: int
    interesting: Callable[[Lineage, Observation], type[Exception] | None]
    budget: int = 150
    calls: int = 0
    best: tuple[Lineage, Choices, Observation] | None = None

    def shrink(self, choices: Choices, cap: int | None = None) -> tuple[Lineage, Choices, Observation] | None:
        """`cap` is the lineage's own cap (G49); `self.steps` stands in when not given."""
        db = InMemoryExampleDatabase()
        db.save(SECONDARY_KEY, choices_to_bytes(choices))
        self.calls = 0
        self.best = None

        @settings(database=db, deadline=None, suppress_health_check=list(HealthCheck), max_examples=1000,
                  phases=(Phase.reuse, Phase.shrink), derandomize=False, print_blob=False, report_multiple_bugs=False)
        @given(evolved(self.options, cap if cap is not None else self.steps))
        def test(l: Lineage) -> None:
            if self.calls >= self.budget:
                # Budget spent: leave the engine now. Returning instead would let Hypothesis go
                # on building candidates (hundreds, 25 ms each) that are never run; `best` is final.
                raise Exhausted()
            self.calls += 1
            obs = self.runner(l)
            exc = self.interesting(l, obs)
            if exc is not None:
                if self.best is None or len(l.code) < len(self.best[0].code):
                    self.best = (l, tuple(current_build_context().data.choices), obs)
                raise exc()

        test._hypothesis_internal_database_key = DATABASE_KEY
        try:
            test()
        except (FindingError, Reached):
            pass
        except Exhausted:                    # budget spent inside the engine
            pass
        except HypothesisException:          # flaky: a once-per-JVM probe or a non-deterministic run
            pass
        return self.best


# ------------------------------------------------------------------------------- the loop
def fuzz(*, corpus_dir: Path, options: GenOptions = GenOptions(), iterations: int | None = None,
         seconds: float | None = None, steps: int = 5, p_new: float = 0.2, seed: int | None = None,
         known: set[str] | None = None, related_index=None, findings_dir: Path = findings.DEFAULT_DIR, shrink_budget: int = 150,
         compact_budget: int = 0, source_shrink_budget: int = 300, language_version: int = 200,
         runner: Runner | None = None, client=None, quiet: bool = False,
         max_depth: int = MAX_DEPTH, verdict: Callable[[Any], Verdict] = compare_run,
         judge_findings: bool = False, pipeline: bool = True, generators: int = 0,
         wall_ms: int = WALL_MS) -> dict[str, Any]:
    """Run the loop. `runner` (or a `HarnessClient` in `client`) runs programs; without either a
    coverage-enabled client is started. `steps` is the mutation steps per round (a fresh lineage
    or one extension), `max_depth` the safety cap on a lineage's depth over all rounds (G49).
    `related_index` (oxpecker.related.KnownIndex) labels each new finding with the regressions it
    probably connects to. `verdict` judges each run (crossver.cross_verdict with a CrossClient).
    With `judge_findings`, each new finding, once shrunk, runs on engine262 too (oxpecker.judge),
    which says which side agrees with the spec. `pipeline` draws the next program while the
    current one runs (see the loop). With `generators` N > 0, N processes draw the programs and
    this one only picks parents, runs, judges and keeps the corpus: drawing is single-threaded
    Python and the slow half of an iteration, while one JVM runs what several processes draw.
    Returns the summary also written to `last-run.json`."""
    rng = Random(seed)
    corpus = Corpus(corpus_dir)
    stats: Counter[str] = Counter()
    sig_counts: Counter[str] = Counter()
    known = set(known or ())
    t0 = time.time()
    last_report = t0
    last_checkpoint = 0.0                    # the first entry found is saved at once
    own_client = None
    if runner is None:
        from .harness.client import HarnessClient
        if client is None:
            own_client = client = HarnessClient(quiet=True, coverage=True)
            client.start()
            if not client.info().get("coverage"):
                client.stop()
                raise RuntimeError("coverage agent not attached; the harness build must include jacocoagent.jar")
        runner = harness_runner(client, language_version, verdict, wall_ms)
    if client is not None:
        client.coverage_checkpoint = str(corpus.exec)
        if corpus.exec.exists():
            covered = client.coverage_load(str(corpus.exec))
            if not quiet:
                print(f"resumed: {len(corpus.entries)} corpus entries, {covered} probes already covered", file=sys.stderr)

    curve: list[dict[str, Any]] = []           # coverage over time (coverage.CURVE), this run's rows
    last_curve = t0
    if client is not None:
        curve.append(coverage.append_row(corpus.dir, t0, 0, len(corpus.entries), client.coverage_count(), 0, start=True))

    lineages = LineageCache()
    lineages.fit(len(corpus.entries))
    pickled: OrderedDict[int, bytes] = OrderedDict()   # with generators: entry id -> its lineage, pickled where it was drawn
    child_timeouts: Counter[int] = Counter()   # entry id -> its children that timed out this run
    weights: list[float] | None = None         # parent_weights, recomputed when either changes
    attempted: Counter[str] = Counter()      # mutation steps tried this run, per mutator (G51)
    rejected: Counter[str] = Counter()       # of those, the ones whose child did not verify
    productive: Counter[str] = Counter()     # steps of the entries added to the corpus this run
    def draw_next() -> tuple[Entry | None, Lineage | None, Lineage, Choices] | None:
        """The next program to run: a corpus entry extended, or a fresh lineage. None when the
        draw was rejected."""
        nonlocal weights
        parent: Entry | None = None
        parent_lineage: Lineage | None = None
        drawn = None
        if corpus.entries and rng.random() > p_new:
            if weights is None:
                weights = parent_weights(corpus.entries, child_timeouts)
            parent = rng.choices(corpus.entries, weights)[0]
            parent_choices = decode(parent.choices)
            parent_lineage = lineages.get(parent, options, steps, corpus.by_id) if parent_choices else None
            if parent_lineage is not None and parent_lineage.depth >= max_depth:
                stats["at-max-depth"] += 1
                drawn = None
            else:
                drawn = extend(parent_choices, options, steps, rng, parent_lineage, max_depth=max_depth) if parent_lineage else None
                stats["extended" if drawn else "extend-failed"] += 1
        if drawn is None:
            parent = None
            drawn = draw_lineage(options, steps, rng=rng)
            stats["fresh"] += 1
        if drawn is None:
            stats["rejected"] += 1
            return None
        lineage, choices = drawn
        base_lineage = parent_lineage if parent is not None else None
        for name, n in lineage.attempted_by.items():
            attempted[name] += n - (base_lineage.attempted_by.get(name, 0) if base_lineage else 0)
        for name, n in lineage.rejected_by.items():
            rejected[name] += n - (base_lineage.rejected_by.get(name, 0) if base_lineage else 0)
        return parent, base_lineage, lineage, choices

    def process(job: tuple[Entry | None, Lineage | None, Lineage, Choices], obs: Observation) -> None:
        """What a run showed: the verdict counted, a finding shrunk and recorded, new probes into
        the corpus."""
        nonlocal weights, last_report, last_checkpoint, last_curve
        parent, base_lineage, lineage, choices = job
        v = obs.verdict
        stats[v.kind] += 1
        if obs.timed_out and parent is not None:
            child_timeouts[parent.id] += 1
            weights = None
        if v.kind in (SOFT, JAVA_TYPES):
            findings.record(lineage, obs.run, v, out_dir=findings_dir)
        elif v.kind == HARD:
            sig = v.signature()
            sig_counts[sig] += 1
            if sig in known:
                findings.record(lineage, obs.run, v, out_dir=findings_dir)
            else:
                known.add(sig)
                stats["new-findings"] += 1
                # Shrink towards this finding's coarse signature, not "any unknown hard
                # verdict": the signature was just added to `known`, so the loop's own
                # predicate would reject every candidate (including the replay of the
                # finding itself) and Hypothesis would never start shrinking.
                coarse = v.coarse_signature()

                def same_bug(l: Lineage, o: Observation, coarse: str = coarse) -> type[Exception] | None:
                    return finding_class(coarse) if o.verdict.kind == HARD and o.verdict.coarse_signature() == coarse else None

                shrinker = Shrinker(runner, options, steps, same_bug, budget=shrink_budget)
                small = shrinker.shrink(choices, lineage.cap) or (lineage, choices, obs)
                record_as, rec_run, rec_verdict = small[0], small[2].run, small[2].verdict
                if client is not None and source_shrink_budget:
                    # then statement-level deletion on the lifted source, which gets much further
                    # than choice shrinking within a budget (proposal §3.7)
                    shrunk = shrink_lifted(client, small[0].source, rec_verdict.coarse_signature(),
                                           language_version, source_shrink_budget, verdict)
                    if shrunk is not None:
                        record_as, rec_run, rec_verdict = shrunk
                related = []
                if related_index is not None:
                    from .related import Fingerprint
                    related = related_index.related(Fingerprint.of("finding", record_as.source, rec_run, rec_verdict))
                judgement = None
                if judge_findings:
                    from . import judge
                    judgement = judge.judge(record_as.source, rec_run)
                    stats[f"judged-{judgement.label}"] += 1
                path = findings.record(record_as, rec_run, rec_verdict, out_dir=findings_dir,
                                       reproducer=True, replay_cmd="oxpecker diff", related=related,
                                       judgement=judgement)
                if parent is not None:
                    parent.found.append(sig)
                if not quiet:
                    print(f"[finding] {rec_verdict.pretty()[:160]}\n          -> {path} "
                          f"({len(record_as.source.splitlines())} lines; lineage shrink {len(lineage.code)} -> "
                          f"{len(small[0].code)} instructions, {len(lineage.steps)} -> {len(small[0].steps)} steps "
                          f"in {shrinker.calls} runs; source shrink {len(small[0].source.splitlines())} -> "
                          f"{len(record_as.source.splitlines())} lines)", file=sys.stderr)
                    if judgement is not None:
                        print(f"          engine262: {judgement.label}: {judgement.summary}", file=sys.stderr)
                    for r in related:
                        print(f"          related: {r}", file=sys.stderr)
        if obs.new_probes > 0 and obs.timed_out:
            # a hang is not a parent (AFL keeps them out of its queue): its children inherit the
            # runaway, and one past the watchdog costs the wall limit plus a JVM restart
            stats["timed-out-not-added"] += 1
        elif obs.new_probes > 0:
            entry = (choices, lineage, obs.new_probe_ids)
            if compact_budget and obs.new_probe_ids:
                compacted = compact(runner, options, lineage.cap, choices, obs.new_probe_ids, compact_budget)
                if compacted is not None:
                    entry = compacted
                    stats["compacted"] += 1
            e_choices, e_lineage, probes = entry
            new_entry = Entry(corpus.next_id(), encode(e_choices), e_lineage.template, [s.name for s in e_lineage.steps],
                              obs.new_probes, probes, parent.id if parent else None,
                              e_lineage.instructions if isinstance(e_lineage, Drawn) else len(e_lineage.code),
                              cap=e_lineage.cap, ms=obs.ms)
            corpus.add(new_entry)
            weights = None
            if isinstance(e_lineage, Lineage):
                if len(corpus.entries) >= lineages.capacity:
                    lineages.fit(len(corpus.entries))
                lineages.put(new_entry.id, e_lineage)
            elif e_lineage.lineage:                          # with generators the lineages live in their processes;
                pickled[new_entry.id] = e_lineage.lineage    # the loop keeps the newest entries' pickled, unopened
                while len(pickled) > PICKLED_LINEAGES:
                    pickled.popitem(last=False)
            for step in e_lineage.steps[len(base_lineage.steps) if base_lineage else 0:]:
                productive[step.name] += 1
            stats["corpus-added"] += 1
            stats["new-probes"] += obs.new_probes
            if client is not None and time.time() - last_checkpoint >= CHECKPOINT_SECONDS:
                # The checkpoint a restarted JVM resumes from (HarnessClient.coverage_checkpoint).
                # Not after every entry: a save is 30 to 50 ms a JVM, an eighth of the loop's time
                # once several processes draw. A JVM that restarts takes the probes found since
                # the last one for new again, which costs a few entries the corpus already has.
                client.coverage_save(str(corpus.exec))
                last_checkpoint = time.time()
        if client is not None and time.time() - last_curve >= coverage.CURVE_EVERY_S:
            last_curve = time.time()
            curve.append(coverage.append_row(corpus.dir, t0, i, len(corpus.entries), client.coverage_count(),
                                             stats["new-findings"]))
        if not quiet and time.time() - last_report > 10:
            last_report = time.time()
            rate = i / (last_report - t0)
            print(f"{i:7} runs {rate:5.1f}/s  corpus {len(corpus.entries)}  new probes {stats['new-probes']}  "
                  f"findings {stats['new-findings']}  " + ", ".join(f"{k}={n}" for k, n in sorted(stats.items())
                                                                 if k in ("equal", "hard", "java-types", "soft", "fixed", "rejected")),
                  file=sys.stderr)

    # With `pipeline`, a program runs on a thread (which waits on the JVM's pipe) while the next
    # one is drawn here: an iteration costs the longer of drawing and running instead of their
    # sum. One request is in flight at most, and the run is processed (shrinking included, which
    # uses the client itself) before the next is submitted; the price is that the next program's
    # parent is picked from the corpus as it was before the run in flight.
    pool = ThreadPoolExecutor(1, thread_name_prefix="oxpecker-run") if pipeline and not generators else None
    pending: tuple[Any, Any] | None = None
    drawers: ProcessPoolExecutor | None = None
    i = 0
    try:
        if generators:
            # Generator processes: this loop picks the parent and a seed, a process draws the
            # program (`generate`), and the results are taken in the order they were asked for,
            # so a run with a seed is the same whichever process answers first. `2 * generators`
            # draws are kept in flight; their parents were picked before the runs ahead of them
            # were processed, as with `pipeline`.
            _GEN["options"] = options                      # for Drawn.code in this process
            drawers = ProcessPoolExecutor(generators, initializer=_generator_init,
                                          initargs=(options, steps, max_depth, str(corpus.file)))
            asked: deque[tuple[Entry | None, Any]] = deque()
            while True:
                while len(asked) < 2 * generators and not (iterations is not None and i >= iterations) \
                        and not (seconds is not None and time.time() - t0 >= seconds):
                    i += 1
                    parent = None
                    if corpus.entries and rng.random() > p_new:
                        if weights is None:
                            weights = parent_weights(corpus.entries, child_timeouts)
                        parent = rng.choices(corpus.entries, weights)[0]
                    asked.append((parent, drawers.submit(generate, parent.id if parent else None, rng.getrandbits(64),
                                                         pickled.get(parent.id) if parent else None)))
                if not asked:
                    break
                parent, future = asked.popleft()
                drawn = future.result()
                if drawn is None:
                    stats["fresh"] += 1
                    stats["rejected"] += 1
                    continue
                stats.update(drawn.counts)
                attempted.update(drawn.attempted)
                rejected.update(drawn.rejected)
                if not drawn.extended:
                    parent = None
                process((parent, parent, drawn, decode(drawn.choices)), runner(drawn))
        while not generators:
            if iterations is not None and i >= iterations:
                break
            if seconds is not None and time.time() - t0 >= seconds:
                break
            i += 1
            job = draw_next()
            if pending is not None:
                process(pending[0], pending[1].result())
                pending = None
            if job is None:
                continue
            if pool is not None:
                pending = (job, pool.submit(runner, job[2]))
            else:
                process(job, runner(job[2]))
        if pending is not None:
            process(pending[0], pending[1].result())
            pending = None
    finally:
        if pool is not None:
            pool.shutdown(wait=True)             # no request in flight when the coverage is saved
        if drawers is not None:
            drawers.shutdown(wait=False, cancel_futures=True)
        report: dict[str, Any] = {}
        if client is not None:
            covered = client.coverage_save(str(corpus.exec))
            curve.append(coverage.append_row(corpus.dir, t0, i, len(corpus.entries), covered, stats["new-findings"]))
            try:
                report = client.coverage_report()
            except Exception:
                report = {}
        if own_client is not None:
            own_client.stop()
    stats["lineage-cache-hits"] = lineages.hits
    stats["lineage-cache-misses"] = lineages.misses
    stats["lineage-rebuilt-from-parent"] = lineages.incremental
    stats["steps-attempted"] = sum(attempted.values())
    stats["steps-rejected"] = sum(rejected.values())
    mutator_stats = {name: {"attempted": attempted[name], "rejected": rejected[name], "corpus": productive[name]}
                     for name in sorted(set(attempted) | set(productive))}
    summary = {"runs": i, "seconds": time.time() - t0, "corpus": len(corpus.entries), "stats": dict(stats),
               "signatures": dict(sig_counts), "coveredProbes": report.get("coveredProbes"),
               "totalProbes": report.get("totalProbes"), "steps": steps, "max_depth": max_depth, "p_new": p_new,
               "max_entry_depth": max((len(e.steps) for e in corpus.entries), default=0),
               "mutators": mutator_stats,
               "coverage": coverage.summarize(report, curve) if report else None}
    (corpus.dir / "last-run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


@dataclass
class Reproducer:
    """A source-shrunk finding: what `findings.record` needs."""
    source: str
    template: str

    @property
    def plain_source(self) -> str:
        return "\n".join(ln for ln in self.source.splitlines() if not ln.lstrip().startswith("__probe(")) + "\n"


def shrink_lifted(client, source: str, coarse: str, language_version: int, budget: int,
                  verdict: Callable[[Any], Verdict] = compare_run):
    """Statement-level delta debugging (`shrink.shrink_source`) while the coarse signature holds;
    returns (Reproducer, RunResult, Verdict) or None if nothing could go."""
    from .shrink import shrink_source

    def verdict_of(src: str):
        return verdict(client.run(src, language_version=language_version, seed=1))

    small = shrink_source(source, lambda src: verdict_of(src).coarse_signature() == coarse, budget=budget)
    if small == source:
        return None
    run = client.run(small, language_version=language_version, seed=1)
    v = verdict(run)
    if v.coarse_signature() != coarse:
        return None
    return Reproducer(small, "shrunk"), run, v


def compact(runner: Runner, options: GenOptions, steps: int, choices: Choices, new_ids: ProbeSet,
            budget: int) -> tuple[Choices, Lineage, ProbeSet] | None:
    """Shrink on insert (proposal §5.3): re-run the entry asking for its new probes and keep the
    subset it reaches again (once-per-JVM probes drop out, D36), then let Hypothesis shrink the
    lineage while it still reaches that subset. `steps` is the lineage's cap (G49).
    Returns (choices, lineage, probes) or None."""
    lineage = replay(choices, options, steps)
    if lineage is None:
        return None
    again = runner(lineage, require=new_ids).required_reached
    if probe_count(again) == 0:
        return None

    def still_reaches(lin: Lineage, obs: Observation) -> type[Exception] | None:
        return Reached if covers(obs.required_reached, again) else None

    def run_requiring(lin: Lineage, require: ProbeSet | None = None) -> Observation:
        return runner(lin, require=again)

    best = Shrinker(run_requiring, options, steps, still_reaches, budget=budget).shrink(choices)
    if best is None:
        return None
    small, small_choices, _ = best
    return small_choices, small, again
