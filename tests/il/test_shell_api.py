"""Each module's shell-facing entry point: one call per pipeline step, deterministic from a seed."""

from __future__ import annotations

from oxpecker.il.analysis import analyse, explain
from oxpecker.il.builder import Builder
from oxpecker.il.lift import js
from oxpecker.il.ops import BinaryOperator
from oxpecker.il.program import Program, dump
from oxpecker.il.strategies import generate, generators, run_generator, templates
from oxpecker.il.types import NUMBER


def test_generate_is_deterministic_from_a_seed():
    a, b = generate(seed=7, max_statements=5), generate(seed=7, max_statements=5)
    assert a.code == b.code and a.source == b.source
    assert isinstance(a.program, Program) and str(a) == a.source and "instructions" in repr(a)
    assert dump(a) == dump(a.code) == str(a.program)


def test_run_generator_alone():
    s = run_generator("while_loop", seed=1)
    assert s.emitted and any(i.op.name == "BeginWhileLoopBody" for i in s.emitted)
    assert "while (" in s.js and str(s) == s.js and "instructions" in repr(s)
    assert run_generator("binary", seed=2).emitted[-1].op.name == "BinaryOperation"


def test_hand_built_il_through_every_step():
    b = Builder()
    n = b.declare(b.load_int(1))
    b.update(n, b.load_int(2), BinaryOperator.ADD)
    assert js(b).splitlines() == ["var n1 = 1;", "n1 += 2;"]
    assert analyse(b).types[n].Is(NUMBER)            # integer at first, number after `+= 2`
    text = explain(b)
    assert "Declare" in dump(b) and "v1: integer [top_var]" in text and str(analyse(b)) == text


def test_explain_reports_the_rhino_model():
    b = Builder()
    with b.plain_function(named=True) as (f, _):
        n = b.declare(b.load_int(1))
        b.reassign(n, b.binary(n, b.load_int(2), BinaryOperator.MUL))
    text = explain(b)
    assert "activation: none (optimizer runs)" in text and "number-var" in text


def test_registry_listing():
    assert "binary" in generators("operations") and "plain" in templates()
    assert set(generators("members")) >= {"literal_property", "literal_method"}


def test_strategies_alone_in_a_fresh_interpreter_registers_everything():
    """A shell that imports only `oxpecker.il.strategies` (as the package docstring suggests) must
    not have to know about the templates module: the registries fill on first use. Other tests
    import templates, so this has to run in its own process."""
    import subprocess
    import sys

    code = (
        "from oxpecker.il.strategies import programs, generate, run_generator, generators, templates\n"
        "print(repr(programs().example()))\n"
        "print(repr(generate(seed=1)), repr(run_generator('while_loop', seed=1)))\n"
        "print(len(generators()), templates())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert "<Generated " in out and "<Snippet while_loop" in out and "'plain'" in out
