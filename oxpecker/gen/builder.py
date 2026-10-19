"""ProgramBuilder: emits JavaScript source text, tracks a typed pool of in-scope variables,
inserts state probes, and records which generator produced which lines.

Its method surface deliberately mirrors Fuzzilli's ProgramBuilder (fresh/pick/define_function/
loops/try) so Fuzzilli's code generators port close to line-for-line, but it produces readable JS
directly instead of an IR.  All randomness goes through Hypothesis `draw`, so shrinking works.

Design modelled on Fuzzilli's Sources/Fuzzilli/Base/ProgramBuilder.swift (Google LLC, Apache-2.0;
see THIRD_PARTY_NOTICES.md). No code is copied.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from hypothesis import strategies as st

from .types import PREFIX, T, compatible


@dataclass(frozen=True)
class Var:
    name: str
    type: T

    def __str__(self) -> str:
        return self.name


@dataclass
class Line:
    indent: int
    text: str
    gen: str | None
    probe: bool = False


@dataclass
class GenOptions:
    min_statements: int = 3
    max_statements: int = 12
    probes: bool = True
    probe_every: int = 1          # probe after every N top-level statements
    probe_width: int = 8          # at most this many variables per probe
    comments: bool = True         # `// [generator]` markers in the source
    strict: bool = False          # strict programs get no `with`/direct eval generators
    toggles: frozenset[str] = frozenset()   # partial-support features: let, generators, for-of, destructuring, spread
    generators: tuple[str, ...] | None = None   # allowlist of generator names
    templates: tuple[str, ...] | None = None    # allowlist of template names
    swarm: bool = True            # each program enables a random subset of generators
    max_depth: int = 3            # nesting depth for function bodies / loops
    nested_statements: tuple[int, int] = (1, 4)


@dataclass
class _Scope:
    kind: str                     # top | function | block
    vars: list[Var] = field(default_factory=list)


class ProgramBuilder:
    def __init__(self, draw: Callable[[st.SearchStrategy[Any]], Any], opts: GenOptions,
                 pool: Sequence[Any] = ()):
        self.draw = draw
        self.opts = opts
        self.pool = list(pool)            # weighted list of enabled generators (see strategies)
        self.lines: list[Line] = []
        self.indent = 0
        self.scopes: list[_Scope] = [_Scope("top")]
        self.counters: dict[str, int] = {}
        self.probe_count = 0
        self.depth = 0
        self.current_gen: str | None = None
        self.gen_usage: dict[str, int] = {}
        self.make_value: Callable[[ProgramBuilder, T], Var] | None = None   # set by the registry

    # ---------------------------------------------------------------- randomness
    def rand_int(self, lo: int, hi: int) -> int:
        return self.draw(st.integers(lo, hi))

    def rand_bool(self) -> bool:
        return self.draw(st.booleans())

    def choice(self, seq: Sequence[Any]) -> Any:
        return self.draw(st.sampled_from(list(seq)))

    def maybe(self, p: float = 0.5) -> bool:
        return self.draw(st.integers(0, 99)) < int(p * 100)

    # ---------------------------------------------------------------- emission
    def emit(self, text: str, *, probe: bool = False) -> None:
        for part in text.split("\n"):
            self.lines.append(Line(self.indent, part, self.current_gen, probe))

    def comment(self, text: str) -> None:
        if self.opts.comments:
            self.emit("// " + text)

    @contextmanager
    def block(self, header: str, footer: str = "}", kind: str = "block") -> Iterator[None]:
        """Emits `header`, indents, pushes a scope, then `footer`."""
        self.emit(header)
        self.indent += 1
        self.scopes.append(_Scope(kind))
        try:
            yield
        finally:
            self.scopes.pop()
            self.indent -= 1
            self.emit(footer)

    @contextmanager
    def capture(self) -> Iterator[list[Line]]:
        """Collects the lines emitted inside the block instead of appending them (they can be
        re-emitted with emit_lines, e.g. to generate the same body twice)."""
        saved, self.lines = self.lines, []
        out: list[Line] = []
        try:
            yield out
        finally:
            out.extend(self.lines)
            self.lines = saved

    def emit_lines(self, lines: list[Line], rename: dict[str, str] | None = None) -> None:
        for ln in lines:
            text = ln.text
            if rename:
                for old, new in rename.items():
                    text = text.replace(old, new)
            self.lines.append(Line(ln.indent + self.indent, text, ln.gen, ln.probe))

    # ---------------------------------------------------------------- variables
    def new_name(self, type: T) -> str:
        prefix = PREFIX[type]
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}{n}"

    def declare(self, type: T, name: str | None = None) -> Var:
        """Registers a variable in the current scope without emitting anything."""
        v = Var(name or self.new_name(type), type)
        self.scopes[-1].vars.append(v)
        return v

    def fresh(self, type: T, expr: str, *, kw: str = "var") -> Var:
        """Emits `var <name> = <expr>;` and registers the variable."""
        v = self.declare(type)
        self.emit(f"{kw} {v.name} = {expr};")
        return v

    def visible(self) -> list[Var]:
        out: list[Var] = []
        for s in self.scopes:
            out.extend(s.vars)
        return out

    def pick(self, type: T | None = None) -> Var | None:
        """A visible variable, preferring `type`. The index is drawn from a fixed range and
        reduced modulo the pool size, so deleting an earlier statement during shrinking does
        not invalidate later references."""
        vis = self.visible()
        cands = [v for v in vis if compatible(v.type, type)]
        if not cands:
            return None
        return cands[self.draw(st.integers(0, 255)) % len(cands)]

    def pick_or_make(self, type: T) -> Var:
        v = self.pick(type)
        if v is None or (type is not T.UNKNOWN and not self.maybe(0.85)):
            assert self.make_value is not None, "registry must install make_value"
            return self.make_value(self, type)
        return v

    def expr(self, type: T, literal: Callable[[ProgramBuilder, T], str] | None = None) -> str:
        """An expression of roughly `type`: an existing variable, or a literal."""
        v = self.pick(type)
        if v is not None and (literal is None or self.maybe(0.7)):
            return v.name
        if literal is not None:
            return literal(self, type)
        return self.pick_or_make(type).name

    # ---------------------------------------------------------------- functions & control
    @contextmanager
    def define_function(self, nparams: int | None = None, *, name: str | None = None,
                        param_types: Sequence[T] | None = None,
                        kind: str = "function") -> Iterator[tuple[Var, list[Var]]]:
        """`function f(a0, a1) { ... }` (kind=function), `var f = function(...) {...};`
        (kind=expression) or `var f = (...) => {...};` (kind=arrow)."""
        if nparams is None:
            nparams = len(param_types) if param_types else self.rand_int(0, 3)
        fvar = self.declare(T.FUNCTION, name)
        params = [Var(f"a{i}", (param_types[i] if param_types and i < len(param_types) else T.UNKNOWN))
                  for i in range(nparams)]
        plist = ", ".join(p.name for p in params)
        if kind == "function":
            header = f"function {fvar.name}({plist}) {{"
            footer = "}"
        elif kind == "arrow":
            header = f"var {fvar.name} = ({plist}) => {{"
            footer = "};"
        else:
            header = f"var {fvar.name} = function({plist}) {{"
            footer = "};"
        self.depth += 1
        try:
            with self.block(header, footer, kind="function"):
                self.scopes[-1].vars.extend(params)
                yield fvar, params
        finally:
            self.depth -= 1

    @contextmanager
    def for_loop(self, iterations: int | None = None, *, var: str | None = None) -> Iterator[Var]:
        if iterations is None:
            iterations = self.rand_int(0, 6)
        i = Var(var or self.new_name(T.INT32), T.INT32)
        self.depth += 1
        try:
            with self.block(f"for (var {i} = 0; {i} < {iterations}; {i}++) {{"):
                self.scopes[-1].vars.append(i)
                yield i
        finally:
            self.depth -= 1

    def try_block(self) -> "_TryBlock":
        return _TryBlock(self)

    # ---------------------------------------------------------------- probes & generators
    def probe(self, label: str | None = None) -> None:
        if not self.opts.probes:
            return
        if self.lines and self.lines[-1].probe and self.lines[-1].indent == self.indent:
            return
        self.probe_count += 1
        vis = self.visible()[-self.opts.probe_width:]
        args = ", ".join([f'"p{self.probe_count}"'] + [v.name for v in vis])
        self.emit(f"__probe({args});", probe=True)

    def run(self, gen: Any) -> None:
        """Runs one registered generator, picking its declared inputs."""
        saved = self.current_gen
        self.current_gen = gen.name
        self.gen_usage[gen.name] = self.gen_usage.get(gen.name, 0) + 1
        try:
            args = [self.pick_or_make(t) for t in gen.inputs]
            gen.fn(self, *args)
        finally:
            self.current_gen = saved

    def run_some(self, n: int | None = None) -> None:
        """Nested statements (function bodies, loop bodies): a few generators from the pool."""
        if self.depth > self.opts.max_depth or not self.pool:
            return
        lo, hi = self.opts.nested_statements
        if n is None:
            n = self.rand_int(lo, hi)
        # One list draw so the shrinker can delete nested statements too.
        gens = self.draw(st.lists(st.sampled_from(self.pool), min_size=0, max_size=n))
        for g in gens:
            self.run(g)

    # ---------------------------------------------------------------- output
    def render(self, *, probes: bool | None = None, comments: bool | None = None,
               header: Sequence[str] = ()) -> str:
        probes = self.opts.probes if probes is None else probes
        comments = self.opts.comments if comments is None else comments
        out: list[str] = [f"// {h}" for h in header]
        if header:
            out.append("")
        last_gen: str | None = None
        for ln in self.lines:
            if ln.probe and not probes:
                continue
            if comments and self._starts_span(ln, last_gen):
                out.append(f"// [{ln.gen}]")
            if not ln.probe:
                last_gen = ln.gen
            out.append("  " * ln.indent + ln.text)
        return "\n".join(out) + "\n"

    @staticmethod
    def _starts_span(ln: Line, last_gen: str | None) -> bool:
        """A `// [generator]` marker goes before the first top-level line of a generator's output,
        but not before a closing brace that merely returns to an enclosing generator."""
        return (ln.gen is not None and ln.gen != last_gen and ln.indent == 0 and not ln.probe
                and not ln.text.startswith("}") and not ln.text.startswith(")"))

    def generator_spans(self, *, probes: bool | None = None, comments: bool | None = None,
                        header_lines: int = 0) -> list[dict[str, Any]]:
        """[{gen, lines:[start,end]}] in 0-based line numbers of the rendered source."""
        probes = self.opts.probes if probes is None else probes
        comments = self.opts.comments if comments is None else comments
        spans: list[dict[str, Any]] = []
        lineno = header_lines + (1 if header_lines else 0)
        last_gen: str | None = None
        for ln in self.lines:
            if ln.probe and not probes:
                continue
            if comments and self._starts_span(ln, last_gen):
                lineno += 1
            if not ln.probe:
                last_gen = ln.gen
            if ln.gen is not None and not ln.probe:
                if spans and spans[-1]["gen"] == ln.gen and spans[-1]["lines"][1] >= lineno - 2:
                    spans[-1]["lines"][1] = lineno
                else:
                    spans.append({"gen": ln.gen, "lines": [lineno, lineno]})
            lineno += 1
        return spans

    def stats(self) -> dict[str, int]:
        statements = sum(1 for ln in self.lines if not ln.probe and ln.text and not ln.text.startswith("//")
                         and not ln.text.startswith("}"))
        return {
            "statements": statements,
            "topLevelStatements": sum(1 for ln in self.lines if not ln.probe and ln.indent == 0
                                      and ln.text and not ln.text.startswith("//") and not ln.text.startswith("}")),
            "vars": sum(len(s.vars) for s in self.scopes) + sum(self.counters.values()),
            "maxDepth": max((ln.indent for ln in self.lines), default=0),
            "probes": sum(1 for ln in self.lines if ln.text.lstrip().startswith("__probe(")),
            "bytes": len(self.render(header=())),
        }


class _TryBlock:
    """Two-phase try/catch/finally emitter:

        tb = b.try_block()
        with tb.body(): ...
        with tb.catch("err"): ...
        with tb.finally_(): ...
        tb.end()
    """

    def __init__(self, b: ProgramBuilder):
        self.b = b
        self.open = False

    @contextmanager
    def body(self) -> Iterator[None]:
        b = self.b
        b.emit("try {")
        b.indent += 1
        b.scopes.append(_Scope("block"))
        try:
            yield
        finally:
            b.scopes.pop()
            b.indent -= 1
        self.open = True

    @contextmanager
    def catch(self, name: str = "err") -> Iterator[Var]:
        b = self.b
        b.emit(f"}} catch ({name}) {{")
        b.indent += 1
        b.scopes.append(_Scope("block"))
        v = Var(name, T.UNKNOWN)
        b.scopes[-1].vars.append(v)
        try:
            yield v
        finally:
            b.scopes.pop()
            b.indent -= 1

    @contextmanager
    def finally_(self) -> Iterator[None]:
        b = self.b
        b.emit("} finally {")
        b.indent += 1
        b.scopes.append(_Scope("block"))
        try:
            yield
        finally:
            b.scopes.pop()
            b.indent -= 1

    def end(self) -> None:
        self.b.emit("}")
        self.open = False
