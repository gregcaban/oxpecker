"""The differential-testing loop without Rhino: a fake runner with a planted bug and planted
probes drives corpus growth, finding detection, shrinking and compaction. One slow test runs the
real harness for a few iterations."""

from __future__ import annotations

import json
from pathlib import Path
from random import Random

import pytest

from oxpecker.fuzz import (Corpus, Observation, Shrinker, compact, decode, draw_lineage, encode, extend, fuzz,
                           replay)
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
    assert len(child.steps) + child.rejected >= len(lineage.steps) + lineage.rejected


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

    best = Shrinker(runner, OPTIONS, STEPS, interesting, budget=60).shrink(choices)
    assert best is not None
    small, small_choices, obs = best
    assert obs.verdict.kind == HARD
    assert len(small.code) < len(lineage.code), "Hypothesis did not shrink (saved under the primary key?)"
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
