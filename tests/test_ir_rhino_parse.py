"""Property: everything the IR generators and lifter produce is JavaScript Rhino's parser accepts.

A syntax error here is a generator or lifter bug, never a Rhino finding: the property shrinks it to
the smallest program the lifter gets wrong. Only the parser is under test, so only the interpreted
mode runs (both modes share the parser; compiled-mode codegen problems are findings and belong to
the differential test). Needs the harness (`cd harness-java && ./gradlew fatJar`).

    pytest tests/test_ir_rhino_parse.py
    OXPECKER_EXAMPLES=1000 pytest tests/test_ir_rhino_parse.py
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, currently_in_test_context, given, note, settings
from hypothesis.database import DirectoryBasedExampleDatabase

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.strategies import Program
from oxpecker.ir.lift import lift
from oxpecker.ir.strategies import programs

EXAMPLES = int(os.environ.get("OXPECKER_EXAMPLES", "100"))
PARSE = settings(max_examples=EXAMPLES, deadline=None,
                 database=DirectoryBasedExampleDatabase(".hypothesis/oxpecker-ir-parse"),
                 suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


def assert_parses(harness, source: str, *, strict: bool = False) -> None:
    run = harness.run(source, modes=("interpreted",), strict=strict, seed=1)
    o = run.results["interpreted"]
    if currently_in_test_context():
        note(source)
    assert o.status != "syntax", f"Rhino rejects the lifted program: {o.thrown}\n{source}"


@PARSE
@given(programs(GenOptions()))
def test_lifted_programs_parse(harness, prog: Program):
    assert_parses(harness, prog.source)            # with probes and comments
    assert_parses(harness, prog.source_plain)      # the plain reproducer


@PARSE
@given(programs(GenOptions(probes=False, comments=False)))
def test_programs_lifted_without_inlining_parse(harness, prog: Program):
    # Every temporary as its own statement: the other extreme of the lifting policy.
    assert_parses(harness, lift(prog.ir, probes=False, comments=False, inline=False).source)


@PARSE
@given(programs(GenOptions(strict=True)))
def test_strict_programs_parse_as_strict_scripts(harness, prog: Program):
    assert_parses(harness, prog.source, strict=True)


def test_the_check_rejects_invalid_javascript(harness):
    """The property is only as good as its check: a syntax error must fail it."""
    import pytest
    # (Not `function () {}`: Rhino accepts an anonymous function declaration statement, which node
    # and the spec reject -- a Rhino leniency, noted 2026-10-25.)
    for bad in ("var x = ;", "{}.m();", "var 1x = 2;"):
        with pytest.raises(AssertionError, match="Rhino rejects"):
            assert_parses(harness, bad)
    assert_parses(harness, "({}).m;")
