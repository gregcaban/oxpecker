"""Coarse static types tracked for generated variables.

They only steer generation (which variable to pick for which slot); nothing checks them.
"""

from __future__ import annotations

from enum import Enum


class T(str, Enum):
    NUMBER = "number"
    INT32 = "int32"          # a number known to be a small integer
    BIGINT = "bigint"
    STRING = "string"
    BOOLEAN = "boolean"
    UNDEFINED = "undefined"
    NULL = "null"
    SYMBOL = "symbol"
    ARRAY = "array"
    OBJECT = "object"
    FUNCTION = "function"
    REGEXP = "regexp"
    TYPEDARRAY = "typedarray"
    MAP = "map"
    SET = "set"
    UNKNOWN = "unknown"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Variable name prefixes, so a program reads as `n1 + s2` rather than `v7 + v9`.
PREFIX: dict[T, str] = {
    T.NUMBER: "n", T.INT32: "i", T.BIGINT: "b", T.STRING: "s", T.BOOLEAN: "t",
    T.UNDEFINED: "u", T.NULL: "z", T.SYMBOL: "y", T.ARRAY: "a", T.OBJECT: "o",
    T.FUNCTION: "f", T.REGEXP: "r", T.TYPEDARRAY: "ta", T.MAP: "m", T.SET: "e",
    T.UNKNOWN: "x",
}

# Types that can be substituted when a generator asks for `want`.
_SUBSTITUTES: dict[T, frozenset[T]] = {
    T.NUMBER: frozenset({T.NUMBER, T.INT32}),
    T.INT32: frozenset({T.INT32}),
    T.OBJECT: frozenset({T.OBJECT, T.ARRAY, T.FUNCTION, T.REGEXP, T.MAP, T.SET, T.TYPEDARRAY}),
}


def compatible(have: T, want: T | None) -> bool:
    if want is None or want is T.UNKNOWN:
        return True
    return have in _SUBSTITUTES.get(want, frozenset({want}))


PRIMITIVES = (T.NUMBER, T.INT32, T.BIGINT, T.STRING, T.BOOLEAN, T.UNDEFINED, T.NULL, T.SYMBOL)
OBJECTS = (T.ARRAY, T.OBJECT, T.FUNCTION, T.REGEXP, T.TYPEDARRAY, T.MAP, T.SET)
