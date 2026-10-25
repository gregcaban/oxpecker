"""Unit tests for the IR generation builder (`oxpecker/ir/builder.py`) and its small strategies."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, find, settings, strategies as st
from hypothesis.control import BuildContext
from hypothesis.internal.conjecture.data import ConjectureData

from oxpecker.gen.builder import GenOptions
from oxpecker.gen.types import T
from oxpecker.ir import registry
from oxpecker.ir.analysis import InvalidProgram, analyse, verify
from oxpecker.ir.builder import (Builder, lifting_choices, make, maybe, nested, pick, pick_or_make, statements,
                                 target)
from oxpecker.ir.ops import (Binary, CallFunction, DeclareVar, Instr, LoadLiteral, Reassign, Update,
                             BeginFunction, BeginFor, BeginCatch, EndTry)
from oxpecker.ir.strategies import ir_programs

registry.load_all()

OPTS = GenOptions()
POOL = registry.weighted(registry.available_generators(OPTS))
SLOW = settings(database=None, max_examples=500, deadline=None, suppress_health_check=list(HealthCheck))
FAST = settings(database=None, max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))


def minimal(strategy, pred=lambda x: True):
    return find(strategy, pred, settings=SLOW)


def choices_recorded(strategy, choices):
    d = ConjectureData.for_choices(choices)
    with BuildContext(d, wrapped_test=lambda: None):
        v = d.draw(strategy)
    return v, len(d.choices)


def lit(b, t, text="0"):
    return b.emit(LoadLiteral(t, text))


def ops(prog):
    return [type(i.op) for i in prog]


# ------------------------------------------------------------------ pick / maybe

@pytest.mark.parametrize("n", [1, 2, 5, 40])
def test_pick_records_exactly_one_choice(n):
    xs = list(range(10, 10 + n))
    v, k = choices_recorded(pick(xs), (0,))
    assert v == 10 and k == 1


def test_sampled_from_single_records_none_but_pick_does():
    assert choices_recorded(st.sampled_from([7]), ())[1] == 0
    assert choices_recorded(pick([7]), (0,))[1] == 1


def test_pick_shrinks_to_first_element():
    assert minimal(pick(["a", "b", "c"])) == "a"
    assert minimal(pick(["a", "b", "c"]), lambda x: x != "a") == "b"


def test_maybe_shrinks_to_false():
    assert minimal(maybe(0.5)) is False
    assert minimal(maybe(0.9)) is False
    assert minimal(maybe(0.5), lambda x: x) is True


def test_maybe_probability_roughly_p():
    for p in (0.25, 0.75):
        n = 1000
        vals = [choices_recorded(maybe(p), (k % 100,))[0] for k in range(n)]
        # exact on the uniform grid 0..99
        assert abs(sum(vals) / n - p) < 0.02


# ------------------------------------------------------------------ visible / assignable

def test_visible_is_most_recent_first_and_filters_by_type():
    b = Builder(OPTS)
    i = lit(b, T.INT32, "1")
    s = lit(b, T.STRING, '"a"')
    n = lit(b, T.NUMBER, "1.5")
    assert b.visible() == [n, s, i]
    assert b.visible(T.NUMBER) == [n, i]          # INT32 substitutes for NUMBER
    assert b.visible(T.INT32) == [i]
    assert b.visible(T.STRING) == [s]
    assert b.visible(T.UNKNOWN) == [n, s, i]
    assert b.visible(T.BOOLEAN) == []


def test_visible_unknown_typed_variable_only_fits_unknown():
    b = Builder(OPTS, visible=[5], types={})
    assert b.visible() == [5]
    assert b.visible(T.STRING) == []


def test_assignable_only_declared_nonconst_noncounter():
    b = Builder(OPTS)
    t = lit(b, T.INT32)
    v = b.declare(t, "var")
    l = b.declare(t, "let")
    c = b.declare(t, "const")
    with b.function(1) as (f, (p,)):
        with b.for_(t) as i:
            got = b.assignable()
    assert t not in got and c not in got and i not in got
    assert set(got) == {v, l, p, f}
    assert got.index(p) < got.index(l) < got.index(v)       # most recent first
    assert b.assignable(T.STRING) == []


def test_assignable_includes_catch_variable():
    b = Builder(OPTS)
    with b.try_() as t:
        with t.catch() as e:
            assert e in b.assignable()


# ------------------------------------------------------------------ declare / const / emit

def test_declare_registers_variable():
    b = Builder(OPTS)
    s = lit(b, T.STRING, '"x"')
    v = b.declare(s, "let", hint="acc")
    assert b.declared[v] == "let" and b.type_of(v) == T.STRING
    ins = b.instrs[-1]
    assert ins.op == DeclareVar("let") and ins.inputs == (s,) and ins.outputs == (v,) and ins.hint == "acc"
    assert v in b.visible()
    verify(b.program())


def test_const_is_pinned_inline_literal():
    b = Builder(OPTS)
    c = b.const(T.INT32, "0")
    ins = b.instrs[-1]
    assert ins.keep is False and ins.op == LoadLiteral(T.INT32, "0") and ins.outputs == (c,)
    assert b.type_of(c) == T.INT32


def test_emit_default_keep_is_none_and_keep_is_passed_through():
    b = Builder(OPTS)
    a = lit(b, T.INT32)
    b.emit(LoadLiteral(T.INT32, "2"), keep=True)
    b.binary("+", a, a)
    assert [i.keep for i in b.instrs] == [None, True, None]


def test_emit_type_override_steers_generation_only():
    b = Builder(OPTS)
    m = b.emit(LoadLiteral(T.OBJECT, "new Map()"), t=T.MAP)
    assert b.type_of(m) == T.MAP
    # without t the generic type
    o = b.emit(LoadLiteral(T.OBJECT, "{}"))
    assert b.type_of(o) == T.OBJECT


def test_emit_t_ignored_when_no_output():
    b = Builder(OPTS)
    f = b.builtin("Math")
    assert b.emit(CallFunction(discard=True), (f,), t=T.STRING) is None


def test_discard_calls_produce_no_output():
    b = Builder(OPTS)
    f = b.builtin("f", T.FUNCTION)
    o = b.builtin("Math")
    n0 = b.next_id
    assert b.emit(CallFunction(discard=True), (f,)) is None
    b.do_call(f, o)
    b.do_method(o, "max")
    assert b.next_id == n0
    assert all(i.outputs == () for i in b.instrs[2:])
    assert b.visible() == [o, f]


def test_update_without_value_produces_no_output_but_changes_type():
    b = Builder(OPTS)
    x = b.declare(lit(b, T.INT32))
    assert b.emit(Update("++", value=False), (x,)) is None
    assert b.instrs[-1].outputs == ()
    assert b.type_of(x) == T.NUMBER
    y = b.emit(Update("++"), (x,))
    assert y is not None and b.type_of(y) == T.NUMBER


def test_reassign_is_a_strong_update_of_generation_type():
    b = Builder(OPTS)
    x = b.declare(lit(b, T.INT32))
    s = lit(b, T.STRING, '"s"')
    b.reassign(x, s)
    assert b.type_of(x) == T.STRING
    verify(b.program())


def test_ids_are_fresh_and_unique():
    b = Builder(OPTS)
    ids = [lit(b, T.INT32) for _ in range(5)]
    assert ids == sorted(set(ids))


# ------------------------------------------------------------------ blocks

def test_function_block_scoping_and_returns():
    b = Builder(OPTS)
    a = lit(b, T.STRING, '"a"')
    assert b.depth == 0
    with b.function(2, param_types=[T.INT32]) as (f, (p, q)):
        assert b.depth == 1
        assert {f, p, q, a} <= set(b.visible())
        assert b.type_of(p) == T.INT32 and b.type_of(q) == T.UNKNOWN
        assert b.declared[f] == "function" and b.declared[p] == "param"
        r = b.binary("+", a, a)
        b.ret(r)
        local = lit(b, T.INT32)
    assert b.depth == 0
    assert f in b.visible()
    assert not ({p, q, r, local} & set(b.visible()))
    assert b.returns[f] == T.STRING
    b.call(f)
    verify(b.program())


def test_function_without_return_returns_undefined_and_bare_return():
    b = Builder(OPTS)
    with b.function(0) as (f, _):
        pass
    assert b.returns[f] == T.UNDEFINED
    with b.function(0) as (g, _):
        b.ret()
    assert b.returns[g] == T.UNDEFINED
    with b.function(0) as (h, _):
        b.ret(lit(b, T.INT32))
        b.ret(lit(b, T.NUMBER))
    assert b.returns[h] == T.NUMBER
    with b.function(0) as (k, _):
        b.ret(lit(b, T.INT32))
        b.ret(lit(b, T.STRING))
    assert b.returns[k] == T.UNKNOWN
    verify(b.program())


def test_ret_outside_function_asserts():
    with pytest.raises(AssertionError):
        Builder(OPTS).ret()


def test_call_uses_recorded_return_type():
    b = Builder(OPTS)
    with b.function(0) as (f, _):
        b.ret(lit(b, T.STRING, '"s"'))
    r = b.call(f)
    assert b.type_of(r) == T.STRING


def test_function_recursion_name_visible_inside():
    b = Builder(OPTS)
    with b.function(0) as (f, _):
        assert f in b.visible()
        b.do_call(f)
    verify(b.program())


def test_if_else_blocks():
    b = Builder(OPTS)
    c = lit(b, T.BOOLEAN, "true")
    with b.if_(c) as blk:
        assert b.depth == 1 and b.frames[-1].kind == "then"
        inner_then = lit(b, T.INT32)
        with blk.else_():
            assert b.depth == 1 and b.frames[-1].kind == "else"
            assert inner_then not in b.visible()
            inner_else = lit(b, T.INT32)
    assert b.depth == 0
    assert not ({inner_then, inner_else} & set(b.visible()))
    prog = b.program()
    verify(prog)
    assert [t.__name__ for t in ops(prog)] == ["LoadLiteral", "BeginIf", "LoadLiteral", "BeginElse", "LoadLiteral",
                                              "EndIf"]


def test_if_without_else():
    b = Builder(OPTS)
    with b.if_(lit(b, T.BOOLEAN, "true")):
        lit(b, T.INT32)
    verify(b.program())
    assert "BeginElse" not in [t.__name__ for t in ops(b.program())]


def test_for_block_counter():
    b = Builder(OPTS)
    n = lit(b, T.INT32, "3")
    with b.for_(n) as i:
        assert b.depth == 1 and i in b.visible() and i in b.loop_counters
        assert b.type_of(i) == T.INT32
        x = lit(b, T.INT32)
    assert i not in b.visible() and x not in b.visible()
    assert b.depth == 0
    b.program()
    verify(b.program())
    assert b.program()[1].op == BeginFor(n) and b.program()[1].inner == (i,)


def test_try_catch_finally():
    b = Builder(OPTS)
    with b.try_() as t:
        in_try = lit(b, T.INT32)
        with t.catch() as e:
            assert e in b.visible() and in_try not in b.visible()
            assert b.declared[e] == "catch" and b.frames[-1].kind == "catch"
            in_catch = lit(b, T.INT32)
        with t.finally_():
            assert b.frames[-1].kind == "finally"
            assert not ({e, in_catch} & set(b.visible()))
    assert b.depth == 0 and e not in b.visible()
    verify(b.program())


def test_try_with_only_finally():
    b = Builder(OPTS)
    with b.try_() as t:
        with t.finally_():
            lit(b, T.INT32)
    prog = b.program()
    verify(prog)
    assert BeginCatch not in ops(prog)


def test_try_without_handler_gets_empty_catch():
    b = Builder(OPTS)
    with b.try_():
        lit(b, T.INT32)
    prog = b.program()
    verify(prog)
    names = [t.__name__ for t in ops(prog)]
    assert names == ["BeginTry", "LoadLiteral", "BeginCatch", "EndTry"]
    assert b.depth == 0


def test_guarded():
    b = Builder(OPTS)
    seen = []
    f = b.builtin("f", T.FUNCTION)
    b.guarded(lambda: b.do_call(f), on_error=lambda e: seen.append(e))
    assert len(seen) == 1
    verify(b.program())
    b2 = Builder(OPTS)
    b2.guarded(lambda: None)
    verify(b2.program())


def test_nested_blocks_verify():
    b = Builder(OPTS)
    x = b.declare(lit(b, T.INT32))
    with b.function(1) as (f, (p,)):
        with b.for_(lit(b, T.INT32, "2")) as i:
            with b.if_(b.binary("<", i, p)) as blk:
                b.emit(Update("++", value=False), (x,))
                with blk.else_():
                    with b.try_() as t:
                        b.ret(i)
                        with t.catch() as e:
                            b.reassign(x, e)
                        with t.finally_():
                            b.reassign(x, p)
        assert b.depth == 1
    assert b.depth == 0
    verify(b.program())


def test_depth_restored_on_exception():
    b = Builder(OPTS)
    with pytest.raises(RuntimeError):
        with b.function(0):
            with b.for_(lit(b, T.INT32)):
                raise RuntimeError
    assert b.depth == 0 and len(b.frames) == 1


def test_variable_defined_in_block_not_usable_after():
    b = Builder(OPTS)
    with b.if_(lit(b, T.BOOLEAN, "true")):
        inner = lit(b, T.INT32)
    b.emit(Binary("+"), (inner, inner))      # bad on purpose
    with pytest.raises(InvalidProgram):
        verify(b.program())


# ------------------------------------------------------------------ provenance

def test_provenance_tags_and_origins():
    b = Builder(OPTS)
    with b.provenance("g1"):
        lit(b, T.INT32)
        with b.provenance("g2"):
            lit(b, T.INT32)
        lit(b, T.INT32)
    lit(b, T.INT32)
    gens = [i.gen for i in b.instrs]
    origins = [i.origin for i in b.instrs]
    assert gens == ["g1", "g2", "g1", None]
    assert origins[0] == origins[2] != origins[1] and origins[3] == 0
    assert b.gen_usage == {"g1": 1, "g2": 1}


# ------------------------------------------------------------------ strategies on a builder

@st.composite
def _drawn(draw, strategy_fn, setup=None):
    b = Builder(OPTS, POOL)
    if setup:
        setup(b)
    r = draw(strategy_fn(b))
    return b, r


def _setup_mixed(b):
    lit(b, T.INT32, "1")
    lit(b, T.STRING, '"s"')


@settings(FAST)
@given(_drawn(lambda b: make(b, T.STRING)))
def test_make_emits_value_of_requested_type(br):
    b, v = br
    assert b.type_of(v) == T.STRING
    assert b.instrs[-1].outputs == (v,)
    verify(b.program())


@settings(FAST)
@given(_drawn(lambda b: make(b, T.UNKNOWN)))
def test_make_unknown(br):
    b, v = br
    assert v in b.types
    verify(b.program())


@settings(FAST)
@given(_drawn(lambda b: pick_or_make(b, T.BOOLEAN), _setup_mixed))
def test_pick_or_make_without_fitting_variable_makes_one(br):
    b, v = br
    assert v not in (0, 1)
    assert b.type_of(v) == T.BOOLEAN
    assert len(b.instrs) > 2
    verify(b.program())


@settings(FAST)
@given(_drawn(lambda b: pick_or_make(b, T.STRING), _setup_mixed))
def test_pick_or_make_result_type_fits(br):
    b, v = br
    assert b.type_of(v) == T.STRING


def test_pick_or_make_can_pick_existing_and_shrinks_to_pick():
    def s(b):
        lit(b, T.INT32, "1")
        lit(b, T.INT32, "2")
    strat = _drawn(lambda b: pick_or_make(b, T.INT32), s)
    picked = minimal(strat, lambda br: len(br[0].instrs) == 2)
    b, v = picked
    assert v == 1                        # most recent fitting variable, nothing new emitted
    made = minimal(strat, lambda br: len(br[0].instrs) == 3)
    assert made[1] == 2


def test_pick_or_make_unknown_want_considers_all_visible():
    def s(b):
        lit(b, T.STRING, '"s"')
    strat = _drawn(lambda b: pick_or_make(b, T.UNKNOWN), s)
    b, v = minimal(strat)
    assert v == 0 and len(b.instrs) == 1


@settings(FAST)
@given(_drawn(lambda b: target(b, T.INT32), lambda b: (lit(b, T.INT32), b.declare(b.next_id - 1, "const"),
                                                       b.declare(0, "let"), b.for_(0).__enter__())))
def test_target_never_returns_const_counter_or_temp(br):
    b, v = br
    assert b.declared.get(v) not in (None, "const")
    assert v not in b.loop_counters


@settings(FAST)
@given(_drawn(lambda b: target(b, T.STRING), _setup_mixed))
def test_target_declares_when_nothing_assignable(br):
    b, v = br
    assert b.declared[v] == "var" and b.type_of(v) == T.STRING
    assert isinstance(b.instrs[-1].op, DeclareVar) and b.instrs[-1].outputs == (v,)
    verify(b.program())


def test_target_picks_existing_assignable_when_not_fresh():
    def s(b):
        b.declare(lit(b, T.INT32))
    strat = _drawn(lambda b: target(b, T.INT32), s)
    b, v = minimal(strat)
    assert v == 1 and len(b.instrs) == 2           # shrinks to the existing declared variable, no new DeclareVar
    b, v = minimal(strat, lambda br: len(br[0].instrs) > 2)
    assert isinstance(b.instrs[-1].op, DeclareVar) and v == b.instrs[-1].outputs[0]


# ------------------------------------------------------------------ statements / nested

@settings(FAST)
@given(st.integers(0, 3), st.integers(0, 3), st.data())
def test_statements_respects_bounds(lo, extra, data):
    hi = lo + extra
    b = Builder(OPTS, POOL)
    data.draw(statements(b, lo, hi))
    n = sum(b.gen_usage.values())
    # generators may themselves use value makers (not counted) or nested statements (counted)
    top_origins = {i.origin for i in b.instrs if i.gen is not None}
    assert lo <= len(top_origins) or n >= lo
    assert n >= lo
    verify(b.program())


def test_statements_exact_bounds_with_simple_pool():
    only = registry.weighted([g for g in registry.available_generators(OPTS) if g.name == "int32"])
    assert only

    @st.composite
    def run(draw):
        b = Builder(OPTS, only)
        draw(statements(b, 2, 4))
        return b

    @settings(FAST)
    @given(run())
    def check(b):
        assert 2 <= b.gen_usage["int32"] <= 4
        assert len({i.origin for i in b.instrs}) == b.gen_usage["int32"]
    check()
    b = minimal(run())
    assert b.gen_usage["int32"] == 2


def test_statements_empty_pool_is_empty():
    b = Builder(OPTS)

    @given(statements(b, 2, 4))
    def check(xs):
        assert xs == []
    check()


def test_nested_does_nothing_when_too_deep():
    @st.composite
    def run(draw, depth):
        b = Builder(OPTS, POOL, depth=depth)
        draw(nested(b))
        return b

    @settings(FAST)
    @given(run(OPTS.max_depth + 1))
    def deep(b):
        assert b.instrs == []
    deep()
    b = minimal(run(0), lambda b: b.instrs)
    assert b.instrs
    # at max_depth exactly it still generates
    find(run(OPTS.max_depth), lambda b: b.instrs, settings=SLOW)


def test_nested_explicit_count_bounds_statements():
    only = registry.weighted([g for g in registry.available_generators(OPTS) if g.name == "int32"])

    @st.composite
    def run(draw):
        b = Builder(OPTS, only)
        draw(nested(b, 2))
        return b

    @settings(FAST)
    @given(run())
    def check(b):
        assert b.gen_usage.get("int32", 0) <= 2
    check()


# ------------------------------------------------------------------ Builder.at

def _sample_program():
    b = Builder(OPTS)
    x = b.declare(lit(b, T.INT32, "1"))                        # 0 lit, 1 decl
    s = lit(b, T.STRING, '"s"')                               # 2
    b.reassign(x, s)                                         # 3
    c = lit(b, T.INT32, "9")                                  # 4
    with b.function(1) as (f, (p,)):                          # 5 BeginFunction
        inner = b.binary("+", p, x)                           # 6
        b.ret(inner)                                          # 7
    with b.for_(c) as i:                                      # 9 BeginFor
        b.binary("+", i, c)                                   # 10
    return b, dict(x=x, s=s, c=c, f=f, p=p, i=i, inner=inner)


def test_at_top_level_context():
    b, v = _sample_program()
    prog = b.program()
    pos = 4
    a = Builder.at(prog, pos, OPTS)
    assert set(a.visible()) == {0, v["x"], v["s"]} or set(a.visible()) == set(analyse(prog).visible_at[pos])
    assert a.visible() == analyse(prog).visible_recent(pos)
    assert a.frames[-1].kind == "top" and a.depth == 0
    assert a.next_id > max(i for ins in prog for i in ins.outputs + ins.inner)
    assert a.declared == analyse(prog).declared
    assert a.loop_counters == {v["i"]}
    assert a.returns[v["f"]] == analyse(prog).returns[v["f"]]


def test_at_is_flow_sensitive():
    b, v = _sample_program()
    prog = b.program()
    before = Builder.at(prog, 3, OPTS)         # before the reassignment
    after = Builder.at(prog, 4, OPTS)          # after it
    assert before.type_of(v["x"]) == T.INT32
    assert after.type_of(v["x"]) == T.STRING
    # and so a string variable is now visible as assignable of STRING type
    assert v["x"] in after.assignable(T.STRING) and v["x"] not in after.assignable(T.INT32)


def test_at_inside_function_and_loop():
    b, v = _sample_program()
    prog = b.program()
    pos_fn = 6
    a = Builder.at(prog, pos_fn, OPTS)
    assert a.frames[-1].kind == "function" and a.depth == 1 and a.frames[-1].fn == v["f"]
    assert v["p"] in a.visible() and v["f"] in a.visible()
    pos_for = 10
    a = Builder.at(prog, pos_for, OPTS)
    assert a.frames[-1].kind == "for" and a.depth == 1
    assert v["i"] in a.visible() and v["i"] in a.loop_counters
    assert v["p"] not in a.visible()
    assert v["i"] not in a.assignable()


def test_at_end_of_program_and_empty_program():
    b, _ = _sample_program()
    prog = b.program()
    a = Builder.at(prog, len(prog), OPTS)
    assert a.depth == 0 and a.frames[-1].kind == "top"
    e = Builder.at((), 0, OPTS)
    assert e.next_id == 0 and e.visible() == [] and e.origin == 1


def test_at_origin_beyond_existing():
    b = Builder(OPTS)
    with b.provenance("g"):
        lit(b, T.INT32)
    a = Builder.at(b.program(), 1, OPTS)
    assert a.origin > max(i.origin for i in b.program())
    with a.provenance("h"):
        lit(a, T.INT32)
    assert a.instrs[0].origin > b.program()[0].origin


def test_at_fresh_ids_do_not_collide():
    b, _ = _sample_program()
    prog = b.program()
    a = Builder.at(prog, 2, OPTS)
    new = lit(a, T.INT32)
    used = {i for ins in prog for i in ins.outputs + ins.inner}
    assert new not in used


@pytest.mark.parametrize("pos", range(0, 13))
def test_inserting_at_every_position_verifies(pos):
    b, _ = _sample_program()
    prog = b.program()
    if pos > len(prog):
        pytest.skip("past end")
    a = Builder.at(prog, pos, OPTS)
    vis = a.visible(T.NUMBER)
    t = lit(a, T.INT32, "5")
    if vis:
        a.binary("+", vis[0], t)
    a.declare(t)
    new = prog[:pos] + a.program() + prog[pos:]
    verify(new)


def test_inserted_block_in_context_verifies():
    b, v = _sample_program()
    prog = b.program()
    a = Builder.at(prog, 6, OPTS)                    # inside the function
    c = lit(a, T.BOOLEAN, "true")
    with a.if_(c):
        a.do_call(v["f"])
        a.ret()
    new = prog[:6] + a.program() + prog[6:]
    verify(new)


# ------------------------------------------------------------------ replay

def test_replay_straight_line():
    b = Builder(OPTS)
    a = lit(b, T.INT32, "1")
    start = len(b.instrs)
    c = lit(b, T.INT32, "2")
    s = b.binary("+", a, c)
    x = b.declare(s, "let")
    body = b.instrs[start:]
    other = lit(b, T.INT32, "7")
    rn = b.replay(body, {a: other})
    copy = b.instrs[start + len(body) + 1:]
    assert len(copy) == len(body)
    new_ids = {rn[c], rn[s], rn[x]}
    assert len(new_ids) == 3 and not new_ids & {a, c, s, x, other}
    assert copy[1].inputs == (other, rn[c])          # input renamed through `rename`, output via fresh id
    assert copy[2].inputs == (rn[s],)
    assert b.declared[rn[x]] == "let" and b.type_of(rn[x]) == T.NUMBER
    assert all(i.keep == o.keep and i.op == o.op for i, o in zip(copy, body))
    assert rn[a] == other
    verify(b.program())


def test_replay_applies_current_provenance():
    b = Builder(OPTS)
    a = lit(b, T.INT32)
    body = list(b.instrs)
    with b.provenance("rep"):
        b.replay(body, {})
    assert b.instrs[-1].gen == "rep" and b.instrs[-1].origin == b.origins


def test_replay_blocks():
    b = Builder(OPTS)
    n = lit(b, T.INT32, "3")
    x = b.declare(n)
    start = len(b.instrs)
    with b.function(1) as (f, (p,)):
        with b.for_(n) as i:
            with b.if_(b.binary("<", i, p)) as blk:
                b.reassign(x, i)
                with blk.else_():
                    with b.try_() as t:
                        b.ret(p)
                        with t.catch() as e:
                            b.reassign(x, e)
                        with t.finally_():
                            pass
    body = b.instrs[start:]
    nb = len(b.instrs)
    rn = b.replay(body, {})
    assert b.depth == 0 and len(b.frames) == 1
    assert b.declared[rn[f]] == "function" and b.declared[rn[p]] == "param" and b.declared[rn[e]] == "catch"
    assert rn[i] in b.loop_counters and rn[i] != i
    assert x not in rn                                  # defined outside: not renamed
    assert b.returns[rn[f]] == b.returns[f]
    assert rn[f] in b.visible()
    assert not ({rn[p], rn[i], rn[e]} & set(b.visible()))
    prog = b.program()
    verify(prog)
    assert ops(prog[start:nb]) == ops(prog[nb:])
    # all ids defined exactly once
    ids = [v for ins in prog for v in ins.outputs + ins.inner]
    assert len(ids) == len(set(ids))


def test_replay_twice_keeps_verifying():
    b = Builder(OPTS)
    start = 0
    x = b.declare(lit(b, T.INT32))
    with b.for_(lit(b, T.INT32, "2")) as i:
        b.emit(Update("++", value=False), (x,))
    body = list(b.instrs)
    b.replay(body, {})
    b.replay(body, {})
    verify(b.program())


# ------------------------------------------------------------------ pin

def test_pin_only_sets_undecided_keeps():
    b = Builder(OPTS)
    a = b.emit(LoadLiteral(T.INT32, "1"), keep=True)      # 0 pinned True
    c = b.emit(LoadLiteral(T.INT32, "2"), keep=False)     # 1 pinned False
    d = b.emit(LoadLiteral(T.INT32, "3"))                 # 2 undecided
    b.do_call(b.builtin("f", T.FUNCTION))                 # 3 builtin undecided, 4 call no output
    b.pin(0)
    assert [i.keep for i in b.instrs] == [True, False, False, False, None]
    assert b.instrs[2].outputs == (d,)


def test_pin_range_and_keep_true():
    b = Builder(OPTS)
    for _ in range(4):
        lit(b, T.INT32)
    b.pin(1, 3, keep=True)
    assert [i.keep for i in b.instrs] == [None, True, True, None]
    b.pin(3, None, True)
    assert b.instrs[3].keep is True and b.instrs[0].keep is None


# ------------------------------------------------------------------ lifting_choices

def _prog_with_keeps():
    b = Builder(OPTS)
    pinned_t = b.emit(LoadLiteral(T.INT32, "1"), keep=True)
    pinned_f = b.emit(LoadLiteral(T.INT32, "2"), keep=False)
    und = b.emit(LoadLiteral(T.INT32, "3"))
    bin_ = b.binary("+", und, pinned_t)
    x = b.declare(bin_)
    with b.function(0) as (f, _):
        inner = lit(b, T.INT32)
        b.ret(inner)
    b.do_call(f)
    b.call(f)
    return b.program()


@settings(FAST)
@given(lifting_choices(_prog_with_keeps()))
def test_lifting_choices_properties(out):
    src = _prog_with_keeps()
    assert len(out) == len(src)
    for a, o in zip(src, out):
        assert o.op == a.op and o.inputs == a.inputs and o.outputs == a.outputs and o.inner == a.inner
        if a.keep is not None:
            assert o.keep is a.keep
        elif a.outputs and not isinstance(a.op, (DeclareVar, BeginFunction)):
            assert isinstance(o.keep, bool)
        else:
            assert o.keep is None


def test_lifting_choices_shrinks_to_all_false():
    src = _prog_with_keeps()
    out = minimal(lifting_choices(src))
    assert [i.keep for i in out] == [True, False, False, False, None, None, False, None, None, None, False]


def test_lifting_choices_can_choose_true():
    src = _prog_with_keeps()
    out = minimal(lifting_choices(src), lambda p: p[2].keep is True)
    assert out[2].keep is True and out[3].keep is False


def test_lifting_choices_start_offset():
    src = _prog_with_keeps()
    out = minimal(lifting_choices(src, 3), lambda p: True)
    assert out[2].keep is None                        # before `start`: untouched
    assert out[3].keep is False


# ------------------------------------------------------------------ shrink friendliness

# The default options force >= 3 statements, each followed by a probe, so the floor for a whole program is ~10
# instructions; the shrink-friendliness check relaxes just that floor.
SMALL = GenOptions(min_statements=1, probes=False)


def _has_mod(p):
    return any(isinstance(i.op, Binary) and i.op.op == "%" for i in p[0])


def test_full_program_shrinks_to_small_program():
    prog, _ = find(ir_programs(SMALL), _has_mod, settings=SLOW)
    assert _has_mod((prog,))
    assert len(prog) <= 4, [(type(i.op).__name__, i.gen) for i in prog]
    verify(prog)


def test_full_program_minimum_is_small_without_predicate():
    prog, _ = minimal(ir_programs(SMALL))
    verify(prog)
    assert len(prog) <= 2


def test_default_options_minimum_is_the_statement_floor():
    prog, _ = minimal(ir_programs(OPTS))
    verify(prog)
    assert len(prog) <= 3 * OPTS.min_statements * 2 + 1      # 3 statements of <= 2 instructions + probes
