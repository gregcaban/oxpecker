"""Environment model: what builtins this Rhino build exposes (constructors, prototype methods,
namespaces, syntax), discovered by running probe.js through the harness once and cached in
rhino-capabilities.json next to this file. The API generators draw from it, so a Rhino upgrade
only needs `oxpecker probe` to refresh the model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROBE_JS = HERE / "probe.js"
CACHE = HERE / "rhino-capabilities.json"

# Builtins the generators should not poke at: host/harness objects, and things whose calls
# are either meaningless or nondeterministic here.
EXCLUDED_GLOBALS = {"J", "print", "__probe", "__isProxy", "__jtype", "__pause", "globalThis", "eval",
                    "Function", "Continuation", "WeakRef", "FinalizationRegistry", "Packages", "java",
                    "javax", "org", "com", "net", "edu", "JavaAdapter", "JavaImporter", "XML", "XMLList",
                    "Namespace", "QName", "gc", "uneval", "Iterator", "StopIteration", "InternalError",
                    "ConsString", "CallSite", "Script", "JavaException", "AggregateError"}


# Syntax capabilities. Each snippet is its own two-mode harness case: a crash or a mode
# difference in *parsing* is a finding in itself, and cannot take the whole probe down.
SYNTAX: dict[str, str] = {
    "let": "let a = 1;", "const": "const a = 1;", "arrow": "var f = () => 1;", "class": "class A {}",
    "generator": "function* g() { yield 1; }", "for-of": "for (var x of []) {}", "spread-call": "Math.max(...[1])",
    "spread-array": "var a = [...[1]];", "object-spread": "var o = {...{}};", "destructuring": "var [a, {b}] = [1, {}];",
    "default-params": "function f(a = 1) {}", "rest-params": "function f(...a) {}", "template": "var s = `a${1}`;",
    "computed-key": "var o = {[1]: 1};", "shorthand-method": "var o = {m() {}};", "getter": "var o = {get x() { return 1; }};",
    "super-in-object": "var o = {m() { return super.x; }};", "new-target": "function f() { return new.target; }",
    "async": "async function f() {}", "await": "async function f() { await 1; }", "optional-chaining": "var a = {}; a?.b;",
    "nullish": "var a = null ?? 1;", "exponent": "var a = 2 ** 3;", "bigint": "var a = 1n;", "numeric-separator": "var a = 1_000;",
    "logical-assign": "var a = 1; a ||= 2;", "regexp-named-groups": "/(?<x>a)/.exec('a').groups.x",
    "regexp-lookbehind": "/(?<=a)b/.test('ab')", "regexp-dotall": "/./s.test('\\n')", "regexp-unicode": "/\\u{1F600}/u.test('x')",
    "label-block": "L: { break L; }", "with": "with ({}) {}", "trailing-comma-call": "Math.max(1,)",
    "getter-numeric": "var o = {get 0() { return 1; }};", "unicode-escape-id": "var \\u0061 = 1;",
    "hashbang": "#!x\n1", "private-field": "class A { #x; }", "static-block": "class A { static {} }",
    "import-meta": "import.meta", "top-level-await": "await 1", "import-call": "import('x')",
    "tagged-template": "String.raw`a${1}`", "octal-literal": "var a = 0o17;", "binary-literal": "var a = 0b101;",
    "legacy-octal": "var a = 017;", "comma-in-for": "for (var i = 0, j = 1; i < 1; i++, j++) {}",
    "label-continue": "L: for (var i = 0; i < 2; i++) { continue L; }", "debugger": "debugger;", "getter-computed": "var o = {get [1]() { return 1; }};",
    "generator-method": "var o = {*g() {}};", "arrow-destructure": "var f = ({a}) => a;",
    # Rhino 1.9.2 parses rest parameters on functions but not on arrows, and no rest element in patterns
    "arrow-rest": "var f = (a, ...r) => r;", "array-rest": "var [a, ...r] = [1, 2];", "object-rest": "var {a, ...r} = {a: 1, b: 2};",
}


def probe_syntax(client: Any, language_version: int = 250) -> dict[str, dict[str, Any]]:
    """{name: {interpreted: status, compiled: status, supported: bool, verdict}} per snippet."""
    from ..harness.compare import compare_run
    out: dict[str, dict[str, Any]] = {}
    for name, src in SYNTAX.items():
        run = client.run(src, language_version=language_version, id=f"syntax-{name}", source_name=f"syntax-{name}.js",
                         continuations=False)
        st = {m: o.status for m, o in run.results.items()}
        v = compare_run(run)
        out[name] = {**st, "supported": all(s == "ok" for s in st.values()), "verdict": v.kind,
                     "detail": v.pretty() if v.kind != "equal" else ""}
    return out


def run_probe(client: Any, language_version: int = 250) -> dict[str, Any]:
    """Runs probe.js in interpreted mode and returns the decoded capability table."""
    run = client.run(PROBE_JS.read_text(encoding="utf-8"), modes=("interpreted",), language_version=language_version,
                     id="capability-probe", source_name="probe.js", wall_ms=30000, continuations=False)
    o = run.results["interpreted"]
    if o.status != "ok" or not o.output:
        raise RuntimeError(f"probe failed: {o.status} {o.thrown or o.engineException}")
    table = json.loads(o.output[0])
    table["syntax"] = probe_syntax(client, language_version)
    table["languageVersion"] = language_version
    table["rhino"] = client.info().get("rhino")
    return table


def save(table: dict[str, Any], path: Path = CACHE) -> None:
    path.write_text(json.dumps(table, indent=1, sort_keys=True), encoding="utf-8")


def load(path: Path = CACHE) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def method_table(table: dict[str, Any]) -> dict[str, list[tuple[str, int]]]:
    """{constructor name: [(method, arity)]} for prototype methods, excluding accessors/symbols."""
    out: dict[str, list[tuple[str, int]]] = {}
    for ctor, methods in table.get("protos", {}).items():
        if ctor in EXCLUDED_GLOBALS:
            continue
        ms = []
        for m in methods:
            if "/" in m and not m.startswith("Symbol("):
                name, _, arity = m.rpartition("/")
                ms.append((name, int(arity)))
        if ms:
            out[ctor] = ms
    return out


def static_table(table: dict[str, Any]) -> dict[str, list[tuple[str, int]]]:
    """{namespace or constructor: [(function, arity)]} for static functions."""
    out: dict[str, list[tuple[str, int]]] = {}
    for owner, members in table.get("statics", {}).items():
        if owner in EXCLUDED_GLOBALS:
            continue
        fs = [(m.rpartition("/")[0], int(m.rpartition("/")[2])) for m in members if "/" in m]
        if fs:
            out[owner] = fs
    return out
