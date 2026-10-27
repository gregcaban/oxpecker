"""Registries built from the capability table: guard test 7 (overlay agrees with the probe),
pools ordered simplest-first, host entries present."""

from __future__ import annotations

from oxpecker.il import environment as env
from oxpecker.il.environment import Builtin, Builtins, Keys, Method, Methods
from oxpecker.il.types import ANY, BIGINT, INTEGER, NUMBER, STRING, Key, KeyKind


def test_builtins_come_from_the_table_with_types():
    assert isinstance(Builtins.MAP, Builtin)
    assert Builtins.MAP.name == "Map" and Builtins.MAP.constructs is not None and Builtins.MAP.constructs.group == "Map"
    assert Builtins.INT32_ARRAY.constructs.group == "TypedArray"
    assert Builtins.MATH.type.group == "Math" and Builtins.MATH.constructs is None
    assert Builtins.NAN.type.Is(NUMBER) and Builtins.UNDEFINED.type.possible.name == "UNDEFINED"
    assert Builtins.PARSE_INT.calls is not None and Builtins.PARSE_INT.calls.Is(NUMBER)
    assert Builtins.STRING.calls is not None and Builtins.STRING.calls == STRING
    assert len(Builtins) > 50


def test_host_hooks_are_registered_by_name():
    assert Builtins.J.type.group == "J"
    assert Builtins.PAUSE.name == "__pause" and Builtins.PAUSE.calls == ANY
    assert Builtins.CONTINUATION.constructs is not None
    assert Builtins.EVAL.name == "eval"
    assert env.builtin_named("Map") is Builtins.MAP
    assert all(b.name not in ("J", "__pause", "Continuation") for b in env.BUILTIN_POOL)


def test_methods_from_prototypes_and_statics():
    assert isinstance(Methods.ARRAY_PUSH, Method)
    assert Methods.ARRAY_PUSH.receiver == "Array" and Methods.ARRAY_PUSH.returns == INTEGER
    assert Methods.MATH_IMUL.receiver == "Math" and Methods.MATH_IMUL.returns == INTEGER and Methods.MATH_IMUL.arity == 2
    assert Methods.ARRAY_CTOR_IS_ARRAY.receiver == "ArrayCtor"
    assert Methods.STRING_CHAR_CODE_AT.params == (NUMBER,)
    assert Methods.J_TYPE_OF.receiver == "J" and Methods.J_TYPE_OF.returns == STRING
    assert Methods.J_TAKES_BIG_INTEGER.params == (BIGINT,)
    assert Methods.REFLECT_GET.receiver == "Reflect"
    assert any(m.name == "push" for m in env.methods_of("Array"))
    assert Key("length", KeyKind.LENGTH) in env.props_of("Array") or Key("length", KeyKind.LENGTH) in Keys.__dict__.values()


def test_keys_have_kinds():
    assert Keys.LENGTH.kind is KeyKind.LENGTH
    assert Keys.PROTO.kind is KeyKind.PROTO and Keys.PROTO.name == "__proto__"
    assert Keys.INDEX_0.kind is KeyKind.INDEX
    assert Keys.TO_STRING.kind is KeyKind.WELL_KNOWN and Keys.CONSTRUCTOR.kind is KeyKind.WELL_KNOWN
    assert Keys.A.kind is KeyKind.ORDINARY and Keys.BANG.name == "!"
    assert all(k.kind is KeyKind.ORDINARY for k in env.ORDINARY_KEYS)
    assert all(k.kind is KeyKind.INDEX for k in env.INDEX_KEYS)


def test_overlay_agrees_with_the_probe_table():
    """Guard 7: every typed method in the overlay exists in Rhino's prototype/static tables."""
    missing = env.overlay_keys() - env.table_method_keys()
    assert not missing, f"overlay names methods the probe does not see: {sorted(missing)}"


def test_pools_are_ordered_simplest_first():
    assert env.INTERESTING_INTEGERS[0] == 0 and env.INTERESTING_INTEGERS[1] == 1
    assert env.INTERESTING_FLOATS[0] == 0.0
    assert env.INTERESTING_STRINGS[0] == ""
    assert all(v >= 0 for v in env.INTERESTING_INTEGERS) and all(v >= 0 for v in env.INTERESTING_FLOATS)
    assert env.REGEXP_PATTERNS[0][0] == "a"
    assert len(env.TYPED_ARRAY_CONSTRUCTORS) >= 10 and len(env.ERROR_CONSTRUCTORS) >= 7


def test_attr_names():
    assert env.attr_name("Int32Array") == "INT32_ARRAY"
    assert env.attr_name("decodeURIComponent") == "DECODE_URI_COMPONENT"
    assert env.attr_name("isNaN") == "IS_NAN"
    assert env.attr_name("Uint8ClampedArray") == "UINT8_CLAMPED_ARRAY"
    assert env.attr_name("charCodeAt") == "CHAR_CODE_AT"
    assert env.attr_name("URIError") == "URI_ERROR"


def test_syntax_support_is_read_from_the_table():
    assert "arrow" in env.SYNTAX_SUPPORTED and "class" not in env.SYNTAX_SUPPORTED
    assert env.PROBED_RHINO
