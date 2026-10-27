"""Java identity hashes in printed values are not a divergence: `JavaProbe@3bde59e5` differs
between any two runs (found by the first ten-minute campaign, 2026-10-26)."""

from oxpecker.harness.client import Outcome
from oxpecker.harness.compare import EQUAL, HARD, canon_identity, compare


def test_identity_hash_is_canonicalised():
    assert canon_identity("x dev.oxpecker.harness.JavaProbe@3bde59e5 y") == "x dev.oxpecker.harness.JavaProbe@ y"
    assert canon_identity("@0Object<Array.prototype>{}") == "@0Object<Array.prototype>{}"     # canon's own @N ids stay
    assert canon_identity(None) is None


def test_outcomes_differing_only_by_identity_hash_are_equal():
    a = Outcome(mode="interpreted", status="ok", value="dev.oxpecker.harness.JavaProbe@1a2b", trace=['"p" | java.lang.Object@ff'])
    b = Outcome(mode="compiled", status="ok", value="dev.oxpecker.harness.JavaProbe@c3d4", trace=['"p" | java.lang.Object@01'])
    assert compare(a, b).kind == EQUAL
    c = Outcome(mode="compiled", status="ok", value="dev.oxpecker.harness.Other@c3d4", trace=['"p" | java.lang.Object@01'])
    assert compare(a, c).kind == HARD


def test_unsupported_in_both_modes_is_skipped():
    from oxpecker.harness.compare import SKIP
    a = Outcome(mode="interpreted", status="unsupported", reason="continuations", trace=['"step" | 1'], continuations=['pause("x")'])
    b = Outcome(mode="compiled", status="unsupported", reason="continuations", trace=[])
    assert compare(a, b).kind == SKIP
    c = Outcome(mode="compiled", status="ok", value="1")
    assert compare(a, c).kind == HARD            # one mode finished, the other could not: still a finding


def test_compiled_refusal_caught_by_the_program_is_skipped():
    """A pause inside the program's own try/catch: the interpreter records it, the compiled mode's
    refusal is swallowed by the catch and both modes go on to throw or finish (2026-10-27
    campaign). Same class as both-unsupported."""
    from oxpecker.harness.compare import SKIP
    a = Outcome(mode="interpreted", status="throw", trace=['"step" | 1'], continuations=['pause("in-try")'])
    b = Outcome(mode="compiled", status="throw", trace=['"step" | 1'], continuations=[])
    assert compare(a, b).kind == SKIP
    c = Outcome(mode="compiled", status="throw", trace=['"step" | 1'], continuations=['pause("in-try")'])
    assert compare(a, c).kind == EQUAL


def test_java_trace_identity_hashes_are_canonicalised_too():
    a = Outcome(mode="interpreted", status="ok", javaTrace=['typeOf("dev.oxpecker.harness.JavaProbe@ef2de") -> "String"'])
    b = Outcome(mode="compiled", status="ok", javaTrace=['typeOf("dev.oxpecker.harness.JavaProbe@a7df") -> "String"'])
    assert compare(a, b).kind == EQUAL
