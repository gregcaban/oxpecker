"""The differential-testing loop without Rhino: a fake runner with a planted bug and planted
probes drives corpus growth, finding detection, shrinking and compaction. One slow test runs the
real harness for a few iterations."""

from __future__ import annotations

import json
import zlib
from collections import Counter
from pathlib import Path
from random import Random

import pytest

from oxpecker.fuzz import (Corpus, Entry, LineageCache, Observation, Shrinker, compact, decode, draw_lineage,
                           encode, extend, fuzz, parent_weights, replay, speed_weight)
from oxpecker.harness.compare import DISCARD, EQUAL, HARD, Verdict
from oxpecker.il import ops
from oxpecker.il.mutate import Lineage
from oxpecker.il.options import GenOptions
from oxpecker.il.program import signature

OPTIONS = GenOptions(max_statements=5, max_depth=2)
STEPS = 4


class FakeRun:
    """Stands in for a RunResult in findings.record."""
    results: dict = {}

    def to_json(self):
        return {"results": {}}


class FakeRunner:
    """Probes = the operation classes a program uses (new ones are new probes, cumulatively, like
    the JVM's store). The planted bug: a compound assignment anywhere in the program (about one
    lineage in five)."""

    def __init__(self):
        self.seen: set[str] = set()
        self.calls = 0

    def __call__(self, lineage: Lineage, require=None) -> Observation:
        self.calls += 1
        code = lineage.code
        names = {type(i.op).__name__ for i in code}
        ids = {"ops": sorted(hash(n) % 100000 for n in names)}
        new = {n for n in names if n not in self.seen}
        self.seen |= new
        new_ids = {"ops": sorted(hash(n) % 100000 for n in new)} if new else {}
        reached = {}
        if require:
            reached = {cls: sorted(set(v) & set(ids.get(cls, ()))) for cls, v in require.items()}
        bug = any(isinstance(i.op, ops.Update) for i in code)
        verdict = Verdict(HARD, ["planted: a compound assignment"]) if bug else Verdict(EQUAL)
        return Observation(verdict, FakeRun(), len(new), new_ids, reached)


class HangingRunner(FakeRunner):
    """FakeRunner where one program in three (by a checksum of its text) runs past the watchdog:
    slow, timed out. Not tied to an operation: the shrinker's runs are not seeded, and when one of
    them is the first to use it, the loop's own runs of it bring no new probes."""

    @staticmethod
    def hangs(source: str) -> bool:
        return zlib.crc32(source.encode()) % 3 == 0

    def __call__(self, lineage: Lineage, require=None) -> Observation:
        obs = super().__call__(lineage, require)
        if self.hangs(lineage.source):
            obs.verdict, obs.ms, obs.timed_out = Verdict(DISCARD, ["both timeout"]), 8000, True
        else:
            obs.ms = 10 + len(lineage.code)
        return obs


def test_draw_replay_and_extend_round_trip():
    drawn = draw_lineage(OPTIONS, STEPS, seed=3)
    assert drawn is not None
    lineage, choices = drawn
    assert choices[-1] is False                         # a complete lineage ends with "no more"
    again = replay(choices, OPTIONS, STEPS)
    assert again is not None and signature(again.code) == signature(lineage.code)
    assert decode(encode(choices)) == choices
    longer = extend(choices, OPTIONS, STEPS, Random(7))
    assert longer is not None
    child, child_choices = longer
    assert signature(child.base.code) == signature(lineage.base.code)
    assert child_choices[: len(choices) - 1] == choices[:-1]
    assert child.depth > lineage.depth
    assert [s.name for s in child.steps[: len(lineage.steps)]] == [s.name for s in lineage.steps]


@pytest.mark.parametrize("seed", range(12))
def test_extension_draws_only_the_new_steps_and_replays_identically(seed: int):
    """Follow-up items 1 and 2 (G48, G49): `extend` continues the cached parent lineage instead
    of replaying it, raises the cap by `steps` for the round, and stores exactly the choices a
    full replay with the child's cap consumes: replaying gives the same program, steps,
    rejected count and cap."""
    drawn = draw_lineage(OPTIONS, STEPS, seed=seed)
    assert drawn is not None
    parent, parent_choices = drawn
    assert parent.cap == STEPS and parent.at_cap == (parent_choices[-1] is not False or parent.depth >= STEPS)
    longer = extend(parent_choices, OPTIONS, STEPS, Random(seed), parent)
    assert longer is not None
    child, child_choices = longer
    assert child.cap == parent.depth + STEPS and child.depth > parent.depth
    kept = len(parent_choices) if parent.at_cap else len(parent_choices) - 1
    assert child_choices[:kept] == parent_choices[:kept] and child_choices[kept] is True
    again = replay(child_choices, OPTIONS, child.cap)
    assert again is not None
    assert signature(again.code) == signature(child.code)
    assert [s.name for s in again.steps] == [s.name for s in child.steps]
    assert again.rejected == child.rejected and again.cap == child.cap
    # without the parent in hand, extend replays it (from its own cap) and draws the same new steps
    same = extend(parent_choices, OPTIONS, STEPS, Random(seed), cap=parent.cap)
    assert same is not None and same[1] == child_choices
    # the safety cap on depth makes an entry a dead end, nothing else does
    assert extend(parent_choices, OPTIONS, STEPS, Random(seed), parent, max_depth=parent.depth) is None
    # the parent lineage itself is untouched
    assert parent.depth == len(parent.steps) + parent.rejected and parent.cap == STEPS


def test_a_parent_that_hit_its_cap_extends_round_after_round():
    """Before G49 an entry at the cap could not be extended (no stop flag to flip). Now each
    round adds up to `steps` more, until `max_depth`."""
    for seed in range(80):
        drawn = draw_lineage(OPTIONS, STEPS, seed=seed)
        if drawn is not None and drawn[0].at_cap:
            break
    else:
        pytest.fail("no lineage reached the cap in 80 seeds")
    parent, choices = drawn
    assert parent.depth == STEPS and choices[-1] is not False or parent.rejected
    depths = [parent.depth]
    for round_ in range(3):
        longer = extend(choices, OPTIONS, STEPS, Random(round_), parent, max_depth=3 * STEPS)
        assert longer is not None, f"round {round_} refused at depth {parent.depth}"
        parent, choices = longer
        depths.append(parent.depth)
        again = replay(choices, OPTIONS, parent.cap)
        assert again is not None and signature(again.code) == signature(parent.code)
    assert depths == sorted(depths) and depths[-1] > STEPS
    assert extend(choices, OPTIONS, STEPS, Random(9), parent, max_depth=parent.depth) is None


def test_lineage_cache_serves_added_entries_and_replays_loaded_ones():
    cache = LineageCache(capacity=2)
    lineages = []
    for seed in range(3):
        lineage, choices = draw_lineage(OPTIONS, STEPS, seed=seed)
        entry = Entry(seed, encode(choices), lineage.template, [s.name for s in lineage.steps], 0)
        lineages.append((entry, lineage))
    cache.put(0, lineages[0][1])
    assert cache.get(lineages[0][0], OPTIONS, STEPS) is lineages[0][1] and cache.hits == 1
    loaded = cache.get(lineages[1][0], OPTIONS, STEPS)              # a miss: replayed from the choices
    assert cache.misses == 1 and loaded is not None
    assert signature(loaded.code) == signature(lineages[1][1].code)
    assert cache.get(lineages[1][0], OPTIONS, STEPS) is loaded      # now cached
    cache.get(lineages[2][0], OPTIONS, STEPS)                        # evicts the least recently used (0)
    assert cache.get(lineages[0][0], OPTIONS, STEPS) is not lineages[0][1] and cache.misses == 3


def test_a_missed_entry_is_rebuilt_from_its_parent_exactly_as_a_full_replay(tmp_path: Path):
    """A cache miss replays only the entry's own round on top of its parent's lineage; on the
    three-hour corpus a full replay cost about 0.23 s per mutation step (lineages to 19 steps)."""
    fuzz(corpus_dir=tmp_path / "corpus", options=OPTIONS, iterations=150, steps=STEPS, seed=4, runner=FakeRunner(),
         findings_dir=tmp_path / "findings", shrink_budget=5, quiet=True, pipeline=False)   # this seed's corpus, drawn in step
    corpus = Corpus(tmp_path / "corpus")
    children = [e for e in corpus.entries if e.parent is not None]
    assert len(children) >= 5 and max(len(e.steps) for e in children) > STEPS      # more than one round deep
    cache = LineageCache(capacity=4)                       # small: ancestors get evicted and rebuilt too
    for e in reversed(corpus.entries):
        rebuilt = cache.get(e, OPTIONS, STEPS, corpus.by_id)
        full = replay(decode(e.choices), OPTIONS, e.cap)
        assert rebuilt is not None and full is not None
        assert rebuilt.source == full.source and rebuilt.depth == full.depth and rebuilt.at_cap == full.at_cap
        assert [s.name for s in rebuilt.steps] == e.steps
    assert cache.incremental >= len(children)


def test_speed_weight_is_afls_exec_time_factor():
    assert speed_weight(1100, 100) == 0.1 and speed_weight(500, 100) == 0.25 and speed_weight(150, 100) == 0.75
    assert speed_weight(100, 100) == 1.0 and speed_weight(20, 100) == 3.0 and speed_weight(40, 100) == 1.5
    assert speed_weight(0, 100) == 1.0                      # no time recorded (older corpora): average


def test_parents_whose_children_time_out_are_picked_less():
    es = [Entry(i, "", "t", [], 1, ms=20) for i in range(3)]
    w = parent_weights(es, Counter({1: 2, 2: 10}))
    assert w[0] == 1.0 and w[1] == 0.25 and w[2] == 0.5 ** 6


def test_a_timed_out_run_is_not_added_to_the_corpus(tmp_path: Path):
    """AFL keeps hangs out of its queue: a hang's children inherit the runaway. On the fresh
    three-hour campaign of 2026-11-02 the 7 % of entries past the watchdog took 97 % of the time."""
    summary = fuzz(corpus_dir=tmp_path / "corpus", options=OPTIONS, iterations=150, steps=STEPS, seed=5,
                   runner=HangingRunner(), findings_dir=tmp_path / "findings", shrink_budget=5, quiet=True)
    corpus = Corpus(tmp_path / "corpus")
    assert summary["stats"]["timed-out-not-added"] > 0
    assert not any(HangingRunner.hangs(replay(decode(e.choices), OPTIONS, e.cap).source) for e in corpus.entries)
    assert all(0 < e.ms < 8000 for e in corpus.entries)
    assert all(0 < e.ms < 8000 for e in corpus.entries)


def test_cmin_builds_every_entry_depth_first_exactly_as_a_full_replay(tmp_path: Path):
    """cmin visits the lineage tree depth first so a child is built from its parent's lineage
    while holding only the ancestors; in corpus order a parent is usually thousands back."""
    from oxpecker.cmin import _lineages_depth_first
    fuzz(corpus_dir=tmp_path / "corpus", options=OPTIONS, iterations=150, steps=STEPS, seed=4, runner=FakeRunner(),
         findings_dir=tmp_path / "findings", shrink_budget=5, quiet=True)
    corpus = Corpus(tmp_path / "corpus")
    built = list(_lineages_depth_first(corpus, OPTIONS, STEPS))
    assert sorted(e.id for e, *_ in built) == sorted(e.id for e in corpus.entries)
    for e, lineage, _, _ in built:
        full = replay(decode(e.choices), OPTIONS, e.cap)
        assert lineage is not None and full is not None and lineage.source == full.source
    assert sum(rebuilt for *_, rebuilt in built) == sum(e.parent is not None for e in corpus.entries)


def test_loop_grows_the_corpus_and_finds_and_shrinks_the_planted_bug(tmp_path: Path):
    runner = FakeRunner()
    corpus_dir = tmp_path / "corpus"
    findings_dir = tmp_path / "findings"
    summary = fuzz(corpus_dir=corpus_dir, options=OPTIONS, iterations=80, steps=STEPS, seed=1, runner=runner,
                   findings_dir=findings_dir, shrink_budget=40, quiet=True)
    assert summary["runs"] == 80
    corpus = Corpus(corpus_dir)
    assert corpus.entries, "nothing reached new probes"
    assert summary["stats"]["corpus-added"] == len(corpus.entries)
    assert summary["stats"].get("extended", 0) > 0
    assert summary["stats"]["lineage-cache-misses"] == 0        # every entry was put on add
    assert summary["stats"]["lineage-cache-hits"] >= summary["stats"]["extended"]
    assert all(e.cap is not None and e.cap >= STEPS for e in corpus.entries)   # G49: the cap is recorded
    assert summary["max_entry_depth"] >= 0 and summary["max_depth"] == 20
    m = summary["mutators"]                                     # G51: per-mutator attempts/rejections/productive steps
    assert m and summary["stats"]["steps-attempted"] == sum(v["attempted"] for v in m.values()) > 0
    assert summary["stats"]["steps-rejected"] == sum(v["rejected"] for v in m.values())
    assert all(v["rejected"] <= v["attempted"] for v in m.values())
    assert sum(v["corpus"] for v in m.values()) <= sum(len(e.steps) for e in corpus.entries)
    first = corpus.replay(0, OPTIONS, STEPS)
    assert first is not None and first.template == corpus.entries[0].template
    assert (corpus_dir / "last-run.json").exists()
    assert summary["stats"].get("new-findings", 0) >= 1, summary
    reproducers = sorted(findings_dir.glob("hard-*.js"))
    assert reproducers, list(findings_dir.iterdir())
    text = reproducers[0].read_text()
    assert "oxpecker-expect: hard" in text and "=" in text
    log = [json.loads(ln) for ln in (findings_dir / "hard.jsonl").read_text().splitlines() if ln.strip()]
    assert log and log[0]["signature"].startswith("hard:")


def test_the_pipelined_loop_runs_one_program_at_a_time_and_processes_every_run(tmp_path: Path):
    """With `pipeline` the next program is drawn while the current one runs on a thread: never two
    runs at once (the JVM answers one request at a time, and shrinking uses it too), every run
    processed, the last one included, and the same loop from the same seed every time."""
    import threading

    class Watching(FakeRunner):
        def __init__(self):
            super().__init__()
            self.running = threading.Lock()
            self.threads: set[str] = set()

        def __call__(self, lineage, require=None):
            assert self.running.acquire(blocking=False), "two runs at once"
            try:
                self.threads.add(threading.current_thread().name)
                return super().__call__(lineage, require)
            finally:
                self.running.release()

    def run(name: str, **kw):
        runner = Watching()
        summary = fuzz(corpus_dir=tmp_path / name, options=OPTIONS, iterations=60, steps=STEPS, seed=7, runner=runner,
                       findings_dir=tmp_path / (name + "-findings"), shrink_budget=5, quiet=True, **kw)
        return runner, summary

    runner, summary = run("a")
    stats = summary["stats"]
    assert summary["runs"] == 60
    assert any(t.startswith("oxpecker-run") for t in runner.threads)
    assert stats.get("equal", 0) + stats.get("hard", 0) == 60 - stats.get("rejected", 0)          # every program drawn was run and judged
    assert summary["corpus"] == stats["corpus-added"] > 0
    again, summary2 = run("b")
    assert summary2["stats"] == stats and Corpus(tmp_path / "b").entries[-1].choices == Corpus(tmp_path / "a").entries[-1].choices
    serial, _ = run("c", pipeline=False)
    assert not any(t.startswith("oxpecker-run") for t in serial.threads)


def test_shrinker_returns_a_smaller_lineage_that_still_fails():
    runner = FakeRunner()
    found = None
    rng = Random(5)
    for _ in range(300):
        drawn = draw_lineage(OPTIONS, STEPS, rng=rng)
        if drawn is None:
            continue
        lineage, choices = drawn
        if runner(lineage).verdict.kind == HARD:
            found = (lineage, choices)
            break
    assert found is not None, "the planted bug never showed in 300 draws"
    lineage, choices = found

    def interesting(l, obs):
        from oxpecker.fuzz import finding_class
        return finding_class("planted") if obs.verdict.kind == HARD else None

    shrinker = Shrinker(runner, OPTIONS, STEPS, interesting, budget=60)
    best = shrinker.shrink(choices)
    assert best is not None
    small, small_choices, obs = best
    assert obs.verdict.kind == HARD
    # one call means the engine treated the example as already shrunk (saved under the primary key)
    assert shrinker.calls > 1, "Hypothesis did not shrink (saved under the primary key?)"
    assert len(small.code) <= len(lineage.code)
    again = replay(small_choices, OPTIONS, STEPS)
    assert again is not None and runner(again).verdict.kind == HARD


def test_compaction_keeps_the_probes_and_does_not_grow_the_entry():
    runner = FakeRunner()
    drawn = draw_lineage(OPTIONS, STEPS, seed=11)
    assert drawn is not None
    lineage, choices = drawn
    obs = runner(lineage)
    assert obs.new_probes > 0
    result = compact(runner, OPTIONS, STEPS, choices, obs.new_probe_ids, budget=40)
    assert result is not None
    small_choices, small, probes = result
    assert probes and len(small.code) <= len(lineage.code)
    assert runner(small, require=probes).required_reached == probes


@pytest.mark.slow
def test_loop_runs_against_rhino(harness, tmp_path: Path):
    if not harness.info().get("coverage"):
        pytest.skip("coverage agent not attached (OXPECKER_COVERAGE=1)")
    summary = fuzz(corpus_dir=tmp_path / "corpus", options=OPTIONS, iterations=12, steps=3, seed=2, client=harness,
                   findings_dir=tmp_path / "findings", shrink_budget=20, quiet=True)
    assert summary["runs"] == 12
    assert (tmp_path / "corpus" / "coverage.exec").exists()
