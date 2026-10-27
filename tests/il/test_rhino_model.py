"""The Rhino model against Rhino itself (design §10 item 6): a function the model predicts
optimizer-typed shows the compiled mode's number-variable representation (a Java double) where
the harness records the Java class of a probed value, and an activation-forced copy of the same
body does not. Needs the harness; the programs are built with the builder, so the test also pins
the shell path IL -> js -> harness.

`J.typeOf` is not the observation point: Rhino converts a number argument for a Java `Object`
parameter to a Double in every mode. `__probe` records the raw Java class of each argument.
"""

from __future__ import annotations

import pytest

from oxpecker.il.analysis import ActivationReason, analyse
from oxpecker.il.builder import Builder
from oxpecker.il.environment import Keys
from oxpecker.il.lift import js
from oxpecker.il.ops import BinaryOperator, Parameters, ProbeLabel
from oxpecker.il.types import NUMBER


BIT_OR, ADD, MUL = BinaryOperator.BIT_OR, BinaryOperator.ADD, BinaryOperator.MUL

# Body shapes for a local `x` computed from the parameter `p`: (make, number_var, observed).
# `number_var` is what the model (Block.findExpressionType, design §5.3) must say about `x`;
# `observed` is the Java class of `x` at a probe in the compiled, optimizer-run function:
#   "double"  a number variable lives in a Java double and is boxed as Double at the boundary;
#   "same"    an AnyType local computed without numeric operands is boxed like the activation copy;
#   "mixed"   an AnyType local whose expression has a NumberType operand: Codegen emits the
#             ScriptRuntime.add(Object, double) overload, a Double, where the activation copy
#             (no optimizer pass, no ISNUMBER marks) keeps Integer + Integer as an Integer.
# Measured against Rhino b95f75d on 2026-10-26 (interpreted: Double for every arithmetic result,
# Integer for a literal or a parameter).
SHAPES = {
    "literal":      (lambda b, p: b.load_int(1), True, "double"),
    "bit_or":       (lambda b, p: b.binary(p, b.load_int(0), BIT_OR), True, "double"),
    "bit_or_plus":  (lambda b, p: b.binary(b.binary(p, b.load_int(0), BIT_OR), b.load_int(1), ADD), True, "double"),
    "plus":         (lambda b, p: b.binary(p, b.load_int(1), ADD), False, "mixed"),      # AnyType: p is a parameter
    "times":        (lambda b, p: b.binary(p, b.load_int(2), MUL), True, "double"),       # MUL is NumberType whatever the operands
    "length":       (lambda b, p: b.get_property(b.create_int_array([1, 2]), Keys.LENGTH), False, "same"),   # a property read
    "length_or":    (lambda b, p: b.binary(b.get_property(b.create_int_array([1, 2]), Keys.LENGTH), b.load_int(0), BIT_OR), True, "double"),
    "param":        (lambda b, p: p, False, "same"),
}


def _pair(shape: str):
    """f: `var x = <shape>(p); __probe(..., x, ...); return x`; g: the same body behind an
    `arguments` access, which forces an activation frame and keeps the optimizer off."""
    make, _, _ = SHAPES[shape]
    b = Builder()
    with b.plain_function(Parameters(1), named=True, types=(NUMBER,)) as (f, (p,)):
        x = b.declare(make(b, p))
        b.probe(ProbeLabel.STEP, 1)
        b.do_return(x)
    with b.plain_function(Parameters(1), named=True, types=(NUMBER,)) as (g, (q,)):
        b.declare(b.get_property(b.arguments(), Keys.LENGTH))
        y = b.declare(make(b, q))
        b.probe(ProbeLabel.STEP, 2)
        b.do_return(y)
    one = b.load_int(1)
    b.call_function(f, [one])
    b.call_function(g, [one])
    code = b.finish()
    return code, f, g


def _probed_types(o) -> tuple[str, str]:
    """The Java class of `x` in f's probe and of `y` in g's probe: the harness records
    `String | <arg1> | <arg2> ...` per probe (the label first); the lifter lists the most
    recently declared variable first, so `x` is arg1."""
    assert len(o.traceJavaTypes) == 2, (o.trace, o.traceJavaTypes)
    return tuple(t.split(" | ")[1] for t in o.traceJavaTypes)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_model_predictions_on_the_pair(shape):
    code, f, g = _pair(shape)
    an = analyse(code)
    f_index, g_index = an.defs[f], an.defs[g]
    assert an.rhino.optimizer_runs(f_index)
    assert not an.rhino.optimizer_runs(g_index)
    assert an.rhino.activation[g_index] == (ActivationReason.ARGUMENTS,)
    locals_of_f = [v for v, d in an.defs.items() if an.function_of[d] == f_index and d > f_index]
    assert any(v in an.rhino.number_var for v in locals_of_f) == SHAPES[shape][1]


@pytest.mark.slow
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_rhino_agrees_with_the_model(harness, shape):
    """The interpreter ignores activation, so f and g agree there. In the compiled mode a number
    variable is a Java double at the boundary (`Double`); a local the model calls AnyType is
    boxed like the activation copy unless its expression mixes in a NumberType operand."""
    code, f, g = _pair(shape)
    source = js(code, probes=True, comments=False)
    run = harness.run(source, modes=("interpreted", "compiled"), wall_ms=3000)
    interp, comp = run.results["interpreted"], run.results["compiled"]
    assert interp.status == "ok" and comp.status == "ok", (interp.thrown, comp.thrown)
    assert comp.compiledActually
    f_interp, g_interp = _probed_types(interp)
    f_comp, g_comp = _probed_types(comp)
    print(f"\n  {shape:12} interpreted f={f_interp} g={g_interp}   compiled f={f_comp} g={g_comp}")
    assert f_interp == g_interp
    _, number_var, observed = SHAPES[shape]
    if number_var:
        assert observed == "double" and f_comp == "Double"
    elif observed == "same":
        assert f_comp == g_comp
    else:
        assert f_comp == "Double" and g_comp == "Integer"
