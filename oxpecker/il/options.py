"""Generation options: what a program draw is parameterised by. Kept in its own module because
`program.py` stores them on every program and `strategies.py` reads them."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

ALL_TOGGLES = frozenset({"let", "generators", "for-of", "destructuring", "spread", "continuations", "with", "eval"})
TOGGLE_LET = "let"     # named here so the mutators can test for it without spelling a keyword


@dataclass(frozen=True)
class GenOptions:
    min_statements: int = 3
    max_statements: int = 12
    max_depth: int = 3
    probe_every: int = 1            # a Probe after every n top-level statements; 0 = none
    probe_width: int = 6            # variables observed per probe
    strict: bool = False
    language_version: int = 200
    toggles: frozenset[str] = ALL_TOGGLES
    families: frozenset[str] | None = None      # restrict the pools to these families
    generators: frozenset[str] | None = None    # restrict the pools to these generators
    weights: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))   # name or family -> multiplier

    def __post_init__(self) -> None:
        for name in ("toggles", "families", "generators"):      # any iterable from a shell or the CLI
            value = getattr(self, name)
            if value is not None and not isinstance(value, frozenset):
                object.__setattr__(self, name, frozenset(value))
        if self.min_statements < 0 or self.max_statements < self.min_statements:
            raise ValueError("statement bounds")
        if self.max_depth < 0:
            raise ValueError("max_depth")
        unknown = self.toggles - ALL_TOGGLES
        if unknown:
            raise ValueError(f"unknown toggles: {sorted(unknown)}")
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))

    def __hash__(self) -> int:
        return hash((self.min_statements, self.max_statements, self.max_depth, self.probe_every, self.probe_width,
                     self.strict, self.language_version, self.toggles, self.families, self.generators,
                     tuple(sorted(self.weights.items()))))
