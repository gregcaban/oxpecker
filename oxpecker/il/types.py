# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/FuzzIL/TypeSystem.swift at commit be2811d00282320381fc8007d64873745772011d,
# simplified as described in docs/design-generation-layer.md §5.1 and §15.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Types: a set of possible kinds with an optional object group, signature, element type and
known keys. `Is` is subsumption, `MayBe` is intersection, `join` is union (over-approximate).
Property keys live here too, because a type's known keys are keys."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, Flag, auto


class Kind(Flag):
    UNDEFINED = auto()
    NULL = auto()
    BOOLEAN = auto()
    INTEGER = auto()
    FLOAT = auto()
    BIGINT = auto()
    STRING = auto()
    SYMBOL = auto()
    OBJECT = auto()
    FUNCTION = auto()


ALL_KINDS = Kind(0)
for _k in Kind:
    ALL_KINDS |= _k


class KeyKind(Enum):
    INDEX = "index"            # "0", "10": array element style keys
    LENGTH = "length"
    PROTO = "proto"            # "__proto__"
    WELL_KNOWN = "well-known"  # "constructor", "toString", "valueOf", "name", "prototype", ...
    ORDINARY = "ordinary"      # "a", "x", "foo"


@dataclass(frozen=True, slots=True)
class Key:
    """A property name with a class, so generators can ask for kinds of keys and the lifter
    decides between dot, index and quoted spellings. The name is data, never code."""
    name: str
    kind: KeyKind = KeyKind.ORDINARY

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a key needs a name")
        if self.kind is KeyKind.INDEX and not (self.name.isdigit() and (self.name == "0" or self.name[0] != "0")):
            raise ValueError(f"not an index key: {self.name!r}")
        if self.kind is KeyKind.LENGTH and self.name != "length":
            raise ValueError("the LENGTH kind is for 'length'")
        if self.kind is KeyKind.PROTO and self.name != "__proto__":
            raise ValueError("the PROTO kind is for '__proto__'")

    @property
    def is_identifier(self) -> bool:
        return _is_identifier(self.name)


def _is_identifier(name: str) -> bool:
    if not name or name[0].isdigit():
        return False
    return all(c.isalnum() or c in "_$" for c in name)


class ParamMode(Enum):
    PLAIN = "plain"
    OPT = "opt"
    REST = "rest"


@dataclass(frozen=True, slots=True)
class Param:
    type: Type
    mode: ParamMode = ParamMode.PLAIN


@dataclass(frozen=True, slots=True)
class Signature:
    """FuzzIL's Signature: parameters (plain, optional, rest; rest last) and a return type."""
    params: tuple[Param, ...]
    returns: Type

    def __post_init__(self) -> None:
        seen_opt = False
        for i, p in enumerate(self.params):
            if p.mode is ParamMode.REST and i != len(self.params) - 1:
                raise ValueError("a rest parameter must be last")
            if p.mode is ParamMode.OPT:
                seen_opt = True
            elif p.mode is ParamMode.PLAIN and seen_opt:
                raise ValueError("a plain parameter cannot follow an optional one")

    @property
    def min_arguments(self) -> int:
        return sum(1 for p in self.params if p.mode is ParamMode.PLAIN)

    @property
    def has_rest(self) -> bool:
        return bool(self.params) and self.params[-1].mode is ParamMode.REST

    def accepts(self, n: int) -> bool:
        if n < self.min_arguments:
            return False
        return self.has_rest or n <= len(self.params)


@dataclass(frozen=True, slots=True)
class Type:
    possible: Kind
    group: str | None = None
    signature: Signature | None = None
    element: Type | None = None
    props: frozenset[Key] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.possible:                       # NOTHING is unique: no group, no signature, no keys
            object.__setattr__(self, "group", None)
            object.__setattr__(self, "signature", None)
            object.__setattr__(self, "element", None)
            object.__setattr__(self, "props", frozenset())
        elif not isinstance(self.props, frozenset):
            object.__setattr__(self, "props", frozenset(self.props))

    # -- construction ---------------------------------------------------------------------
    @staticmethod
    def object(group: str | None = None, props: frozenset[Key] | tuple[Key, ...] = ()) -> Type:
        return Type(Kind.OBJECT, group=group, props=frozenset(props))

    @staticmethod
    def function(signature: Signature | None = None, group: str | None = None) -> Type:
        return Type(Kind.FUNCTION, group=group, signature=signature)

    @staticmethod
    def array(element: Type | None = None) -> Type:
        return Type(Kind.OBJECT, group="Array", element=element, props=frozenset({LENGTH_KEY}))

    # -- queries -----------------------------------------------------------------------------
    @property
    def is_nothing(self) -> bool:
        return not self.possible

    @property
    def is_union(self) -> bool:
        return len(list(k for k in Kind if k in self.possible)) > 1

    def Is(self, other: Type) -> bool:
        """Every value of this type is a value of `other` (subsumption; TypeSystem.swift:521-700).
        Group, signature, element and keys are "at least these" bounds on the other side."""
        if self.is_nothing:
            return True
        if other.is_nothing:
            return False
        if self.possible & ~other.possible:
            return False
        if other.group is not None and self.group != other.group:
            return False
        if other.signature is not None and self.signature != other.signature:
            return False
        if other.element is not None and (self.element is None or not self.element.Is(other.element)):
            return False
        return other.props <= self.props

    def MayBe(self, other: Type) -> bool:
        """Some value could be of both types (intersection; TypeSystem.swift:512)."""
        if not (self.possible & other.possible):
            return False
        if self.group is not None and other.group is not None and self.group != other.group:
            return False
        return True

    def MayNotBe(self, other: Type) -> bool:
        return not self.Is(other)

    def join(self, other: Type) -> Type:
        """Union: possible kinds united, everything else kept only when both agree
        (TypeSystem.swift:920-1098, without the definite set)."""
        if self.is_nothing:
            return other
        if other.is_nothing:
            return self
        element = None
        if self.element is not None and other.element is not None:
            element = self.element.join(other.element)
        return Type(
            self.possible | other.possible,
            group=self.group if self.group == other.group else None,
            signature=self.signature if self.signature == other.signature else None,
            element=element,
            props=self.props & other.props,
        )

    def with_prop(self, key: Key) -> Type:
        return Type(self.possible, self.group, self.signature, self.element, self.props | {key})

    def without_prop(self, key: Key) -> Type:
        return Type(self.possible, self.group, self.signature, self.element, self.props - {key})

    def with_signature(self, signature: Signature) -> Type:
        return Type(self.possible, self.group, signature, self.element, self.props)

    def with_element(self, element: Type) -> Type:
        return Type(self.possible, self.group, self.signature, element, self.props)

    def __str__(self) -> str:
        if not self.possible:
            return "nothing"
        if self.possible == ALL_KINDS and self.group is None:
            return "any"
        kinds = "|".join(k.name.lower() for k in Kind if k in self.possible)
        extra = []
        if self.group:
            extra.append(self.group)
        if self.signature:
            extra.append("sig")
        if self.element is not None:
            extra.append(f"of {self.element}")
        if self.props:
            extra.append("{" + ",".join(sorted(k.name for k in self.props)) + "}")
        return kinds + ("(" + " ".join(extra) + ")" if extra else "")


LENGTH_KEY = Key("length", KeyKind.LENGTH)

NOTHING = Type(Kind(0))
ANY = Type(ALL_KINDS)
UNDEFINED = Type(Kind.UNDEFINED)
NULL = Type(Kind.NULL)
NULLISH = Type(Kind.UNDEFINED | Kind.NULL)
BOOLEAN = Type(Kind.BOOLEAN)
INTEGER = Type(Kind.INTEGER)
FLOAT = Type(Kind.FLOAT)
NUMBER = Type(Kind.INTEGER | Kind.FLOAT)
BIGINT = Type(Kind.BIGINT)
STRING = Type(Kind.STRING)
SYMBOL = Type(Kind.SYMBOL)
PRIMITIVE = Type(Kind.BOOLEAN | Kind.INTEGER | Kind.FLOAT | Kind.STRING)
OBJECT = Type(Kind.OBJECT)
FUNCTION = Type(Kind.FUNCTION)
ARRAY = Type.array()
NUMERIC = Type(Kind.INTEGER | Kind.FLOAT | Kind.BIGINT)
