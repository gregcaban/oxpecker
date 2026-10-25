"""Program analysis and verifier of the IR (oxpecker/ir/analysis.py): scoping, flow-sensitive types,
storage, activation, return types and the verify() rules. No JVM.

    pytest tests/test_ir_analysis.py
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.types import T
from oxpecker.ir.analysis import InvalidProgram, analyse, binary_type, join, unary_type, update_type, verify
from oxpecker.ir.ops import (ARITH, BITWISE, COMPARE, LOGICAL, RELATION, BeginCatch, BeginElse, BeginFinally, BeginFor,
                             BeginFunction, BeginIf, BeginTry, Binary, CallFunction, CallMethod, CompoundAssign,
                             Construct, CreateArray, CreateObject, DeclareVar, DeleteProperty, EndFor, EndFunction,
                             EndIf, EndTry, Eval, GetElement, GetProperty, Instr, LoadBuiltin, LoadLiteral, Probe,
                             Reassign, Return, SetElement, SetProperty, Throw, Unary, Update)
from oxpecker.ir.strategies import ir_programs


def I(op, inputs=(), outputs=(), inner=(), **kw):
    return Instr(op, tuple(inputs), tuple(outputs), tuple(inner), **kw)


def lit(v, text, t=T.INT32, **kw):
    return I(LoadLiteral(t, text), (), (v,), **kw)


class B:
    """A tiny program builder allocating fresh variable ids; `probe()` marks points to query."""

    def __init__(self):
        self.p: list[Instr] = []
        self.n = 0
        self.probes: list[int] = []

    def add(self, op, *inputs, out=0, inner=0):
        outs = tuple(range(self.n, self.n + out))
        self.n += out
        ins = tuple(range(self.n, self.n + inner))
        self.n += inner
        self.p.append(I(op, inputs, outs, ins))
        return outs + ins

    def prog(self):
        return tuple(self.p)

    # values
    def lit(self, t=T.INT32, text="0"):
        return self.add(LoadLiteral(t, text), out=1)[0]

    def null(self):
        return self.lit(T.NULL, "null")

    def undef(self):
        return self.lit(T.UNDEFINED, "undefined")

    def s(self):
        return self.lit(T.STRING, '"s"')

    def obj(self):
        return self.add(CreateObject(), out=1)[0]

    def arr(self):
        return self.add(CreateArray(), out=1)[0]

    def fnref(self):
        return self.add(LoadBuiltin("f", T.FUNCTION), out=1)[0]

    def declare(self, v, kind="var"):
        return self.add(DeclareVar(kind), v, out=1)[0]

    def var(self, t=T.INT32, kind="var"):
        return self.declare(self.lit(t), kind)

    def assign(self, target, value):
        self.add(Reassign(), target, value)

    def probe(self):
        self.probes.append(len(self.p))
        self.add(Probe())

    def at(self, v):
        an = analyse(self.prog())
        return [an.type_at(i, v) for i in self.probes]

    # blocks
    def if_(self, c=None):
        self.add(BeginIf(), self.lit() if c is None else c)

    def else_(self):
        self.add(BeginElse())

    def end_if(self):
        self.add(EndIf())

    def for_(self, n=3):
        return self.add(BeginFor(n), inner=1)[0]

    def end_for(self):
        self.add(EndFor())

    def try_(self):
        self.add(BeginTry())

    def catch(self):
        return self.add(BeginCatch(), inner=1)[0]

    def finally_(self):
        self.add(BeginFinally())

    def end_try(self):
        self.add(EndTry())

    def fn(self, arity=0, **kw):
        f, *ps = self.add(BeginFunction(arity, **kw), out=1, inner=arity)
        return (f, *ps) if arity else f

    def end_fn(self):
        self.add(EndFunction())

    def ret(self, v=None):
        self.add(Return(), *(() if v is None else (v,)))


def bad(prog, match):
    with pytest.raises(InvalidProgram, match=match):
        verify(prog)


# ---------------------------------------------------------------------------- join

@pytest.mark.parametrize("a,b,want", [
    (None, None, None),
    (None, T.STRING, T.STRING),
    (T.STRING, None, T.STRING),
    (None, T.UNKNOWN, T.UNKNOWN),
    (T.INT32, T.INT32, T.INT32),
    (T.UNKNOWN, T.UNKNOWN, T.UNKNOWN),
    (T.NUMBER, T.INT32, T.NUMBER),
    (T.INT32, T.NUMBER, T.NUMBER),
    (T.ARRAY, T.OBJECT, T.OBJECT),
    (T.ARRAY, T.FUNCTION, T.OBJECT),
    (T.MAP, T.SET, T.OBJECT),
    (T.REGEXP, T.TYPEDARRAY, T.OBJECT),
    (T.OBJECT, T.FUNCTION, T.OBJECT),
    (T.INT32, T.STRING, T.UNKNOWN),
    (T.NULL, T.OBJECT, T.UNKNOWN),
    (T.NULL, T.UNDEFINED, T.UNKNOWN),
    (T.BIGINT, T.NUMBER, T.UNKNOWN),
    (T.SYMBOL, T.STRING, T.UNKNOWN),
    (T.UNKNOWN, T.INT32, T.UNKNOWN),
    (T.OBJECT, T.UNKNOWN, T.UNKNOWN),
    (T.BOOLEAN, T.INT32, T.UNKNOWN),
])
def test_join(a, b, want):
    assert join(a, b) == want
    assert join(b, a) == want


# ---------------------------------------------------------------------------- type tables

@pytest.mark.parametrize("op", COMPARE + RELATION)
@pytest.mark.parametrize("a,b", [(T.INT32, T.INT32), (T.UNKNOWN, T.STRING), (None, None), (T.BIGINT, T.INT32)])
def test_binary_comparisons_are_boolean(op, a, b):
    assert binary_type(op, a, b) == T.BOOLEAN


@pytest.mark.parametrize("op,a,b,want", [
    ("&&", T.INT32, T.INT32, T.INT32),
    ("||", T.INT32, T.NUMBER, T.NUMBER),
    ("??", T.ARRAY, T.FUNCTION, T.OBJECT),
    ("&&", T.INT32, T.STRING, T.UNKNOWN),
    ("||", None, None, T.UNKNOWN),
    ("??", None, T.STRING, T.STRING),
    ("||", T.BIGINT, T.BIGINT, T.BIGINT),
])
def test_binary_logical(op, a, b, want):
    assert binary_type(op, a, b) == want


@pytest.mark.parametrize("op", ARITH + BITWISE)
def test_binary_bigint_pair(op):
    assert binary_type(op, T.BIGINT, T.BIGINT) == T.BIGINT


@pytest.mark.parametrize("op", BITWISE)
@pytest.mark.parametrize("a,b,want", [
    (T.INT32, T.INT32, T.INT32), (T.NUMBER, T.STRING, T.INT32), (T.OBJECT, T.BOOLEAN, T.INT32),
    (T.UNKNOWN, T.INT32, T.UNKNOWN), (T.INT32, T.UNKNOWN, T.UNKNOWN),
    (T.BIGINT, T.INT32, T.UNKNOWN), (T.INT32, T.BIGINT, T.UNKNOWN),
])
def test_binary_bitwise(op, a, b, want):
    assert binary_type(op, a, b) == want


@pytest.mark.parametrize("a,b,want", [
    (T.STRING, T.INT32, T.STRING), (T.INT32, T.STRING, T.STRING), (T.UNKNOWN, T.STRING, T.STRING),
    (T.STRING, T.STRING, T.STRING), (T.OBJECT, T.STRING, T.STRING),
    (T.INT32, T.INT32, T.NUMBER), (T.NUMBER, T.NUMBER, T.NUMBER), (T.BOOLEAN, T.NULL, T.NUMBER),
    (T.UNDEFINED, T.INT32, T.NUMBER),
    (T.UNKNOWN, T.INT32, T.UNKNOWN), (T.BIGINT, T.INT32, T.UNKNOWN), (T.OBJECT, T.INT32, T.UNKNOWN),
    (None, T.INT32, T.UNKNOWN),
])
def test_binary_plus(a, b, want):
    assert binary_type("+", a, b) == want


@pytest.mark.parametrize("op", [o for o in ARITH if o != "+"])
@pytest.mark.parametrize("a,b,want", [
    (T.INT32, T.INT32, T.NUMBER), (T.STRING, T.INT32, T.NUMBER), (T.OBJECT, T.OBJECT, T.NUMBER),
    (T.UNKNOWN, T.INT32, T.UNKNOWN), (T.INT32, T.UNKNOWN, T.UNKNOWN),
    (T.BIGINT, T.INT32, T.UNKNOWN), (T.INT32, T.BIGINT, T.UNKNOWN),
])
def test_binary_other_arith(op, a, b, want):
    assert binary_type(op, a, b) == want


@pytest.mark.parametrize("op,a,want", [
    ("!", T.INT32, T.BOOLEAN), ("!", T.UNKNOWN, T.BOOLEAN),
    ("typeof", T.INT32, T.STRING), ("typeof", None, T.STRING),
    ("void", T.STRING, T.UNDEFINED),
    ("+", T.STRING, T.NUMBER), ("+", T.UNKNOWN, T.NUMBER),
    ("-", T.INT32, T.NUMBER), ("-", T.STRING, T.NUMBER), ("-", T.BIGINT, T.BIGINT), ("-", T.UNKNOWN, T.UNKNOWN),
    ("~", T.INT32, T.INT32), ("~", T.STRING, T.INT32), ("~", T.BIGINT, T.BIGINT), ("~", T.UNKNOWN, T.UNKNOWN),
])
def test_unary_type(op, a, want):
    assert unary_type(op, a) == want


@pytest.mark.parametrize("a,want", [
    (T.INT32, T.NUMBER), (T.NUMBER, T.NUMBER), (T.STRING, T.NUMBER), (None, T.NUMBER),
    (T.BIGINT, T.BIGINT), (T.UNKNOWN, T.UNKNOWN),
])
def test_update_type(a, want):
    assert update_type(a) == want


# ---------------------------------------------------------------------------- verify: rejections

def _undefined_use():
    return (I(Unary("-"), (5,), (0,)),)


def _defined_twice_lit():
    return (lit(0, "1"), lit(0, "2"))


def _defined_twice_inner():
    b = B()
    f, p = b.fn(1)
    b.end_fn()
    return b.prog()[:1] + (I(LoadLiteral(T.INT32, "1"), (), (p,)),) + b.prog()[1:]


def _else_top():
    return (I(BeginElse()),)


def _else_in_for():
    b = B()
    b.for_()
    b.else_()
    b.end_for()
    return b.prog()


def _else_twice():
    b = B()
    b.if_()
    b.else_()
    b.else_()
    b.end_if()
    return b.prog()


def _catch_top():
    return (I(BeginCatch(), (), (), (0,)),)


def _catch_in_if():
    b = B()
    b.if_()
    b.catch()
    b.end_if()
    return b.prog()


def _catch_twice():
    b = B()
    b.try_()
    b.catch()
    b.catch()
    b.end_try()
    return b.prog()


def _finally_top():
    return (I(BeginFinally()),)


def _finally_twice():
    b = B()
    b.try_()
    b.finally_()
    b.finally_()
    b.end_try()
    return b.prog()


def _end_if_closes_for():
    b = B()
    b.for_()
    b.end_if()
    return b.prog()


def _end_for_closes_if():
    b = B()
    b.if_()
    b.end_for()
    return b.prog()


def _end_fn_closes_try():
    b = B()
    b.try_()
    b.end_fn()
    return b.prog()


def _end_at_top():
    return (I(EndIf()),)


def _try_alone():
    b = B()
    b.try_()
    b.end_try()
    return b.prog()


def _unclosed_if():
    b = B()
    b.if_()
    return b.prog()


def _unclosed_fn():
    b = B()
    b.fn()
    return b.prog()


def _return_top():
    return (I(Return()),)


def _return_in_top_if():
    b = B()
    b.if_()
    b.ret()
    b.end_if()
    return b.prog()


def _fn_wrong_params():
    return (I(BeginFunction(2), (), (0,), (1,)), I(EndFunction()))


def _fn_no_output():
    return (I(BeginFunction(0)), I(EndFunction()))


def _for_no_counter():
    return (lit(0, "1"), I(BeginFor(3)), I(EndFor()))


def _assign_temp(op):
    def mk():
        b = B()
        t = b.lit()
        x = b.lit()
        if op == "reassign":
            b.assign(t, x)
        elif op == "compound":
            b.add(CompoundAssign("+"), t, x)
        else:
            b.add(Update("++", value=False), t)
        return b.prog()
    return mk


def _assign_const(op):
    def mk():
        b = B()
        c = b.var(kind="const")
        x = b.lit()
        if op == "reassign":
            b.assign(c, x)
        elif op == "compound":
            b.add(CompoundAssign("+"), c, x)
        else:
            b.add(Update("++", value=False), c)
        return b.prog()
    return mk


def _assign_counter(op):
    def mk():
        b = B()
        i = b.for_()
        x = b.lit()
        if op == "reassign":
            b.assign(i, x)
        elif op == "compound":
            b.add(CompoundAssign("+"), i, x)
        else:
            b.add(Update("++", value=False), i)
        b.end_for()
        return b.prog()
    return mk


def _callee(kind, op):
    def mk():
        b = B()
        v = b.var(kind)
        b.add(op, v, out=0 if getattr(op, "discard", False) else 1)
        return b.prog()
    return mk


REJECTED = [
    ("use-not-defined", _undefined_use, "not visible"),
    ("defined-twice-output", _defined_twice_lit, "defined twice"),
    ("defined-twice-param", _defined_twice_inner, "defined twice"),
    ("else-top", _else_top, "else at 0 outside an if"),
    ("else-in-for", _else_in_for, "outside an if"),
    ("else-twice", _else_twice, "outside an if"),
    ("catch-top", _catch_top, "BeginCatch at 0 in the wrong place"),
    ("catch-in-if", _catch_in_if, "BeginCatch .* in the wrong place"),
    ("catch-twice", _catch_twice, "BeginCatch .* in the wrong place"),
    ("finally-top", _finally_top, "BeginFinally at 0 in the wrong place"),
    ("finally-twice", _finally_twice, "BeginFinally .* in the wrong place"),
    ("endif-closes-for", _end_if_closes_for, "EndIf at 1 closes a for block"),
    ("endfor-closes-if", _end_for_closes_if, "EndFor .* closes a then block"),
    ("endfn-closes-try", _end_fn_closes_try, "EndFunction .* closes a try block"),
    ("end-at-top", _end_at_top, "closes a top block"),
    ("try-alone", _try_alone, "EndTry .* closes a try block"),
    ("unclosed-if", _unclosed_if, "unclosed then block"),
    ("unclosed-fn", _unclosed_fn, "unclosed function block"),
    ("return-top", _return_top, "return at 0 outside a function"),
    ("return-in-top-if", _return_in_top_if, "outside a function"),
    ("fn-param-count", _fn_wrong_params, "needs one output and 2 parameters"),
    ("fn-no-output", _fn_no_output, "needs one output and 0 parameters"),
    ("for-no-counter", _for_no_counter, "needs a counter"),
    *[(f"temp-{op}", _assign_temp(op), "assignment to temporary") for op in ("reassign", "compound", "update")],
    *[(f"const-{op}", _assign_const(op), "assignment to const") for op in ("reassign", "compound", "update")],
    *[(f"counter-{op}", _assign_counter(op), "assignment to loop counter") for op in ("reassign", "compound", "update")],
    *[(f"call-{t.value}", _callee(t, CallFunction()), f"callee v\\d+ is {t.value}")
      for t in (T.INT32, T.STRING, T.NULL, T.UNDEFINED, T.OBJECT, T.ARRAY, T.BIGINT)],
    ("new-int", _callee(T.INT32, Construct()), "callee v\\d+ is int32"),
    ("new-null", _callee(T.NULL, Construct()), "callee v\\d+ is null"),
]


@pytest.mark.parametrize("mk,match", [pytest.param(mk, m, id=i) for i, mk, m in REJECTED])
def test_verify_rejects(mk, match):
    bad(mk(), match)


def _access(t, kind):
    b = B()
    o = b.var(t)
    k = b.lit()
    if kind == "get":
        b.add(GetProperty("p"), o, out=1)
    elif kind == "set":
        b.add(SetProperty("p"), o, k)
    elif kind == "getel":
        b.add(GetElement(), o, k, out=1)
    elif kind == "setel":
        b.add(SetElement(), o, k, k)
    elif kind == "delete":
        b.add(DeleteProperty("p"), o, out=1)
    else:
        b.add(CallMethod("m"), o, out=1)
    return b.prog()


ACCESS = ("get", "set", "getel", "setel", "delete", "method")


@pytest.mark.parametrize("t", [T.NULL, T.UNDEFINED])
@pytest.mark.parametrize("kind", ACCESS)
def test_verify_rejects_property_access_on_null_or_undefined(t, kind):
    bad(_access(t, kind), f"property access on v\\d+, which is {t.value}")


@pytest.mark.parametrize("t", [T.OBJECT, T.INT32, T.STRING, T.UNKNOWN, T.ARRAY, T.BOOLEAN])
@pytest.mark.parametrize("kind", ACCESS)
def test_verify_accepts_property_access_on_other_types(t, kind):
    verify(_access(t, kind))


def test_null_as_value_slot_is_fine():
    b = B()
    o = b.obj()
    n = b.null()
    b.add(SetProperty("p"), o, n)
    verify(b.prog())


# ---------------------------------------------------------------------------- verify: acceptances

def test_flow_sensitive_null_reassigned_before_access():
    b = B()
    x = b.var(T.NULL)
    b.assign(x, b.obj())
    b.add(GetProperty("p"), x, out=1)
    verify(b.prog())


def test_flow_sensitive_object_reassigned_to_null_then_access_fails():
    b = B()
    x = b.var(T.OBJECT)
    x_obj = b.obj()
    b.assign(x, x_obj)
    b.assign(x, b.null())
    b.add(GetProperty("p"), x, out=1)
    bad(b.prog(), "property access")


def test_null_in_one_branch_only_is_not_definite():
    b = B()
    x = b.declare(b.null())
    b.if_()
    b.assign(x, b.obj())
    b.end_if()
    b.add(GetProperty("p"), x, out=1)        # null or object: UNKNOWN
    verify(b.prog())


def test_null_in_both_branches_is_definite():
    b = B()
    x = b.declare(b.undef())
    b.if_()
    b.assign(x, b.null())
    b.else_()
    b.assign(x, b.null())
    b.end_if()
    b.add(GetProperty("p"), x, out=1)        # null join undefined would be UNKNOWN; null join null is null
    bad(b.prog(), "property access")


def test_null_to_object_inside_loop_then_access_inside_body():
    b = B()
    x = b.declare(b.null())
    b.for_()
    b.add(GetProperty("p"), x, out=1)        # first iteration: null; fixpoint says null|object = UNKNOWN
    b.assign(x, b.obj())
    b.end_for()
    verify(b.prog())


def test_call_after_reassign_to_function():
    b = B()
    x = b.var(T.INT32)
    b.assign(x, b.fnref())
    b.add(CallFunction(), x, out=1)
    b.add(Construct(), x, out=1)
    verify(b.prog())


def test_call_after_reassign_to_int_fails():
    b = B()
    x = b.declare(b.fnref())
    b.assign(x, b.lit())
    b.add(CallFunction(), x, out=1)
    bad(b.prog(), "callee v\\d+ is int32")


@pytest.mark.parametrize("t", [T.FUNCTION, T.UNKNOWN])
def test_call_and_construct_on_function_or_unknown(t):
    b = B()
    v = b.add(LoadBuiltin("f", t), out=1)[0]
    b.add(CallFunction(), v, out=1)
    b.add(Construct(), v, out=1)
    verify(b.prog())


def test_call_a_declared_function_and_a_param():
    b = B()
    f, p = b.fn(1)
    b.add(CallFunction(), f, p, out=1)
    b.add(CallFunction(), p, out=1)           # parameters are UNKNOWN
    b.end_fn()
    b.add(CallFunction(), f, b.lit(), out=1)
    verify(b.prog())


def test_assignment_targets_that_are_allowed():
    b = B()
    v = b.var(kind="var")
    l = b.var(kind="let")
    x = b.lit()
    f, p = b.fn(1)
    b.assign(p, x if False else p)
    b.end_fn()
    b.try_()
    b.add(Throw(), x)
    e = b.catch()
    b.assign(e, x)
    b.end_try()
    b.assign(v, x)
    b.add(CompoundAssign("+"), l, x)
    b.add(Update("--", value=False), v)
    b.add(Update("++", prefix=True), l, out=1)
    b.assign(f, b.fnref())
    verify(b.prog())


def test_discarded_calls_have_no_output():
    b = B()
    f = b.fn()
    b.end_fn()
    b.add(CallFunction(discard=True), f)
    verify(b.prog())


def test_valid_structures():
    b = B()
    b.try_()
    b.finally_()
    b.end_try()
    b.try_()
    b.catch()
    b.end_try()
    b.try_()
    b.catch()
    b.finally_()
    b.end_try()
    b.if_()
    b.end_if()
    b.if_()
    b.else_()
    b.end_if()
    f = b.fn()
    b.if_()
    b.ret()
    b.end_if()
    b.end_fn()
    verify(b.prog())


# ---------------------------------------------------------------------------- visibility

def test_block_local_definition_is_not_visible_after_the_block():
    b = B()
    b.if_()
    t = b.lit()
    b.end_if()
    b.add(Unary("-"), t, out=1)
    bad(b.prog(), "not visible")


def test_block_local_declared_var_is_not_visible_after_the_block():
    b = B()
    b.for_()
    x = b.var()
    b.end_for()
    b.add(Unary("-"), x, out=1)
    bad(b.prog(), "not visible")


def test_then_definition_not_visible_in_else():
    b = B()
    b.if_()
    t = b.lit()
    b.else_()
    b.add(Unary("-"), t, out=1)
    b.end_if()
    bad(b.prog(), "not visible")


def test_outer_variables_are_visible_in_nested_blocks():
    b = B()
    x = b.lit()
    b.if_()
    b.for_()
    f = b.fn()
    b.add(Unary("-"), x, out=1)
    b.end_fn()
    b.end_for()
    b.end_if()
    verify(b.prog())


def test_param_not_visible_after_function():
    b = B()
    f, p = b.fn(1)
    b.end_fn()
    b.add(Unary("-"), p, out=1)
    bad(b.prog(), "not visible")


def test_function_local_not_visible_after_function():
    b = B()
    b.fn()
    t = b.lit()
    b.end_fn()
    b.add(Unary("-"), t, out=1)
    bad(b.prog(), "not visible")


def test_function_name_visible_inside_and_after():
    b = B()
    f = b.fn()
    b.add(CallFunction(), f, out=1)
    b.end_fn()
    b.add(CallFunction(), f, out=1)
    an = verify(b.prog())
    assert f in an.visible_at[1] and f in an.visible_at[-1]


def test_function_name_defined_in_block_is_scoped_to_it():
    b = B()
    b.if_()
    f = b.fn()
    b.end_fn()
    b.add(CallFunction(), f, out=1)       # still inside the if: visible
    b.end_if()
    b.add(CallFunction(), f, out=1)       # after the if: not
    bad(b.prog(), "not visible")


def test_catch_variable_only_inside_catch():
    b = B()
    b.try_()
    e = b.catch()
    b.add(Unary("-"), e, out=1)
    b.end_try()
    verify(b.prog())
    b.add(Unary("-"), e, out=1)
    bad(b.prog(), "not visible")


def test_catch_variable_not_visible_in_finally():
    b = B()
    b.try_()
    e = b.catch()
    b.finally_()
    b.add(Unary("-"), e, out=1)
    b.end_try()
    bad(b.prog(), "not visible")


def test_loop_counter_only_inside_loop():
    b = B()
    i = b.for_()
    b.add(Unary("-"), i, out=1)
    b.end_for()
    verify(b.prog())
    b.add(Unary("-"), i, out=1)
    bad(b.prog(), "not visible")


def test_visible_at_order_and_depth_info():
    b = B()
    a = b.lit()
    b.if_()
    c = b.lit()
    b.probe()
    b.end_if()
    an = analyse(b.prog())
    # instructions: 0 lit a, 1 lit cond, 2 if, 3 lit c, 4 probe, 5 end
    assert an.visible_at[4] == [a, 1, c]
    assert an.visible_recent(4) == [c, 1, a]
    assert an.depth_at[4] == 1 and an.depth_at[0] == 0 and an.depth_at[-1] == 0
    assert an.frame_at[4] == "then" and an.frame_at[0] == "top"
    assert an.block_end == {2: 5}
    assert len(an.visible_at) == len(b.p) + 1


def test_middle_block_end_and_fn_at():
    b = B()
    b.if_()
    b.else_()
    b.end_if()
    f = b.fn()
    b.probe()
    b.end_fn()
    b.try_()
    b.catch()
    b.finally_()
    b.end_try()
    an = analyse(b.prog())
    assert an.middle[1] == [2]
    assert an.block_end[1] == 3
    assert an.fn_at[5] == f and an.fn_at[0] is None
    assert an.frame_at[5] == "function"
    # try at 7, catch 8, finally 9
    assert an.middle[7] == [8, 9] and an.block_end[7] == 10


def test_use_count_and_users():
    b = B()
    x = b.lit()
    b.add(Unary("-"), x, out=1)
    b.add(Unary("+"), x, out=1)
    an = analyse(b.prog())
    assert an.use_count[x] == 2 and an.users[x] == [1, 2]


# ---------------------------------------------------------------------------- declared / reassigned / counters

def test_declared_kinds_and_counters():
    b = B()
    v = b.var(kind="var")
    l = b.var(kind="let")
    c = b.var(kind="const")
    t = b.lit()
    f, p = b.fn(1)
    b.end_fn()
    b.try_()
    e = b.catch()
    b.end_try()
    i = b.for_()
    b.end_for()
    an = analyse(b.prog())
    assert (an.declared[v], an.declared[l], an.declared[c]) == ("var", "let", "const")
    assert an.declared[f] == "function" and an.declared[p] == "param" and an.declared[e] == "catch"
    assert t not in an.declared and i not in an.declared
    assert an.loop_counters == {i}
    assert an.assignable(v) and an.assignable(f) and an.assignable(p) and an.assignable(e)
    assert not an.assignable(c) and not an.assignable(i) and not an.assignable(t)


def test_reassigned_collects_all_targets():
    b = B()
    a = b.var()
    c = b.var()
    d = b.var()
    x = b.lit()
    b.assign(a, x)
    b.add(CompoundAssign("+"), c, x)
    b.add(Update("++", value=False), d)
    an = analyse(b.prog())
    assert an.reassigned == {a, c, d}


# ---------------------------------------------------------------------------- flow-sensitive types

def test_strong_update_straight_line():
    b = B()
    x = b.var(T.INT32)
    b.probe()
    b.assign(x, b.s())
    b.probe()
    b.assign(x, b.null())
    b.probe()
    assert b.at(x) == [T.INT32, T.STRING, T.NULL]


def test_compound_assign_and_update_are_strong_updates():
    b = B()
    x = b.var(T.INT32)
    b.add(CompoundAssign("+"), x, b.s())
    b.probe()
    b.add(Update("++", value=False), x)
    b.probe()
    y = b.var(T.BIGINT)
    b.add(Update("++", value=False), y)
    b.probe()
    assert b.at(x)[:2] == [T.STRING, T.NUMBER]
    assert b.at(y)[2] == T.BIGINT


def test_update_with_value_output_type():
    b = B()
    x = b.var(T.INT32)
    (o,) = b.add(Update("++", prefix=True), x, out=1)
    an = analyse(b.prog())
    assert an.types[o] == T.NUMBER


def test_if_without_else_joins_with_entry():
    b = B()
    x = b.var(T.NULL)
    b.if_()
    b.assign(x, b.obj())
    b.probe()
    b.end_if()
    b.probe()
    assert b.at(x) == [T.OBJECT, T.UNKNOWN]


def test_if_without_else_same_type_stays():
    b = B()
    x = b.var(T.INT32)
    b.if_()
    b.assign(x, b.lit())
    b.end_if()
    b.probe()
    assert b.at(x) == [T.INT32]


def test_if_without_else_number_int_join():
    b = B()
    x = b.var(T.INT32)
    b.if_()
    b.assign(x, b.lit(T.NUMBER, "1.5"))
    b.end_if()
    b.probe()
    assert b.at(x) == [T.NUMBER]


def test_if_else_joins_branches_not_entry():
    b = B()
    x = b.var(T.NULL)
    b.if_()
    b.assign(x, b.arr())
    b.else_()
    b.probe()
    b.assign(x, b.obj())
    b.end_if()
    b.probe()
    assert b.at(x) == [T.NULL, T.OBJECT]      # array join object; entry null is gone


def test_if_else_one_branch_unassigned_keeps_entry_there():
    b = B()
    x = b.var(T.INT32)
    b.if_()
    b.assign(x, b.s())
    b.else_()
    b.end_if()
    b.probe()
    assert b.at(x) == [T.UNKNOWN]


def test_if_else_both_strong_updates_to_same_type():
    b = B()
    x = b.var(T.NULL)
    b.if_()
    b.assign(x, b.s())
    b.else_()
    b.assign(x, b.s())
    b.end_if()
    b.probe()
    assert b.at(x) == [T.STRING]


def test_nested_if():
    b = B()
    x = b.var(T.INT32)
    b.if_()
    b.assign(x, b.s())
    b.if_()
    b.assign(x, b.null())
    b.probe()                                  # null
    b.else_()
    b.probe()                                  # string
    b.end_if()
    b.probe()                                  # null join string
    b.else_()
    b.probe()                                  # int32 (entry)
    b.end_if()
    b.probe()
    assert b.at(x) == [T.NULL, T.STRING, T.UNKNOWN, T.INT32, T.UNKNOWN]


def test_nested_if_inner_resolves_back():
    b = B()
    x = b.var(T.INT32)
    b.if_()
    b.if_()
    b.assign(x, b.lit(T.NUMBER, "1.5"))
    b.else_()
    b.assign(x, b.lit(T.NUMBER, "2.5"))
    b.end_if()
    b.probe()
    b.end_if()
    b.probe()
    assert b.at(x) == [T.NUMBER, T.NUMBER]


def test_for_zero_iterations_joins_entry():
    b = B()
    x = b.var(T.NULL)
    b.for_()
    b.assign(x, b.obj())
    b.end_for()
    b.probe()
    assert b.at(x) == [T.UNKNOWN]


def test_for_body_sees_join_of_entry_and_back_edge():
    b = B()
    x = b.var(T.NULL)
    b.for_()
    b.probe()
    b.assign(x, b.obj())
    b.probe()
    b.end_for()
    assert b.at(x) == [T.UNKNOWN, T.OBJECT]


def test_for_without_assignment_keeps_type():
    b = B()
    x = b.var(T.INT32)
    b.for_()
    b.probe()
    b.end_for()
    b.probe()
    assert b.at(x) == [T.INT32, T.INT32]


def test_for_fixpoint_propagates_through_second_variable():
    # for { y = x; x = "s" }: y only becomes unknown on the second iteration of the analysis
    b = B()
    x = b.var(T.INT32)
    y = b.var(T.INT32)
    b.for_()
    b.assign(y, x)
    b.assign(x, b.s())
    b.end_for()
    b.probe()
    assert b.at(y) == [T.UNKNOWN]
    assert b.at(x) == [T.UNKNOWN]


def test_for_fixpoint_chain_of_three():
    b = B()
    x = b.var(T.INT32)
    y = b.var(T.INT32)
    z = b.var(T.INT32)
    b.for_()
    b.assign(z, y)
    b.assign(y, x)
    b.assign(x, b.s())
    b.end_for()
    b.probe()
    assert b.at(z) == [T.UNKNOWN] and b.at(y) == [T.UNKNOWN]


def test_nested_for():
    b = B()
    x = b.var(T.NULL)
    b.for_()
    b.for_()
    b.probe()
    b.assign(x, b.obj())
    b.end_for()
    b.probe()
    b.end_for()
    b.probe()
    assert b.at(x) == [T.UNKNOWN, T.UNKNOWN, T.UNKNOWN]


def test_nested_for_inner_strong_update_visible_in_inner_after():
    b = B()
    x = b.var(T.INT32)
    b.for_()
    b.assign(x, b.s())
    b.for_()
    b.probe()
    b.end_for()
    b.end_for()
    b.probe()
    assert b.at(x) == [T.STRING, T.UNKNOWN]


def test_loop_with_condition_assignment_inside_if():
    b = B()
    x = b.var(T.INT32)
    b.for_()
    b.if_()
    b.assign(x, b.lit(T.NUMBER, "0.5"))
    b.end_if()
    b.end_for()
    b.probe()
    assert b.at(x) == [T.NUMBER]


def test_try_catch_entry_joins_entry_and_try_end():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.assign(x, b.s())
    b.probe()                                  # string
    e = b.catch()
    b.probe()                                  # int32 join string
    b.assign(x, b.s())
    b.end_try()
    b.probe()                                  # string (try end) join string (catch end)
    assert b.at(x) == [T.STRING, T.UNKNOWN, T.STRING]


def test_try_catch_nothing_assigned_in_try():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.catch()
    b.probe()
    b.assign(x, b.s())
    b.end_try()
    b.probe()
    assert b.at(x) == [T.INT32, T.UNKNOWN]


def test_try_catch_after_joins_try_and_catch_ends():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.assign(x, b.lit(T.NUMBER, "1.5"))
    b.catch()
    b.assign(x, b.lit())
    b.end_try()
    b.probe()
    assert b.at(x) == [T.NUMBER]


def test_catch_variable_is_unknown():
    b = B()
    b.try_()
    e = b.catch()
    b.probe()
    b.end_try()
    assert b.at(e) == [T.UNKNOWN]


def test_try_finally_entry_joins_entry_and_try_end():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.assign(x, b.s())
    b.finally_()
    b.probe()
    b.end_try()
    b.probe()
    assert b.at(x) == [T.UNKNOWN, T.UNKNOWN]


def test_try_finally_strong_update_in_finally():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.assign(x, b.s())
    b.finally_()
    b.assign(x, b.lit())
    b.end_try()
    b.probe()
    assert b.at(x) == [T.INT32]


def test_try_finally_without_assignment_in_try():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.finally_()
    b.probe()
    b.end_try()
    b.probe()
    assert b.at(x) == [T.INT32, T.INT32]


def test_try_catch_finally():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.assign(x, b.s())
    b.catch()
    b.assign(x, b.s())
    b.finally_()
    b.probe()                                  # join(entry int32, after string)
    b.end_try()
    b.probe()
    assert b.at(x) == [T.UNKNOWN, T.UNKNOWN]


def test_try_catch_finally_all_same():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.assign(x, b.lit())
    b.catch()
    b.assign(x, b.lit())
    b.finally_()
    b.probe()
    b.end_try()
    b.probe()
    assert b.at(x) == [T.INT32, T.INT32]


def test_try_catch_finally_finally_overrides():
    b = B()
    x = b.var(T.INT32)
    b.try_()
    b.assign(x, b.s())
    b.catch()
    b.assign(x, b.null())
    b.finally_()
    b.assign(x, b.arr())
    b.end_try()
    b.probe()
    assert b.at(x) == [T.ARRAY]


def test_function_body_does_not_leak_assignments_locally():
    # the function body runs separately: assignments to its own locals/params stay inside
    b = B()
    f, p = b.fn(1)
    b.probe()
    b.assign(p, b.s())
    b.probe()
    b.end_fn()
    b.probe()
    assert b.at(p) == [T.UNKNOWN, T.STRING, T.UNKNOWN]


def test_function_name_is_function_typed():
    b = B()
    f = b.fn()
    b.probe()
    b.end_fn()
    b.probe()
    assert b.at(f) == [T.FUNCTION, T.FUNCTION]


# ---------------------------------------------------------------------------- escaping / captured

def test_escaping_variable_is_typed_by_program_wide_join():
    b = B()
    x = b.var(T.INT32)
    b.probe()
    f = b.fn()
    b.assign(x, b.s())
    b.end_fn()
    b.probe()
    b.assign(x, b.lit())
    b.probe()
    an = analyse(b.prog())
    assert x in an.escaping and x in an.captured
    assert an.types[x] == T.UNKNOWN
    assert b.at(x) == [T.UNKNOWN] * 3


def test_escaping_with_consistent_types_stays_precise():
    b = B()
    x = b.var(T.INT32)
    f = b.fn()
    b.assign(x, b.lit())
    b.end_fn()
    b.probe()
    assert b.at(x) == [T.INT32]
    assert x in analyse(b.prog()).escaping


def test_assignment_in_own_function_is_not_escaping():
    b = B()
    f = b.fn()
    x = b.var(T.INT32)
    b.assign(x, b.s())
    b.probe()
    b.end_fn()
    an = analyse(b.prog())
    assert x not in an.escaping and x not in an.captured
    assert b.at(x) == [T.STRING]


def test_param_assigned_in_own_function_is_not_escaping():
    b = B()
    f, p = b.fn(1)
    b.assign(p, b.s())
    b.end_fn()
    an = analyse(b.prog())
    assert p not in an.escaping and p not in an.captured


def test_param_assigned_in_nested_function_escapes():
    b = B()
    f, p = b.fn(1)
    g = b.fn()
    b.assign(p, b.s())
    b.end_fn()
    b.end_fn()
    an = analyse(b.prog())
    assert p in an.escaping and p in an.captured and g not in an.escaping


def test_captured_when_read_in_nested_function_only():
    b = B()
    x = b.var()
    t = b.lit()
    other = b.var()
    f = b.fn()
    b.add(Unary("-"), x, out=1)
    b.add(Unary("-"), t, out=1)
    b.end_fn()
    an = analyse(b.prog())
    assert an.captured == {x, t}
    assert not an.escaping


def test_function_name_used_in_its_own_body_is_not_captured_by_itself():
    b = B()
    f = b.fn()
    b.add(CallFunction(), f, out=1)
    b.end_fn()
    # f is owned by the top level and used in its own body: counted as captured
    assert f in analyse(b.prog()).captured


def test_top_level_use_is_not_captured():
    b = B()
    x = b.var()
    b.add(Unary("-"), x, out=1)
    assert analyse(b.prog()).captured == set()


# ---------------------------------------------------------------------------- storage

def storage_of(b, *vs):
    an = analyse(b.prog())
    return [an.storage[v] for v in vs]


def test_storage_top_level():
    b = B()
    v, l, c = b.var(kind="var"), b.var(kind="let"), b.var(kind="const")
    t = b.lit()
    assert storage_of(b, v, l, c, t) == ["top-var", "top-let", "top-const", "temp-top"]


def test_storage_function_locals_and_params():
    b = B()
    f, p = b.fn(1)
    v, l, c = b.var(kind="var"), b.var(kind="let"), b.var(kind="const")
    t = b.lit()
    b.end_fn()
    assert storage_of(b, f, p, v, l, c, t) == ["function", "param", "local-var", "local-let", "local-const",
                                               "temp-local"]


def test_storage_block_declarations():
    b = B()
    b.if_()
    v, l, c = b.var(kind="var"), b.var(kind="let"), b.var(kind="const")
    b.end_if()
    assert storage_of(b, v, l, c) == ["local-var", "block-let", "block-const"]


def test_storage_block_inside_function():
    b = B()
    b.fn()
    b.for_()
    v, l = b.var(kind="var"), b.var(kind="let")
    b.end_for()
    b.end_fn()
    assert storage_of(b, v, l) == ["local-var", "block-let"]


def test_storage_block_inside_catch_try_else():
    b = B()
    b.try_()
    l1 = b.var(kind="let")
    e = b.catch()
    l2 = b.var(kind="let")
    b.finally_()
    l3 = b.var(kind="let")
    b.end_try()
    b.if_()
    b.else_()
    l4 = b.var(kind="const")
    b.end_if()
    assert storage_of(b, l1, e, l2, l3, l4) == ["block-let", "catch", "block-let", "block-let", "block-const"]


def test_storage_counter_and_catch_and_temps_in_blocks():
    b = B()
    i = b.for_()
    t = b.lit()
    b.end_for()
    assert storage_of(b, i, t) == ["counter", "temp-top"]


def test_storage_activation_via_arguments():
    b = B()
    f, p = b.fn(1)
    v, l = b.var(kind="var"), b.var(kind="let")
    b.add(LoadBuiltin("arguments"), out=1)
    t = b.lit()
    b.end_fn()
    g, q = b.fn(1)
    w = b.var()
    b.end_fn()
    an = analyse(b.prog())
    assert an.activation[f] is True and an.activation[g] is False
    assert an.storage[p] == "activation-param"
    assert an.storage[v] == "activation-local-var" and an.storage[l] == "activation-local-let"
    assert an.storage[t] == "activation-temp-local"
    assert an.storage[q] == "param" and an.storage[w] == "local-var"
    assert an.storage[f] == "function"


@pytest.mark.parametrize("name", ["arguments", "eval"])
def test_activation_by_builtin(name):
    b = B()
    f = b.fn()
    b.add(LoadBuiltin(name), out=1)
    b.end_fn()
    assert analyse(b.prog()).activation[f] is True


@pytest.mark.parametrize("code,expected", [
    ("eval({0})", True),
    ("with ({0}) {{ }}", True),
    ("with({0}) {{ }}", True),
    ("arguments.length", True),
    ("{0} + 1", False),
    ("evaluate({0})", False),
    ("width ({0})", False),
])
def test_activation_by_eval_code(code, expected):
    b = B()
    f = b.fn()
    x = b.lit()
    b.add(Eval(code), x)
    b.end_fn()
    assert analyse(b.prog()).activation[f] is expected


def test_activation_by_eval_code_with_output():
    b = B()
    f = b.fn()
    x = b.lit()
    b.add(Eval("eval({0})", T.UNKNOWN), x, out=1)
    b.end_fn()
    assert analyse(b.prog()).activation[f] is True


def test_activation_is_per_function_body_not_nested():
    b = B()
    outer = b.fn()
    inner = b.fn()
    b.add(LoadBuiltin("arguments"), out=1)
    b.end_fn()
    b.end_fn()
    an = analyse(b.prog())
    assert an.activation[inner] is True and an.activation[outer] is False


def test_activation_top_level_eval_has_no_function():
    b = B()
    b.add(Eval("eval('1')"), )
    an = analyse(b.prog())
    assert not any(an.activation.values())


def test_storage_of_params_with_activation_in_block():
    b = B()
    f, p = b.fn(1)
    b.if_()
    b.add(LoadBuiltin("arguments"), out=1)
    l = b.var(kind="let")
    b.end_if()
    b.end_fn()
    an = analyse(b.prog())
    assert an.activation[f] and an.storage[p] == "activation-param"
    assert an.storage[l] == "block-let"


# ---------------------------------------------------------------------------- return types

def _fn_returning(*vals):
    b = B()
    f = b.fn()
    for t in vals:
        b.ret(b.lit(t))
    b.end_fn()
    return b, f


def test_return_plain():
    b, f = _fn_returning(T.STRING)
    assert analyse(b.prog()).returns[f] == T.STRING


def test_return_two_joined():
    b, f = _fn_returning(T.INT32, T.NUMBER)
    assert analyse(b.prog()).returns[f] == T.NUMBER
    b, f = _fn_returning(T.INT32, T.STRING)
    assert analyse(b.prog()).returns[f] == T.UNKNOWN


def test_return_objects_join_to_object():
    b = B()
    f = b.fn()
    b.ret(b.arr())
    b.ret(b.obj())
    b.end_fn()
    assert analyse(b.prog()).returns[f] == T.OBJECT


def test_no_return_is_undefined():
    b = B()
    f = b.fn()
    b.lit()
    b.end_fn()
    assert analyse(b.prog()).returns[f] == T.UNDEFINED


def test_bare_return_is_undefined():
    b = B()
    f = b.fn()
    b.ret()
    b.end_fn()
    assert analyse(b.prog()).returns[f] == T.UNDEFINED


def test_bare_return_and_value_join():
    b = B()
    f = b.fn()
    b.if_()
    b.ret()
    b.end_if()
    b.ret(b.lit())
    b.end_fn()
    assert analyse(b.prog()).returns[f] == T.UNKNOWN


def test_conditional_return_can_fall_off_the_end():
    b = B()
    f = b.fn()
    b.if_()
    b.ret(b.lit())
    b.end_if()
    b.end_fn()
    assert analyse(b.prog()).returns[f] in (T.UNKNOWN,)


def test_return_param_is_unknown():
    b = B()
    f, p = b.fn(1)
    b.ret(p)
    b.end_fn()
    assert analyse(b.prog()).returns[f] == T.UNKNOWN


def test_return_in_nested_function_belongs_to_it():
    b = B()
    f = b.fn()
    g = b.fn()
    b.ret(b.s())
    b.end_fn()
    b.end_fn()
    an = analyse(b.prog())
    assert an.returns[g] == T.STRING and an.returns[f] == T.UNDEFINED


def test_call_result_uses_callee_return_type():
    b = B()
    f, = (b.fn(),)
    b.ret(b.s())
    b.end_fn()
    (r,) = b.add(CallFunction(), f, out=1)
    an = analyse(b.prog())
    assert an.types[r] == T.STRING


def test_call_result_of_no_return_function_is_undefined():
    b = B()
    f = b.fn()
    b.end_fn()
    (r,) = b.add(CallFunction(), f, out=1)
    assert analyse(b.prog()).types[r] == T.UNDEFINED


def test_call_before_definition_order_is_irrelevant_for_nested_calls():
    # g calls f which is defined earlier; g's return type follows f's
    b = B()
    f = b.fn()
    b.ret(b.lit())
    b.end_fn()
    g = b.fn()
    (r,) = b.add(CallFunction(), f, out=1)
    b.ret(r)
    b.end_fn()
    (r2,) = b.add(CallFunction(), g, out=1)
    an = analyse(b.prog())
    assert an.returns[g] == T.INT32 and an.types[r2] == T.INT32


def test_recursion_converges():
    b = B()
    f = b.fn()
    b.if_()
    b.ret(b.lit())
    b.end_if()
    (r,) = b.add(CallFunction(), f, out=1)
    b.ret(r)
    b.end_fn()
    (top,) = b.add(CallFunction(), f, out=1)
    an = analyse(b.prog())
    assert an.returns[f] == T.INT32
    assert an.types[top] == T.INT32 and an.types[r] == T.INT32


def test_mutual_recursion_converges():
    b = B()
    f = b.fn()
    g = b.fn()
    b.ret(b.s())
    b.end_fn()
    (r,) = b.add(CallFunction(), g, out=1)
    b.ret(r)
    b.end_fn()
    an = analyse(b.prog())
    assert an.returns[g] == T.STRING and an.returns[f] == T.STRING


def test_recursion_with_conflicting_types():
    b = B()
    f = b.fn()
    b.if_()
    b.ret(b.lit())
    b.end_if()
    (r,) = b.add(CallFunction(), f, out=1)
    b.ret(b.add(Unary("typeof"), r, out=1)[0])
    b.end_fn()
    assert analyse(b.prog()).returns[f] == T.UNKNOWN


def test_reassigned_function_call_is_unknown():
    b = B()
    f = b.fn()
    b.ret(b.s())
    b.end_fn()
    b.assign(f, b.fnref())
    (r,) = b.add(CallFunction(), f, out=1)
    assert analyse(b.prog()).types[r] == T.UNKNOWN


def test_call_of_param_or_variable_is_unknown():
    b = B()
    f = b.fn()
    b.ret(b.s())
    b.end_fn()
    v = b.declare(f)
    (r,) = b.add(CallFunction(), v, out=1)
    assert analyse(b.prog()).types[r] == T.UNKNOWN


def test_function_reassigned_in_other_function_is_unknown():
    b = B()
    f = b.fn()
    b.ret(b.s())
    b.end_fn()
    g = b.fn()
    b.assign(f, b.fnref())
    b.end_fn()
    (r,) = b.add(CallFunction(), f, out=1)
    assert analyse(b.prog()).types[r] == T.UNKNOWN


# ---------------------------------------------------------------------------- expression types

def test_expression_output_types():
    b = B()
    s, n = b.s(), b.lit()
    a = b.arr()
    o = b.obj()
    (c,) = b.add(Binary("+"), s, n, out=1)
    (u,) = b.add(Unary("!"), n, out=1)
    (len_,) = b.add(GetProperty("length"), a, out=1)
    (len_o,) = b.add(GetProperty("length"), o, out=1)
    (len_s,) = b.add(GetProperty("length"), s, out=1)
    (el,) = b.add(GetElement(), a, n, out=1)
    (d,) = b.add(DeleteProperty("x"), o, out=1)
    (m,) = b.add(CallMethod("push"), a, n, out=1)
    (m2,) = b.add(CallMethod("slice"), a, out=1)
    (m3,) = b.add(CallMethod("slice"), o, out=1)
    (m4,) = b.add(CallMethod("includes"), a, n, out=1)
    (m5,) = b.add(CallMethod("join"), a, out=1)
    (m6,) = b.add(CallMethod("zzz"), a, out=1)
    mp = b.add(LoadBuiltin("Map", T.FUNCTION), out=1)[0]
    (nm,) = b.add(Construct(), mp, out=1)
    ob = b.add(LoadBuiltin("Object", T.FUNCTION), out=1)[0]
    (no,) = b.add(Construct(), ob, out=1)
    fx = b.fn()
    b.end_fn()
    (nf,) = b.add(Construct(), fx, out=1)
    (cond,) = b.add(__import__("oxpecker.ir.ops", fromlist=["Conditional"]).Conditional(), n, a, o, out=1)
    (ev,) = b.add(Eval("1", T.BIGINT), out=1)
    an = analyse(b.prog())
    t = an.types
    assert t[c] == T.STRING and t[u] == T.BOOLEAN
    assert t[len_] == T.INT32 and t[len_o] == T.UNKNOWN and t[len_s] == T.INT32
    assert t[el] == T.UNKNOWN and t[d] == T.BOOLEAN
    assert (t[m], t[m2], t[m3], t[m4], t[m5], t[m6]) == (T.NUMBER, T.ARRAY, T.UNKNOWN, T.BOOLEAN, T.STRING,
                                                         T.UNKNOWN)
    assert t[nm] == T.MAP and t[no] == T.OBJECT and t[nf] == T.OBJECT
    assert t[cond] == T.OBJECT and t[ev] == T.BIGINT


def test_loop_counter_is_int32():
    b = B()
    i = b.for_()
    b.end_for()
    assert analyse(b.prog()).types[i] == T.INT32


def test_type_at_temporary_is_its_single_type_and_declared_is_flow_sensitive():
    b = B()
    t = b.lit(T.STRING, '"s"')
    x = b.declare(t)
    b.probe()
    b.assign(x, b.lit())
    b.probe()
    an = analyse(b.prog())
    assert [an.type_at(i, t) for i in b.probes] == [T.STRING, T.STRING]
    assert [an.type_at(i, x) for i in b.probes] == [T.STRING, T.INT32]
    assert an.types[x] == T.UNKNOWN            # the program-wide join
    assert an.type_at(0, 999) == T.UNKNOWN     # unknown ids


def test_type_at_before_definition_falls_back_to_program_type():
    b = B()
    b.probe()
    x = b.var(T.STRING)
    an = analyse(b.prog())
    assert an.type_at(b.probes[0], x) == T.STRING


def test_env_at_has_one_entry_per_instruction_plus_end():
    b = B()
    x = b.var(T.INT32)
    b.if_()
    b.assign(x, b.s())
    b.end_if()
    an = analyse(b.prog())
    assert len(an.env_at) == len(b.p) + 1
    assert an.env_at[-1][x] == T.UNKNOWN
    assert an.env_at[2][x] == T.INT32


def test_declare_var_copies_input_type():
    b = B()
    a = b.arr()
    x = b.declare(a)
    assert analyse(b.prog()).types[x] == T.ARRAY


def test_analyse_does_not_modify_and_is_repeatable():
    b = B()
    x = b.var(T.NULL)
    b.for_()
    b.assign(x, b.obj())
    b.end_for()
    p = b.prog()
    a1, a2 = analyse(p), analyse(p)
    assert a1.types == a2.types and a1.env_at == a2.env_at and a1.storage == a2.storage


# ---------------------------------------------------------------------------- property tests

@settings(deadline=None, database=None, suppress_health_check=list(HealthCheck), max_examples=50)
@given(ir_programs(GenOptions()))
def test_generated_programs_are_valid_and_deterministic(pm):
    prog, _ = pm
    an = verify(prog)
    for i, ins in enumerate(prog):
        vis = set(an.visible_at[i])
        assert all(v in vis for v in ins.inputs), (i, ins)
    again = analyse(prog)
    assert an.types == again.types
    assert an.returns == again.returns
    assert an.env_at == again.env_at
    assert an.storage == again.storage
    assert an.visible_at == again.visible_at
