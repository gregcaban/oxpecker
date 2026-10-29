"""Mutators (design §10 item 8): children differ from parents and verify, fewer than 35 % of
mutations are rejected, mutations are local (median share of the parent kept >= 90 %),
lineages shrink to the needed step; and what each oxpecker mutator must do to the Rhino model."""

from __future__ import annotations

import difflib
import statistics
from collections import Counter

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import oxpecker.il.templates  # noqa: F401
from oxpecker.il import ops
from oxpecker.il.analysis import analyse, verify
from oxpecker.il.lift import LiftOptions, lift
from oxpecker.il.mutate import (MUTATORS, Lineage, Rejected, evolved, insertion_points, lineage, mutate, mutation,
                                mutator_weights, mutators, try_mutation)
from oxpecker.il.options import GenOptions
from oxpecker.il.program import signature
from oxpecker.il.strategies import draw, generate, programs

SMALL = dict(max_statements=6, max_depth=2)
SEEDS = range(12)


def _kept(parent, child) -> float:
    a = [i.op.name for i in parent.code]
    b = [i.op.name for i in child.code]
    return difflib.SequenceMatcher(None, a, b).ratio() * (len(a) + len(b)) / (2 * len(a))


@pytest.mark.parametrize("name", mutators())
def test_each_mutator_changes_the_program_and_the_child_verifies(name):
    changed = 0
    rejected = 0
    for seed in SEEDS:
        parent = generate(seed, **SMALL)
        try:
            m = mutate(parent, seed=seed, name=name)
        except Rejected:
            rejected += 1
            continue
        assert m.name == name
        assert signature(m.code) != signature(parent.code), f"{name} returned the parent unchanged"
        verify(m.code, strict=m.program.options.strict)
        lift(m.code, LiftOptions())
        assert all(i.index == k for k, i in enumerate(m.code))
        assert any(i.step == 1 for i in m.code) or name == "activation", "the changed instructions carry step=1"
        changed += 1
    assert changed > 0, f"{name} was never applicable on {len(SEEDS)} programs"


def test_rejection_rate_is_below_35_percent():
    total = rejected = 0
    for seed in SEEDS:
        parent = generate(seed, **SMALL)
        for name, _ in MUTATORS:
            total += 1
            try:
                mutate(parent, seed=seed, name=name)
            except Rejected:
                rejected += 1
    assert rejected / total < 0.35, f"{rejected}/{total} rejected"


def test_mutations_are_local():
    kept = []
    for seed in SEEDS:
        parent = generate(seed, **SMALL)
        for name, _ in MUTATORS:
            if name == "combine":
                continue       # appends a whole program by design
            try:
                m = mutate(parent, seed=seed, name=name)
            except Rejected:
                continue
            kept.append(_kept(parent, m.program))
    assert statistics.median(kept) >= 0.9, statistics.median(kept)


def test_mutate_is_deterministic_and_diff_shows_the_change():
    parent = generate(2, **SMALL)
    a = mutate(parent, seed=5)
    b = mutate(parent, seed=5)
    assert a.name == b.name and signature(a.code) == signature(b.code)
    assert "parent" in a.diff() and ("+" in a.diff() or "-" in a.diff())


def test_declaration_kind_never_makes_an_assigned_variable_const():
    seen = set()
    for seed in SEEDS:
        parent = generate(seed, **SMALL)
        try:
            m = mutate(parent, seed=seed, name="declaration_kind")
        except Rejected:
            continue
        an = analyse(m.code)
        for instr in m.code:
            if isinstance(instr.op, ops.Declare):
                seen.add(instr.op.kind)
                if instr.op.kind is ops.DeclarationKind.CONST:
                    assert len(an.assignments.get(instr.output, [])) <= 1
    assert seen, "no Declare site was ever flipped"


def test_activation_flips_the_model():
    flipped = 0
    for seed in SEEDS:
        parent = generate(seed, **SMALL)
        try:
            m = mutate(parent, seed=seed, name="activation")
        except Rejected:
            continue
        before = analyse(parent.code).rhino
        after = analyse(m.code).rhino
        # one function gained or lost its only reason (an inserted arrow is itself a new function)
        assert Counter(before.activation.values()) != Counter(after.activation.values())
        flipped += 1
    assert flipped > 0


def test_number_flip_turns_a_number_variable_into_any():
    flipped = 0
    for seed in SEEDS:
        parent = generate(seed, **SMALL)
        before = analyse(parent.code).rhino
        if not before.number_var:
            continue
        try:
            m = mutate(parent, seed=seed, name="number_flip")
        except Rejected:
            continue
        after = analyse(m.code).rhino
        assert len(after.number_var) < len(before.number_var) or any(i.gen == "number_flip" for i in m.code)
        flipped += 1
    assert flipped > 0


def test_insertion_points_skip_dead_code_and_literals():
    from oxpecker.il.builder import Builder
    from oxpecker.il.environment import Keys
    from oxpecker.il.ops import Parameters
    b = Builder()
    with b.plain_function(Parameters(0)) as (f, _):
        b.do_return(b.load_int(1))
    v = b.load_int(2)
    with b.object_literal() as lit:
        lit.add_property(Keys.A, v)
    code = b.finish()
    an = analyse(code)
    points = insertion_points(code, an)
    ret = next(i for i, instr in enumerate(code) if isinstance(instr.op, ops.Return))
    assert ret + 1 not in points                       # after the return: dead
    inside = next(i for i, instr in enumerate(code) if isinstance(instr.op, ops.ObjectLiteralAddProperty))
    assert inside not in points                        # inside the literal: no statements
    assert 0 in points and len(code) in points


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                                                 HealthCheck.filter_too_much])
@given(evolved(GenOptions(**SMALL), steps=3))
def test_lineages_verify_and_lift(l: Lineage):
    assert isinstance(l, Lineage)
    for step in l.steps:
        verify(step.program.code, strict=l.options.strict)
        lift(step.program.code, LiftOptions())
    assert str(l)                                     # the final program lifts


def test_lineage_shell_api_is_deterministic():
    a = lineage(seed=4, steps=3, **SMALL)
    b = lineage(seed=4, steps=3, **SMALL)
    assert [s.name for s in a.steps] == [s.name for s in b.steps]
    assert signature(a.code) == signature(b.code)
    assert repr(a).startswith("<Lineage ")


def test_lineages_shrink_to_the_needed_step():
    """A property that needs one particular mutator shrinks to a lineage with that single step.
    The search runs through the seeded provider (`find` would use Hypothesis's stock one, whose
    draws depend on which modules are loaded, G43) and the shrink from a saved example, the way
    `fuzz.Shrinker` does it."""
    from hypothesis import Phase
    from hypothesis.control import BuildContext
    from hypothesis.database import InMemoryExampleDatabase, choices_to_bytes

    from oxpecker.il.strategies import seeded_data

    strategy = evolved(GenOptions(max_statements=3, max_depth=1), steps=3)
    needed = lambda l: any(s.name == "reassign" for s in l.steps)   # noqa: E731
    for seed in range(300):
        data = seeded_data(seed)
        with BuildContext(data, wrapped_test=lambda: None):
            found = data.draw(strategy)
        if needed(found):
            data.freeze()
            break
    else:
        pytest.fail("no lineage with a reassign step in 300 seeds")
    key = b"oxpecker.test.shrink_to_the_needed_step"
    db = InMemoryExampleDatabase()
    db.save(key + b".secondary", choices_to_bytes(tuple(data.choices)))   # the primary key counts as already shrunk
    seen: list[Lineage] = []

    class Needed(Exception):
        pass

    @settings(database=db, phases=(Phase.reuse, Phase.shrink), max_examples=400, deadline=None, derandomize=False,
              suppress_health_check=list(HealthCheck), print_blob=False, report_multiple_bugs=False)
    @given(strategy)
    def shrink(l: Lineage) -> None:
        if needed(l):
            seen.append(l)
            raise Needed

    shrink._hypothesis_internal_database_key = key
    with pytest.raises(Needed):
        shrink()
    minimal = seen[-1]                      # Hypothesis re-runs the minimal failing example last
    assert [s.name for s in minimal.steps] == ["reassign"], [s.name for s in minimal.steps]
    assert len(minimal.steps) <= len(found.steps)


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                                                 HealthCheck.filter_too_much])
@given(st.data())
def test_mutation_strategy_rejects_instead_of_raising(data):
    parent = data.draw(programs(GenOptions(**SMALL)))
    child = data.draw(mutation(parent.program))
    verify(child.code)


def test_mutator_weights_lower_combine_and_codegen_and_lineages_count_per_mutator():
    """G50: `combine` and `codegen` are drawn less than the rest; G51: a lineage counts the steps
    it tried and rejected per mutator, consistently with its depth."""
    w = mutator_weights()
    assert set(w) == set(mutators()) and w["combine"] < w["codegen"] < w["operation"]
    attempted = {name: 0 for name in mutators()}
    for seed in range(120):
        l = lineage(seed, steps=3, max_statements=3, max_depth=1)
        assert sum(l.attempted_by.values()) == l.depth == len(l.steps) + l.rejected
        assert sum(l.rejected_by.values()) == l.rejected
        assert all(l.rejected_by[n] <= l.attempted_by.get(n, 0) for n in l.rejected_by)
        for name, n in l.attempted_by.items():
            attempted[name] += n
    total = sum(attempted.values())
    assert total >= 60, total
    share = w["combine"] / sum(w.values())
    assert attempted["combine"] / total < 2.5 * share + 0.02, attempted     # about 4 % expected, 11 % uniform
