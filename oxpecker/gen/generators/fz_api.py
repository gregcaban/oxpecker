# Ported from Fuzzilli (https://github.com/googleprojectzero/fuzzilli), Sources/Fuzzilli/CodeGen/CodeGenerators.swift (ApiMethodCall/ApiFunctionCall) and JavaScriptEnvironment.swift,
# Copyright 2019 Google LLC, licensed under the Apache License, Version 2.0
# (see docs/licenses/Apache-2.0.txt and THIRD_PARTY_NOTICES.md). Re-implemented in Python against
# oxpecker's ProgramBuilder and filtered to the language subset Rhino supports.
"""API generators driven by the probed environment model (Fuzzilli's ApiMethodCall /
ApiFunctionCall / ConstructorCall over JavaScriptEnvironment, here over what Rhino actually
exposes: oxpecker/gen/environment/rhino-capabilities.json, refreshed with `oxpecker probe`)."""

from __future__ import annotations

from .. import environment
from ..builder import ProgramBuilder
from ..registry import generator
from ..types import T
from .values import literal

_TABLE = environment.load()
METHODS = environment.method_table(_TABLE) if _TABLE else {}
STATICS = environment.static_table(_TABLE) if _TABLE else {}
CTORS = sorted(n for n, c in (_TABLE or {}).get("constructors", {}).items()
               if c.get("isCtor") and n not in environment.EXCLUDED_GLOBALS and n in METHODS) if _TABLE else []

# Which of our coarse types can act as a receiver for which prototype.
RECEIVERS: dict[str, T] = {
    "Array": T.ARRAY, "String": T.STRING, "Number": T.NUMBER, "Boolean": T.BOOLEAN, "Object": T.OBJECT,
    "RegExp": T.REGEXP, "Map": T.MAP, "Set": T.SET, "TypedArray": T.TYPEDARRAY, "BigInt": T.BIGINT,
    "Symbol": T.SYMBOL, "Date": T.OBJECT, "Error": T.OBJECT, "Promise": T.OBJECT,
}
# Methods that are nondeterministic or pointless for a differential oracle.
SKIP_METHODS = {"toLocaleString", "toLocaleDateString", "toLocaleTimeString", "toLocaleLowerCase",
                "toLocaleUpperCase", "localeCompare", "getTimezoneOffset", "then", "catch", "finally"}
NAMESPACES = ["Math", "JSON", "Reflect", "Object", "Array", "Number", "String", "Symbol", "BigInt", "Date",
              "Promise", "ArrayBuffer", "Proxy"]
SKIP_STATICS = {"now", "random", "all", "allSettled", "race", "any", "withResolvers", "resolve", "reject",
                "revocable", "for", "keyFor"}


def _receiver(b: ProgramBuilder, proto: str) -> str:
    t = RECEIVERS[proto]
    if proto == "Date":
        return f"new Date({literal(b, T.NUMBER)})"
    if proto == "Error":
        return f"new {b.choice(['Error', 'TypeError', 'RangeError'])}({literal(b, T.STRING)})"
    if proto == "Promise":
        return f"Promise.resolve({literal(b, T.NUMBER)})"
    e = b.expr(t, literal)
    # `1.toString()` is a SyntaxError; literals (and anything else non-identifier) get parentheses.
    return e if e.isidentifier() else f"({e})"


def _args(b: ProgramBuilder, arity: int) -> str:
    # Mostly the declared arity, sometimes fewer or more; callbacks for the usual suspects.
    n = max(0, arity + b.choice([0, 0, 0, -1, 1]))
    out = []
    for _ in range(n):
        r = b.rand_int(0, 9)
        if r == 0:
            out.append(b.choice(["function (x, i) { return x; }", "function (a, b) { return a - b; }",
                                 "function () { return 0; }", "(x) => x + 1", "Math.max"]))
        else:
            out.append(b.expr(b.choice([T.NUMBER, T.INT32, T.STRING, T.UNKNOWN, T.ARRAY, T.OBJECT]), literal))
    return ", ".join(out)


def _guarded(b: ProgramBuilder, expr: str, out: T = T.UNKNOWN) -> None:
    tb = b.try_block()
    with tb.body():
        b.fresh(out, expr)
    with tb.catch("e"):
        b.emit('__probe("apierr", e && e.name);' if b.opts.probes else "// threw")
    tb.end()


@generator("api_method_call", family="api", weight=6, output=T.UNKNOWN)
def gen_api_method_call(b: ProgramBuilder):
    """Call a prototype method Rhino actually implements on a receiver of the matching type,
    with arity-guided arguments (from the capability probe)."""
    if not METHODS:
        b.comment("api_method_call: run `oxpecker probe` first")
        return
    proto = b.choice([p for p in RECEIVERS if p in METHODS])
    name, arity = b.choice([m for m in METHODS[proto] if m[0] not in SKIP_METHODS] or [("toString", 0)])
    _guarded(b, f"{_receiver(b, proto)}.{name}({_args(b, arity)})")


@generator("api_method_wrong_receiver", family="api", weight=3, output=T.UNKNOWN)
def gen_api_method_wrong_receiver(b: ProgramBuilder):
    """Apply a prototype method to a receiver of another type via .call (Array methods on
    strings/array-likes, String methods on numbers, Map methods on plain objects → TypeError)."""
    if not METHODS:
        return
    proto = b.choice([p for p in RECEIVERS if p in METHODS])
    name, arity = b.choice([m for m in METHODS[proto] if m[0] not in SKIP_METHODS] or [("toString", 0)])
    recv = b.expr(b.choice([T.ARRAY, T.STRING, T.OBJECT, T.NUMBER, T.NULL, T.UNDEFINED, T.TYPEDARRAY]), literal)
    extra = _args(b, arity)
    _guarded(b, f"{proto}.prototype.{name}.call({recv}{', ' + extra if extra else ''})")


@generator("api_static_call", family="api", weight=5, output=T.UNKNOWN)
def gen_api_static_call(b: ProgramBuilder):
    """Call a static builtin function (Math.*, Object.*, Array.*, Reflect.*, JSON.*, Number.*...)."""
    if not STATICS:
        return
    owner = b.choice([n for n in NAMESPACES if n in STATICS])
    name, arity = b.choice([f for f in STATICS[owner] if f[0] not in SKIP_STATICS] or [("toString", 0)])
    _guarded(b, f"{owner}.{name}({_args(b, arity)})")


@generator("api_construct", family="api", weight=3, output=T.OBJECT)
def gen_api_construct(b: ProgramBuilder):
    """`new` on a probed constructor with drawn arguments, then one method call on the result."""
    if not CTORS:
        return
    ctor = b.choice([c for c in CTORS if c not in ("Proxy", "Promise", "Symbol")])
    arity = (_TABLE or {}).get("constructors", {}).get(ctor, {}).get("length", 1)
    o = b.declare(T.OBJECT)
    tb = b.try_block()
    with tb.body():
        b.emit(f"var {o} = new {ctor}({_args(b, min(arity, 2))});")
        if ctor in METHODS:
            name, marity = b.choice([m for m in METHODS[ctor] if m[0] not in SKIP_METHODS] or [("toString", 0)])
            b.fresh(T.UNKNOWN, f"{o}.{name}({_args(b, marity)})")
    with tb.catch("e"):
        b.emit('__probe("ctorerr", e && e.name);' if b.opts.probes else "// construction threw")
    tb.end()
