"""Coverage-guided corpus fuzzing (`oxpecker fuzz`), the Fuzzilli/AFL loop on top of oxpecker's
generators:

    corpus  = programs (as choice sequences) that each covered Rhino probes nothing before them had
    loop    = pick a parent uniformly from the corpus (or draw a fresh program with probability
              p_new), mutate its choice sequence, build, run in both modes, compare;
              new probes → add to corpus; finding with an unknown signature → shrink, record
    persist = corpus.jsonl + coverage.exec in the corpus directory, so the next run resumes the
              frontier instead of starting from zero

Every program is valid by construction because mutation happens on the choice sequence, not on
JS text (see gen/rand.py).
"""

from __future__ import annotations

import json
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import findings
from .shrink import shrink_source
from .gen.builder import GenOptions
from .gen.rand import ChoiceRand, mutate
from .gen.strategies import Program, build_program, options_dict
from .harness.client import HarnessClient, RunResult
from .harness.compare import HARD, JAVA_TYPES, SOFT, Verdict, compare_run


@dataclass
class Entry:
    id: int
    choices: list[int]
    template: str
    new_probes: int
    parent: int | None = None
    added_at: float = field(default_factory=time.time)
    found: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return self.__dict__


class Corpus:
    def __init__(self, dir: Path):
        self.dir = dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = dir / "corpus.jsonl"
        self.exec = dir / "coverage.exec"
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


def shrink(choices: list[int], opts: GenOptions, oracle, *, budget: int = 400) -> list[int]:
    """Delta debugging over the choice sequence: keep removing chunks and zeroing values while
    `oracle(program)` still holds. The result rebuilds to a smaller program with the same finding."""
    best = list(choices)
    tries = 0

    def holds(c: list[int]) -> bool:
        nonlocal tries
        tries += 1
        return oracle(build_program(ChoiceRand(c, seed=0), opts))

    # 1. chunk deletion, halving granularity
    n = max(1, len(best) // 2)
    while n >= 1 and tries < budget:
        i = 0
        progressed = False
        while i < len(best) and tries < budget:
            cand = best[:i] + best[i + n:]
            if cand and holds(cand):
                best = cand
                progressed = True
            else:
                i += n
        if not progressed:
            n //= 2
    # 2. truncate the tail (draws past the finding are usually irrelevant)
    lo, hi = 1, len(best)
    while lo < hi and tries < budget:
        mid = (lo + hi) // 2
        if holds(best[:mid]):
            best, hi = best[:mid], mid
        else:
            lo = mid + 1
    # 3. minimise values (smaller draws = earlier choices, shorter literals)
    for i in range(len(best)):
        if tries >= budget:
            break
        for v in (0, 1, best[i] // 2):
            if v < best[i]:
                cand = best[:i] + [v] + best[i + 1:]
                if holds(cand):
                    best = cand
                    break
    return best


def fuzz(*, corpus_dir: Path, opts: GenOptions, iterations: int | None = None, seconds: float | None = None,
         p_new: float = 0.2, language_version: int = 200, seed: int | None = None, known: set[str] | None = None,
         findings_dir: Path = findings.DEFAULT_DIR, save_every: int = 200, quiet: bool = False) -> dict[str, Any]:
    rng = random.Random(seed)
    corpus = Corpus(corpus_dir)
    stats: Counter[str] = Counter()
    sig_counts: Counter[str] = Counter()
    known = set(known or ())
    t0 = time.time()
    last_report = t0

    with HarnessClient(quiet=True, coverage=True) as client:
        if not client.info().get("coverage"):
            raise RuntimeError("coverage agent not attached; the harness build must include jacocoagent.jar")
        if corpus.exec.exists():
            covered = client.coverage_load(str(corpus.exec))
            if not quiet:
                print(f"resumed: {len(corpus.entries)} corpus entries, {covered} probes already covered", file=sys.stderr)
        i = 0
        while True:
            if iterations is not None and i >= iterations:
                break
            if seconds is not None and time.time() - t0 >= seconds:
                break
            i += 1
            # --- choose and build
            parent: Entry | None = None
            if corpus.entries and rng.random() > p_new:
                parent = rng.choice(corpus.entries)
                others = [e.choices for e in rng.sample(corpus.entries, min(4, len(corpus.entries)))]
                choices = mutate(parent.choices, rng, others=others)
                stats["mutated"] += 1
            else:
                choices = []
                stats["fresh"] += 1
            rand = ChoiceRand(choices, seed=rng.getrandbits(32))
            try:
                prog = build_program(rand, opts)
            except Exception as e:  # a generator bug; count and continue
                stats["build-error"] += 1
                if not quiet:
                    print(f"[build error] {type(e).__name__}: {e}", file=sys.stderr)
                continue
            # --- run and compare
            run = client.run(prog.source, language_version=language_version, seed=1, coverage_bucket=prog.template)
            verdict = compare_run(run)
            stats[verdict.kind] += 1
            fresh = sum(o.newProbes or 0 for o in run.results.values())
            if fresh > 0:
                corpus.add(Entry(corpus.next_id(), rand.choices[: rand.consumed], prog.template, fresh,
                                 parent=parent.id if parent else None))
                stats["corpus-added"] += 1
                stats["new-probes"] += fresh
            # --- findings
            if verdict.kind in (SOFT, JAVA_TYPES):
                findings.record(prog, run, verdict, out_dir=findings_dir, choices=rand.choices[: rand.consumed])
            if verdict.kind == HARD:
                sig = verdict.signature()
                sig_counts[sig] += 1
                if sig in known:
                    findings.record(prog, run, verdict, out_dir=findings_dir, choices=rand.choices[: rand.consumed])
                else:
                    known.add(sig)
                    stats["new-findings"] += 1
                    small = _shrink_finding(client, rand.choices[: rand.consumed], opts, verdict.coarse_signature(),
                                            language_version)
                    sprog = build_program(ChoiceRand(small, seed=0), opts)
                    srun = client.run(sprog.source, language_version=language_version, seed=1)
                    sv = compare_run(srun)
                    if sv.coarse_signature() != verdict.coarse_signature():   # should not happen; keep the original
                        sprog, srun, sv = prog, run, verdict
                    # Then statement/line-level deletion on the source, which gets much further.
                    coarse = verdict.coarse_signature()

                    def src_oracle(src: str) -> bool:
                        return compare_run(client.run(src, language_version=language_version, seed=1)).coarse_signature() == coarse

                    small_src = shrink_source(sprog.source, src_oracle)
                    srun = client.run(small_src, language_version=language_version, seed=1)
                    sv = compare_run(srun)
                    sprog = Program(source=small_src, source_plain=_strip_probes(small_src), template=sprog.template,
                                    enabled_generators=sprog.enabled_generators, generator_usage=sprog.generator_usage,
                                    generator_spans=[], stats=sprog.stats, options=sprog.options)
                    known.add(sv.signature())
                    path = findings.record(sprog, srun, sv, out_dir=findings_dir, reproducer=True, choices=small,
                                           replay_cmd="oxpecker diff")
                    if parent is not None:
                        parent.found.append(sig)
                    print(f"[finding] {verdict.pretty()[:160]}\n          → {path} ({len(sprog.source_plain.splitlines())} lines)",
                          file=sys.stderr)
            # --- bookkeeping
            if i % save_every == 0:
                client.coverage_save(str(corpus.exec))
            if not quiet and time.time() - last_report > 10:
                last_report = time.time()
                rate = i / (last_report - t0)
                print(f"{i:7} runs {rate:5.1f}/s  corpus {len(corpus.entries)}  new probes {stats['new-probes']}  "
                      f"findings {stats['new-findings']}  " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())
                                                                     if k in ("equal", "hard", "java-types", "soft", "discard", "skip")),
                      file=sys.stderr)
        client.coverage_save(str(corpus.exec))
        report = client.coverage_report()
    summary = {"runs": i, "seconds": time.time() - t0, "corpus": len(corpus.entries), "stats": dict(stats),
               "signatures": dict(sig_counts), "coveredProbes": report.get("coveredProbes"),
               "totalProbes": report.get("totalProbes"), "options": options_dict(opts)}
    (corpus_dir / "last-run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def _strip_probes(src: str) -> str:
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("__probe(")) + "\n"


def _shrink_finding(client: HarnessClient, choices: list[int], opts: GenOptions, coarse: str, language_version: int) -> list[int]:
    """Shrinks while the *coarse* signature (bug class) is preserved; exact values may change."""
    def oracle(p: Program) -> bool:
        run = client.run(p.source, language_version=language_version, seed=1)
        return compare_run(run).coarse_signature() == coarse
    return shrink(choices, opts, oracle)
