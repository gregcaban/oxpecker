"""The differential-testing loop without Rhino: a fake runner with a planted bug and planted
probes drives corpus growth, finding detection, shrinking and compaction. One slow test runs the
real harness for a few iterations."""

from __future__ import annotations

import json
from pathlib import Path
from random import Random

import pytest

from oxpecker.fuzz import (Corpus, Entry, LineageCache, Observation, Shrinker, compact, decode, draw_lineage,
                           encode, extend, fuzz, replay)
from oxpecker.harness.compare import EQUAL, HARD, Verdict
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
