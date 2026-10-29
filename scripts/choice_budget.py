"""How much of Hypothesis's per-test-case budget programs and lineages use (design §10 item 9,
proposal §5.2). Every test case is capped at BUFFER_SIZE bytes of serialised choices; this
draws programs and lineages with ordinary random choices and reports, per kind and per family:
the number of choices, their serialised size, and that size as a share of the budget.
Re-run after adding a family: string draws (property names, literals) are what grows it.

    .venv/bin/python scripts/choice_budget.py            # 40 samples per kind
    .venv/bin/python scripts/choice_budget.py -n 100 --families

Uses Hypothesis internals (`ConjectureData`, `BuildContext`) for measurement only.
"""

from __future__ import annotations

import argparse
import statistics

from hypothesis.control import BuildContext
from hypothesis.errors import StopTest
from hypothesis.internal.conjecture.engine import BUFFER_SIZE

import oxpecker.il.templates  # noqa: F401  (registration)
from oxpecker.il.mutate import evolved
from oxpecker.il.options import GenOptions
from oxpecker.il.strategies import FAMILY_ORDER, REGISTRY, programs, seeded_data


def measure(strategy, seed: int) -> tuple[int, int] | None:
    data = seeded_data(seed)
    try:
        with BuildContext(data, wrapped_test=lambda: None):
            data.draw(strategy)
    except StopTest:                       # overrun or rejection
        return None
    data.freeze()
    return len(data.choices), data.length


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
    ap.add_argument("--families", action="store_true", help="also one row per family (its statements over the value makers)")
    args = ap.parse_args()
    opts = GenOptions()
    print(f"min/median/max over {args.n} seeds; BUFFER_SIZE = {BUFFER_SIZE}")
    report("programs()", programs(opts), args.n)
    report("programs(), 16 statements, depth 4", programs(GenOptions(max_statements=16, max_depth=4)), args.n)
    for k in (1, 5, 20):
        report(f"lineage, up to {k} steps", evolved(opts, steps=k), args.n)
    if args.families:
        present = {s.family for s in REGISTRY.values()}
        for family in FAMILY_ORDER:
            if family in present and family not in ("values", "members", "cases"):
                report(f"family {family}", programs(GenOptions(families=frozenset({family, "values"}))), args.n)


if __name__ == "__main__":
    main()
