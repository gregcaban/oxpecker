"""Several loops side by side (oxpecker.workers, fuzz.Peer): a worker takes the entries of the
others that reach probes new to it, a finding is claimed by one, the corpora merge into one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oxpecker import HARNESS_BIN, workers
from oxpecker.fuzz import Corpus, Entry, Peer, claim, decode, fuzz, replay
from oxpecker.il.program import signature

from test_fuzz import OPTIONS, STEPS, FakeRunner


def loop(corpus: Path, findings: Path, seed: int, runner: FakeRunner, **kw):
    return fuzz(corpus_dir=corpus, options=OPTIONS, iterations=80, steps=STEPS, seed=seed, runner=runner,
                findings_dir=findings, shrink_budget=5, quiet=True, **kw)


def test_a_worker_takes_the_entries_of_a_peer_that_are_new_to_it(tmp_path: Path):
    """B, with nothing covered, syncs with A's finished corpus: A's entries are run in B, the ones
    reaching probes new to B join its corpus under B's ids, marked with where they came from, and
    draw the very lineage they drew in A. A third worker reading B passes those over."""
    loop(tmp_path / "w0", tmp_path / "f0", 1, FakeRunner())
    a = Corpus(tmp_path / "w0")
    assert len(a.entries) > 5
    runner_b = FakeRunner()
    summary = fuzz(corpus_dir=tmp_path / "w1", options=OPTIONS, iterations=0, steps=STEPS, seed=2, runner=runner_b,
                   findings_dir=tmp_path / "f1", quiet=True, peers=[Peer(tmp_path / "w0")], sync_seconds=0)
    b = Corpus(tmp_path / "w1")
    stats = summary["stats"]
    assert stats["sync-seen"] == len(a.entries) and stats["sync-kept"] == len(b.entries) > 0
    assert runner_b.calls == len(a.entries)                    # each of A's entries ran once in B
    assert runner_b.seen == {type(i.op).__name__ for e in range(len(a.entries)) for i in a.replay(e, OPTIONS).code}
    for e in b.entries:
        name, _, peer_id = e.origin.partition(":")
        theirs = a.by_id[int(peer_id)]
        assert name == "w0" and e.choices == theirs.choices and e.steps == theirs.steps
        if e.parent is not None:                                # the parent, under the id it has in B
            assert b.by_id[e.parent].origin == f"w0:{theirs.parent}"
    assert Peer(tmp_path / "w1").new_entries() == []           # nothing of B's own to take


def test_sync_rebuilds_a_peer_entry_from_its_parent_exactly_as_a_full_replay(tmp_path: Path):
    loop(tmp_path / "w0", tmp_path / "f0", 4, FakeRunner(), pipeline=False)
    a = Corpus(tmp_path / "w0")
    peer = Peer(tmp_path / "w0")
    children = 0
    for e in peer.new_entries():
        built = peer.lineage(e, OPTIONS, STEPS, lambda entry_id: None)
        full = replay(decode(e.choices), OPTIONS, e.cap)
        assert signature(built.code) == signature(full.code) and [s.name for s in built.steps] == e.steps
        children += e.parent is not None
    assert children > 0 and peer.new_entries() == []
    with (tmp_path / "w0" / "corpus.jsonl").open("a") as f:    # a line the peer is still writing waits
        f.write(json.dumps(a.entries[0].to_json())[:40])
    assert peer.new_entries() == []


def test_a_finding_is_claimed_by_the_first_worker_to_see_it(tmp_path: Path):
    assert claim(tmp_path / "claims", "hard:x", "w0")
    assert not claim(tmp_path / "claims", "hard:x", "w1")
    assert claim(tmp_path / "claims", "hard:y", "w1")
    # two loops over the same planted bug: one shrinks and records it, the other counts it
    one = loop(tmp_path / "w0", tmp_path / "f0", 3, FakeRunner(), claims=tmp_path / "c")
    two = loop(tmp_path / "w1", tmp_path / "f1", 5, FakeRunner(), claims=tmp_path / "c")
    assert one["stats"]["new-findings"] == 1 and "new-findings" not in two["stats"]
    assert two["stats"]["findings-claimed-elsewhere"] == 1 and two["stats"]["hard"] > 0


def entry(id: int, parent: int | None = None, origin: str | None = None) -> Entry:
    return Entry(id, "00", "t", [], 1, {}, parent, origin=origin)


def test_prepare_copies_the_seed_and_merge_puts_the_workers_corpora_back_into_one(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    seed = "".join(json.dumps(entry(i).to_json()) + "\n" for i in range(3))
    (corpus / "corpus.jsonl").write_text(seed)
    own0, peers0 = workers.prepare(corpus, 0, 2)
    own1, peers1 = workers.prepare(corpus, 1, 2)
    assert (own0 / "corpus.jsonl").read_text() == seed and [p.name for p in peers0] == ["w1"]
    assert peers0[0].offset == len(seed) and peers0[0].seed_entries == 3 and peers1[0].name == "w0"
    c0, c1 = Corpus(own0), Corpus(own1)
    c0.add(entry(3, parent=1))                     # w0: its own, on a seed entry
    c1.add(entry(3, parent=None))                  # w1: its own
    c1.add(entry(4, parent=3, origin="w0:3"))      # w1: taken from w0
    c1.add(entry(5, parent=4))                     # w1: its own, on the one it took
    c0.add(entry(4, parent=3))                     # w0: its own, on its own
    assert [e.id for e in peers1[0].new_entries()] == [3, 4]           # w1 reads w0 past the seed
    assert workers.prepare(corpus, 0, 2)[1][0].offset == len(seed)     # a second run: the seed's size is kept
    assert workers.merge(corpus, 2) == 7
    merged = Corpus(corpus).entries
    assert [e.id for e in merged] == list(range(7)) and all(e.origin is None for e in merged)
    assert [e.parent for e in merged] == [None, None, None, 1, 3, None, 3]   # w0: 3, 4; w1: 5, 6 (on w0's 3)


@pytest.mark.slow
def test_two_workers_run_against_rhino_and_merge(tmp_path: Path):
    if not HARNESS_BIN.exists():
        pytest.skip("harness not built: cd harness-java && ./gradlew fatJar")
    from oxpecker.cli import main
    corpus = tmp_path / "corpus"
    code = main(["fuzz", "-o", str(corpus), "--workers", "2", "--seconds", "25", "--sync-seconds", "5", "--no-judge",
                 "--findings-dir", str(tmp_path / "findings"), "--max-statements", "5", "--shrink-budget", "5",
                 "--source-shrink-budget", "5", "-q"])
    summary = json.loads((corpus / "last-run.json").read_text())
    logs = "\n".join((corpus / f"w{k}" / "fuzz.log").read_text() for k in range(2))
    assert code == 0 and summary["exit_codes"] == [0, 0], logs[-2000:]
    assert summary["workers"] == 2 and all(w["runs"] > 0 for w in summary["per_worker"])
    assert sum(w["stats"].get("sync-seen", 0) for w in summary["per_worker"]) > 0
    merged = Corpus(corpus).entries
    own = sum(1 for k in range(2) for e in Corpus(corpus / f"w{k}").entries if e.origin is None)
    assert len(merged) == own == summary["corpus"] > 0 and (corpus / "coverage.exec").exists()
    from oxpecker.il.options import GenOptions
    assert replay(decode(merged[-1].choices), GenOptions(max_statements=5), merged[-1].cap) is not None
