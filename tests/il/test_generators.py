"""Per-generator structure tests (guard 3) and the op-class enumeration (guard 4): drawing each
generator alone produces only typed operations and the operations it is for."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import oxpecker.il.templates  # noqa: F401
from oxpecker.il import ops
from oxpecker.il.analysis import verify
from oxpecker.il.builder import Builder
from oxpecker.il.lift import LiftOptions, lift
from oxpecker.il.options import GenOptions
from oxpecker.il.strategies import REGISTRY, lifting_choices, prefix, variable

DRAWS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                                                         HealthCheck.filter_too_much])

# What each generator must emit (design §7.7, §10 item 3): op classes that appear in every draw
# ("all") or in at least some draws ("some"). A generator not listed must still verify and lift.
MUST_EMIT: dict[str, tuple[tuple[type, ...], tuple[type, ...]]] = {
    "integer": ((ops.LoadInteger,), ()),
    "negative_number": ((ops.UnaryOperation,), ()),
    "string": ((ops.LoadString,), ()),
    "array": ((ops.CreateArray,), ()),
    "regexp": ((ops.LoadRegExp,), ()),
    "typed_array": ((ops.Construct, ops.LoadBuiltin), ()),
    "trivial_function": ((ops.BeginPlainFunction, ops.EndPlainFunction), ()),
    "symbol": ((ops.LoadBuiltin,), (ops.CallFunction, ops.GetProperty)),
    "unary": ((ops.UnaryOperation,), ()),
    "binary": ((ops.BinaryOperation,), ()),
    "compare": ((ops.Compare,), ()),
    "update": ((ops.Update,), ()),
    "reassignment": ((), (ops.Reassign,)),
    "type_test": ((ops.TypeOf, ops.Compare, ops.LoadString), ()),
    "number_computation": ((ops.LoadBuiltin,), (ops.BinaryOperation, ops.CallMethod)),
    "int32_coerce": ((), (ops.BinaryOperation,)),
    "if_else": ((ops.BeginIf, ops.EndIf), (ops.BeginElse,)),
    "while_loop": ((ops.BeginWhileLoopHeader, ops.BeginWhileLoopBody, ops.EndWhileLoop, ops.Declare), ()),
    "do_while_loop": ((ops.BeginDoWhileLoopBody, ops.EndDoWhileLoop), ()),
    "simple_for_loop": ((ops.BeginForLoopInitializer, ops.BeginForLoopCondition, ops.BeginForLoopAfterthought, ops.BeginForLoopBody, ops.EndForLoop), ()),
    "for_in_loop": ((ops.BeginForEachLoop,), ()),
    "repeat_loop": ((ops.BeginRepeatLoop, ops.EndRepeatLoop), ()),
    "try_catch": ((ops.BeginTry, ops.BeginCatch, ops.TypeOf), (ops.ThrowException,)),
    "try_finally": ((ops.BeginTry, ops.BeginFinally), ()),
    "switch": ((ops.BeginSwitch, ops.EndSwitch), (ops.BeginSwitchCase, ops.BeginSwitchDefaultCase)),
    "eval": ((ops.BeginCodeString, ops.EndCodeString, ops.CallFunction), ()),
    "with_statement": ((ops.BeginWith,), ()),
    "plain_function": ((ops.BeginPlainFunction, ops.CallFunction), (ops.Return,)),
    "generator_function": ((ops.BeginGeneratorFunction,), (ops.Yield, ops.YieldEach)),
    "function_call": ((ops.CallFunction,), ()),
    "constructor_call": ((ops.Construct,), ()),
    "object_literal": ((ops.BeginObjectLiteral, ops.EndObjectLiteral), (ops.ObjectLiteralAddProperty,)),
    "property_assignment": ((ops.SetProperty,), ()),
    "property_configuration": ((), (ops.ConfigureProperty,)),
    "destruct_array": ((ops.Destruct,), ()),
    "proxy": ((ops.Construct, ops.BeginObjectLiteral), ()),
    "super_property_retrieval": ((ops.GetSuperProperty, ops.BeginObjectLiteralMethod), ()),
    "super_method_call": ((ops.CallSuperMethod,), ()),
    "computed_property_update": ((ops.UpdateComputedProperty,), ()),
    "prototype_overwrite": ((), (ops.SetProperty, ops.CallMethod)),
    # compiler-targeted (design §7.7)
    "numeric_local_type_flip": ((ops.BeginPlainFunction, ops.Declare, ops.Return, ops.CallFunction, ops.BeginTry), (ops.Reassign, ops.Update, ops.UnaryOperation)),
    "activation_pair": ((ops.BeginPlainFunction, ops.EndPlainFunction), (ops.LoadArguments, ops.BeginArrowFunction, ops.BeginCatch)),
    "direct_call_arity": ((ops.BeginPlainFunction, ops.CallFunction), (ops.Reassign,)),
    "callsite_relink": ((ops.BeginRepeatLoop, ops.CallFunction), ()),
    "property_site_shape_change": ((ops.GetProperty, ops.BeginRepeatLoop), (ops.DeleteProperty, ops.Reassign)),
    "string_concat_chain": ((ops.Update, ops.BeginRepeatLoop), (ops.CallMethod,)),
    "int32_boundary_arith": ((ops.Declare, ops.BinaryOperation, ops.Compare), ()),
    "super_in_object_literal": ((ops.ObjectLiteralSetPrototype, ops.BeginObjectLiteralMethod), (ops.GetSuperProperty, ops.GetComputedSuperProperty)),
    "const_site": ((ops.Declare,), (ops.BinaryOperation,)),
    "negative_zero": ((ops.UnaryOperation, ops.CallMethod), ()),
    # java-interop
    "java_typed_call": ((ops.CallMethod, ops.BeginTry, ops.LoadBuiltin), ()),
    "java_echo": ((ops.CallMethod,), ()),
    "java_echo_arith": ((ops.CallMethod,), (ops.BinaryOperation, ops.UnaryOperation)),
    "java_returned_value": ((ops.CallMethod, ops.BeginTry), ()),
    "java_numeric_pipeline": ((ops.BeginPlainFunction, ops.CallMethod, ops.UnaryOperation), ()),
    # continuations
    "pause_in_function": ((ops.BeginPlainFunction, ops.CallFunction), ()),
    "pause_in_loop": ((ops.BeginRepeatLoop, ops.CallFunction), ()),
    "native_continuation": ((ops.Construct,), ()),
}


@st.composite
def alone(draw, name: str):
    """A program made of a small prefix and one invocation of the generator `name`."""
    b = Builder(GenOptions(max_depth=2))
    draw(prefix(b, 2, 4))
    spec = REGISTRY[name]
    if not spec.applicable(b):
        return None
    inputs = [draw(variable(b, t)) for t in spec.inputs]
    for t in spec.required:
        pool = b.pool(t).exact
        inputs.append(pool[draw(st.integers(0, len(pool) - 1))])
    before = len(b.code)
    result = draw(spec.strategy(b, *inputs))
    if result is None and len(b.code) == before:
        return None                      # the generator declined its inputs (returned None, emitted nothing)
    code = b.finish()
    return code, before


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_generator_alone_verifies_lifts_and_emits_its_operations(name):
    must_all, must_some = MUST_EMIT.get(name, ((), ()))
    seen: set[type] = set()
    draws = 0

    @given(alone(name))
    @DRAWS
    def run(result):
        nonlocal draws
        if result is None:
            return
        code, before = result
        draws += 1
        verify(code)
        emitted = {type(i.op) for i in code[before:]}
        for cls in must_all:
            assert cls in emitted, f"{name} did not emit {cls.__name__}:\n" + "\n".join(str(i) for i in code[before:])
        seen.update(emitted)
        lift(code, LiftOptions(probes=False))

    run()
    if draws == 0 and REGISTRY[name].context != ops.Context.JAVASCRIPT:
        pytest.skip(f"{name} needs a context the standalone harness does not open")
    assert draws > 0, f"{name} was never applicable"
    for cls in must_some:
        assert cls in seen, f"{name} never emitted {cls.__name__} in {draws} draws"


def test_every_generator_has_a_docstring_and_a_family():
    for spec in REGISTRY.values():
        assert spec.doc, spec.name
        assert spec.family


def test_generated_programs_contain_only_typed_operations():
    """Guard 4: the set of operation classes used is a subset of the declared ones (no text ops)."""
    from oxpecker.il.strategies import generate
    allowed = set(ops.all_operation_classes())
    for seed in range(5):
        gen = generate(seed=seed, min_statements=2, max_statements=6)
        assert {type(i.op) for i in gen.code} <= allowed


def test_loop_labels_exclude_switch_and_if_labels():
    """A `break label` inside a loop may only name a loop (found by the completion test: a switch
    label inside a loop was offered to loop_break)."""
    from oxpecker.il.generators.control import loop_labels
    from oxpecker.il.ops import Comparator
    b = Builder(GenOptions(max_depth=3))
    with b.repeat(3) as (i, loop_label):
        cond = b.compare(i, b.load_int(1), Comparator.EQUAL)
        with b.if_(cond) as _if:
            assert loop_labels(b) == [loop_label]          # not the if's label
            case_value = b.load_bool(True)         # loaded before the switch opens
            with b.switch(cond) as sw:
                with sw.case(case_value):
                    with b.repeat(2) as (_, inner_label):
                        # outer labels are hidden inside a switch (builder hiding contexts);
                        # the switch's own label is visible but is not a loop label
                        assert loop_labels(b) == [inner_label]
                        b.break_(inner_label)
    verify(b.finish())
