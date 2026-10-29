# The registry shape follows Fuzzilli's JavaScriptEnvironment.swift (Copyright 2019-2025 Google
# LLC, Apache License 2.0; commit be2811d00282320381fc8007d64873745772011d): object groups with
# typed properties and method signatures, builtins registered with types. The data comes from
# oxpecker's own probe of Rhino (rhino-capabilities.json) plus the hand-typed overlay below.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Typed registries of what Rhino exposes: builtins, property keys, methods, value pools.

Generators never name a builtin, key or method by string; they use the attributes of `Builtins`,
`Keys` and `Methods`. The lifter is the only reader of `.name`. `oxpecker probe` refreshes the
table; `tests/il/test_environment.py` checks the overlay against it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .. import environment as probe
from .types import (
    ANY,
    ARRAY,
    BIGINT,
    BOOLEAN,
    FLOAT,
    FUNCTION,
    INTEGER,
    NULL,
    NUMBER,
    OBJECT,
    STRING,
    SYMBOL,
    UNDEFINED,
    Key,
    KeyKind,
    Param,
    ParamMode,
    Signature,
    Type,
)


# ------------------------------------------------------------------------------- entries
@dataclass(frozen=True, slots=True)
class Builtin:
    name: str                              # the global binding; spelled by the lifter only
    type: Type                             # what LoadBuiltin gives
    constructs: Type | None = None         # type of `new B(...)`, if constructible
    calls: Type | None = None              # type of `B(...)`, if callable
    signature: Signature | None = None     # parameters of the call/construct
    group: str | None = None               # the instance group this constructor makes

    def __repr__(self) -> str:
        return f"Builtin({self.name!r})"

    def _repr_pretty_(self, p, cycle) -> None:    # Hypothesis prints dataclasses field by field unless told otherwise
        p.text(repr(self))


@dataclass(frozen=True, slots=True)
class Method:
    receiver: str                          # group name of the receiver ("Array", "Math", "ArrayCtor", "J")
    name: str
    arity: int = 0
    params: tuple[Type, ...] = ()
    returns: Type = ANY

    def __repr__(self) -> str:
        return f"Method({self.receiver + '.' + self.name!r})"

    def _repr_pretty_(self, p, cycle) -> None:    # Hypothesis prints dataclasses field by field unless told otherwise
        p.text(repr(self))


RHINO_TABLE_PATH: Path = probe.CACHE


def _snake(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.replace("__", "_").upper()


_ATTR_OVERRIDES = {"isNaN": "IS_NAN", "NaN": "NAN", "isFinite": "IS_FINITE", "isXMLName": "IS_XML_NAME",
                   "escape": "ESCAPE", "unescape": "UNESCAPE", "encodeURI": "ENCODE_URI", "decodeURI": "DECODE_URI",
                   "encodeURIComponent": "ENCODE_URI_COMPONENT", "decodeURIComponent": "DECODE_URI_COMPONENT"}


def attr_name(name: str) -> str:
    return _ATTR_OVERRIDES.get(name) or _snake(name).strip("_")


def _is_identifier(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name))


# ------------------------------------------------------------------------------- overlay
# Facts the probe cannot see. Keys are "Group" for constructors/namespaces, "Group.method" for
# prototype methods, "GroupCtor.method" for statics. A test checks every key against the table.
_CTOR = "Ctor"
_TYPED_ARRAYS = ("Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array", "Int32Array",
                 "Uint32Array", "Float16Array", "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array")
_ERRORS = ("Error", "EvalError", "RangeError", "ReferenceError", "SyntaxError", "TypeError", "URIError",
           "InternalError", "AggregateError")

GLOBAL_TYPES: dict[str, Type] = {
    "NaN": FLOAT, "Infinity": FLOAT, "undefined": UNDEFINED,
    "Math": Type.object("Math"), "JSON": Type.object("JSON"), "Reflect": Type.object("Reflect"),
}
# instance group a constructor makes (default: its own name)
INSTANCE_GROUP: dict[str, str] = {ta: "TypedArray" for ta in _TYPED_ARRAYS}
NOT_CONSTRUCTIBLE = frozenset({"Symbol", "BigInt"})      # callable constructors that throw on `new`
# Instances that are not plain objects: a NativeContinuation is a callable (BaseFunction).
INSTANCE_TYPE: dict[str, Type] = {"Continuation": Type.function(group="Continuation")}
CALLS: dict[str, Type] = {                     # `B(...)` for callable constructors and functions
    "String": STRING, "Number": NUMBER, "Boolean": BOOLEAN, "BigInt": BIGINT, "Symbol": SYMBOL,
    "Array": ARRAY, "Object": OBJECT, "RegExp": Type.object("RegExp"), "Date": STRING,
    "parseInt": NUMBER, "parseFloat": NUMBER, "isNaN": BOOLEAN, "isFinite": BOOLEAN,
    "encodeURI": STRING, "decodeURI": STRING, "encodeURIComponent": STRING, "decodeURIComponent": STRING,
    "escape": STRING, "unescape": STRING, "eval": ANY,
    **{e: Type.object(e) for e in _ERRORS},
}
RETURNS: dict[str, Type] = {                   # method result types
    "Array.push": INTEGER, "Array.pop": ANY, "Array.shift": ANY, "Array.unshift": INTEGER, "Array.map": ARRAY,
    "Array.filter": ARRAY, "Array.slice": ARRAY, "Array.concat": ARRAY, "Array.join": STRING, "Array.indexOf": INTEGER,
    "Array.includes": BOOLEAN, "Array.reverse": ARRAY, "Array.sort": ARRAY, "Array.flat": ARRAY, "Array.fill": ARRAY,
    "Array.splice": ARRAY, "Array.forEach": UNDEFINED, "Array.reduce": ANY, "Array.some": BOOLEAN, "Array.every": BOOLEAN,
    "Array.find": ANY, "Array.findIndex": INTEGER, "Array.keys": OBJECT, "Array.values": OBJECT, "Array.entries": OBJECT,
    "Array.at": ANY, "Array.toString": STRING, "Array.lastIndexOf": INTEGER,
    "String.charAt": STRING, "String.charCodeAt": NUMBER, "String.codePointAt": NUMBER, "String.concat": STRING,
    "String.indexOf": INTEGER, "String.lastIndexOf": INTEGER, "String.slice": STRING, "String.substring": STRING,
    "String.substr": STRING, "String.toUpperCase": STRING, "String.toLowerCase": STRING, "String.trim": STRING,
    "String.split": ARRAY, "String.replace": STRING, "String.replaceAll": STRING, "String.repeat": STRING,
    "String.padStart": STRING, "String.padEnd": STRING, "String.includes": BOOLEAN, "String.startsWith": BOOLEAN,
    "String.endsWith": BOOLEAN, "String.normalize": STRING, "String.at": STRING, "String.localeCompare": INTEGER,
    "String.match": ANY, "String.search": INTEGER, "String.toString": STRING, "String.valueOf": STRING,
    "Number.toString": STRING, "Number.toFixed": STRING, "Number.toPrecision": STRING, "Number.toExponential": STRING,
    "Number.valueOf": NUMBER,
    "Boolean.valueOf": BOOLEAN, "Boolean.toString": STRING,
    "Object.hasOwnProperty": BOOLEAN, "Object.toString": STRING, "Object.valueOf": ANY, "Object.isPrototypeOf": BOOLEAN,
    "Object.propertyIsEnumerable": BOOLEAN,
    "Map.get": ANY, "Map.set": Type.object("Map"), "Map.has": BOOLEAN, "Map.delete": BOOLEAN, "Map.clear": UNDEFINED,
    "Map.keys": OBJECT, "Map.values": OBJECT, "Map.entries": OBJECT, "Map.forEach": UNDEFINED,
    "Set.add": Type.object("Set"), "Set.has": BOOLEAN, "Set.delete": BOOLEAN, "Set.clear": UNDEFINED, "Set.values": OBJECT,
    "WeakMap.get": ANY, "WeakMap.set": Type.object("WeakMap"), "WeakMap.has": BOOLEAN, "WeakSet.add": Type.object("WeakSet"),
    "WeakSet.has": BOOLEAN,
    "RegExp.test": BOOLEAN, "RegExp.exec": ANY, "RegExp.toString": STRING,
    "Function.call": ANY, "Function.apply": ANY, "Function.bind": FUNCTION, "Function.toString": STRING,
    "Date.getTime": NUMBER, "Date.toISOString": STRING, "Date.valueOf": NUMBER, "Date.getFullYear": INTEGER,
    "TypedArray.subarray": Type.object("TypedArray"), "TypedArray.slice": Type.object("TypedArray"), "TypedArray.join": STRING,
    "TypedArray.indexOf": INTEGER, "TypedArray.includes": BOOLEAN, "TypedArray.fill": Type.object("TypedArray"),
    "TypedArray.set": UNDEFINED, "TypedArray.at": ANY,
    "ArrayBuffer.slice": Type.object("ArrayBuffer"),
    "Symbol.toString": STRING, "Symbol.valueOf": SYMBOL,
    "BigInt.toString": STRING, "BigInt.valueOf": BIGINT,
    "Error.toString": STRING,
    "Promise.then": Type.object("Promise"), "Promise.catch": Type.object("Promise"), "Promise.finally": Type.object("Promise"),
    # statics
    "ArrayCtor.isArray": BOOLEAN, "ArrayCtor.from": ARRAY, "ArrayCtor.of": ARRAY,
    "ObjectCtor.keys": ARRAY, "ObjectCtor.values": ARRAY, "ObjectCtor.entries": ARRAY, "ObjectCtor.assign": OBJECT,
    "ObjectCtor.freeze": OBJECT, "ObjectCtor.seal": OBJECT, "ObjectCtor.preventExtensions": OBJECT,
    "ObjectCtor.isFrozen": BOOLEAN, "ObjectCtor.isSealed": BOOLEAN, "ObjectCtor.isExtensible": BOOLEAN,
    "ObjectCtor.getPrototypeOf": ANY, "ObjectCtor.setPrototypeOf": OBJECT, "ObjectCtor.create": OBJECT,
    "ObjectCtor.defineProperty": OBJECT, "ObjectCtor.getOwnPropertyNames": ARRAY, "ObjectCtor.getOwnPropertyDescriptor": ANY,
    "ObjectCtor.is": BOOLEAN, "ObjectCtor.fromEntries": OBJECT,
    "NumberCtor.isInteger": BOOLEAN, "NumberCtor.isSafeInteger": BOOLEAN, "NumberCtor.isNaN": BOOLEAN,
    "NumberCtor.isFinite": BOOLEAN, "NumberCtor.parseInt": NUMBER, "NumberCtor.parseFloat": NUMBER,
    "StringCtor.fromCharCode": STRING, "StringCtor.fromCodePoint": STRING, "StringCtor.raw": STRING,
    "SymbolCtor.for": SYMBOL, "SymbolCtor.keyFor": ANY,
    "BigIntCtor.asIntN": BIGINT, "BigIntCtor.asUintN": BIGINT,
    "JSON.stringify": ANY, "JSON.parse": ANY,
    "Reflect.get": ANY, "Reflect.set": BOOLEAN, "Reflect.has": BOOLEAN, "Reflect.ownKeys": ARRAY, "Reflect.apply": ANY,
    "Reflect.construct": OBJECT, "Reflect.getPrototypeOf": ANY, "Reflect.setPrototypeOf": BOOLEAN,
    "Reflect.defineProperty": BOOLEAN, "Reflect.deleteProperty": BOOLEAN, "Reflect.getOwnPropertyDescriptor": ANY,
    "DateCtor.now": NUMBER,
}
_MATH_NUMBER = ("abs", "acos", "acosh", "asin", "asinh", "atan", "atanh", "atan2", "ceil", "cbrt", "expm1", "cos",
                "cosh", "exp", "floor", "fround", "hypot", "log", "log1p", "log2", "log10", "max", "min", "pow",
                "random", "round", "sign", "sin", "sinh", "sqrt", "tan", "tanh", "trunc")
RETURNS.update({f"Math.{m}": NUMBER for m in _MATH_NUMBER})
RETURNS.update({"Math.imul": INTEGER, "Math.clz32": INTEGER})
PARAMS: dict[str, tuple[Type, ...]] = {
    "Array.push": (ANY,), "Array.map": (FUNCTION,), "Array.filter": (FUNCTION,), "Array.forEach": (FUNCTION,),
    "Array.reduce": (FUNCTION, ANY), "Array.join": (STRING,), "Array.indexOf": (ANY,), "Array.slice": (NUMBER, NUMBER),
    "Array.some": (FUNCTION,), "Array.every": (FUNCTION,), "Array.find": (FUNCTION,), "Array.sort": (FUNCTION,),
    "String.charAt": (NUMBER,), "String.charCodeAt": (NUMBER,), "String.codePointAt": (NUMBER,), "String.slice": (NUMBER, NUMBER),
    "String.indexOf": (STRING,), "String.repeat": (NUMBER,), "String.split": (STRING,), "String.padStart": (NUMBER, STRING),
    "Number.toString": (NUMBER,), "Number.toFixed": (NUMBER,), "Number.toPrecision": (NUMBER,),
    "Map.get": (ANY,), "Map.set": (ANY, ANY), "Map.has": (ANY,), "Set.add": (ANY,), "Set.has": (ANY,),
    "Function.call": (ANY,), "Function.apply": (ANY, ARRAY), "Function.bind": (ANY,),
    "ObjectCtor.keys": (OBJECT,), "ObjectCtor.values": (OBJECT,), "ObjectCtor.entries": (OBJECT,), "ObjectCtor.freeze": (OBJECT,),
    "ObjectCtor.seal": (OBJECT,), "ObjectCtor.preventExtensions": (OBJECT,), "ObjectCtor.getPrototypeOf": (OBJECT,),
    "ObjectCtor.setPrototypeOf": (OBJECT, ANY), "ObjectCtor.is": (ANY, ANY), "ObjectCtor.assign": (OBJECT, ANY),
    "ObjectCtor.getOwnPropertyNames": (OBJECT,), "ObjectCtor.defineProperty": (OBJECT, ANY, OBJECT),
    "JSON.stringify": (ANY,), "JSON.parse": (STRING,),
    "Reflect.get": (OBJECT, ANY), "Reflect.set": (OBJECT, ANY, ANY), "Reflect.has": (OBJECT, ANY), "Reflect.ownKeys": (OBJECT,),
    "Reflect.apply": (FUNCTION, ANY, ARRAY), "Reflect.construct": (FUNCTION, ARRAY),
    "Math.imul": (NUMBER, NUMBER), "Math.max": (NUMBER, NUMBER), "Math.min": (NUMBER, NUMBER), "Math.pow": (NUMBER, NUMBER),
    "Math.floor": (NUMBER,), "Math.abs": (NUMBER,), "Math.sqrt": (NUMBER,), "Math.round": (NUMBER,), "Math.trunc": (NUMBER,),
    "StringCtor.fromCharCode": (NUMBER,), "NumberCtor.isInteger": (ANY,), "SymbolCtor.for": (STRING,),
    "BigIntCtor.asIntN": (NUMBER, BIGINT), "BigIntCtor.asUintN": (NUMBER, BIGINT),
}

# The Java boundary (harness-java/.../JavaProbe.java): methods of the `J` host object.
_J_METHODS: tuple[tuple[str, int, tuple[Type, ...], Type], ...] = (
    ("takesInt", 1, (NUMBER,), INTEGER), ("takesLong", 1, (NUMBER,), NUMBER), ("takesShort", 1, (NUMBER,), INTEGER),
    ("takesByte", 1, (NUMBER,), INTEGER), ("takesChar", 1, (STRING,), STRING), ("takesFloat", 1, (NUMBER,), FLOAT),
    ("takesDouble", 1, (NUMBER,), FLOAT), ("takesBoolean", 1, (BOOLEAN,), BOOLEAN), ("takesString", 1, (STRING,), STRING),
    ("takesInteger", 1, (NUMBER,), INTEGER), ("takesNumber", 1, (NUMBER,), NUMBER), ("takesBigInteger", 1, (BIGINT,), BIGINT),
    ("takesCharSequence", 1, (STRING,), STRING),
    ("echo", 1, (ANY,), ANY), ("typeOf", 1, (ANY,), STRING), ("over", 1, (ANY,), STRING),
    ("anInt", 0, (), INTEGER), ("aLong", 0, (), NUMBER), ("maxLong", 0, (), NUMBER), ("aFloat", 0, (), FLOAT),
    ("aDouble", 0, (), FLOAT), ("negZero", 0, (), FLOAT), ("nan", 0, (), FLOAT), ("aShort", 0, (), INTEGER),
    ("aByte", 0, (), INTEGER), ("aChar", 0, (), STRING), ("boxedInt", 0, (), INTEGER), ("boxedLong", 0, (), NUMBER),
    ("bigInteger", 0, (), BIGINT), ("aString", 0, (), STRING), ("nothing", 0, (), NULL),
    ("intArray", 1, (NUMBER,), Type.object("JavaArray")), ("doubleArray", 1, (NUMBER,), Type.object("JavaArray")),
    ("objectArray", 1, (NUMBER,), Type.object("JavaArray")), ("stringArray", 0, (), Type.object("JavaArray")),
    ("list", 0, (), Type.object("JavaList")), ("map", 0, (), Type.object("JavaMap")),
    ("sum", 1, (ANY,), NUMBER), ("size", 1, (OBJECT,), INTEGER), ("get", 2, (OBJECT, ANY), ANY),
    ("bump", 0, (), INTEGER),
)
_J_PROPERTIES: tuple[tuple[str, Type], ...] = (("field", INTEGER), ("PI_ISH", FLOAT), ("staticCounter", INTEGER))
# Function.prototype: the probe excludes `Function` from the API generators, but calls, apply and
# bind are language-level operations the generators need.
_FUNCTION_METHODS: tuple[tuple[str, int, tuple[Type, ...], Type], ...] = (
    ("call", 1, (ANY,), ANY), ("apply", 2, (ANY, ARRAY), ANY), ("bind", 1, (ANY,), FUNCTION), ("toString", 0, (), STRING),
)
HAND_WRITTEN_RECEIVERS: frozenset[str] = frozenset({"J", "Function"})

# Host hooks and globals the probe excludes from the API generators but the IL needs by name.
_HOST_BUILTINS: tuple[Builtin, ...] = (
    Builtin("J", Type.object("J", props=frozenset(Key(n) for n, _ in _J_PROPERTIES)), group="J"),
    Builtin("__pause", FUNCTION, calls=ANY, signature=Signature((Param(ANY),), ANY)),
    Builtin("Continuation", Type.function(group="Continuation" + _CTOR), constructs=INSTANCE_TYPE["Continuation"], group="Continuation"),
    Builtin("eval", FUNCTION, calls=ANY, signature=Signature((Param(STRING),), ANY)),
    Builtin("Function", FUNCTION, constructs=FUNCTION, calls=FUNCTION, group="Function"),
    Builtin("globalThis", OBJECT, group="GlobalThis"),
)
_EXCLUDED_FROM_API = probe.EXCLUDED_GLOBALS


# ------------------------------------------------------------------------------- building
def _load_table(path: Path = RHINO_TABLE_PATH) -> dict[str, Any]:
    table = probe.load(path)
    if table is None:
        raise RuntimeError(f"capability table missing: {path}; run `oxpecker probe`")
    return table


def _builtin_from_table(name: str, info: dict[str, Any], globals_: dict[str, str]) -> Builtin | None:
    if not _is_identifier(name):
        return None
    if name in GLOBAL_TYPES:
        t = GLOBAL_TYPES[name]
        return Builtin(name, t, group=t.group)
    kind = globals_.get(name)
    if info.get("isCtor"):
        group = INSTANCE_GROUP.get(name, name)
        return Builtin(name, Type.function(group=name + _CTOR),
                       constructs=None if name in NOT_CONSTRUCTIBLE else Type.object(group),
                       calls=CALLS.get(name), group=group)
    if kind == "function":
        return Builtin(name, Type.function(group=name + _CTOR), calls=CALLS.get(name, ANY))
    return Builtin(name, Type.object(name), group=name)


def _parse_member(entry: str) -> tuple[str, str, int] | None:
    """'push/1' -> ('method', 'push', 1); 'size:acc' -> ('accessor', 'size', 0); 'x=' -> ('data', 'x', 0)."""
    if entry.startswith("Symbol("):
        return None
    if "/" in entry:
        name, _, arity = entry.rpartition("/")
        return ("method", name, int(arity))
    if entry.endswith(":acc"):
        return ("accessor", entry[:-4], 0)
    if entry.endswith("="):
        return ("data", entry[:-1], 0)
    return None


class _Namespace:
    """Attribute access by registry name plus iteration over all entries."""

    def __init__(self, entries: Mapping[str, Any]):
        self._entries = dict(entries)
        for k, v in entries.items():
            setattr(self, k, v)

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, key: str) -> Any:
        return self._entries[key]

    def get(self, key: str) -> Any:
        return self._entries.get(key)

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)


def _build(table: dict[str, Any]) -> tuple[_Namespace, _Namespace, _Namespace, dict[str, tuple[Method, ...]], dict[str, frozenset[Key]]]:
    globals_: dict[str, str] = table["globals"]
    ctors: dict[str, dict[str, Any]] = table["constructors"]
    builtins: dict[str, Builtin] = {}
    for name, kind in globals_.items():
        if name in _EXCLUDED_FROM_API or not _is_identifier(name):
            continue
        b = _builtin_from_table(name, ctors.get(name, {}), globals_)
        if b is not None:
            builtins[attr_name(name)] = b
    for b in _HOST_BUILTINS:
        builtins[attr_name(b.name)] = b

    methods: dict[str, Method] = {}
    by_group: dict[str, list[Method]] = {}
    props_by_group: dict[str, set[Key]] = {}
    keys: dict[str, Key] = {}

    def add_method(receiver: str, name: str, arity: int) -> None:
        full = f"{receiver}.{name}"
        m = Method(receiver, name, arity, PARAMS.get(full, ()), RETURNS.get(full, ANY))
        methods[f"{attr_name(receiver)}_{attr_name(name)}"] = m
        by_group.setdefault(receiver, []).append(m)

    for group, members in table.get("protos", {}).items():
        if group in _EXCLUDED_FROM_API or not _is_identifier(group):
            continue
        for entry in members:
            parsed = _parse_member(entry)
            if parsed is None:
                continue
            what, name, arity = parsed
            if not _is_identifier(name):
                continue
            if what == "method":
                add_method(group, name, arity)
            else:
                props_by_group.setdefault(group, set()).add(_key(name))
            keys.setdefault(name, _key(name))
    for owner, members in table.get("statics", {}).items():
        if owner in _EXCLUDED_FROM_API or not _is_identifier(owner):
            continue
        receiver = owner if owner in GLOBAL_TYPES else owner + _CTOR
        for entry in members:
            parsed = _parse_member(entry)
            if parsed is None:
                continue
            what, name, arity = parsed
            if not _is_identifier(name):
                continue
            if what == "method":
                add_method(receiver, name, arity)
            else:
                props_by_group.setdefault(receiver, set()).add(_key(name))
            keys.setdefault(name, _key(name))
    for name, arity, params, returns in _J_METHODS:
        m = Method("J", name, arity, params, returns)
        methods[f"J_{attr_name(name)}"] = m
        by_group.setdefault("J", []).append(m)
    for name, arity, params, returns in _FUNCTION_METHODS:
        m = Method("Function", name, arity, params, returns)
        methods[f"FUNCTION_{attr_name(name)}"] = m
        by_group.setdefault("Function", []).append(m)
        keys.setdefault(name, _key(name))
    for name, _ in _J_PROPERTIES:
        props_by_group.setdefault("J", set()).add(_key(name))
    for k in _INTERESTING_KEY_NAMES:
        keys.setdefault(k.name, k)
    key_ns = {_key_attr(k): k for k in keys.values()}
    return (_Namespace(builtins), _Namespace(key_ns), _Namespace(methods),
            {g: tuple(ms) for g, ms in by_group.items()}, {g: frozenset(ps) for g, ps in props_by_group.items()})


def _key(name: str) -> Key:
    if name == "length":
        return Key(name, KeyKind.LENGTH)
    if name == "__proto__":
        return Key(name, KeyKind.PROTO)
    if name.isdigit():
        return Key(name, KeyKind.INDEX)
    if name in _WELL_KNOWN_NAMES:
        return Key(name, KeyKind.WELL_KNOWN)
    return Key(name, KeyKind.ORDINARY)


def _key_attr(k: Key) -> str:
    if k.kind is KeyKind.INDEX:
        return f"INDEX_{k.name}"
    if k.name == "!":
        return "BANG"
    return attr_name(k.name) if _is_identifier(k.name) else "KEY_" + re.sub(r"\W", "_", k.name).upper()


_WELL_KNOWN_NAMES = frozenset({"constructor", "toString", "valueOf", "prototype", "name", "message", "hasOwnProperty",
                               "toJSON", "then", "next", "done", "value", "get", "set", "has"})
_INTERESTING_KEY_NAMES: tuple[Key, ...] = tuple(
    [Key(n) for n in ("a", "b", "c", "d", "e", "f", "x", "y", "m", "n", "g", "foo", "!")]
    + [Key(n, KeyKind.INDEX) for n in ("0", "1", "2", "10", "42")]
    + [Key("length", KeyKind.LENGTH), Key("__proto__", KeyKind.PROTO)]
    + [Key(n, KeyKind.WELL_KNOWN) for n in sorted(_WELL_KNOWN_NAMES)]
)

_TABLE = _load_table()
Builtins, Keys, Methods, METHODS_BY_GROUP, PROPS_BY_GROUP = _build(_TABLE)
PROBED_RHINO: str = str(_TABLE.get("rhino", ""))
SYNTAX_SUPPORTED: frozenset[str] = frozenset(k for k, v in _TABLE.get("syntax", {}).items() if v.get("supported"))


def methods_of(group: str | None) -> tuple[Method, ...]:
    return METHODS_BY_GROUP.get(group or "", ())


def props_of(group: str | None) -> frozenset[Key]:
    return PROPS_BY_GROUP.get(group or "", frozenset())


def builtin_named(name: str) -> Builtin:
    b = Builtins.get(attr_name(name))
    if b is None:
        raise KeyError(name)
    return b


# ------------------------------------------------------------------------------- pools (simplest first)
INTERESTING_INTEGERS: tuple[int, ...] = (
    0, 1, 2, 3, 4, 5, 7, 8, 10, 16, 64, 127, 128, 255, 256, 1000, 1024, 4096, 65535, 65536,
    2 ** 30, 2 ** 31 - 1, 2 ** 31, 2 ** 32 - 1, 2 ** 32, 2 ** 53 - 1, 2 ** 53,
    0x3FFFFFFF, 0x40000000, 0x7FFFFFFE, 0xBFFFFFFF, 0xC0000000, 0xFFFFFFFE,
)
INTERESTING_FLOATS: tuple[float, ...] = (
    0.0, 0.5, 1.0, 1.5, 0.1, 2.0, 3.0, 1e3, 1e6, 1e9, 1e12, 1e21, 1e-15, 5e-324, 2.2250738585072014e-308,
    1.7976931348623157e308, 4503599627370496.0, 9007199254740992.0,
)
INTERESTING_STRINGS: tuple[str, ...] = ("", "a", "0", "1", "-0", "10", "abc", "1e3", " 1", "x", "length", "undefined",
                                        "NaN", "true", "é", "😀", "\ud800", "\n", "\\", "\"", "'")
JS_TYPE_NAMES: tuple[str, ...] = ("undefined", "boolean", "number", "string", "symbol", "function", "object", "bigint")
INTERESTING_KEYS: tuple[Key, ...] = _INTERESTING_KEY_NAMES
ORDINARY_KEYS: tuple[Key, ...] = tuple(k for k in INTERESTING_KEYS if k.kind is KeyKind.ORDINARY)
INDEX_KEYS: tuple[Key, ...] = tuple(k for k in INTERESTING_KEYS if k.kind is KeyKind.INDEX)

# From Fuzzilli's interestingRegExps (JavaScriptEnvironment.swift:76-303), the part Rhino's engine parses;
# (pattern, flags the pattern is incompatible with). Characters are pattern text, not code.
from .ops import RegExpFlag as _RF  # noqa: E402

REGEXP_PATTERNS: tuple[tuple[str, _RF], ...] = (
    ("a", _RF.NONE), ("ab", _RF.NONE), ("a|b", _RF.NONE), ("a*", _RF.NONE), ("a+", _RF.NONE), ("a?", _RF.NONE),
    ("a{2}", _RF.NONE), ("a{1,3}", _RF.NONE), ("[abc]", _RF.NONE), ("[^abc]", _RF.NONE), ("[a-z]", _RF.NONE),
    ("\\d+", _RF.NONE), ("\\w*", _RF.NONE), ("\\s", _RF.NONE), (".", _RF.NONE), ("^a", _RF.NONE), ("a$", _RF.NONE),
    ("(a)(b)?", _RF.NONE), ("(?:ab)+", _RF.NONE), ("(a)\\1", _RF.NONE), ("(?<n>a)", _RF.NONE), ("(?=a)", _RF.NONE),
    ("(?!a)", _RF.NONE), ("(?<=a)b", _RF.NONE), ("(?<!a)b", _RF.NONE), ("\\b", _RF.NONE), ("a*?", _RF.NONE),
    ("[\\u0041-\\u005A]", _RF.NONE), ("\\u{1F600}", _RF.IGNORE_CASE),
)
REGEXP_QUANTIFIERS: tuple[str, ...] = ("*", "+", "?")

TYPED_ARRAY_CONSTRUCTORS: tuple[Builtin, ...] = tuple(Builtins[attr_name(n)] for n in _TYPED_ARRAYS if Builtins.get(attr_name(n)))
ERROR_CONSTRUCTORS: tuple[Builtin, ...] = tuple(Builtins[attr_name(n)] for n in _ERRORS if Builtins.get(attr_name(n)))
BUILTIN_POOL: tuple[Builtin, ...] = tuple(sorted((b for b in Builtins if b.name not in ("J", "__pause", "Continuation")),
                                                 key=lambda b: b.name))


def overlay_keys() -> frozenset[str]:
    return frozenset(k for k in set(RETURNS) | set(PARAMS) if k.split(".")[0] not in HAND_WRITTEN_RECEIVERS)


def table_method_keys() -> frozenset[str]:
    return frozenset(f"{m.receiver}.{m.name}" for m in Methods if m.receiver not in HAND_WRITTEN_RECEIVERS)
