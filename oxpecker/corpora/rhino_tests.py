"""Rhino's own JS tests as replay corpora (brief §10.2):

- `rhino-jstests`: tests/testsrc/jstests/**/*.js|*.jstest; `load("testsrc/x.js")` lines are inlined.
- `rhino-mozilla`: the legacy Mozilla suite tests/testsrc/tests/<suite>/<dir>/*.js, each prefixed
  with the shell.js chain (tests/shell.js, <suite>/shell.js, <dir>/shell.js) like jsDriver does.
  Its reportCompare() prints PASSED/FAILED lines, so the output comparison is value-level.

Doctests (tests/testsrc/doctests) use a shell transcript format and are not replayed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from . import Case
from .test262 import RHINO

TESTSRC = RHINO / "tests" / "testsrc"
JSTESTS = TESTSRC / "jstests"
MOZILLA = TESTSRC / "tests"

# Suites needing LiveConnect (`Packages`, `java.*`), E4X, or a browser; and driver files.
MOZILLA_SKIP_DIRS = {"e4x", "lc2", "lc3", "src"}
MOZILLA_SKIP_FILES = {"shell.js", "browser.js", "template.js", "importList.js", "menuhead.js", "userhookeach.js",
                      "js-test-driver-begin.js", "js-test-driver-end.js"}

_LOAD = re.compile(r"""^\s*load\(\s*['"]([^'"]+)['"]\s*\)\s*;?\s*$""", re.M)


def _inline_loads(source: str, base: Path, depth: int = 0) -> str:
    def repl(m: re.Match[str]) -> str:
        rel = m.group(1)
        f = base / rel if not rel.startswith("testsrc/") else RHINO / "tests" / rel
        if not f.exists() or depth > 3:
            return f"// oxpecker: could not inline load({rel!r})"
        return f"// ---- load {rel}\n" + _inline_loads(f.read_text(encoding="utf-8", errors="replace"), base, depth + 1)
    return _LOAD.sub(repl, source)


def jstests(*, filter: str | None = None, limit: int | None = None) -> Iterator[Case]:
    files = sorted(p for p in JSTESTS.rglob("*") if p.suffix in (".js", ".jstest") and p.is_file())
    n = 0
    for f in files:
        rel = str(f.relative_to(JSTESTS))
        if filter and not rel.startswith(filter):
            continue
        if "helpers" in f.name:
            continue
        src = _inline_loads(f.read_text(encoding="utf-8", errors="replace"), RHINO / "tests")
        yield Case(id=rel, source=src, path=rel, language_version=250, tags={"corpus": "rhino-jstests"})
        n += 1
        if limit and n >= limit:
            return


def mozilla(*, filter: str | None = None, limit: int | None = None) -> Iterator[Case]:
    root_shell = MOZILLA / "shell.js"
    n = 0
    for f in sorted(MOZILLA.rglob("*.js")):
        rel = f.relative_to(MOZILLA)
        parts = rel.parts
        if f.name in MOZILLA_SKIP_FILES or parts[0] in MOZILLA_SKIP_DIRS or len(parts) < 2:
            continue
        if filter and not str(rel).startswith(filter):
            continue
        chain = [root_shell]
        for i in range(1, len(parts)):
            chain.append(MOZILLA.joinpath(*parts[:i]) / "shell.js")
        src_parts = []
        for shell in chain:
            if shell.exists():
                src_parts.append(f"// ---- {shell.relative_to(MOZILLA)}\n" + shell.read_text(encoding="utf-8", errors="replace"))
        src_parts.append(f"// ---- {rel}\n" + f.read_text(encoding="utf-8", errors="replace"))
        # The driver's reportCompare counts and prints; make the summary deterministic.
        src_parts.append("\nif (typeof jsTestDriverEnd === 'function') { try { jsTestDriverEnd(); } catch (e) { print('driver-end threw ' + e); } }")
        yield Case(id=str(rel), source="\n".join(src_parts), path=str(rel), language_version=250,
                   tags={"corpus": "rhino-mozilla", "suite": parts[0]})
        n += 1
        if limit and n >= limit:
            return
