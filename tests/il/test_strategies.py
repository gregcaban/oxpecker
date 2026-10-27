"""Strategy plumbing: fixed pools, context-independent picks, programs() verify, lifting choices."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import oxpecker.il.templates  # noqa: F401  (registers generators and templates)
from oxpecker.il import ops
from oxpecker.il.analysis import verify
from oxpecker.il.builder import Builder
from oxpecker.il.lift import LiftOptions, lift
from oxpecker.il.ops import Context, Lift
from oxpecker.il.options import GenOptions
from oxpecker.il.strategies import (
    FAMILY_ORDER,
    REGISTRY,
    TEMPLATES,
    VALUE_MAKERS,
    draw,
    fallback,
    generate,
    lifting_choices,
    maybe,
    pick,
    pools,
    programs,
    variable,
)
from oxpecker.il.types import ANY, FUNCTION, INTEGER, NUMBER, OBJECT, STRING

FAST = settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


def test_registry_is_populated_and_sorted_simplest_first():
    assert len(REGISTRY) > 60 and len(TEMPLATES) >= 4
    p = pools(GenOptions())
    names = [s.name for s in p.statements]
    assert names[0] == "integer"                                         # values first, the simplest value first
    families = [FAMILY_ORDER.index(s.family) for s in p.statements]
    assert families == sorted(families)
    assert all(s.family == "members" for s in p.members) and p.members
    assert all(s.family in ("values",) for s in p.values) and len(p.values) >= 8
    for t in (INTEGER, NUMBER, STRING, OBJECT, FUNCTION, ANY):
        assert t in VALUE_MAKERS


def test_pool_is_fixed_across_contexts_and_weights_are_repetition():
    p = pools(GenOptions())
    assert p.for_context(Context.JAVASCRIPT) is p.statements
    assert p.for_context(Context.JAVASCRIPT | Context.SUBROUTINE | Context.LOOP) is p.statements
    assert p.for_context(Context.OBJECT_LITERAL) is p.members
    assert p.for_context(Context.SWITCH_BLOCK) is p.cases or p.cases == ()
    counts = {}
    for s in p.statements:
        counts[s.name] = counts.get(s.name, 0) + 1
    assert counts["binary"] == REGISTRY["binary"].weight and counts["void"] == 1
    half = pools(GenOptions(weights={"binary": 0.5}))
    assert sum(1 for s in half.statements if s.name == "binary") == round(REGISTRY["binary"].weight * 0.5)
    none = pools(GenOptions(weights={"operations": 0}))
    assert not any(s.family == "operations" for s in none.statements)
    only = pools(GenOptions(families=frozenset({"values"})))
    assert all(s.family == "values" for s in only.statements)


def test_fallback_is_the_first_applicable_entry():
    b = Builder()
    p = pools(GenOptions())
    first = fallback(p.statements, b)
    assert first is not None and first.family == "values"
    assert not REGISTRY["return"].applicable(b) and not REGISTRY["loop_break"].applicable(b)
    assert REGISTRY["super_property_retrieval"].applicable(b)          # scaffolds its own context
    b.depth = 0
    assert not REGISTRY["plain_function"].applicable(b)               # no nesting budget left


@given(st.data())
@FAST
def test_pick_and_maybe(data):
    xs = list(range(1, 10))
    assert pick(data.draw, xs) in xs
    assert data.draw(maybe(0.5)) in (True, False)


@given(st.data())
@FAST
def test_variable_draws_or_makes_a_value_of_the_type(data):
    b = Builder()
    v = data.draw(variable(b, STRING))
    assert b.type_of(v).Is(STRING)
    w = data.draw(variable(b, FUNCTION))
    assert b.type_of(w).MayBe(FUNCTION)
    b.finish()


@given(programs(GenOptions()))
@FAST
def test_programs_verify_and_lift(gen):
    code = gen.code
    verify(code)
    assert all(i.lift is not None for i in code if i.op.outputs() == 1 and not i.op.is_block)
    assert gen.template in TEMPLATES
    src = gen.source
    assert "__probe(" in src
    assert "__probe(" not in gen.plain_source


@given(programs(GenOptions(strict=True, min_statements=1, max_statements=4)))
@FAST
def test_strict_programs_have_no_sloppy_constructs(gen):
    assert all(not isinstance(i.op, ops.BeginWith) for i in gen.code)
    assert gen.source.startswith('"use strict";')


@given(programs(GenOptions(min_statements=1, max_statements=3, toggles=frozenset())))
@FAST
def test_toggles_remove_generators(gen):
    names = {i.gen for i in gen.code}
    assert not names & {"generator_function", "for_of_loop", "destruct_array", "destruct_object", "with_statement", "eval"}


def test_lifting_choices_fill_every_value():
    b = Builder()
    b.load_int(1)
    b.emit(ops.LoadInteger(2), lift=Lift.CONST)
    code = b.finish()
    filled = draw(lifting_choices(code), seed=1)
    assert filled[0].lift is not None and filled[1].lift is Lift.CONST


def test_provenance_is_stamped():
    gen = generate(seed=5, min_statements=2, max_statements=4)
    assert any(i.gen for i in gen.code)
    assert all(i.gen or isinstance(i.op, ops.Probe) for i in gen.code if i.index >= 0)
