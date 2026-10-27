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
import random
import sys
import time
from collections import Counter, OrderedDict
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
from . import findings
from .harness.compare import HARD, JAVA_TYPES, SOFT, Verdict, compare_run
from .il.mutate import Lineage, evolved
from .il.options import GenOptions
from .il.strategies import seeded_data

ProbeSet = dict[str, list[int]]
Choices = tuple[Any, ...]
MAX_DEPTH = 20            # safety cap on a lineage's depth across extension rounds (G49): the choice
                          # sequence grows by a few hundred bytes per step against Hypothesis's 8 KiB
DATABASE_KEY = b"oxpecker.fuzz.lineage"      # the lineage test's database key for the shrink runs
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

    def add(self, e: Entry) -> None:
        self.entries.append(e)
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
    recently used entries are dropped past `capacity`."""

    def __init__(self, capacity: int = 512):
        self.capacity = capacity
        self._lineages: OrderedDict[int, Lineage] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def put(self, entry_id: int, lineage: Lineage) -> None:
        self._lineages[entry_id] = lineage
        self._lineages.move_to_end(entry_id)
        while len(self._lineages) > self.capacity:
            self._lineages.popitem(last=False)

    def get(self, entry: Entry, options: GenOptions, steps: int) -> Lineage | None:
        """The entry's lineage, replayed from its choices on a miss (None if it no longer decodes)."""
        cached = self._lineages.get(entry.id)
        if cached is not None:
            self.hits += 1
            self._lineages.move_to_end(entry.id)
            return cached
        self.misses += 1
        choices = decode(entry.choices)
        cap = entry.cap if entry.cap is not None else steps
        lineage = replay(choices, options, cap) if choices is not None else None
        if lineage is not None:
            self.put(entry.id, lineage)
        return lineage


# ------------------------------------------------------------------------------- running one lineage
@dataclass
class Observation:
    """What one run of a lineage's final program showed."""
    verdict: Verdict
    run: Any                          # RunResult (or a stand-in with .results / .to_json)
    new_probes: int = 0
    new_probe_ids: ProbeSet = field(default_factory=dict)
    required_reached: ProbeSet = field(default_factory=dict)


class Runner(Protocol):
    def __call__(self, lineage: Lineage, require: ProbeSet | None = None) -> Observation: ...


def harness_runner(client, language_version: int = 200) -> Runner:
    """Runs a lineage's final program in both Rhino modes, asking for new probe ids."""
    def run(lineage: Lineage, require: ProbeSet | None = None) -> Observation:
        res = client.run(lineage.source, language_version=language_version, seed=1,
                         coverage_bucket=lineage.template, coverage_new_ids=True, coverage_require=require)
        new_ids: ProbeSet = {}
        reached: ProbeSet = {}
        new = 0
        for o in res.results.values():
            new += o.newProbes or 0
            union(new_ids, o.newProbeIds or {})
            union(reached, o.requiredReached or {})
        return Observation(compare_run(res), res, new, new_ids, reached)
    return run


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
                return
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
        except HypothesisException:          # flaky: a once-per-JVM probe or a non-deterministic run
            pass
        return self.best


# ------------------------------------------------------------------------------- the loop
def fuzz(*, corpus_dir: Path, options: GenOptions = GenOptions(), iterations: int | None = None,
         seconds: float | None = None, steps: int = 5, p_new: float = 0.2, seed: int | None = None,
         known: set[str] | None = None, findings_dir: Path = findings.DEFAULT_DIR, shrink_budget: int = 150,
         compact_budget: int = 0, source_shrink_budget: int = 300, language_version: int = 200,
         runner: Runner | None = None, client=None, save_every: int = 100, quiet: bool = False,
         max_depth: int = MAX_DEPTH) -> dict[str, Any]:
    """Run the loop. `runner` (or a `HarnessClient` in `client`) runs programs; without either a
    coverage-enabled client is started. `steps` is the mutation steps per round (a fresh lineage
    or one extension), `max_depth` the safety cap on a lineage's depth over all rounds (G49).
    Returns the summary also written to `last-run.json`."""
    rng = Random(seed)
    corpus = Corpus(corpus_dir)
    stats: Counter[str] = Counter()
    sig_counts: Counter[str] = Counter()
    known = set(known or ())
    t0 = time.time()
    last_report = t0
    own_client = None
    if runner is None:
        from .harness.client import HarnessClient
        if client is None:
            own_client = client = HarnessClient(quiet=True, coverage=True)
            client.start()
            if not client.info().get("coverage"):
                client.stop()
                raise RuntimeError("coverage agent not attached; the harness build must include jacocoagent.jar")
        runner = harness_runner(client, language_version)
        if corpus.exec.exists():
            covered = client.coverage_load(str(corpus.exec))
            if not quiet:
                print(f"resumed: {len(corpus.entries)} corpus entries, {covered} probes already covered", file=sys.stderr)

    def is_new_hard(l: Lineage, obs: Observation) -> type[Exception] | None:
        v = obs.verdict
        return finding_class(v.signature()) if v.kind == HARD and v.signature() not in known else None

    shrinker = Shrinker(runner, options, steps, is_new_hard, budget=shrink_budget)
    lineages = LineageCache()
    attempted: Counter[str] = Counter()      # mutation steps tried this run, per mutator (G51)
    rejected: Counter[str] = Counter()       # of those, the ones whose child did not verify
    productive: Counter[str] = Counter()     # steps of the entries added to the corpus this run
    i = 0
    try:
        while True:
            if iterations is not None and i >= iterations:
                break
            if seconds is not None and time.time() - t0 >= seconds:
                break
            i += 1
            parent: Entry | None = None
            parent_lineage: Lineage | None = None
            drawn = None
            if corpus.entries and rng.random() > p_new:
                parent = rng.choice(corpus.entries)
                parent_choices = decode(parent.choices)
                parent_lineage = lineages.get(parent, options, steps) if parent_choices else None
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
                continue
            lineage, choices = drawn
            base_lineage = parent_lineage if parent is not None else None
            for name, n in lineage.attempted_by.items():
                attempted[name] += n - (base_lineage.attempted_by.get(name, 0) if base_lineage else 0)
            for name, n in lineage.rejected_by.items():
                rejected[name] += n - (base_lineage.rejected_by.get(name, 0) if base_lineage else 0)
            obs = runner(lineage)
            v = obs.verdict
            stats[v.kind] += 1
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
                    small = shrinker.shrink(choices, lineage.cap) or (lineage, choices, obs)
                    record_as, rec_run, rec_verdict = small[0], small[2].run, small[2].verdict
                    if client is not None and source_shrink_budget:
                        # then statement-level deletion on the lifted source, which gets much further
                        # than choice shrinking within a budget (proposal §3.7)
                        shrunk = shrink_lifted(client, small[0].source, rec_verdict.coarse_signature(),
                                               language_version, source_shrink_budget)
                        if shrunk is not None:
                            record_as, rec_run, rec_verdict = shrunk
                    path = findings.record(record_as, rec_run, rec_verdict, out_dir=findings_dir,
                                           reproducer=True, replay_cmd="oxpecker diff")
                    if parent is not None:
                        parent.found.append(sig)
                    if not quiet:
                        print(f"[finding] {rec_verdict.pretty()[:160]}\n          -> {path} "
                              f"({len(record_as.source.splitlines())} lines, {len(small[0].steps)} lineage steps, "
                              f"{shrinker.calls} shrink runs)", file=sys.stderr)
            if obs.new_probes > 0:
                entry = (choices, lineage, obs.new_probe_ids)
                if compact_budget and obs.new_probe_ids:
                    compacted = compact(runner, options, lineage.cap, choices, obs.new_probe_ids, compact_budget)
                    if compacted is not None:
                        entry = compacted
                        stats["compacted"] += 1
                e_choices, e_lineage, probes = entry
                new_entry = Entry(corpus.next_id(), encode(e_choices), e_lineage.template, [s.name for s in e_lineage.steps],
                                  obs.new_probes, probes, parent.id if parent else None, len(e_lineage.code),
                                  cap=e_lineage.cap)
                corpus.add(new_entry)
                lineages.put(new_entry.id, e_lineage)
                for step in e_lineage.steps[len(base_lineage.steps) if base_lineage else 0:]:
                    productive[step.name] += 1
                stats["corpus-added"] += 1
                stats["new-probes"] += obs.new_probes
            if client is not None and i % save_every == 0:
                client.coverage_save(str(corpus.exec))
            if not quiet and time.time() - last_report > 10:
                last_report = time.time()
                rate = i / (last_report - t0)
                print(f"{i:7} runs {rate:5.1f}/s  corpus {len(corpus.entries)}  new probes {stats['new-probes']}  "
                      f"findings {stats['new-findings']}  " + ", ".join(f"{k}={n}" for k, n in sorted(stats.items())
                                                                     if k in ("equal", "hard", "java-types", "soft", "rejected")),
                      file=sys.stderr)
    finally:
        report: dict[str, Any] = {}
        if client is not None:
            client.coverage_save(str(corpus.exec))
            try:
                report = client.coverage_report()
            except Exception:
                report = {}
        if own_client is not None:
            own_client.stop()
    stats["lineage-cache-hits"] = lineages.hits
    stats["lineage-cache-misses"] = lineages.misses
    stats["steps-attempted"] = sum(attempted.values())
    stats["steps-rejected"] = sum(rejected.values())
    mutator_stats = {name: {"attempted": attempted[name], "rejected": rejected[name], "corpus": productive[name]}
                     for name in sorted(set(attempted) | set(productive))}
    summary = {"runs": i, "seconds": time.time() - t0, "corpus": len(corpus.entries), "stats": dict(stats),
               "signatures": dict(sig_counts), "coveredProbes": report.get("coveredProbes"),
               "totalProbes": report.get("totalProbes"), "steps": steps, "max_depth": max_depth, "p_new": p_new,
               "max_entry_depth": max((len(e.steps) for e in corpus.entries), default=0),
               "mutators": mutator_stats}
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


def shrink_lifted(client, source: str, coarse: str, language_version: int, budget: int):
    """Statement-level delta debugging (`shrink.shrink_source`) while the coarse signature holds;
    returns (Reproducer, RunResult, Verdict) or None if nothing could go."""
    from .shrink import shrink_source

    def verdict_of(src: str):
        return compare_run(client.run(src, language_version=language_version, seed=1))

    small = shrink_source(source, lambda src: verdict_of(src).coarse_signature() == coarse, budget=budget)
    if small == source:
        return None
    run = client.run(small, language_version=language_version, seed=1)
    v = compare_run(run)
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
