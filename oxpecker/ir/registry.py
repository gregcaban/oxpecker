"""Registry of IR generators, templates and value makers.

A generator is a Hypothesis strategy -- an `@st.composite` function `(draw, b)` over a `Builder`
-- plus a name and metadata (family, weight, toggle, strict compatibility). The name and metadata
are what the pool, swarm selection, `--generator` filters, provenance comments and the
inspector's catalogue need; the strategy is what Hypothesis draws. `@generator(...)` only
registers: it records the metadata and wraps the strategy in one more `st.composite` that
attributes what it emits to the generator's name.

    @generator("binary_bitwise", family="operations", weight=3, output=T.INT32)
    @st.composite
    def binary_bitwise(draw, b):
        ...

Same `Generator` record as `gen/registry.py` (so the inspector's catalogue reads both), in
separate dictionaries.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import sys
from pathlib import Path

from hypothesis import strategies as st
from collections.abc import Callable
from typing import Any

from ..gen.builder import GenOptions
from ..gen.registry import Generator
from ..gen.types import T

GENERATORS: dict[str, Generator] = {}
TEMPLATES: dict[str, Generator] = {}
VALUE_MAKERS: dict[T, Callable[[Any], int]] = {}


def _attributed(name: str, strategy_fn):
    """`strategy_fn(b)` with everything it emits attributed to `name` (provenance)."""
    @st.composite
    def run(draw, b):
        with b.provenance(name):
            return draw(strategy_fn(b))
    run.__wrapped__ = strategy_fn         # inspect.getsource (the catalogue) follows __wrapped__
    run.__doc__ = strategy_fn.__doc__
    return run


def _doc(fn) -> str:
    return (getattr(fn, "__doc__", None) or "").strip()


def generator(name: str, *, family: str, weight: int = 1, toggle: str | None = None,
              sloppy_only: bool = False, output: T | None = None):
    """Registers a strategy `(b) -> SearchStrategy` as a named generator; returns it unchanged."""
    def deco(strategy_fn):
        if name in GENERATORS:
            raise ValueError(f"duplicate IR generator {name}")
        GENERATORS[name] = Generator(name, _attributed(name, strategy_fn), family, weight, (), toggle,
                                     sloppy_only, output, description=_doc(strategy_fn))
        _SOURCES[name] = strategy_fn
        return strategy_fn
    return deco


def template(name: str, *, weight: int = 1, toggle: str | None = None):
    def deco(strategy_fn):
        TEMPLATES[name] = Generator(name, _attributed(name, strategy_fn), "template", weight, (), toggle, False,
                                    None, description=_doc(strategy_fn), kind="template")
        _SOURCES[name] = strategy_fn
        return strategy_fn
    return deco


def value_maker(t: T):
    """Registers the strategy `make(b, t)` / `pick_or_make(b, t)` draws when no variable fits. Made
    values are attributed to the generator that asked for them, as today."""
    def deco(strategy_fn):
        VALUE_MAKERS[t] = strategy_fn
        return strategy_fn
    return deco


_loaded = False


def load_all() -> None:
    global _loaded
    if _loaded:
        return
    from . import generators, templates  # noqa: F401  (templates register on import)
    for m in pkgutil.iter_modules(generators.__path__):
        importlib.import_module(f"{generators.__name__}.{m.name}")
    _loaded = True


# Simplest first (proposal §3.8 rule 4): shrinking lowers indices, so index 0 of the pool and of the
# template list must be the simplest thing -- not whatever sorts first by name.
FAMILY_ORDER = ["values", "operations", "properties", "containers", "calls", "control", "compiler-targeted"]
SIMPLEST_TEMPLATE = "plain"


def _simplicity(g: Generator) -> tuple:
    rank = FAMILY_ORDER.index(g.family) if g.family in FAMILY_ORDER else len(FAMILY_ORDER)
    return rank, g.name


def available_generators(opts: GenOptions) -> list[Generator]:
    load_all()
    gens = [g for g in GENERATORS.values() if g.toggle is None or g.toggle in opts.toggles]
    if opts.strict:
        gens = [g for g in gens if not g.sloppy_only]
    if opts.generators is not None:
        gens = [g for g in gens if g.name in opts.generators]
    return sorted(gens, key=_simplicity)


def available_templates(opts: GenOptions) -> list[Generator]:
    load_all()
    tpls = [t for t in TEMPLATES.values() if t.toggle is None or t.toggle in opts.toggles]
    if opts.templates is not None:
        tpls = [t for t in tpls if t.name in opts.templates]
    return sorted(tpls, key=lambda t: (t.name != SIMPLEST_TEMPLATE, t.name))


def weighted(gens: list[Generator]) -> list[Generator]:
    """Each generator repeated `weight` times, in a fixed order: an index into this list means the
    same generator in every context (proposal §3.8 rule 3)."""
    return [g for g in gens for _ in range(g.weight)]


_SOURCES: dict[str, Any] = {}          # name -> the registered (undecorated-by-us) strategy function


def _source(fn) -> tuple[str, str | None]:
    """The source of a registered strategy function, decorators included. `inspect.getsource`
    cannot see through `st.composite` (its `__wrapped__` is a generated proxy), so find the `def`
    by name in its module."""
    module = sys.modules.get(getattr(fn, "__module__", ""), None)
    name = getattr(fn, "__name__", None)
    if module is None or name is None:
        return "", None
    try:
        lines = inspect.getsource(module).splitlines()
        path = inspect.getsourcefile(module)
    except (OSError, TypeError):
        return "", None
    starts = [i for i, ln in enumerate(lines) if re.match(rf"(async\s+)?def {re.escape(name)}\(", ln)]
    if not starts:
        return "", path
    i = starts[0]
    while i > 0 and lines[i - 1].startswith("@"):
        i -= 1
    j = starts[0] + 1
    while j < len(lines) and (not lines[j] or lines[j][0] in " \t)"):
        j += 1
    rel = Path(path).resolve()
    rel = str(rel.relative_to(Path.cwd())) if rel.is_relative_to(Path.cwd()) else str(rel)
    return "\n".join(lines[i:j]).rstrip() + "\n", rel


def catalogue() -> list[dict[str, Any]]:
    load_all()
    out = []
    for g in list(GENERATORS.values()) + list(TEMPLATES.values()):
        entry = g.catalogue_entry()
        entry["sourceSnippet"], entry["sourcePath"] = _source(_SOURCES[g.name])
        out.append(entry)
    return out
