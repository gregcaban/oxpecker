"""Generator-layer tests that need no JVM: determinism, every generator fires and parses."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, Phase, given, seed, settings

from oxpecker.cli import generate_programs
from oxpecker.gen.builder import GenOptions
from oxpecker.gen.registry import GENERATORS, TEMPLATES, load_all
from oxpecker.gen.strategies import programs

ALL_TOGGLES = frozenset({"let", "generators", "for-of", "destructuring", "spread", "continuations"})


def test_registry_loads():
    load_all()
    assert len(GENERATORS) >= 60
    assert len(TEMPLATES) >= 5


def test_generation_is_deterministic_for_a_seed():
    a = generate_programs(GenOptions(), 10, seed=7)
    b = generate_programs(GenOptions(), 10, seed=7)
    assert [p.source for p in a] == [p.source for p in b]


@pytest.mark.parametrize("name", sorted(GENERATORS) if load_all() is None else [])
def test_each_generator_emits_something(name):
    opts = GenOptions(generators=(name,), templates=("plain",), toggles=ALL_TOGGLES, swarm=False,
                      min_statements=1, max_statements=3, probes=False, comments=False)
    progs = generate_programs(opts, 3, seed=1)
    assert progs
    assert any(name in p.generator_usage for p in progs), f"{name} never fired"
    assert all(p.source_plain.strip() for p in progs)


@pytest.mark.skipif(subprocess.run(["which", "node"], capture_output=True).returncode != 0, reason="node not installed")
def test_generated_programs_parse_with_node(tmp_path: Path):
    """Syntax errors in both modes are generator bugs; catch them without the JVM."""
    opts = GenOptions(toggles=ALL_TOGGLES, probes=False, comments=False)
    progs = generate_programs(opts, 60, seed=3)
    bad = []
    for i, p in enumerate(progs):
        f = tmp_path / f"c{i}.js"
        f.write_text(p.source_plain)
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        if r.returncode != 0:
            msg = next((ln for ln in r.stderr.splitlines() if "SyntaxError" in ln), "?")
            loc = next((ln for ln in r.stderr.splitlines() if ln.startswith(str(f))), "")
            lineno = int(loc.rsplit(":", 1)[1]) if loc else 0
            lines = p.source_plain.splitlines()
            offending = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else f"<line {lineno} of {len(lines)}>"
            bad.append(f"{msg} @ {offending!r}")
    assert not bad, "\n".join(bad)


@seed(1)
@settings(max_examples=30, phases=[Phase.generate], database=None, deadline=None, suppress_health_check=list(HealthCheck))
@given(programs(GenOptions(toggles=ALL_TOGGLES)))
def test_metadata_is_consistent(p):
    lines = p.source.splitlines()
    for span in p.generator_spans:
        s, e = span["lines"]
        assert 0 <= s <= e < len(lines)
    assert p.stats["probes"] == sum(1 for ln in lines if ln.lstrip().startswith("__probe("))


def test_choice_sequence_replays_and_mutates(tmp_path: Path):
    """A recorded choice sequence rebuilds the identical program; mutated sequences still build
    (validity by construction) and parse."""
    import random
    from oxpecker.gen.rand import ChoiceRand, mutate
    from oxpecker.gen.strategies import build_program

    opts = GenOptions(toggles=ALL_TOGGLES, probes=False, comments=False)
    rng = random.Random(1)
    rand = ChoiceRand(seed=1)
    p1 = build_program(rand, opts)
    recorded = rand.choices[: rand.consumed]
    p2 = build_program(ChoiceRand(recorded, seed=99), opts)
    assert p1.source == p2.source
    bad = []
    for k in range(40):
        c = mutate(recorded, rng, others=[recorded])
        p = build_program(ChoiceRand(c, seed=k), opts)
        f = tmp_path / f"m{k}.js"
        f.write_text(p.source_plain)
        if subprocess.run(["which", "node"], capture_output=True).returncode == 0:
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "?")
    assert not bad, bad
