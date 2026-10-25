"""How much of Hypothesis's per-test-case budget programs and lineages use (critique §5).

Every test case is capped at BUFFER_SIZE (8 * 1024) bytes of *serialized* choices (decision notes in
docs/proposal-hypothesis-ir.md §5.2). This draws programs with ordinary random choices and reports,
per kind: the number of choices, their serialized size, and that size as a share of the budget.
Re-run after porting a family: string draws (property names, literals) are what grows it.

    .venv/bin/python scripts/choice_budget.py            # 40 samples per kind
    .venv/bin/python scripts/choice_budget.py -n 100

Uses Hypothesis internals (`ConjectureData`, `BuildContext`) for measurement only.
"""

from __future__ import annotations

import argparse
import statistics
from random import Random

from hypothesis.control import BuildContext
from hypothesis.errors import StopTest
from hypothesis.internal.conjecture.data import ConjectureData
from hypothesis.internal.conjecture.engine import BUFFER_SIZE

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.strategies import programs as string_programs
from oxpecker.ir.mutate import evolved
from oxpecker.ir.strategies import ir_programs


def measure(strategy, seed: int) -> tuple[int, int] | None:
    data = ConjectureData(random=Random(seed))
    try:
        with BuildContext(data, wrapped_test=lambda: None):
            data.draw(strategy)
    except StopTest:                       # overrun or rejection
        return None
    return len(data.nodes), data.length


def report(label: str, strategy, n: int) -> None:
    rows = [r for r in (measure(strategy, s) for s in range(n)) if r is not None]
    if not rows:
        print(f"{label:34} no valid samples")
        return
    choices, size = [r[0] for r in rows], [r[1] for r in rows]
    share = [x / BUFFER_SIZE for x in size]
    print(f"{label:34} n={len(rows):3}  choices {min(choices):4}/{int(statistics.median(choices)):4}/{max(choices):4}"
          f"  bytes {min(size):5}/{int(statistics.median(size)):5}/{max(size):5}"
          f"  budget {min(share):5.1%}/{statistics.median(share):5.1%}/{max(share):5.1%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=40)
    args = ap.parse_args()
    opts = GenOptions()
    print(f"min/median/max over {args.n} seeds; BUFFER_SIZE = {BUFFER_SIZE}")
    report("string generator (gen/)", string_programs(opts), args.n)
    report("IR generator (ir/)", ir_programs(opts), args.n)
    for k in (1, 5, 20):
        report(f"IR lineage, up to {k} steps", evolved(opts, max_steps=k), args.n)


if __name__ == "__main__":
    main()
