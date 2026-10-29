"""The type lattice: subsumption, intersection, union, and their laws."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from oxpecker.il.types import (
    ANY,
    ARRAY,
    BOOLEAN,
    FLOAT,
    FUNCTION,
    INTEGER,
    NOTHING,
    NULL,
    NULLISH,
    NUMBER,
    OBJECT,
    STRING,
    UNDEFINED,
    Key,
    KeyKind,
    Kind,
    Param,
    ParamMode,
    Signature,
    Type,
)
import pytest


def test_basic_subsumption():
    assert INTEGER.Is(NUMBER) and not NUMBER.Is(INTEGER)
    assert NUMBER.Is(ANY) and not ANY.Is(NUMBER)
    assert NOTHING.Is(INTEGER) and not INTEGER.Is(NOTHING)
    assert NULL.Is(NULLISH) and UNDEFINED.Is(NULLISH) and not NULL.Is(UNDEFINED)
    assert ARRAY.Is(OBJECT) and not OBJECT.Is(ARRAY)
    assert Type.object("Map").Is(OBJECT) and not OBJECT.Is(Type.object("Map"))
    assert not Type.object("Map").Is(Type.object("Set"))


def test_props_are_lower_bounds():
    k = Key("x")
    assert Type.object(props=(k,)).Is(OBJECT)
    assert not OBJECT.Is(Type.object(props=(k,)))
    assert OBJECT.with_prop(k).Is(Type.object(props=(k,)))
    assert not OBJECT.with_prop(k).without_prop(k).Is(Type.object(props=(k,)))


def test_maybe():
    assert NUMBER.MayBe(INTEGER) and INTEGER.MayBe(NUMBER)
    assert not STRING.MayBe(NUMBER)
    assert ANY.MayBe(NULLISH)
    assert not Type.object("Map").MayBe(Type.object("Set"))
    assert Type.object("Map").MayBe(OBJECT)
    assert NUMBER.MayNotBe(INTEGER) and not INTEGER.MayNotBe(NUMBER)


def test_join():
    assert INTEGER.join(FLOAT) == NUMBER
    assert INTEGER.join(STRING).possible == Kind.INTEGER | Kind.STRING
    assert NOTHING.join(STRING) == STRING and STRING.join(NOTHING) == STRING
    assert Type.object("Map").join(Type.object("Set")).group is None
    assert Type.object("Map").join(Type.object("Map")).group == "Map"
    a, b = Key("a"), Key("b")
    assert Type.object(props=(a, b)).join(Type.object(props=(a,))).props == frozenset({a})
    assert Type.array(INTEGER).join(Type.array(STRING)).element.possible == Kind.INTEGER | Kind.STRING
    assert Type.array(INTEGER).join(OBJECT).element is None


def test_signatures():
    s = Signature((Param(INTEGER), Param(STRING, ParamMode.OPT), Param(ANY, ParamMode.REST)), NUMBER)
    assert s.min_arguments == 1 and s.has_rest
    assert s.accepts(1) and s.accepts(10) and not s.accepts(0)
    assert not Signature((Param(INTEGER),), ANY).accepts(2)
    with pytest.raises(ValueError):
        Signature((Param(ANY, ParamMode.REST), Param(ANY)), ANY)
    with pytest.raises(ValueError):
        Signature((Param(ANY, ParamMode.OPT), Param(ANY)), ANY)
    f = Type.function(s)
    assert f.Is(FUNCTION) and not FUNCTION.Is(f)


def test_keys():
    assert Key("0", KeyKind.INDEX).is_identifier is False
    assert Key("abc").is_identifier and not Key("a-b").is_identifier
    with pytest.raises(ValueError):
        Key("01", KeyKind.INDEX)
    with pytest.raises(ValueError):
        Key("", KeyKind.ORDINARY)
    with pytest.raises(ValueError):
        Key("x", KeyKind.LENGTH)


def test_str():
    assert str(ANY) == "any" and str(NOTHING) == "nothing"
    assert str(NUMBER) == "integer|float"
    assert "Array" in str(ARRAY)


KINDS = st.sampled_from(list(Kind))
types = st.builds(
    lambda ks, g: Type(Kind(0) if not ks else __import__("functools").reduce(lambda a, b: a | b, ks), group=g),
    st.lists(KINDS, max_size=4), st.sampled_from([None, "A", "B"]),
)


@given(types, types, types)
def test_lattice_laws(a, b, c):
    assert a.Is(a)
    assert a.Is(a.join(b)) and b.Is(a.join(b))
    assert a.join(b) == b.join(a)
    assert a.join(b).join(c) == a.join(b.join(c))
    assert a.Is(ANY) and NOTHING.Is(a)
    if a.Is(b) and b.Is(a):
        assert a.possible == b.possible
    if a.Is(b) and not a.is_nothing:
        assert a.MayBe(b)
