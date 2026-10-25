"""IR generator registry, program strategy and per-generator behaviour (oxpecker/ir).

    pytest tests/test_ir_registry.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, find, given, settings
from hypothesis import strategies as st

from oxpecker.cli import generate_programs
from oxpecker.gen.builder import GenOptions
from oxpecker.gen.strategies import Program
from oxpecker.gen.types import T
from oxpecker.ir import registry
from oxpecker.ir.analysis import verify
from oxpecker.ir.builder import Builder
from oxpecker.ir.lift import lift
from oxpecker.ir.registry import (GENERATORS, TEMPLATES, available_generators, available_templates, catalogue,
                                  generator, load_all, weighted)
from oxpecker.ir.strategies import ir_programs, programs

load_all()
NODE = shutil.which("node")
RELAXED = settings(deadline=None, database=None, suppress_health_check=list(HealthCheck))
SMALL = settings(RELAXED, max_examples=15)
ALL_NAMES = sorted(GENERATORS)


def fresh_builder(opts: GenOptions | None = None) -> Builder:
    opts = opts or GenOptions()
    return Builder(opts, weighted(available_generators(opts)))


# ---------------------------------------------------------------------------- registration

@pytest.fixture
def throwaway():
    name = "zz_throwaway_generator"
    yield name
    GENERATORS.pop(name, None)
    registry._SOURCES.pop(name, None)


def test_generator_registration_stores_metadata(throwaway):
    @generator(throwaway, family="zzfam", weight=7, toggle="let", sloppy_only=True, output=T.INT32)
    @st.composite
    def zz_strategy(draw, b):
        """Does a thing.

        More text."""
        return b.declare(draw(st.just(b.const(T.INT32, "1"))))

    g = GENERATORS[throwaway]
    assert (g.name, g.family, g.weight, g.toggle, g.sloppy_only, g.output) == \
        (throwaway, "zzfam", 7, "let", True, T.INT32)
    assert g.description.startswith("Does a thing.")
    assert g.kind == "generator"
    assert registry._SOURCES[throwaway] is zz_strategy


def test_generator_decorator_returns_original_function(throwaway):
    def strat(b):
        return st.just(1)

    assert generator(throwaway, family="f")(strat) is strat


def test_duplicate_generator_name_raises(throwaway):
    generator(throwaway, family="f")(lambda b: st.just(1))
    with pytest.raises(ValueError, match="duplicate"):
        generator(throwaway, family="f")(lambda b: st.just(2))
    # the first registration is untouched
    assert GENERATORS[throwaway].family == "f"


def test_real_generator_description_is_docstring():
    for g in GENERATORS.values():
        assert g.description, g.name
    assert GENERATORS["unary"].description.startswith("Unary operator")


# ---------------------------------------------------------------------------- provenance

@st.composite
def _drawn_builder(draw, names: tuple[str, ...], nvars: int = 0):
    b = fresh_builder()
    for _ in range(nvars):
        draw(GENERATORS["int32"].fn(b))
    for n in names:
        draw(GENERATORS[n].fn(b))
    return b


@RELAXED
@given(_drawn_builder(("binary_arith",)))
def test_instructions_attributed_to_calling_generator_including_value_makers(b):
    # No variables exist, so operands come from value makers inside the generator.
    assert b.instrs
    assert {i.gen for i in b.instrs} == {"binary_arith"}


@RELAXED
@given(_drawn_builder(("int32", "binary_arith", "binary_arith", "unary"), nvars=0))
def test_each_invocation_has_a_fresh_origin(b):
    by_origin: dict[int, set[str | None]] = {}
    for i in b.instrs:
        by_origin.setdefault(i.origin, set()).add(i.gen)
    assert all(len(gens) == 1 for gens in by_origin.values())
    assert len(by_origin) == 4
    assert b.gen_usage == {"int32": 1, "binary_arith": 2, "unary": 1}
    gens_in_origin_order = [next(iter(by_origin[o])) for o in sorted(by_origin)]
    assert gens_in_origin_order == ["int32", "binary_arith", "binary_arith", "unary"]


@RELAXED
@given(st.data())
def test_gen_usage_counts_invocations(data):
    b = fresh_builder()
    n = data.draw(st.integers(1, 5))
    for _ in range(n):
        data.draw(GENERATORS["number"].fn(b))
    assert b.gen_usage == {"number": n}
    assert len({i.origin for i in b.instrs}) == n


def test_template_instructions_attributed_to_template():
    @RELAXED
    @given(st.data())
    def run(data):
        opts = GenOptions(generators=("int32",), templates=("plain",))
        b = fresh_builder(opts)
        data.draw(TEMPLATES["plain"].fn(b))
        assert b.gen_usage["plain"] == 1
        assert b.gen_usage["int32"] >= 1
        probes = [i for i in b.instrs if type(i.op).__name__ == "Probe"]
        assert probes and probes[-1].gen == "plain"
        assert all(i.gen in ("plain", "int32") for i in b.instrs)

    run()


@RELAXED
@given(ir_programs(GenOptions()))
def test_usage_metadata_excludes_template(res):
    _, meta = res
    assert meta["template"] not in meta["usage"]
    assert set(meta["usage"]) <= set(meta["enabled"])
    assert all(n >= 1 for n in meta["usage"].values())


# ---------------------------------------------------------------------------- pool selection

def test_weighted_repeats_by_weight_in_fixed_order():
    gens = available_generators(GenOptions())
    pool = weighted(gens)
    assert len(pool) == sum(g.weight for g in gens)
    expected = [g for g in gens for _ in range(g.weight)]
    assert [g.name for g in pool] == [g.name for g in expected]
    assert [g.name for g in weighted(gens)] == [g.name for g in pool]
    assert weighted([]) == []


def test_available_generators_simplest_first():
    """Rule 4: index 0 (where shrinking goes) is the simplest -- families in FAMILY_ORDER, then by name."""
    from oxpecker.ir.registry import FAMILY_ORDER
    gens = available_generators(GenOptions())
    ranks = [FAMILY_ORDER.index(g.family) if g.family in FAMILY_ORDER else len(FAMILY_ORDER) for g in gens]
    assert ranks == sorted(ranks)
    assert gens[0].family == "values"
    for rank in set(ranks):
        names = [g.name for g, r in zip(gens, ranks) if r == rank]
        assert names == sorted(names)


def test_available_generators_filters_toggles(throwaway):
    generator(throwaway, family="f", toggle="zz_toggle")(lambda b: st.just(1))
    assert throwaway not in [g.name for g in available_generators(GenOptions())]
    assert throwaway in [g.name for g in available_generators(GenOptions(toggles=frozenset({"zz_toggle"})))]


def test_available_generators_strict_drops_sloppy_only(throwaway):
    generator(throwaway, family="f", sloppy_only=True)(lambda b: st.just(1))
    assert throwaway in [g.name for g in available_generators(GenOptions())]
    assert throwaway not in [g.name for g in available_generators(GenOptions(strict=True))]


def test_available_generators_allowlist():
    got = available_generators(GenOptions(generators=("unary", "number", "does_not_exist")))
    assert [g.name for g in got] == ["number", "unary"]
    assert available_generators(GenOptions(generators=())) == []


def test_available_templates_sorted_and_filtered():
    tpls = available_templates(GenOptions())
    assert [t.name for t in tpls] == ["plain"] + sorted(set(TEMPLATES) - {"plain"})   # simplest first
    assert [t.name for t in available_templates(GenOptions(templates=("plain",)))] == ["plain"]
    assert available_templates(GenOptions(templates=("nope",))) == []
    saved = TEMPLATES["plain"]
    try:
        TEMPLATES["plain"] = type(saved)(**{**saved.__dict__, "toggle": "zz_toggle"})
        assert "plain" not in [t.name for t in available_templates(GenOptions())]
        assert "plain" in [t.name for t in available_templates(GenOptions(toggles=frozenset({"zz_toggle"})))]
    finally:
        TEMPLATES["plain"] = saved


# ---------------------------------------------------------------------------- swarm

@RELAXED
@given(ir_programs(GenOptions(swarm=True)))
def test_swarm_subset_is_nonempty(res):
    _, meta = res
    assert meta["enabled"]
    assert set(meta["enabled"]) <= set(GENERATORS)


@pytest.mark.xfail(strict=True, reason=(
    "known gap, decision D23 (corrected): hypothesis.find's minimal example does not re-enable every generator "
    "(13 of 23 with derandomize=True). Re-enabling an excluded generator changes the pool, so the same "
    "choice sequence generates a different (often longer) program; the shrinker then rejects the "
    "'excluded=False' step as shortlex-larger and leaves booleans at True."))
def test_swarm_minimal_example_enables_all_generators():
    # Five fixed seeds (deterministic); measured 2026-10-25: random runs end with 11-32 of 32 enabled.
    import random
    opts = GenOptions(swarm=True)
    want = sorted(g.name for g in available_generators(opts))
    for s in range(5):
        _, meta = find(ir_programs(opts), lambda r: True, random=random.Random(s),
                       settings=settings(RELAXED, max_examples=1000))   # shrink budget scales with max_examples
        assert meta["enabled"] == want, f"seed {s}: {len(meta['enabled'])} of {len(want)} enabled"


def test_no_swarm_enables_everything():
    opts = GenOptions(swarm=False)

    @RELAXED
    @given(ir_programs(opts))
    def run(res):
        assert res[1]["enabled"] == sorted(g.name for g in available_generators(opts))

    run()


# ---------------------------------------------------------------------------- programs

@RELAXED
@given(ir_programs(GenOptions()))
def test_ir_programs_verify(res):
    prog, _ = res
    verify(prog)


@RELAXED
@given(programs(GenOptions()))
def test_programs_record(p):
    assert isinstance(p, Program)
    verify(p.ir)
    assert p.options["ir"] is True
    lines = p.source.splitlines()
    assert lines[0] == f"// template: {p.template}"
    assert lines[1] == "// generators: " + ", ".join(p.enabled_generators)
    assert "__probe(" not in p.source_plain
    assert "// [" not in p.source_plain
    assert p.template in TEMPLATES
    for span in p.generator_spans:
        a, z = span["lines"]
        assert 1 <= a <= z <= len(lines)
        assert span["gen"] in GENERATORS or span["gen"] in TEMPLATES   # templates emit code too


@RELAXED
@given(programs(GenOptions(comments=True)))
def test_generator_spans_match_comment_lines(p):
    lines = p.source.splitlines()
    assert p.generator_spans
    seen_comment = False
    for span in p.generator_spans:
        a = span["lines"][0]
        # `lines` are counted from the top of the file; the marker is the line just before the span
        # whenever the span starts a new top-level statement.
        before = lines[a - 1]
        if before.startswith("// ["):
            seen_comment = True
            assert before == f"// [{span['gen']}]"
    assert seen_comment
    # every marker names the generator of the span that follows it
    markers = [(i, ln) for i, ln in enumerate(lines) if ln.startswith("// [")]
    starts = {s["lines"][0]: s["gen"] for s in p.generator_spans}
    for i, ln in markers:
        assert starts.get(i + 1) == ln[4:-1], (i, ln)


@RELAXED
@given(programs(GenOptions(probes=False)))
def test_probes_off(p):
    assert "__probe(" not in p.source


@RELAXED
@given(programs(GenOptions(probes=True)))
def test_probes_on_emit_probes(p):
    assert "__probe(" in p.source


@RELAXED
@given(programs(GenOptions(comments=False)))
def test_comments_off(p):
    assert not any(ln.startswith("// [") for ln in p.source.splitlines())


def test_generate_programs_deterministic():
    a = generate_programs(GenOptions(), 10, seed=3, ir=True)
    b = generate_programs(GenOptions(), 10, seed=3, ir=True)
    assert len(a) == len(b) == 10
    assert [p.source for p in a] == [p.source for p in b]
    assert all(p.options["ir"] for p in a)


# ---------------------------------------------------------------------------- catalogue

def test_catalogue_has_entry_per_generator_and_template():
    entries = catalogue()
    assert sorted(e["id"] for e in entries if e["kind"] == "generator") == sorted(GENERATORS)
    assert sorted(e["id"] for e in entries if e["kind"] == "template") == sorted(TEMPLATES)
    assert len(entries) == len(GENERATORS) + len(TEMPLATES)
    for e in entries:
        src_fn = registry._SOURCES[e["id"]]
        assert e["sourceSnippet"], e["id"]
        assert f"def {src_fn.__name__}(" in e["sourceSnippet"], e["id"]
        assert e["sourcePath"] and e["sourcePath"].endswith(".py"), e["id"]
        assert e["description"] == (src_fn.__doc__ or "").strip()


# ---------------------------------------------------------------------------- per generator

@st.composite
def _single_generator_program(draw, name: str):
    b = fresh_builder()
    for _ in range(draw(st.integers(0, 2))):
        draw(GENERATORS[draw(st.sampled_from(["number", "int32", "string"]))].fn(b))
    draw(GENERATORS[name].fn(b))
    return b


@pytest.mark.parametrize("name", ALL_NAMES)
def test_generator_alone(name, tmp_path: Path):
    sources: list[str] = []

    @SMALL
    @given(_single_generator_program(name))
    def run(b):
        prog = b.program()
        verify(prog)
        assert any(i.gen == name for i in prog), f"{name} emitted nothing attributed to it"
        assert b.gen_usage.get(name, 0) >= 1
        sources.append(lift(prog).source)
        sources.append(lift(prog, probes=False, comments=False).source)

    run()
    assert sources
    if NODE:
        files = []
        for k, s in enumerate(sources):
            f = tmp_path / f"{name}_{k}.js"
            f.write_text(s)
            files.append(f)
        # one node invocation per file (--check takes a single script), all in one test
        bad = []
        for f in files:
            r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(f.read_text() + r.stderr)
        assert not bad, bad[0]
