"""test262 replay (brief §10.1): the suite vendored in Rhino, filtered the way Rhino's own
Test262SuiteTest filters it, each test prefixed with the harness includes it declares, run in
both modes and compared at value level.

    oxpecker corpus test262 --filter language/statements/try --limit 200
    oxpecker corpus test262 --known-divergences        # files test262.properties tags per mode
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from . import Case

RHINO = Path(__file__).resolve().parents[2].parent / "rhino"
TEST262 = RHINO / "tests" / "test262"
PROPERTIES = RHINO / "tests" / "testsrc" / "test262.properties"

# Mirrors Test262SuiteTest.UNSUPPORTED_FEATURES at the pinned commit.
UNSUPPORTED_FEATURES = {"Atomics", "IsHTMLDDA", "async-functions", "async-iteration", "class",
                        "class-fields-private", "class-fields-public", "SharedArrayBuffer",
                        "tail-call-optimization", "Temporal", "upsert", "u180e"}
# Flags our harness cannot honour: async tests need $DONE plumbing, modules aren't supported.
UNSUPPORTED_FLAGS = {"async", "module", "CanBlockIsFalse", "CanBlockIsTrue"}
# Features whose tests only make sense with a real multi-realm / agent host.
UNSUPPORTED_HOST_FEATURES = {"cross-realm"}

# Minimal $262 host object (Rhino's runner provides a Java one). createRealm is unsupported, so
# tests needing it throw identically in both modes.
SHIM_262 = """\
var $262 = {
  global: this,
  gc: function () {},
  evalScript: function (src) { return (0, eval)(src); },
  detachArrayBuffer: function (buf) { throw new Error("detachArrayBuffer unsupported in oxpecker"); },
  createRealm: function () { throw new Error("createRealm unsupported in oxpecker"); },
  agent: {}
};
"""

_META_RE = re.compile(r"/\*---(.*?)---\*/", re.S)


def parse_frontmatter(source: str) -> dict:
    """Enough YAML for test262 metadata: includes/flags/features (inline or block lists), negative."""
    m = _META_RE.search(source)
    meta: dict = {"includes": [], "flags": [], "features": [], "negative": None}
    if not m:
        return meta
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        i += 1
        if not stripped or stripped.startswith("#") or line.startswith(" ") and ":" not in stripped and not stripped.startswith("-"):
            continue
        key, _, rest = stripped.partition(":")
        key, rest = key.strip(), rest.strip()
        if key in ("includes", "flags", "features"):
            if rest.startswith("["):
                items = [x.strip().strip("'\"") for x in rest.strip("[]").split(",") if x.strip()]
            else:
                items = []
                while i < len(lines) and lines[i].strip().startswith("-"):
                    items.append(lines[i].strip()[1:].strip().strip("'\""))
                    i += 1
            meta[key] = items
        elif key == "negative":
            neg: dict[str, str] = {}
            while i < len(lines) and lines[i].startswith(" ") and ":" in lines[i]:
                k, _, v = lines[i].strip().partition(":")
                neg[k.strip()] = v.strip()
                i += 1
            meta["negative"] = neg
    return meta


_PROP_LINE = re.compile(r"^(~|\s*[!#]\s*|\s+)?(\S+)(?:[^\S\r\n]+(.*))?$")


def parse_properties(path: Path = PROPERTIES) -> tuple[set[str], dict[str, str]]:
    """Returns (excluded top-level folders, {relative test path: comment}) for files Rhino marks
    as failing. The comment carries the per-mode tags ("compiled", "{interpreted,debugger}", ...)."""
    excluded: set[str] = set()
    failing: dict[str, str] = {}
    top = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _PROP_LINE.match(raw)
        if not m:
            continue
        marker, p, comment = m.group(1), m.group(2), (m.group(3) or "").strip()
        if marker is None or marker == "~":
            top = p
            if marker == "~":
                excluded.add(p)
        elif top is not None and p.endswith(".js"):
            failing[f"{top}/{p}"] = comment
        # sub-folder summary lines ("RegExp/legacy-accessors/index 4/4 (100.0%)") carry no files
    return excluded, failing


_MODE_TAG = re.compile(r"\{?(interpreted|compiled|debugger)(?:,(interpreted|compiled|debugger))*\}?")


def mode_tags(comment: str) -> set[str]:
    """Modes named in a test262.properties comment, e.g. 'compiled' or '{interpreted,debugger}'."""
    tags: set[str] = set()
    for tok in comment.replace("{", " ").replace("}", " ").replace(",", " ").split():
        if tok in ("interpreted", "compiled", "debugger"):
            tags.add(tok)
    return tags


def known_divergences() -> dict[str, set[str]]:
    """Files Rhino marks as failing in some modes only: {relative path: failing modes}."""
    _, failing = parse_properties()
    out = {}
    for p, comment in failing.items():
        tags = mode_tags(comment)
        if tags and tags != {"interpreted", "compiled", "debugger"}:
            out[p] = tags
    return out


def build_source(test_path: Path, meta: dict, strict: bool) -> str:
    parts: list[str] = []
    if strict:
        parts.append('"use strict";')
    body = test_path.read_text(encoding="utf-8")
    if "raw" in meta["flags"]:
        return ("\n".join(parts) + "\n" if parts else "") + body
    parts.append(SHIM_262)
    for inc in ["assert.js", "sta.js"] + [i for i in meta["includes"] if i not in ("assert.js", "sta.js")]:
        f = TEST262 / "harness" / inc
        if f.exists():
            parts.append(f"// ---- include {inc}\n" + f.read_text(encoding="utf-8"))
    parts.append(f"// ---- {test_path.relative_to(TEST262 / 'test')}")
    parts.append(body)
    return "\n".join(parts)


def cases(*, filter: str | None = None, limit: int | None = None, strict_variants: bool = True,
          only_known: bool = False) -> Iterator[Case]:
    root = TEST262 / "test"
    if not root.exists():
        raise FileNotFoundError(f"test262 not found at {root}; run `git submodule update --init` in ../rhino")
    excluded, failing = parse_properties()
    known = known_divergences()
    n = 0
    files = sorted(root.rglob("*.js"))
    for f in files:
        rel = str(f.relative_to(root))
        if "_FIXTURE" in f.name:
            continue
        if filter and not rel.startswith(filter):
            continue
        if only_known and rel not in known:
            continue
        if any(rel.startswith(ex + "/") for ex in excluded) and rel not in known:
            continue
        meta = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        if UNSUPPORTED_FEATURES & set(meta["features"]) or UNSUPPORTED_HOST_FEATURES & set(meta["features"]):
            continue
        if UNSUPPORTED_FLAGS & set(meta["flags"]):
            continue
        variants = [False, True] if strict_variants else [False]
        if "onlyStrict" in meta["flags"]:
            variants = [True]
        elif "noStrict" in meta["flags"] or "raw" in meta["flags"]:
            variants = [False]
        tags = {"rhinoFailing": failing.get(rel, "")} if rel in failing else {}
        if rel in known:
            tags["knownDivergence"] = ",".join(sorted(known[rel]))
        if meta["negative"]:
            tags["negative"] = f"{meta['negative'].get('phase')}:{meta['negative'].get('type')}"
        for strict in variants:
            yield Case(id=rel + (" [strict]" if strict else ""), source=build_source(f, meta, strict),
                       path=rel, strict=strict, tags=dict(tags))
        n += 1
        if limit and n >= limit:
            return
