"""Several fuzz loops side by side (`oxpecker fuzz --workers N`): the loop's generation is
single-threaded Python, so more programs a second means more processes.

    CORPUS/corpus.jsonl, coverage.exec   the seed every worker starts from a copy of (may be absent);
                                         rewritten at the end with the workers' corpora merged, so
                                         the next campaign minimises and reuses it as it does a
                                         single loop's
    CORPUS/wK/                           worker K's own corpus, coverage store, status.json, fuzz.log
    CORPUS/claims/                       one file per new finding's signature: the worker that
                                         creates it shrinks and records the finding (fuzz.claim)
    FINDINGS/wK/                         worker K's findings

A worker is the ordinary `oxpecker fuzz` process with `--worker-index K`: its own JVM, its own
corpus, nothing shared in memory and nothing two processes write. They collaborate the way AFL's
instances do: every `--sync-seconds` a worker reads the new lines of the others' corpus.jsonl,
runs each entry in its own JVM and keeps those reaching probes new to it (fuzz.Peer). The
supervisor here starts them, passes a stop signal on, prints their progress added up, and merges.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import ROOT
from .fuzz import Corpus, Entry, Peer


def worker_dir(corpus: Path, k: int) -> Path:
    return Path(corpus) / f"w{k}"


def prepare(corpus: Path, k: int, workers: int) -> tuple[Path, list[Peer]]:
    """Worker K's corpus directory, started from a copy of the seed corpus if it is new, and the
    other workers as peers. `sync.json` in the worker's directory keeps the seed's size (the
    part of every peer's corpus that is the same seed) for a later run over the same directory."""
    corpus = Path(corpus)
    own = worker_dir(corpus, k)
    state_file = own / "sync.json"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
    else:
        own.mkdir(parents=True, exist_ok=True)
        seed = corpus / "corpus.jsonl"
        state = {"seed_bytes": 0, "seed_entries": 0}
        if seed.exists() and not (own / "corpus.jsonl").exists():
            data = seed.read_bytes()
            (own / "corpus.jsonl").write_bytes(data)
            if (corpus / "coverage.exec").exists():
                shutil.copyfile(corpus / "coverage.exec", own / "coverage.exec")
            state = {"seed_bytes": len(data), "seed_entries": sum(1 for ln in data.splitlines() if ln.strip())}
        state_file.write_text(json.dumps(state), encoding="utf-8")
    peers = [Peer(worker_dir(corpus, j), state["seed_bytes"], state["seed_entries"]) for j in range(workers) if j != k]
    return own, peers


def merge(corpus: Path, workers: int) -> int:
    """CORPUS/corpus.jsonl from the workers' corpora: the seed entries once, then each worker's
    own entries renumbered, parents following; an entry a worker took from another is that
    other's entry and is not written twice. Returns the number of entries."""
    corpus = Path(corpus)
    dirs = [worker_dir(corpus, k) for k in range(workers) if (worker_dir(corpus, k) / "corpus.jsonl").exists()]
    if not dirs:
        return 0
    seed_entries = 0
    if (dirs[0] / "sync.json").exists():
        seed_entries = json.loads((dirs[0] / "sync.json").read_text(encoding="utf-8"))["seed_entries"]
    loaded = {d.name: Corpus(d).entries for d in dirs}
    merged: list[Entry] = [e for e in loaded[dirs[0].name] if e.id < seed_entries]
    new_id: dict[tuple[str, int], int] = {}
    next_id = max((e.id for e in merged), default=-1) + 1
    for name, entries in loaded.items():                         # ids first: a parent may be in any worker
        for e in entries:
            if e.id >= seed_entries and e.origin is None:
                new_id[(name, e.id)] = next_id
                next_id += 1
    for name, entries in loaded.items():
        origin_of = {e.id: tuple(e.origin.rsplit(":", 1)) for e in entries if e.origin is not None}
        for e in entries:
            if e.id < seed_entries or e.origin is not None:
                continue
            parent = e.parent
            if parent is not None and parent >= seed_entries:
                if parent in origin_of:                           # a parent taken from another worker
                    other, other_id = origin_of[parent]
                    parent = new_id.get((other, int(other_id)))
                else:
                    parent = new_id.get((name, parent))
            d = e.to_json()
            d.update(id=new_id[(name, e.id)], parent=parent)
            merged.append(Entry(**d))
    tmp = corpus / "corpus.jsonl.tmp"
    tmp.write_text("".join(json.dumps(e.to_json()) + "\n" for e in merged), encoding="utf-8")
    tmp.replace(corpus / "corpus.jsonl")
    return len(merged)


def merge_coverage(corpus: Path, workers: int) -> int | None:
    """CORPUS/coverage.exec: every worker's store loaded into one JVM and saved. None when no
    worker has one or the harness has no coverage agent."""
    from .harness.client import HarnessClient
    corpus = Path(corpus)
    stores = [worker_dir(corpus, k) / "coverage.exec" for k in range(workers)]
    stores = [s for s in stores if s.exists()]
    if not stores:
        return None
    with HarnessClient(quiet=True, coverage=True) as client:
        if not client.info().get("coverage"):
            return None
        for s in stores:
            client.coverage_load(str(s))
        return client.coverage_save(str(corpus / "coverage.exec"))


def _status(corpus: Path, workers: int) -> list[dict[str, Any]]:
    out = []
    for k in range(workers):
        try:
            out.append(json.loads((worker_dir(corpus, k) / "status.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append({})
    return out


def supervise(corpus: Path, workers: int, argv: list[str], *, quiet: bool = False, report_seconds: float = 10.0) -> int:
    """Start `workers` copies of `oxpecker fuzz ARGV --worker-index K`, wait for them, merge.
    SIGTERM and SIGINT are passed on to the workers (scripts/campaign.sh stop), and the corpora
    written so far are still merged. Returns the worst exit code."""
    corpus = Path(corpus)
    corpus.mkdir(parents=True, exist_ok=True)
    procs: list[subprocess.Popen[bytes]] = []
    logs = []
    # the workers import this checkout's oxpecker, whatever the working directory is
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p))
    for k in range(workers):
        worker_dir(corpus, k).mkdir(parents=True, exist_ok=True)
        log = (worker_dir(corpus, k) / "fuzz.log").open("ab")
        logs.append(log)
        procs.append(subprocess.Popen([sys.executable, "-m", "oxpecker.cli", "fuzz", *argv, "--worker-index", str(k)],
                                      stdout=log, stderr=subprocess.STDOUT, env=env))
    stopping = False

    def stop(signum: int, frame: Any) -> None:
        nonlocal stopping
        stopping = True
        for p in procs:
            if p.poll() is None:
                p.terminate()

    previous = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
    t0 = time.time()
    try:
        while any(p.poll() is None for p in procs):
            time.sleep(0.5 if stopping else report_seconds)
            if quiet or stopping:
                continue
            st = _status(corpus, workers)
            runs = sum(s.get("runs", 0) for s in st)
            stats: dict[str, int] = {}
            for s in st:
                for key, n in s.get("stats", {}).items():
                    stats[key] = stats.get(key, 0) + n
            alive = sum(p.poll() is None for p in procs)
            print(f"{runs:7} runs {runs / (time.time() - t0):5.1f}/s  corpus {sum(s.get('corpus', 0) for s in st)}  "
                  f"new probes {stats.get('new-probes', 0)}  findings {stats.get('new-findings', 0)}  "
                  f"workers {alive}/{workers}  taken from peers {stats.get('sync-kept', 0)}/{stats.get('sync-seen', 0)}  "
                  + ", ".join(f"{k}={stats[k]}" for k in ("equal", "hard", "java-types", "soft", "fixed", "rejected") if k in stats),
                  file=sys.stderr, flush=True)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            p.wait()
        for log in logs:
            log.close()
    seconds = time.time() - t0
    entries = merge(corpus, workers)
    covered = merge_coverage(corpus, workers)
    summaries = []
    for k in range(workers):
        try:
            summaries.append(json.loads((worker_dir(corpus, k) / "last-run.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            summaries.append(_status(corpus, workers)[k])            # a worker that was stopped
    runs = sum(s.get("runs", 0) for s in summaries)
    summary = {"workers": workers, "runs": runs, "seconds": seconds, "runs_per_second": runs / seconds if seconds else 0.0,
               "corpus": entries, "coveredProbes": covered,
               "exit_codes": [p.returncode for p in procs],
               "per_worker": [{"runs": s.get("runs"), "corpus": s.get("corpus"), "stats": s.get("stats")} for s in summaries]}
    (corpus / "last-run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if not quiet:
        print(json.dumps({k: v for k, v in summary.items() if k != "per_worker"}, indent=1))
    codes = [p.returncode for p in procs]
    if stopping:
        return 0
    return max((abs(c) for c in codes), default=0)
