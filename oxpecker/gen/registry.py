"""Registry of code generators and templates, and the catalogue the inspector UI reads."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .builder import GenOptions, ProgramBuilder
from .types import T

GenFn = Callable[..., None]


@dataclass
class Generator:
    name: str
    fn: GenFn
    family: str
    weight: int = 1
    inputs: tuple[T, ...] = ()
    toggle: str | None = None       # partial-support feature this needs (let, generators, for-of, ...)
    sloppy_only: bool = False       # `with`, direct eval
    output: T | None = None
    description: str = ""
    kind: str = "generator"

    def catalogue_entry(self) -> dict[str, Any]:
        try:
            src = inspect.getsource(self.fn)
            path = str(Path(inspect.getsourcefile(self.fn) or "").relative_to(Path.cwd())) \
                if inspect.getsourcefile(self.fn) and Path(inspect.getsourcefile(self.fn) or "").is_relative_to(Path.cwd()) \
                else inspect.getsourcefile(self.fn)
        except (OSError, TypeError):
            src, path = "", None
        return {
            "id": self.name,
            "kind": self.kind,
            "family": self.family,
            "weight": self.weight,
            "toggle": self.toggle,
            "sloppyOnly": self.sloppy_only,
            "inputTypes": [t.value for t in self.inputs],
            "outputType": self.output.value if self.output else "unknown",
            "description": self.description,
            "sourcePath": path,
            "sourceSnippet": src,
        }


GENERATORS: dict[str, Generator] = {}
TEMPLATES: dict[str, Generator] = {}
# Per-type literal/value makers: T -> fn(b) -> Var
VALUE_MAKERS: dict[T, Callable[[ProgramBuilder], Any]] = {}


def generator(name: str, *, family: str, weight: int = 1, inputs: tuple[T, ...] = (),
              toggle: str | None = None, sloppy_only: bool = False, output: T | None = None):
    def deco(fn: GenFn) -> GenFn:
        if name in GENERATORS:
            raise ValueError(f"duplicate generator {name}")
        GENERATORS[name] = Generator(name, fn, family, weight, inputs, toggle, sloppy_only, output,
                                     description=(fn.__doc__ or "").strip())
        return fn
    return deco


def template(name: str, *, weight: int = 1, toggle: str | None = None):
    def deco(fn: GenFn) -> GenFn:
        TEMPLATES[name] = Generator(name, fn, "template", weight, (), toggle, False, None,
                                    description=(fn.__doc__ or "").strip(), kind="template")
        return fn
    return deco


def value_maker(type: T):
    """Registers the generator used by `b.pick_or_make(type)` when no variable fits."""
    def deco(fn: Callable[[ProgramBuilder], Any]):
        VALUE_MAKERS[type] = fn
        return fn
    return deco


def make_value(b: ProgramBuilder, type: T):
    fn = VALUE_MAKERS.get(type) or VALUE_MAKERS[T.NUMBER]
    saved = b.current_gen
    try:
        return fn(b)
    finally:
        b.current_gen = saved


def available_generators(opts: GenOptions) -> list[Generator]:
    out = []
    for g in GENERATORS.values():
        if g.toggle is not None and g.toggle not in opts.toggles:
            continue
        if g.sloppy_only and opts.strict:
            continue
        if opts.generators is not None and g.name not in opts.generators:
            continue
        out.append(g)
    return out


def available_templates(opts: GenOptions) -> list[Generator]:
    out = []
    for t in TEMPLATES.values():
        if t.toggle is not None and t.toggle not in opts.toggles:
            continue
        if opts.templates is not None and t.name not in opts.templates:
            continue
        out.append(t)
    return out


def weighted(gens: list[Generator]) -> list[Generator]:
    """Expands by weight so `sampled_from` draws proportionally."""
    out: list[Generator] = []
    for g in gens:
        out.extend([g] * max(1, g.weight))
    return out


def catalogue() -> list[dict[str, Any]]:
    load_all()
    return [g.catalogue_entry() for g in GENERATORS.values()] + \
           [t.catalogue_entry() for t in TEMPLATES.values()]


_loaded = False


def load_all() -> None:
    """Imports every generator module so the registry is populated."""
    global _loaded
    if _loaded:
        return
    from .generators import (  # noqa: F401
        values, containers, operations, properties, calls, control, compiler, exotic, toggled,
    )
    from . import templates  # noqa: F401
    _loaded = True
