"""The golden twenty (design §10 item 5): the lifted source of twenty seeded programs, checked
in under tests/il/golden/. A change in any generator, the lifter or the strategies changes them;
the diff is the review. Regenerate deliberately with

    OXPECKER_UPDATE_GOLDEN=1 pytest tests/il/test_golden.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import oxpecker.il.templates  # noqa: F401
from oxpecker.il.strategies import generate

GOLDEN = Path(__file__).parent / "golden"
SEEDS = list(range(20))
OPTIONS = dict(max_statements=8, max_depth=3)


@pytest.mark.parametrize("seed", SEEDS)
def test_golden_lifted_program(seed: int):
    path = GOLDEN / f"seed_{seed:02d}.js"
    source = generate(seed, **OPTIONS).source
    if os.environ.get("OXPECKER_UPDATE_GOLDEN"):
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(source, encoding="utf-8")
    assert path.exists(), f"no golden file for seed {seed}; run with OXPECKER_UPDATE_GOLDEN=1"
    expected = path.read_text(encoding="utf-8")
    assert source == expected, (f"seed {seed} lifts differently from {path.name}; if the change is intended, "
                                "regenerate with OXPECKER_UPDATE_GOLDEN=1 and review the diff")


def test_seeded_generation_does_not_depend_on_loaded_modules():
    """Hypothesis's stock provider answers some draws with constants scraped from every loaded
    module's source; `SeededProvider` leaves those out, so importing the loop module (or any
    other) cannot change what a seed generates (G43)."""
    before = generate(11, **OPTIONS).source
    import oxpecker.fuzz  # noqa: F401
    import oxpecker.cli  # noqa: F401
    assert generate(11, **OPTIONS).source == before
