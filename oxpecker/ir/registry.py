"""Registry of IR generators, templates and value makers.

Same decorator surface and the same `Generator` record as `gen/registry.py` (so the inspector's
catalogue reads both), in separate dictionaries: a family is ported by writing its generators
against `ir.builder.Builder` and registering them here.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any

from ..gen.builder import GenOptions
from ..gen.registry import Generator
from ..gen.types import T

GENERATORS: dict[str, Generator] = {}
TEMPLATES: dict[str, Generator] = {}
VALUE_MAKERS: dict[T, Callable[[Any], int]] = {}


def generator(name: str, *, family: str, weight: int = 1, inputs: tuple[T, ...] = (),
              toggle: str | None = None, sloppy_only: bool = False, output: T | None = None):
    def deco(fn):
        if name in GENERATORS:
            raise ValueError(f"duplicate IR generator {name}")
        GENERATORS[name] = Generator(name, fn, family, weight, inputs, toggle, sloppy_only, output,
                                     description=(fn.__doc__ or "").strip())
        return fn
    return deco


def template(name: str, *, weight: int = 1, toggle: str | None = None):
    def deco(fn):
        TEMPLATES[name] = Generator(name, fn, "template", weight, (), toggle, False, None,
                                    description=(fn.__doc__ or "").strip(), kind="template")
        return fn
    return deco


def value_maker(t: T):
    """The generator `Builder.make(t)` / `pick_or_make(t)` uses when no variable fits."""
    def deco(fn):
        VALUE_MAKERS[t] = fn
        return fn
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


def available_generators(opts: GenOptions) -> list[Generator]:
    load_all()
    gens = [g for g in GENERATORS.values() if g.toggle is None or g.toggle in opts.toggles]
    if opts.strict:
        gens = [g for g in gens if not g.sloppy_only]
    if opts.generators is not None:
        gens = [g for g in gens if g.name in opts.generators]
    return sorted(gens, key=lambda g: g.name)


def available_templates(opts: GenOptions) -> list[Generator]:
    load_all()
    tpls = [t for t in TEMPLATES.values() if t.toggle is None or t.toggle in opts.toggles]
    if opts.templates is not None:
        tpls = [t for t in tpls if t.name in opts.templates]
    return sorted(tpls, key=lambda t: t.name)


def weighted(gens: list[Generator]) -> list[Generator]:
    """Each generator repeated `weight` times, in a fixed order: an index into this list means the
    same generator in every context (proposal §3.8 rule 3)."""
    return [g for g in gens for _ in range(g.weight)]


def catalogue() -> list[dict[str, Any]]:
    load_all()
    return [g.catalogue_entry() for g in GENERATORS.values()] + [t.catalogue_entry() for t in TEMPLATES.values()]
