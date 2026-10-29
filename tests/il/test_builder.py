"""The builder: every block helper emits a balanced, checkable group; hiding; pools; at(); replay."""

from __future__ import annotations

import pytest

from oxpecker.il import ops
from oxpecker.il.analysis import Storage, analyse, verify
from oxpecker.il.builder import Builder, BuilderError
from oxpecker.il.environment import Builtins, Keys, Methods
from oxpecker.il.ops import BinaryOperator as B
from oxpecker.il.ops import Comparator as C
from oxpecker.il.ops import Context, DeclarationKind, ForEachKind, Lift, Parameters
from oxpecker.il.ops import UnaryOperator as U
from oxpecker.il.options import GenOptions
from oxpecker.il.program import Var, renumber
from oxpecker.il.types import ANY, FUNCTION, INTEGER, NUMBER, OBJECT, STRING


def test_simple_emits_and_finish():
    b = Builder()
    x = b.load_int(1)
    y = b.load_float(2.5)
    z = b.binary(x, y, B.ADD)
    n = b.declare(z)
    b.reassign(n, x)
    code = b.finish()
    verify(code)
    assert [i.op.name for i in code] == ["LoadInteger", "LoadFloat", "BinaryOperation", "Declare", "Reassign"]
    assert b.type_of(z) == NUMBER and b.type_of(n) == INTEGER
    assert b.storage[n] is Storage.TOP_VAR and b.is_assignable(n) and not b.is_assignable(z)


def test_emit_checks_inputs_and_context():
    b = Builder()
    x = b.load_int(1)
    with pytest.raises(BuilderError):
        b.emit(ops.BinaryOperation(B.ADD), [x])
    with pytest.raises(BuilderError):
        b.do_return(x)                                   # not in a subroutine
    with pytest.raises(BuilderError):
        b.emit(ops.TypeOf(), [Var(99)])
    with pytest.raises(BuilderError):
        b.emit(ops.EndIf())


def test_all_block_helpers_produce_valid_programs():
    b = Builder()
    one = b.load_int(1)
    with b.plain_function(Parameters(2), named=True, types=(NUMBER, NUMBER)) as (f, (p, q)):
        s = b.binary(p, q, B.ADD)
        with b.if_(b.compare(s, one, C.GREATER)) as br:
            b.do_return(s)
            with br.else_():
                b.do_return(one)
    with b.arrow_function(Parameters(1)) as (g, (a,)):
        b.do_return(a)
    with b.generator_function() as (gen, _):
        b.yield_(one)
        b.yield_each(b.create_array([one]))
    k, cm, cg, cs = (b.load_string(s) for s in ("k", "cm", "cg", "cs"))
    spread_src = b.create_array([])
    proto = b.load_null()
    with b.object_literal() as o:                   # members only inside; keys are loaded before (FuzzIL's rule)
        o.add_property(Keys.A, one)
        o.add_element(0, one)
        o.add_computed(k, one)
        with o.method(Keys.M, Parameters(1)) as (this, (arg,)):
            b.do_return(b.get_super_property(Keys.A))
        with o.getter(Keys.G) as this:
            b.do_return(this)
        with o.setter(Keys.G) as (this, v):
            b.set_property(this, Keys.B, v)
        with o.computed_method(cm) as (this, _):
            b.do_return(this)
        with o.computed_getter(cg) as this:
            b.do_return(this)
        with o.computed_setter(cs) as (this, v):
            pass
        o.copy_properties(spread_src)
        o.set_prototype(proto)
        with pytest.raises(BuilderError):
            b.load_int(3)                            # no statements inside an object literal
    obj = o.result
    with b.while_loop() as loop:
        c = b.compare(one, one, C.LESS)
        with loop.body(c) as label:
            b.break_(label)
            b.continue_()
    with b.do_while_loop() as loop:
        b.continue_(loop.label)
        with loop.header() as h:
            h.done(b.load_bool(False))
    with b.for_loop() as loop:
        i0 = b.load_int(0)
        with loop.condition([i0]) as (i,):
            cond = b.compare(i, one, C.LESS)
        with loop.afterthought(cond) as (i2,):
            b.unary(U.POST_INC, i2)
        with loop.body() as ((i3,), label):
            b.update(i3, one, B.ADD)
    arr = b.create_array([one, one])
    with b.for_each(ForEachKind.OF, arr) as ((e,), label):
        b.typeof(e)
    with b.for_each(ForEachKind.IN, obj) as ((k,), label):
        b.get_computed(obj, k)
    with b.repeat(3) as (i, label):
        b.block_break(label) if False else None
        b.break_()
    with b.try_() as t:
        b.throw(one)
        with t.catch() as e:
            b.typeof(e)
        with t.finally_():
            pass
    with b.try_() as t:
        with t.finally_():
            pass
    two = b.load_int(2)                       # case values are computed before the switch (FuzzIL's rule)
    with b.switch(one) as sw:
        with sw.case(one):
            b.switch_break()
        with sw.case(two, falls_through=True):
            pass
        with sw.default():
            pass
    with b.block() as label:
        b.block_break(label)
    with b.with_(obj):
        b.get_property(obj, Keys.A)
    with b.code_string() as cs:
        b.load_int(7)
    b.call_function(b.builtin(Builtins.EVAL), [cs.result])
    b.probe()
    code = b.finish()
    an = verify(code)
    assert an.types[f].Is(FUNCTION) and an.returns[f].Is(NUMBER)
    assert Context.METHOD not in b.context and b.context == Context.JAVASCRIPT
    assert an.storage[obj] is Storage.TEMP and an.types[obj].group.startswith("_Object")


def test_frames_enforce_their_protocol():
    b = Builder()
    with pytest.raises(BuilderError):
        with b.while_loop():
            pass
    b = Builder()
    with pytest.raises(BuilderError):
        with b.try_():
            pass
    b = Builder()
    with pytest.raises(BuilderError):
        with b.for_loop() as loop:
            with loop.body():
                pass
    b = Builder()
    with pytest.raises(BuilderError):
        with b.object_literal() as o:
            o.set_prototype(b.load_null())
            o.set_prototype(b.load_null())
    b = Builder()
    with pytest.raises(BuilderError):
        with b.switch(b.load_int(1)) as sw:
            with sw.default():
                pass
            with sw.default():
                pass


def test_function_variable_is_hidden_in_its_body_and_labels_are_hidden_in_subroutines():
    b = Builder()
    with b.repeat(2) as (i, label):
        assert i in b.visible() and label not in b.visible()
        with b.plain_function() as (f, _):
            assert f not in b.visible()              # no trivial recursion
            assert label in b._hidden                # labels never cross a function boundary
            with pytest.raises(BuilderError):
                b.break_(label)                      # and the context forbids it anyway
        assert f in b.visible()
        b.break_(label)
    assert label not in b._hidden and not b.in_scope(label)
    with b.plain_function(recursive=True) as (g, _):
        assert g in b.visible()
    b.finish()


def test_pools_and_visibility_order():
    b = Builder()
    s = b.load_string("a")
    n = b.load_int(1)
    f = b.builtin(Builtins.PARSE_INT)
    d = b.declare(n)
    assert b.visible() == [d, f, n, s]
    assert b.visible(assignable=True) == [d] and b.visible(declared=True) == [d]
    p = b.pool(NUMBER)
    assert p.exact == (d, n) and p.all == (d, f, n, s)
    assert b.pool(FUNCTION).exact == (f,)
    u = b.call_function(f, [s])
    assert u in b.pool(NUMBER).exact                 # parseInt returns a number
    with b.repeat(1) as (i, label):
        assert label not in b.visible() and i in b.visible() and not b.is_assignable(i)
    b.hide(s)
    assert s not in b.visible()
    b.unhide(s)
    assert s in b.visible()


def test_rhino_model_on_open_code():
    b = Builder()
    with b.plain_function(Parameters(1), named=True) as (f, (p,)):
        n = b.declare(b.load_int(1))
        b.reassign(n, b.binary(n, b.load_int(2), B.MUL))
        assert n in b.rhino.number_var                 # computed while the function is still open
        b.reassign(n, b.load_string("s"))
        assert n not in b.rhino.number_var
    with b.plain_function() as (g, _):
        b.call_function(f, [b.load_int(1)])
    code = b.finish()
    assert f in analyse(code).rhino.direct_call_target


def test_at_and_replay():
    b = Builder()
    x = b.load_int(1)
    with b.plain_function(Parameters(1)) as (f, (p,)):
        y = b.binary(p, x, B.ADD)
        b.do_return(y)
    end = len(b.code)
    code = b.finish()
    # insert a declaration before the function
    b2 = Builder.at(code, 1)
    assert b2.visible() == [x]
    d = b2.declare(x)
    new = b2.new_code()
    assert new[0].op.name == "Declare" and new[0].inputs == (x,) and new[0].output >= max(v for i in code for v in i.inouts) + 1
    spliced = renumber(list(code[:1]) + list(new) + list(code[1:]))
    verify(spliced)
    # replay the function twice
    b3 = Builder()
    z = b3.load_int(2)
    start = len(b3.code)
    with b3.plain_function(Parameters(1)) as (g, (q,)):
        b3.do_return(b3.binary(q, z, B.MUL))
    stop = len(b3.code)
    mapping = b3.replay(start, stop)
    code3 = b3.finish()
    verify(code3)
    assert mapping[g] != g and code3[-1].op.name == "EndPlainFunction"
    assert code3[stop].op == code3[start].op and code3[stop].gen == code3[start].gen


def test_strictness_is_tracked_per_frame():
    b = Builder(GenOptions(strict=True))
    assert b.strict
    b = Builder()
    assert not b.strict
    with b.plain_function(strict=True):
        assert b.strict
        with b.arrow_function():
            assert b.strict
    assert not b.strict


def test_lift_is_recorded_on_the_instruction():
    b = Builder()
    i = b.emit(ops.LoadInteger(1), lift=Lift.CONST)
    assert i.lift is Lift.CONST and b.code[0].lift is Lift.CONST
