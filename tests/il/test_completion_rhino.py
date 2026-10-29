"""Completion rate (design §10 item 10): with no guards, the share of generated programs that run
to their end in interpreted mode, per family. A family whose programs mostly throw early is a
generator problem (loose inputs, wrong receivers), never a Rhino finding. Needs the harness.

    pytest tests/il/test_completion_rhino.py -s          # prints the table
    OXPECKER_EXAMPLES=200 pytest tests/il/test_completion_rhino.py -s
"""

from __future__ import annotations

import os
from collections import Counter

import pytest

import oxpecker.il.templates  # noqa: F401
from oxpecker.il.options import GenOptions
from oxpecker.il.strategies import FAMILY_ORDER, REGISTRY, generate

EXAMPLES = int(os.environ.get("OXPECKER_EXAMPLES", "40"))
ONLY = frozenset(os.environ.get("OXPECKER_FAMILIES", "").split(",")) - {""}     # OXPECKER_FAMILIES=calls,control

# Floors set from the measurements of 2026-10-26 (40 programs per family, templates drawn by
# weight). First measurement: values 100%, operations 78%, calls 40%, control 57%,
# compiler-targeted 95%, java-interop 98%, continuations 85%, objects 75%, all 52%. After G38
# (loose inputs half fresh values), for-header kinds never const, non-constructible/non-callable
# builtins skipped, super methods every prototype has, unguarded continuations: operations 72%,
# calls 75%, control 88%, compiler-targeted 95%, java-interop 100%, continuations 82%,
# objects 98%, all 62%. Raise them as the generators improve; a drop below is a regression.
FLOORS: dict[str, float] = {
    "all": 0.50,
    "values": 0.90,
    "operations": 0.75,       # 90 % after G46
    "calls": 0.60,
    "control": 0.75,
    "compiler-targeted": 0.80,
    "java-interop": 0.85,
    "continuations": 0.70,
    "objects": 0.85,
}


def _families() -> list[str]:
    present = {s.family for s in REGISTRY.values() if s.context.name == "JAVASCRIPT" and s.inputs == () and s.required == ()}
    return [f for f in FAMILY_ORDER if f in present and f not in ("members", "cases")]


def _rate(harness, options: GenOptions, n: int, seed0: int, bugs: list[str],
          thrown: Counter | None = None) -> tuple[float, Counter]:
    """Runs n programs; a syntax error or an engine crash is a generator or lifter bug and is
    recorded in `bugs` with the seed that reproduces it (`generate(seed, ...)`). `thrown` counts
    what the programs that threw threw (name and the start of the message)."""
    statuses: Counter[str] = Counter()
    for k in range(n):
        kw = dict(max_statements=options.max_statements, families=options.families, probe_every=0)
        gen = generate(seed0 + k, **kw)
        run = harness.run(gen.plain_source, modes=("interpreted",), wall_ms=3000)
        o = run.results["interpreted"]
        statuses[o.status] += 1
        if o.status == "throw" and thrown is not None and o.thrown:
            thrown[f"{o.thrown.get('name')}: {str(o.thrown.get('message', ''))[:70]}"] += 1
        where = f"generate({seed0 + k}, {', '.join(f'{a}={v!r}' for a, v in kw.items())})"
        if o.status == "syntax":
            bugs.append(f"{where}: syntax {o.thrown}\n{gen.plain_source}")
        elif o.status == "crash" and o.reason != "jvm-died":
            # an engine exception past JS try/catch is a Rhino finding, not a generator problem:
            # it is printed with its seed so it can be minimised and recorded under tests/regressions
            print(f"\n  engine crash (a Rhino finding): {where}: {(o.engineException or {}).get('clazz')} {(o.engineException or {}).get('message')}")
    return statuses["ok"] / n, statuses


@pytest.mark.slow
def test_completion_rate_per_family(harness):
    rows: list[tuple[str, float, Counter, Counter]] = []
    bugs: list[str] = []
    for family in _families():
        if ONLY and family not in ONLY:
            continue
        # the family's statements over the value makers (values are always available as the prefix)
        options = GenOptions(max_statements=8, families=frozenset({family, "values"}))
        thrown: Counter[str] = Counter()
        rate, statuses = _rate(harness, options, EXAMPLES, 1000, bugs, thrown)
        rows.append((family, rate, statuses, thrown))
    if not ONLY:
        thrown = Counter()
        rate, statuses = _rate(harness, GenOptions(max_statements=10), EXAMPLES, 2000, bugs, thrown)
        rows.append(("all", rate, statuses, thrown))
    print()
    for family, rate, statuses, thrown in rows:
        print(f"  {family:20} ok {rate:5.0%}   " + ", ".join(f"{k}={n}" for k, n in sorted(statuses.items())))
        for what, n in thrown.most_common(5):
            print(f"      {n:3}  {what}")
    assert not bugs, "syntax errors (generator/lifter bugs):\n\n" + "\n\n".join(bugs)
    low = [(f, r) for f, r, _, _ in rows if r < FLOORS.get(f, 0.0)]
    assert not low, "completion rate below the floor: " + ", ".join(f"{f} {r:.0%} < {FLOORS[f]:.0%}" for f, r in low)
