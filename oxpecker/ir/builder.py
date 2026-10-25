"""What generators see while they run: the instructions being built, the scope and type state at
the insertion point, and draw helpers that follow the rules for shrink-friendly strategies
(docs/proposal-hypothesis-ir.md §3.8):

- `choice(pool)` is for CONSTANT pools only (literal spellings, operators): `sampled_from`.
- `pick(xs)` is for anything whose size depends on context (visible variables, candidates): an
  integer index, because a one-element `sampled_from` records no choice and misaligns the rest.
- visible variables are listed most recent first, so shrinking pulls references towards local
  values and leaves older code dead (and deletable).
- `maybe(p)` shrinks towards False: optional things disappear when shrunk.
- statement lists are `st.lists` over a per-statement strategy, never "draw a count, then loop".
- every generator invocation is its own `@st.composite` draw, so it gets its own span: the
  shrinker can delete or simplify one generator's output as a unit.

A `Builder` either starts empty (a fresh program) or `at(prog, pos)` -- the context before
`prog[pos]` -- for the code-generation mutator. Both use the same generators.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from hypothesis import strategies as st

from ..gen.builder import GenOptions
from ..gen.types import T, compatible
from .analysis import analyse, binary_type, join, unary_type, update_type
from .ops import (BeginCatch, BeginElse, BeginFinally, BeginFor, BeginFunction, BeginIf, BeginTry, Binary,
                  CallFunction, CallMethod, CompoundAssign, Conditional, Construct, CreateArray, CreateObject,
                  DeclareVar, DeleteProperty, EndFor, EndFunction, EndIf, EndTry, Eval, GetElement, GetProperty, Instr,
                  LoadBuiltin, LoadLiteral, Op, Probe, Program, Reassign, Return, Unary, Update)
from . import registry


@dataclass
class _Frame:
    kind: str                         # top | function | then | else | for | try | catch | finally
    vars: list[int] = field(default_factory=list)
    fn: int | None = None


class Builder:
    def __init__(self, opts: GenOptions, pool: Sequence[Any] = (), *, visible: Sequence[int] = (),
                 types: dict[int, T] | None = None, returns: dict[int, T] | None = None, next_id: int = 0,
                 frame: str = "top", fn: int | None = None, depth: int = 0,
                 loop_counters: Sequence[int] = (), origin: int = 0, declared: dict[int, str] | None = None):
        self.opts = opts
        self.pool = list(pool)                       # weighted, fixed-order generator list (registry)
        self.instrs: list[Instr] = []
        self.frames: list[_Frame] = [_Frame(frame, list(visible), fn)]
        self.types: dict[int, T] = dict(types or {})
        self.returns: dict[int, T] = dict(returns or {})
        self.loop_counters: set[int] = set(loop_counters)
        self.declared: dict[int, str] = dict(declared or {})   # assignable class: var/let/const/param/catch/function
        self.next_id = next_id
        self.depth = depth
        self.current_gen: str | None = None
        self.origin = origin
        self.origins = origin                        # last origin handed out
        self.gen_usage: dict[str, int] = {}
        self._draws: list[Any] = []

    @classmethod
    def at(cls, prog: Program, pos: int, opts: GenOptions, pool: Sequence[Any] = ()) -> "Builder":
        """Context for inserting before `prog[pos]`, typed by a fresh analysis."""
        an = analyse(prog)
        ids = [v for ins in prog for v in ins.outputs + ins.inner]
        types = dict(an.types) | dict(an.env_at[pos])            # declared variables: their type HERE
        return cls(opts, pool, visible=an.visible_at[pos], types=types, returns=an.returns,
                   next_id=max(ids, default=-1) + 1, frame=an.frame_at[pos], fn=an.fn_at[pos],
                   depth=an.depth_at[pos], loop_counters=an.loop_counters, declared=an.declared,
                   origin=max((ins.origin for ins in prog), default=0) + 1)

    def program(self) -> Program:
        return tuple(self.instrs)

    # ------------------------------------------------------------ drawing
    def draw(self, strategy: st.SearchStrategy) -> Any:
        return self._draws[-1](strategy)

    @contextmanager
    def drawing(self, draw) -> Iterator[None]:
        self._draws.append(draw)
        try:
            yield
        finally:
            self._draws.pop()

    def choice(self, pool: Sequence[Any]) -> Any:
        """From a CONSTANT pool (operators, literal spellings)."""
        return self.draw(st.sampled_from(tuple(pool)))

    def pick(self, xs: Sequence[Any]) -> Any:
        """From a context-dependent list: an index draw, whatever the list's size."""
        return xs[self.draw(st.integers(0, len(xs) - 1))]

    def int(self, lo: int, hi: int) -> int:
        return self.draw(st.integers(lo, hi))

    def maybe(self, p: float = 0.5) -> bool:
        """True with probability ~p; shrinks towards False."""
        return self.draw(st.integers(0, 99)) >= 100 - round(p * 100)

    # ------------------------------------------------------------ variables
    def visible(self, want: T | None = None) -> list[int]:
        """Visible variables, most recent first, whose type fits `want`."""
        vs = [v for f in reversed(self.frames) for v in reversed(f.vars)]
        return vs if want is None else [v for v in vs if compatible(self.types.get(v, T.UNKNOWN), want)]

    def assignable(self, want: T | None = None) -> list[int]:
        """Visible declared variables that may be assigned (not const, not a loop counter)."""
        return [v for v in self.visible(want) if self.declared.get(v) not in (None, "const")
                and v not in self.loop_counters]

    def type_of(self, v: int) -> T:
        return self.types.get(v, T.UNKNOWN)

    def pick_or_make(self, want: T | None) -> int:
        """A visible variable of a fitting type, or (sometimes, or when there is none) a new value."""
        make = self.maybe(0.25)
        pool = self.visible(want if want is not T.UNKNOWN else None)
        if make or not pool:
            return self.make(want or T.UNKNOWN)
        return self.pick(pool)

    def make(self, t: T) -> int:
        maker = registry.VALUE_MAKERS.get(t) or registry.VALUE_MAKERS[T.UNKNOWN]
        return maker(self)

    def target(self, want: T | None) -> int:
        """A declared variable to assign to (fitting type, not const, not a loop counter), or a
        newly declared one initialised with a made value."""
        make = self.maybe(0.25)
        pool = self.assignable(want if want is not T.UNKNOWN else None)
        if make or not pool:
            return self.declare(self.make(want or T.UNKNOWN))
        return self.pick(pool)

    def declare(self, init: int, kind: str = "var", hint: str | None = None) -> int:
        """`kind name = init;` -- a declared, assignable variable (decision D15)."""
        v = self._new()
        self.types[v] = self.type_of(init)
        self.declared[v] = kind
        self._append(DeclareVar(kind), (init,), (v,), hint=hint)
        self.frames[-1].vars.append(v)
        return v

    def const(self, t: T, text: str) -> int:
        """A literal that is part of a fixed form (the `0` in `x | 0`): always spelled inline."""
        return self.emit(LoadLiteral(t, text), keep=False)

    def literal(self, t: T, pool: Sequence[str]) -> int:
        return self.emit(LoadLiteral(t, self.choice(pool)))

    # ------------------------------------------------------------ emission
    def _new(self) -> int:
        self.next_id += 1
        return self.next_id - 1

    def _append(self, op: Op, inputs=(), outputs=(), inner=(), *, keep: bool = False, hint: str | None = None) -> None:
        self.instrs.append(Instr(op, tuple(inputs), tuple(outputs), tuple(inner), keep, hint,
                                 self.current_gen, self.origin))

    def emit(self, op: Op, inputs: Sequence[int] = (), t: T | None = None, *, keep: bool | None = None,
             hint: str | None = None) -> int | None:
        """Appends an instruction; returns its output (a temporary) if it has one.

        `t` states a more precise output type than the generic rule knows (`new Map()` is a MAP);
        it only steers generation -- analysis re-derives types on its own. `keep` (drawn when not
        given, shrinking towards False = inline) says whether the lifter must keep the value as its
        own `var` statement instead of folding it into its use (decision D16)."""
        inputs = tuple(inputs)
        out = self._result_type(op, inputs)
        if out is not None and t is not None:
            out = t
        if out is None:
            self._append(op, inputs)
            self._effects(op, inputs)
            return None
        if keep is None:
            keep = self.maybe(0.2)
        v = self._new()
        self.types[v] = out
        self._append(op, inputs, (v,), keep=keep, hint=hint)
        self.frames[-1].vars.append(v)
        self._effects(op, inputs)
        return v

    def _result_type(self, op: Op, inputs: tuple[int, ...]) -> T | None:
        ts = [self.type_of(v) for v in inputs]
        if isinstance(op, (LoadLiteral, LoadBuiltin)):
            return op.type
        if isinstance(op, CreateArray):
            return T.ARRAY
        if isinstance(op, CreateObject):
            return T.OBJECT
        if isinstance(op, Unary):
            return unary_type(op.op, ts[0])
        if isinstance(op, Binary):
            return binary_type(op.op, ts[0], ts[1])
        if isinstance(op, Conditional):
            return join(ts[1], ts[2]) or T.UNKNOWN
        if isinstance(op, (CallFunction, CallMethod, Construct)) and op.discard:
            return None
        if isinstance(op, CallFunction):
            return self.returns.get(inputs[0], T.UNKNOWN)
        if isinstance(op, (CallMethod, GetProperty)):
            return T.UNKNOWN
        if isinstance(op, Construct):
            return T.OBJECT
        if isinstance(op, Update):
            return update_type(ts[0]) if op.value else None
        if isinstance(op, Eval):
            return op.out
        if isinstance(op, GetElement):
            return T.UNKNOWN
        if isinstance(op, DeleteProperty):
            return T.BOOLEAN
        return None

    def _effects(self, op: Op, inputs: tuple[int, ...]) -> None:
        """Generation-time types: strong updates, as the analysis does on straight-line code (in a
        branch this is optimistic; it only steers which variables generators pick)."""
        if isinstance(op, Reassign):
            self.types[inputs[0]] = self.type_of(inputs[1])
        elif isinstance(op, CompoundAssign):
            self.types[inputs[0]] = binary_type(op.op, self.type_of(inputs[0]), self.type_of(inputs[1]))
        elif isinstance(op, Update):
            self.types[inputs[0]] = update_type(self.type_of(inputs[0]))

    # Shorthands generators use a lot.
    def binary(self, op: str, a: int, b: int) -> int:
        return self.emit(Binary(op), (a, b))

    def unary(self, op: str, a: int) -> int:
        return self.emit(Unary(op), (a,))

    def builtin(self, name: str, t: T = T.OBJECT) -> int:
        return self.emit(LoadBuiltin(name, t))

    def call(self, f: int, *args: int) -> int:
        return self.emit(CallFunction(), (f, *args))

    def method(self, obj: int, name: str, *args: int) -> int:
        return self.emit(CallMethod(name), (obj, *args))

    def construct(self, ctor: int, *args: int) -> int:
        return self.emit(Construct(), (ctor, *args))

    def do_call(self, f: int, *args: int) -> None:
        """`f(args);` as a statement."""
        self.emit(CallFunction(discard=True), (f, *args))

    def do_method(self, obj: int, name: str, *args: int) -> None:
        """`obj.name(args);` as a statement."""
        self.emit(CallMethod(name, discard=True), (obj, *args))

    def error_name(self, e: int) -> int:
        """`e && e.name`, short-circuiting (a thrown null/undefined has no name)."""
        return self.eval("{0} && {0}.name", e, out=T.UNKNOWN)

    def reassign(self, target: int, value: int) -> None:
        self.emit(Reassign(), (target, value))

    def eval(self, code: str, *inputs: int, out: T | None = None, pure: bool = False) -> int | None:
        return self.emit(Eval(code, out, pure), inputs)

    def ret(self, v: int | None = None) -> None:
        f = self.frames[-1].fn
        assert f is not None, "return outside a function"
        self._append(Return(), (v,) if v is not None else ())
        t = self.type_of(v) if v is not None else T.UNDEFINED
        self.returns[f] = join(self.returns.get(f), t) or T.UNKNOWN

    def probe(self, label: str = "") -> None:
        if self.opts.probes:
            self._append(Probe(label))

    # ------------------------------------------------------------ blocks
    @contextmanager
    def function(self, arity: int, *, style: str = "decl", strict: bool = False,
                 param_types: Sequence[T] = ()) -> Iterator[tuple[int, list[int]]]:
        f = self._new()
        params = [self._new() for _ in range(arity)]
        self._append(BeginFunction(arity, style, strict), (), (f,), params)
        self.types[f] = T.FUNCTION
        self.declared[f] = "function"
        self.declared.update({p: "param" for p in params})
        for k, p in enumerate(params):
            self.types[p] = param_types[k] if k < len(param_types) else T.UNKNOWN
        self.frames[-1].vars.append(f)
        self.frames.append(_Frame("function", list(params), f))
        self.depth += 1
        try:
            yield f, params
        finally:
            self.depth -= 1
            self.frames.pop()
            self._append(EndFunction())
            self.returns.setdefault(f, T.UNDEFINED)

    @contextmanager
    def if_(self, cond: int) -> Iterator["_IfBlock"]:
        self._append(BeginIf(), (cond,))
        self.frames.append(_Frame("then", fn=self.frames[-1].fn))
        self.depth += 1
        blk = _IfBlock(self)
        try:
            yield blk
        finally:
            self.depth -= 1
            self.frames.pop()
            self._append(EndIf())

    @contextmanager
    def for_(self, count: int) -> Iterator[int]:
        i = self._new()
        self._append(BeginFor(count), (), (), (i,))
        self.types[i] = T.INT32
        self.loop_counters.add(i)
        self.frames.append(_Frame("for", [i], self.frames[-1].fn))
        self.depth += 1
        try:
            yield i
        finally:
            self.depth -= 1
            self.frames.pop()
            self._append(EndFor())

    @contextmanager
    def try_(self) -> Iterator["_TryBlock"]:
        self._append(BeginTry())
        self.frames.append(_Frame("try", fn=self.frames[-1].fn))
        self.depth += 1
        blk = _TryBlock(self)
        try:
            yield blk
        finally:
            if not blk.handled:                 # a try needs a handler: default to an empty catch
                with blk.catch():
                    pass
            self.depth -= 1
            self.frames.pop()
            self._append(EndTry())

    def guarded(self, body, *, on_error=None) -> None:
        """`try { body() } catch (e) { on_error(e) }` -- keeps a program alive past a throw."""
        with self.try_() as t:
            body()
            with t.catch() as e:
                if on_error:
                    on_error(e)

    # ------------------------------------------------------------ statements
    def run(self, gen: Any) -> None:
        """Runs one registered generator, picking its declared inputs. Every invocation gets its
        own origin (provenance), nested ones included."""
        saved = self.current_gen, self.origin
        self.origins += 1
        self.current_gen, self.origin = gen.name, self.origins
        self.gen_usage[gen.name] = self.gen_usage.get(gen.name, 0) + 1
        try:
            args = [self.pick_or_make(t) for t in gen.inputs]
            gen.fn(self, *args)
        finally:
            self.current_gen, self.origin = saved

    def repeat(self, fn, lo: int, hi: int) -> list[Any]:
        """Calls `fn()` lo..hi times as ONE `st.lists` draw, each call its own span, so the shrinker
        can delete any single repetition (rule 1). Use instead of `for _ in range(b.int(lo, hi))`."""
        return self.draw(st.lists(_call(self, fn), min_size=lo, max_size=hi))

    def replay(self, instrs: Sequence[Instr], rename: dict[int, int]) -> dict[int, int]:
        """Re-emits straight-line instructions (no blocks) with fresh ids for what they define and
        `rename` applied to what they use -- "the same body twice". Returns the full renaming."""
        rn = dict(rename)
        for ins in instrs:
            assert not ins.inner and not isinstance(ins.op, (BeginFunction, BeginIf, BeginFor, BeginTry)), ins
            inputs = tuple(rn.get(v, v) for v in ins.inputs)
            if isinstance(ins.op, DeclareVar):
                out = self.declare(inputs[0], ins.op.kind, ins.hint)
            else:
                out = self.emit(ins.op, inputs, t=self.types.get(ins.outputs[0]) if ins.outputs else None,
                                keep=ins.keep, hint=ins.hint)
            if ins.outputs:
                rn[ins.outputs[0]] = out
        return rn

    def statement_list(self, lo: int, hi: int, *, probe: bool = False) -> None:
        """`lo..hi` generator invocations here, as one `st.lists` draw (rule 1)."""
        if not self.pool:
            return
        self.draw(st.lists(_statement(self, probe), min_size=lo, max_size=hi))

    def run_some(self, n: int | None = None) -> None:
        """Nested statements (function and loop bodies)."""
        if self.depth > self.opts.max_depth:
            return
        lo, hi = self.opts.nested_statements
        self.statement_list(0, hi if n is None else n)


class _IfBlock:
    def __init__(self, b: Builder):
        self.b = b

    @contextmanager
    def else_(self) -> Iterator[None]:
        b = self.b
        b.frames.pop()
        b._append(BeginElse())
        b.frames.append(_Frame("else", fn=b.frames[-1].fn))
        yield


class _TryBlock:
    def __init__(self, b: Builder):
        self.b = b
        self.handled = False

    @contextmanager
    def catch(self) -> Iterator[int]:
        b = self.b
        b.frames.pop()
        e = b._new()
        b._append(BeginCatch(), (), (), (e,))
        b.types[e] = T.UNKNOWN
        b.declared[e] = "catch"
        b.frames.append(_Frame("catch", [e], b.frames[-1].fn))
        self.handled = True
        yield e

    @contextmanager
    def finally_(self) -> Iterator[None]:
        b = self.b
        b.frames.pop()
        b._append(BeginFinally())
        b.frames.append(_Frame("finally", fn=b.frames[-1].fn))
        self.handled = True
        yield


@st.composite
def _call(draw, b: Builder, fn):
    with b.drawing(draw):
        return fn()


@st.composite
def _statement(draw, b: Builder, probe: bool):
    """One generator invocation (its own span), from the FIXED weighted pool (rule 3)."""
    with b.drawing(draw):
        gen = b.choice(b.pool)
        b.run(gen)
        if probe:
            b.probe()
