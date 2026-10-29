"""Property: everything the generators and the lifter produce is JavaScript Rhino's parser accepts.

A syntax error here is a generator or lifter bug, never a Rhino finding; the property shrinks it
to the smallest program the lifter gets wrong. Only the parser is under test, so only the
interpreted mode runs (both modes share it). Needs the harness (`cd harness-java && ./gradlew fatJar`).

    pytest tests/il/test_lift_rhino.py
    OXPECKER_EXAMPLES=500 pytest tests/il/test_lift_rhino.py
"""

from __future__ import annotations

import os

import pytest
from hypothesis.errors import InvalidArgument
from hypothesis import HealthCheck, given, note, settings
from hypothesis.database import DirectoryBasedExampleDatabase

import oxpecker.il.templates  # noqa: F401
from oxpecker.il.lift import LiftOptions, lift
from oxpecker.il.options import GenOptions
from oxpecker.il.mutate import evolved
from oxpecker.il.strategies import programs

EXAMPLES = int(os.environ.get("OXPECKER_EXAMPLES", "100"))
PARSE = settings(max_examples=EXAMPLES, deadline=None,
                 database=DirectoryBasedExampleDatabase(".hypothesis/oxpecker-il-parse"),
                 suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much])


def assert_parses(harness, source: str, *, strict: bool = False) -> None:
    run = harness.run(source, modes=("interpreted",), strict=strict, seed=1, wall_ms=3000)
    o = run.results["interpreted"]
    try:
        note(source)
    except InvalidArgument:          # called outside a Hypothesis test
        pass
    assert o.status != "syntax", f"Rhino rejects the lifted program: {o.thrown}\n{source}"
    assert o.status != "crash" or "Unexpected node type" not in str(o.engineException), f"Rhino's compiler crashed on:\n{source}"


@PARSE
@given(programs(GenOptions()))
def test_lifted_programs_parse(harness, gen):
    assert_parses(harness, gen.source)             # with probes and comments
    assert_parses(harness, gen.plain_source)       # the plain reproducer


@PARSE
@given(programs(GenOptions(strict=True)))
def test_strict_programs_parse_as_strict_scripts(harness, gen):
    assert_parses(harness, gen.source, strict=True)


@PARSE
@given(programs(GenOptions(max_depth=4, max_statements=16)))
def test_deeply_nested_programs_parse(harness, gen):
    assert_parses(harness, gen.plain_source)


@PARSE
@given(evolved(GenOptions(max_statements=8), steps=4))
def test_mutated_programs_parse(harness, l):
    assert_parses(harness, l.source)
    assert_parses(harness, l.plain_source)


def test_the_check_rejects_invalid_javascript(harness):
    """The property is only as good as its check: a syntax error must fail it."""
    for bad in ("var x = ;", "{}.m();", "var 1x = 2;"):
        with pytest.raises(AssertionError, match="Rhino rejects"):
            assert_parses(harness, bad)
    assert_parses(harness, "({}).m;")
