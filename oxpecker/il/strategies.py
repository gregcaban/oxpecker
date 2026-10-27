"""Generators, value makers, templates and programs as Hypothesis strategies.

The rules of docs/proposal-hypothesis-ir.md §3.8 live here, not in generator code: `st.lists` for
every repetition, `pick` for every context-dependent pool, fixed pools (one for all statement
contexts, one for object-literal members, one for switch cases) with weights as repetition, a
context-independent fallback, visible variables most recent first, `maybe()` shrinking to False,
lifting choices drawn after generation. The builder never draws; every choice is a `draw` here
or in a generator."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Callable, Sequence

from hypothesis import strategies as st

from . import ops as O
from .analysis import verify
from .builder import Builder, BuilderError
from .lift import LiftOptions, lift
from .ops import Context, Lift
from .options import TOGGLE_LET, GenOptions
from .program import Instruction, Program, Var
from .types import ANY, BIGINT, NUMBER, SYMBOL, Type

# ------------------------------------------------------------------------------- primitives
def pick(draw, xs: Sequence):
    """`xs[i]` for a drawn index. For any pool whose size depends on the context (visible
    variables, candidates): a one-element `sampled_from` records no choice and would shift every
    later choice when the pool shrinks to one element."""
    if not xs:
        raise ValueError("pick from an empty pool")
    return xs[draw(st.integers(0, len(xs) - 1))]


_MAYBE_CACHE: dict[int, st.SearchStrategy[bool]] = {}


def maybe(p: float = 0.5) -> st.SearchStrategy[bool]:
    """A boolean that shrinks to False; `p` is the sampling weight (True with probability p).
    Implemented as one index draw into a fixed list (no `map`: a mapped strategy needs a build
    context, which a bare `draw(strategy, seed)` from the shell does not have)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be strictly between 0 and 1")
    w = max(1, min(99, int(round(p * 100))))
    if w not in _MAYBE_CACHE:
        _MAYBE_CACHE[w] = st.sampled_from(tuple([False] * (100 - w) + [True] * w))
    return _MAYBE_CACHE[w]


# ------------------------------------------------------------------------------- registry
FAMILY_ORDER: tuple[str, ...] = ("values", "containers", "operations", "properties", "calls", "control",
                                 "compiler-targeted", "java-interop", "continuations", "exotic", "api",
                                 "objects", "members", "cases", "toggled")


@dataclass(frozen=True)
class GeneratorSpec:
    name: str
    family: str
    weight: int
    rank: int                        # order within the family: lower is simpler (shrinking moves towards it)
    context: Context
    inputs: tuple[Type, ...]
    required: tuple[Type, ...]
    produces: Type | None
    toggle: str | None
    sloppy_only: bool
    source: str
    strategy: Callable
    doc: str
    scaffolds: bool = False          # can open its own context when drawn outside it (design §7.2)
    is_block: bool = False           # opens a block: needs nesting budget

    def applicable(self, b: Builder) -> bool:
        if self.toggle is not None and self.toggle not in b.options.toggles:
            return False
        if self.sloppy_only and b.strict:
            return False
        if self.is_block and b.depth <= 0:
            return False
        if self.context & ~b.context and not self.scaffolds:
            return False
        for t in self.required:
            if not b.pool(t).exact:
                return False
        return True


REGISTRY: dict[str, GeneratorSpec] = {}
VALUE_MAKERS: dict[Type, GeneratorSpec] = {}
TEMPLATES: dict[str, "TemplateSpec"] = {}


def registered() -> None:
    """Populate the registries on first use. Generators and templates register themselves when their
    modules are imported, and only `oxpecker.il.templates` imports them all; a shell that imports
    this module alone would otherwise find both registries empty. The import is deferred to call
    time because `templates` imports this module (the cycle is harmless once this module is loaded)."""
    if not TEMPLATES:
        import oxpecker.il.templates  # noqa: F401  (registration)


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    weight: int
    strategy: Callable
    doc: str


def generator(name: str, *, family: str, weight: int = 1, rank: int = 50, context: Context = Context.JAVASCRIPT,
              inputs: Sequence[Type] = (), required: Sequence[Type] = (), produces: Type | None = None,
              toggle: str | None = None, sloppy_only: bool = False, source: str = "",
              scaffolds: bool = False, block: bool = False):
    """Register a generator strategy. The decorated object is an `st.composite` function
    `(b, *inputs) -> strategy`; the wrapper stamps provenance on what it emits."""
    if family not in FAMILY_ORDER:
        raise ValueError(f"unknown family {family!r}")
    if name in REGISTRY:
        raise ValueError(f"duplicate generator {name!r}")

    def decorate(strategy):
        doc = inspect.getdoc(getattr(strategy, "__wrapped__", strategy)) or ""

        @st.composite
        def attributed(draw, b: Builder, *args):
            """A generator is an `st.composite` when it makes choices and a plain function
            `(b, *inputs)` when it makes none; both are invoked the same way."""
            saved = (b.current_gen, b.origin, b.default_kind)
            b.current_gen = name
            b.origin = b.origin + 1
            b.default_kind = draw(declaration_kinds(b.options))
            try:
                result = strategy(b, *args)
                return draw(result) if isinstance(result, st.SearchStrategy) else result
            finally:
                b.current_gen, b.origin, b.default_kind = saved[0], b.origin, saved[2]

        attributed.__name__ = name
        spec = GeneratorSpec(name, family, weight, rank, context, tuple(inputs), tuple(required), produces, toggle,
                             sloppy_only, source, attributed, doc, scaffolds, block)
        REGISTRY[name] = spec
        attributed.spec = spec
        attributed.inner = strategy               # the generator function itself (for source listings)
        return attributed

    return decorate


def value_maker(t: Type):
    """Mark a registered generator as the fallback for `variable(t)` on an empty pool."""
    def decorate(strategy):
        VALUE_MAKERS[t] = strategy.spec
        return strategy
    return decorate


def template(name: str, *, weight: int = 1):
    def decorate(strategy):
        TEMPLATES[name] = TemplateSpec(name, weight, strategy, inspect.getdoc(strategy) or "")
        return strategy
    return decorate


DECLARATION_KINDS: tuple[O.DeclarationKind, ...] = (O.DeclarationKind.VAR, O.DeclarationKind.VAR,
                                                     O.DeclarationKind.LET, O.DeclarationKind.CONST)
MUTABLE_KINDS: tuple[O.DeclarationKind, ...] = (O.DeclarationKind.VAR, O.DeclarationKind.LET)


def declaration_kinds(options: GenOptions, *, mutable: bool = False) -> st.SearchStrategy[O.DeclarationKind]:
    """The kind of a declaration site (G25): var twice, let, const; only var without the `let`
    toggle; `mutable` leaves const out for a variable its generator will reassign."""
    if TOGGLE_LET not in options.toggles:
        return st.just(O.DeclarationKind.VAR)
    return st.sampled_from(MUTABLE_KINDS if mutable else DECLARATION_KINDS)


# ------------------------------------------------------------------------------- pools
@dataclass(frozen=True)
class Pools:
    statements: tuple[GeneratorSpec, ...]
    members: tuple[GeneratorSpec, ...]
    cases: tuple[GeneratorSpec, ...]
    values: tuple[GeneratorSpec, ...]

    def for_context(self, ctx: Context) -> tuple[GeneratorSpec, ...]:
        if Context.OBJECT_LITERAL in ctx and Context.JAVASCRIPT not in ctx:
            return self.members
        if Context.SWITCH_BLOCK in ctx and Context.JAVASCRIPT not in ctx:
            return self.cases
        return self.statements


_POOL_CACHE: dict[tuple, Pools] = {}


def pools(options: GenOptions) -> Pools:
    """Fixed, ordered lists: by family order then name, each entry repeated by its weight times
    the option multipliers. Filtering removes entries and never reorders the rest."""
    registered()
    key = (options.families, options.generators, tuple(sorted(options.weights.items())), tuple(REGISTRY))
    if key in _POOL_CACHE:
        return _POOL_CACHE[key]
    specs = sorted(REGISTRY.values(), key=lambda s: (FAMILY_ORDER.index(s.family), s.rank, s.name))
    if options.families is not None:
        specs = [s for s in specs if s.family in options.families]
    if options.generators is not None:
        specs = [s for s in specs if s.name in options.generators]
    statements: list[GeneratorSpec] = []
    members: list[GeneratorSpec] = []
    cases: list[GeneratorSpec] = []
    values: list[GeneratorSpec] = []
    for s in specs:
        n = round(s.weight * options.weights.get(s.name, options.weights.get(s.family, 1.0)))
        if n <= 0:
            continue
        if s.family == "members":
            members.extend([s] * n)
        elif s.family == "cases":
            cases.extend([s] * n)
        else:
            statements.extend([s] * n)
        if s in VALUE_MAKERS.values() and s not in values:
            values.append(s)
    p = Pools(tuple(statements), tuple(members), tuple(cases), tuple(values))
    _POOL_CACHE[key] = p
    return p


def fallback(pool: Sequence[GeneratorSpec], b: Builder) -> GeneratorSpec | None:
    for s in pool:
        if s.applicable(b):
            return s
    return None


# ------------------------------------------------------------------------------- variables
@st.composite
def variable(draw, b: Builder, t: Type = ANY) -> Var:
    """A visible variable for use as `t`: an exact match with a drawn `maybe(0.8)`, else one that
    may be of the type, else any; on an empty pool the value maker for `t` is drawn (design §7.1).
    The boolean is drawn whether or not it is needed, so the choice keeps its meaning."""
    want_exact = draw(maybe(0.8))
    p = b.pool(t)
    if p.exact and want_exact:
        return pick(draw, p.exact)
    if p.exact:
        return pick(draw, p.exact + p.maybe)
    if p.all and t == ANY:
        return pick(draw, p.all)
    # no variable is known to have the type: a may-be one (an unknown, often a parameter) or a
    # fresh value of the type, half and half, so loose inputs stay but do not dominate the
    # completion rate (an `undefined` callee, a Symbol in arithmetic)
    maker = _maker_for(t)
    if p.maybe and (maker is None or not maker.applicable(b) or draw(maybe(0.5))):
        return pick(draw, p.maybe)
    if maker is not None and maker.applicable(b):
        v = draw(maker.strategy(b))
        if v is not None:
            return v
    if p.maybe:
        return pick(draw, p.maybe)
    if p.all:
        return pick(draw, p.all)
    return draw(VALUE_MAKERS[ANY].strategy(b)) if ANY in VALUE_MAKERS else b.load_int(0)


def _maker_for(t: Type) -> GeneratorSpec | None:
    if t in VALUE_MAKERS:
        return VALUE_MAKERS[t]
    for mt, spec in VALUE_MAKERS.items():
        if mt.Is(t):
            return spec
    return None


def poisons_arithmetic(b: Builder, v: Var) -> bool:
    """A Symbol throws in every arithmetic or relational operation and a BigInt throws when mixed
    with a Number: the values the completion test showed behind most TypeErrors of the
    operations family (G46)."""
    t = b.type_of(v)
    return t.Is(SYMBOL) or t.Is(BIGINT)


@st.composite
def operand(draw, b: Builder, like: Var | None = None) -> Var:
    """A value for arithmetic: with p=0.85 one that is neither a Symbol nor a BigInt (unless
    `like` is a BigInt, then another BigInt, which is the one legal mix); otherwise any value,
    so type confusion stays in the mix at a rate the completion rate can afford (G46)."""
    if like is not None and b.type_of(like).Is(BIGINT):
        return draw(variable(b, BIGINT))
    if not draw(maybe(0.85)):
        return draw(variable(b))
    clean = [v for v in b.pool(ANY).all if not poisons_arithmetic(b, v)]
    if clean:
        return pick(draw, clean)
    return draw(variable(b, NUMBER))


@st.composite
def assignable(draw, b: Builder, t: Type = ANY) -> Var:
    """A declared, assignable variable preferably of type `t`; declares one from a temporary if none exists."""
    p = b.pool(t, assignable=True)
    if p.exact:
        return pick(draw, p.exact)
    if p.all:
        return pick(draw, p.all)
    return b.declare(draw(variable(b, t)), draw(declaration_kinds(b.options, mutable=True)))


@st.composite
def arguments_for(draw, b: Builder, f: Var) -> list[Var]:
    """Argument variables for calling `f`: one per declared parameter, drawn for its type, plus
    0..3 extra for a rest parameter or an unknown signature (ProgramBuilder.swift:1260-1302)."""
    sig = b.type_of(f).signature
    args: list[Var] = []
    if sig is None:
        for _ in range(draw(st.integers(0, 3))):
            args.append(draw(variable(b)))
        return args
    for p in sig.params:
        if p.mode is p.mode.__class__.REST:
            for _ in range(draw(st.integers(0, 3))):
                args.append(draw(variable(b, p.type)))
        elif p.mode is p.mode.__class__.OPT and not draw(maybe(0.5)):
            break
        else:
            args.append(draw(variable(b, p.type)))
    return args


# ------------------------------------------------------------------------------- code
@st.composite
def one_statement(draw, b: Builder, pool: Sequence[GeneratorSpec]) -> None:
    """One generator invocation: index into the fixed pool; an inapplicable pick falls back to
    the first applicable entry; loose inputs are drawn for their types."""
    spec = pool[draw(st.integers(0, len(pool) - 1))]
    if not spec.applicable(b):
        spec = fallback(pool, b)
        if spec is None:
            return
    inputs = [draw(variable(b, t)) for t in spec.inputs]
    for t in spec.required:
        inputs.append(pick(draw, b.pool(t).exact))
    draw(spec.strategy(b, *inputs))


@st.composite
def statements(draw, b: Builder, lo: int | None = None, hi: int | None = None) -> int:
    """`st.lists` over generator invocations for the context now open: one span per statement."""
    pool = pools(b.options).for_context(b.context)
    if not pool:
        return 0
    lo = b.options.min_statements if lo is None else lo
    hi = b.options.max_statements if hi is None else hi
    n = 0
    for _ in draw(st.lists(one_statement(b, pool), min_size=lo, max_size=hi)):
        n += 1
    return n


@st.composite
def nested(draw, b: Builder, n: int = 2, lo: int = 0) -> int:
    """Statements inside a block a generator opened; spends one level of the nesting budget."""
    if b.depth <= 0:
        return 0
    b.depth -= 1
    try:
        return draw(statements(b, lo, n))
    finally:
        b.depth += 1


@st.composite
def prefix(draw, b: Builder, lo: int = 2, hi: int = 5) -> list[Var]:
    """The program prefix: an `st.lists` of value-maker draws (Fuzzilli's buildPrefix), so
    generators that follow have visible variables."""
    pool = pools(b.options).values

    @st.composite
    def one(draw2):
        spec = pool[draw2(st.integers(0, len(pool) - 1))]
        if not spec.applicable(b):
            spec = fallback(pool, b)
        return draw2(spec.strategy(b)) if spec is not None else None

    if not pool:
        return []
    return [v for v in draw(st.lists(one(), min_size=lo, max_size=hi)) if v is not None]


@st.composite
def lifting_choices(draw, code: Sequence[Instruction]) -> tuple[Instruction, ...]:
    """Draw the `lift` of every value-producing instruction that has none: from the fixed list
    (INLINE, VAR, LET, CONST), shrinking to INLINE (design §3.6)."""
    out = []
    for instr in code:
        if instr.lift is None and instr.op.outputs() == 1 and not instr.op.is_block:
            out.append(instr.with_(lift=draw(st.sampled_from(LIFT_CHOICES))))
        else:
            out.append(instr)
    return tuple(out)


LIFT_CHOICES: tuple[Lift, ...] = (Lift.INLINE, Lift.INLINE, Lift.INLINE, Lift.VAR, Lift.LET, Lift.CONST)


@dataclass
class Generated:
    """A generated program and its sources."""
    program: Program
    template: str

    @property
    def code(self) -> tuple[Instruction, ...]:
        return self.program.code

    @property
    def source(self) -> str:
        return lift(self.code, LiftOptions(probes=True, comments=True, strict=self.program.options.strict,
                                           probe_width=self.program.options.probe_width))

    @property
    def plain_source(self) -> str:
        return lift(self.code, LiftOptions(probes=False, comments=False, strict=self.program.options.strict))

    def __str__(self) -> str:
        return self.source

    def __repr__(self) -> str:
        return "<Generated " + self.template + ": " + str(len(self.code)) + " instructions>"

    def _repr_pretty_(self, p, cycle) -> None:    # Hypothesis prints dataclasses field by field unless told otherwise
        p.text(repr(self))


@st.composite
def programs(draw_, options: GenOptions = GenOptions(), template: str | None = None) -> Generated:
    """A whole program: template (drawn by weight unless `template` names one), builder, verify,
    lifting choices. A verification failure is a generator bug and raises; nothing is filtered."""
    registered()
    if template is not None:
        tname = template
    else:
        names = sorted(TEMPLATES)
        weighted = [n for n in names for _ in range(TEMPLATES[n].weight)]
        tname = weighted[draw_(st.integers(0, len(weighted) - 1))]
    b = Builder(options)
    draw_(TEMPLATES[tname].strategy(b))
    code = b.finish()
    code = draw_(lifting_choices(code))
    verify(code, strict=options.strict)
    return Generated(Program(code, options, tname), tname)


# ------------------------------------------------------------------------------- from a shell
class SeededProvider:
    """Hypothesis's own provider minus its "local constants": the stock provider sometimes answers
    an integer, float or string draw with a constant scraped from the source of every loaded
    module (p=0.05), so `generate(seed)` would depend on which modules a process has imported
    (importing the loop module changed seed 11's program by one integer, 150 for 200). Seeded
    draws here come from the seed alone."""

    def __new__(cls, *args, **kwargs):
        from hypothesis.internal.conjecture.providers import HypothesisProvider

        class _Seeded(HypothesisProvider):
            def _maybe_draw_constant(self, choice_type, constraints, *, p=0.05):
                return None

        return _Seeded(*args, **kwargs)


def seeded_data(seed: int | None = None, *, rng=None, prefix=None):
    """A `ConjectureData` whose draws come from `seed` (or `rng`) after replaying `prefix`."""
    from random import Random

    from hypothesis.internal.conjecture.data import ConjectureData

    return ConjectureData(random=rng if rng is not None else Random(seed), prefix=prefix, provider=SeededProvider)


def draw(strategy: st.SearchStrategy, seed: int = 0):
    """Run a strategy exactly once, deterministically from `seed` (and from the seed alone, see
    `SeededProvider`). This is how to run a builder-mutating generator by hand:
    `strategy.example()` runs a strategy several times."""
    from hypothesis.control import BuildContext

    data = seeded_data(seed)
    with BuildContext(data, wrapped_test=lambda: None):
        return data.draw(strategy)


def generate(seed: int = 0, *, template: str | None = None, **options) -> Generated:
    """One generated program from a seed: `.code`, `.source`, `.plain_source`, `.program`."""
    return draw(programs(GenOptions(**options), template), seed)


@dataclass
class Snippet:
    """A generator run alone: the prefix it was given and what it emitted."""
    name: str
    code: tuple[Instruction, ...]
    start: int                    # index of the generator's first instruction

    @property
    def emitted(self) -> tuple[Instruction, ...]:
        return self.code[self.start:]

    @property
    def js(self) -> str:
        return lift(self.code, LiftOptions(probes=False, comments=True))

    def __str__(self) -> str:
        return self.js

    def __repr__(self) -> str:
        return "<Snippet " + self.name + ": " + str(len(self.emitted)) + " instructions>"


def run_generator(name: str, seed: int = 0, *, prefix_size: int = 3, **options) -> Snippet:
    """Run one registered generator after a small prefix of values, deterministically from a seed."""
    registered()
    spec = REGISTRY[name]

    @st.composite
    def alone(d):
        b = Builder(GenOptions(**options))
        d(prefix(b, prefix_size, prefix_size))
        if not spec.applicable(b):
            raise RuntimeError(f"{name} is not applicable at top level (context {spec.context})")
        inputs = [d(variable(b, t)) for t in spec.inputs]
        for t in spec.required:
            pool = b.pool(t).exact
            inputs.append(pool[d(st.integers(0, len(pool) - 1))])
        start = len(b.code)
        d(spec.strategy(b, *inputs))
        code = d(lifting_choices(b.finish()))
        verify(code)
        return Snippet(name, code, start)

    return draw(alone(), seed)


def generators(family: str | None = None) -> list[str]:
    """Registered generator names, optionally one family."""
    registered()
    return sorted(n for n, s in REGISTRY.items() if family is None or s.family == family)


def templates() -> list[str]:
    registered()
    return sorted(TEMPLATES)
