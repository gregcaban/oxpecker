"""Mutators (oxpecker/ir/mutate.py) and lineages: invariants, what each mutator changes, rejection
rates, locality, and that Hypothesis shrinks a lineage down to the steps a property needs."""

from __future__ import annotations

import shutil
import subprocess
from collections import Counter

import pytest
from hypothesis import HealthCheck, assume, find, given, seed, settings
from hypothesis import strategies as st

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.types import T
from oxpecker.ir.analysis import analyse, verify
from oxpecker.ir.lift import lift
from oxpecker.ir.mutate import (CLASSES, MUTATORS, Lineage, _class, activation_mutation, evolved, input_mutation,
                                mutation, operation_mutation, reassign_mutation)
from oxpecker.ir.ops import BeginFunction, Eval, Reassign, signature
from oxpecker.ir.strategies import ir_programs

OPTS = GenOptions(max_statements=6)
RELAXED = settings(deadline=None, database=None, suppress_health_check=list(HealthCheck))
APPLIES = {name: applies for name, _, applies in MUTATORS}
STRATEGY = {name: m for name, m, _ in MUTATORS}


@st.composite
def parent_and_child(draw, name: str):
    """A generated parent and one application of mutator `name` (skipped where it does not apply)."""
    prog, _ = draw(ir_programs(OPTS))
    an = analyse(prog)
    assume(APPLIES[name](prog, an))
    return prog, draw(STRATEGY[name](prog, an, OPTS))


def _lcs(a: list, b: list) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------- per mutator

@pytest.mark.parametrize("name", [n for n, _, _ in MUTATORS])
def test_mutator_children_mostly_verify_and_always_differ(name):
    counts = Counter()

    @seed(7)
    @settings(RELAXED, max_examples=80)
    @given(parent_and_child(name))
    def check(pc):
        parent, child = pc
        assert child != parent
        try:
            verify(child)
            counts["valid"] += 1
        except Exception:
            counts["invalid"] += 1

    check()
    total = counts["valid"] + counts["invalid"]
    assert total > 20
    assert counts["invalid"] / total < 0.35, counts      # an invalid child is a wasted harness run


@seed(3)
@settings(RELAXED, max_examples=60)
@given(parent_and_child("operation"))
def test_operation_changes_exactly_one_instruction_and_never_to_itself(pc):
    parent, child = pc
    assert len(parent) == len(child)
    diffs = [i for i, (a, b) in enumerate(zip(parent, child)) if a != b]
    assert len(diffs) == 1
    a, b = parent[diffs[0]], child[diffs[0]]
    assert a.inputs == b.inputs and a.outputs == b.outputs and (a.op != b.op or a.keep != b.keep)


@seed(3)
@settings(RELAXED, max_examples=60)
@given(parent_and_child("input"))
def test_input_rewires_exactly_one_operand_to_a_visible_variable(pc):
    parent, child = pc
    diffs = [i for i, (a, b) in enumerate(zip(parent, child)) if a != b]
    assert len(diffs) == 1
    i = diffs[0]
    changed = [k for k, (x, y) in enumerate(zip(parent[i].inputs, child[i].inputs)) if x != y]
    assert len(changed) == 1
    assert child[i].inputs[changed[0]] in analyse(parent).visible_at[i]


@seed(3)
@settings(RELAXED, max_examples=60)
@given(parent_and_child("reassign"))
def test_reassign_crosses_a_type_class(pc):
    parent, child = pc
    an = analyse(parent)
    (p,) = [i for i, ins in enumerate(child) if isinstance(ins.op, Reassign) and ins.gen == "mutation:reassign"]
    v, w = child[p].inputs
    assert an.assignable(v)
    assert _class(an.type_at(p, v)) != _class(an.type_at(p, w))


@seed(3)
@settings(RELAXED, max_examples=40)
@given(parent_and_child("activation"))
def test_activation_moves_a_function_onto_the_activation_path(pc):
    parent, child = pc
    before, after = analyse(parent), analyse(child)
    flipped = [f for f, a in after.activation.items() if a and not before.activation.get(f, False)]
    assert len(flipped) == 1


def test_codegen_inside_a_strict_function_emits_no_sloppy_only_code():
    """D29: the pool at a strict insertion point has no sloppy-only generators."""
    from oxpecker.ir.builder import Builder
    from oxpecker.ir.mutate import codegen_mutation

    b = Builder(OPTS)
    with b.function(0, strict=True) as _:
        b.ret(b.const(T.INT32, "1"))
    prog = b.program()

    @seed(5)
    @settings(RELAXED, max_examples=60)
    @given(st.data())
    def check(data):
        child = data.draw(codegen_mutation(prog, analyse(prog), OPTS))
        inside = child[1:child.index(next(ins for ins in child[1:] if type(ins.op).__name__ == "EndFunction"))]
        assert not any(isinstance(ins.op, Eval) and "with (" in ins.op.code for ins in inside)

    check()


# ---------------------------------------------------------------------------- lineages

@RELAXED
@given(evolved(OPTS))
def test_lineages_verify_and_lift(lin: Lineage):
    verify(lin.program)
    lift(lin.program)
    assert all(s in STRATEGY for s in lin.steps)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_lineages_parse_with_node(tmp_path):
    files = []

    @seed(2)
    @settings(RELAXED, max_examples=60)
    @given(evolved(OPTS))
    def collect(lin):
        f = tmp_path / f"l{len(files)}.js"
        f.write_text(lift(lin.program).source)
        files.append(f)

    collect()
    for f in files:
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr + f.read_text()


def test_one_mutation_keeps_most_of_the_parent():
    """Locality (proposal §4.3): one IR mutation preserves the parent's instructions."""
    kept: list[float] = []

    @seed(11)
    @settings(RELAXED, max_examples=80)
    @given(st.data())
    def check(data):
        prog, _ = data.draw(ir_programs(OPTS))
        _, child = data.draw(mutation(prog, OPTS))
        parent = [signature(i) for i in prog]
        kept.append(_lcs(parent, [signature(i) for i in child]) / max(1, len(parent)))

    check()
    kept.sort()
    assert kept[len(kept) // 2] >= 0.9                    # median


def test_a_shrunk_lineage_keeps_only_the_steps_the_property_needs():
    """The point of lineages: Hypothesis deletes mutation steps the failure does not need."""
    def needs_reassign(lin: Lineage) -> bool:
        return any(i.gen == "mutation:reassign" for i in lin.program)

    lin = find(evolved(OPTS, max_steps=8), needs_reassign,
               settings=settings(RELAXED, max_examples=1000, derandomize=True))
    assert needs_reassign(lin)
    assert lin.steps == ["reassign"], lin.steps
