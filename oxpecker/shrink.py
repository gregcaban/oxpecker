"""Source-level reproducer shrinking: delta debugging over top-level statements, then over
single lines, while an oracle (usually "same coarse verdict signature") still holds.

Complements the choice-sequence shrinker in fuzz.py: deleting an early *choice* re-contextualises
every later draw and usually loses the bug, whereas deleting a *statement* leaves the rest of the
program intact. Programs are built so that top-level statements start at brace depth 0, which is
all the structure this needs.
"""

from __future__ import annotations

import re
from collections.abc import Callable


def split_statements(source: str) -> list[list[str]]:
    """Groups lines into top-level units: a unit ends where the brace depth returns to 0 at the
    end of a line. Comment lines attach to the following unit."""
    units: list[list[str]] = []
    cur: list[str] = []
    depth = 0
    for line in source.splitlines():
        cur.append(line)
        depth += _depth_delta(line)
        if depth <= 0 and line.strip() and not line.lstrip().startswith("//"):
            depth = 0
            units.append(cur)
            cur = []
    if cur:
        units.append(cur)
    return units


def _depth_delta(line: str) -> int:
    # Ignore braces inside strings/regexps/comments well enough for generated code.
    code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`|//.*$', "", line)
    return code.count("{") - code.count("}")


def shrink_source(source: str, oracle: Callable[[str], bool], *, budget: int = 300) -> str:
    """Returns a smaller source for which `oracle` still holds (the original if nothing can go)."""
    tries = 0

    def holds(s: str) -> bool:
        nonlocal tries
        tries += 1
        return oracle(s)

    # ---- 1. top-level units, ddmin-style chunk removal
    units = split_statements(source)
    n = max(1, len(units) // 2)
    while n >= 1 and tries < budget:
        i = 0
        progressed = False
        while i < len(units) and tries < budget:
            cand = units[:i] + units[i + n:]
            if cand and holds(_join(cand)):
                units = cand
                progressed = True
            else:
                i += n
        if not progressed:
            n //= 2

    # ---- 2. single lines inside the remaining units (keeping braces balanced)
    lines = _join(units).splitlines()
    i = 0
    while i < len(lines) and tries < budget:
        ln = lines[i]
        if _depth_delta(ln) == 0 and ln.strip() and not ln.strip().startswith("}") :
            cand = lines[:i] + lines[i + 1:]
            if holds("\n".join(cand) + "\n"):
                lines = cand
                continue
        i += 1
    # ---- 3. drop comment lines (they carry no behaviour; keep probe labels readable)
    lines = [ln for ln in lines if not ln.strip().startswith("//")]
    return "\n".join(lines) + "\n"


def _join(units: list[list[str]]) -> str:
    return "\n".join(ln for u in units for ln in u) + "\n"
