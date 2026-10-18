"""Seed and replay corpora: test262 (as vendored by Rhino), Rhino's own tests, other engines'
regression suites. Each importer yields Case objects the harness can run."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Case:
    id: str
    source: str
    path: str
    strict: bool = False
    language_version: int = 250
    tags: dict[str, str] = field(default_factory=dict)   # e.g. {"knownDivergence": "compiled"}
