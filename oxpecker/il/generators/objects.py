# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/CodeGen/CodeGenerators.swift at commit be2811d00282320381fc8007d64873745772011d
# (object literals and members 861-1078, property/element/computed operations 1938-2170,
# destructuring 2493-2578, super 2585-2707, prototype/callback/proxy/hierarchy 3141-3251, 3274-3290,
# 3508-3556, 3837). Weights from CodeGeneratorWeights.swift.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Objects: literals and their members, properties, elements, computed properties, destructuring,
prototypes, proxies, and `super` inside object-literal methods (self-scaffolding)."""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import strategies as st

from ..builder import Builder, ObjectLiteralFrame
from ..environment import INDEX_KEYS, INTERESTING_KEYS, ORDINARY_KEYS, Builtins, Keys, Methods, props_of
from ..ops import BinaryOperator, Context, DeclarationKind, Parameters, PropertyFlag, PropertyType
from ..ops import ArrayElement, ArrayPattern, Binding, ObjectPattern, ObjectProperty
from ..program import Var
from ..strategies import assignable, generator, maybe, nested, pick, statements, syntax_ok, variable
from ..types import ANY, ARRAY, FUNCTION, NULLISH, NUMBER, OBJECT, Key, Type

TRAP_KEYS = (Keys.GET, Keys.SET, Keys.HAS, Key("deleteProperty"), Key("ownKeys"), Key("getOwnPropertyDescriptor"),
             Key("defineProperty"), Key("getPrototypeOf"), Key("setPrototypeOf"), Key("apply"), Key("construct"),
             Key("isExtensible"), Key("preventExtensions"))
KEY_POOL = st.sampled_from(INTERESTING_KEYS)
INDEX_POOL = st.integers(-2, 10)


def known_keys(b: Builder, obj: Var) -> list[Key]:
    t = b.type_of(obj)
    return sorted(t.props | props_of(t.group), key=lambda k: k.name)


@st.composite
def a_key(draw, b: Builder, obj: Var):
    """An existing key of the object with a drawn weight, else an interesting key."""
    keys = known_keys(b, obj)
    use_known = draw(maybe(0.5))
    if keys and use_known:
        return pick(draw, keys)
    return draw(KEY_POOL)


# ------------------------------------------------------------------ object literals and members
@generator("object_literal", family="objects", weight=10, block=True, produces=OBJECT, source="CodeGenerators.swift:861")
@st.composite
def object_literal(draw, b: Builder) -> Var:
    """An object literal whose members come from the member pool."""
    with b.object_literal() as o:
        draw(statements(b, 0, 5))
    return o.result


@generator("literal_property", family="members", weight=20, context=Context.OBJECT_LITERAL, source="CodeGenerators.swift:876")
@st.composite
def literal_property(draw, b: Builder) -> None:
    """`key: value` with a key not yet in the literal."""
    frame = b.current_literal
    candidates = [k for k in ORDINARY_KEYS if k not in frame.keys] or list(ORDINARY_KEYS)
    frame.add_property(pick(draw, candidates), draw(variable(b)))


@generator("literal_element", family="members", weight=5, context=Context.OBJECT_LITERAL, source="CodeGenerators.swift:887")
@st.composite
def literal_element(draw, b: Builder) -> None:
    """`0: value`."""
    frame = b.current_literal
    index = draw(st.integers(0, 10))
    while index in frame.elements:
        index += 1
    frame.add_element(index, draw(variable(b)))


@generator("literal_method", family="members", weight=5, context=Context.OBJECT_LITERAL, block=True, source="CodeGenerators.swift:925")
@st.composite
def literal_method(draw, b: Builder) -> None:
    """`key(params) {...}`, sometimes a generator method."""
    frame = b.current_literal
    candidates = [k for k in ORDINARY_KEYS if k not in frame.keys] or list(ORDINARY_KEYS)
    generator_method = draw(maybe(0.1)) and "generators" in b.options.toggles
    with frame.method(pick(draw, candidates), Parameters(draw(st.integers(0, 2))), generator=generator_method) as (this, args):
        draw(nested(b))
        if b.visible() and draw(maybe(0.8)):
            b.do_return(pick(draw, b.visible()))


@generator("literal_getter", family="members", weight=3, context=Context.OBJECT_LITERAL, block=True, source="CodeGenerators.swift:994")
@st.composite
def literal_getter(draw, b: Builder) -> None:
    """`get key() { ...; return v }`."""
    frame = b.current_literal
    candidates = [k for k in ORDINARY_KEYS if k not in frame.keys] or list(ORDINARY_KEYS)
    with frame.getter(pick(draw, candidates)) as this:
        draw(nested(b))
        b.do_return(pick(draw, b.visible()))


@generator("literal_setter", family="members", weight=3, context=Context.OBJECT_LITERAL, block=True, source="CodeGenerators.swift:1018")
@st.composite
def literal_setter(draw, b: Builder) -> None:
    """`set key(v) {...}`."""
    frame = b.current_literal
    candidates = [k for k in ORDINARY_KEYS if k not in frame.keys] or list(ORDINARY_KEYS)
    with frame.setter(pick(draw, candidates)) as (this, v):
        draw(nested(b))


@generator("literal_prototype", family="members", weight=5, context=Context.OBJECT_LITERAL, required=(OBJECT,),
           source="CodeGenerators.swift:917")
def literal_prototype(b: Builder, proto: Var) -> None:
    """`__proto__: obj`."""
    frame = b.current_literal
    if not frame.has_prototype:
        frame.set_prototype(proto)


# ------------------------------------------------------------------ properties
@generator("property_retrieval", family="properties", weight=20, inputs=(OBJECT,), source="CodeGenerators.swift:1938")
@st.composite
def property_retrieval(draw, b: Builder, obj: Var) -> Var:
    """`obj.key`, with `?.` when the receiver may be nullish."""
    optional = draw(maybe(0.5)) and b.type_of(obj).MayBe(NULLISH)
    return b.get_property(obj, draw(a_key(b, obj)), optional=optional)


@generator("property_assignment", family="properties", weight=20, inputs=(OBJECT,), source="CodeGenerators.swift:1961")
@st.composite
def property_assignment(draw, b: Builder, obj: Var) -> None:
    """`obj.key = value`."""
    b.set_property(obj, draw(a_key(b, obj)), draw(variable(b)))


@generator("property_update", family="properties", weight=10, inputs=(OBJECT,), source="CodeGenerators.swift:1983")
@st.composite
def property_update(draw, b: Builder, obj: Var) -> None:
    """`obj.key op= value`."""
    b.update_property(obj, draw(a_key(b, obj)), draw(variable(b, NUMBER)), draw(st.sampled_from(BinaryOperator)))


@generator("property_removal", family="properties", weight=5, inputs=(OBJECT,), source="CodeGenerators.swift:1999")
@st.composite
def property_removal(draw, b: Builder, obj: Var) -> Var:
    """`delete obj.key`."""
    return b.delete_property(obj, draw(a_key(b, obj)))


@generator("property_configuration", family="properties", weight=10, inputs=(OBJECT,), source="CodeGenerators.swift:2007")
@st.composite
def property_configuration(draw, b: Builder, obj: Var) -> None:
    """`Object.defineProperty(obj, key, {...})` as a value, a getter, a setter or both."""
    key = draw(a_key(b, obj))
    flags = draw(st.sampled_from(PROPERTY_FLAG_SETS))
    kind = draw(st.sampled_from(PropertyType))
    if kind is PropertyType.VALUE:
        b.configure_property(obj, key, flags, kind, draw(variable(b)))
        return
    fns = b.pool(FUNCTION).exact
    if not fns:
        return
    flags &= ~PropertyFlag.WRITABLE
    if kind is PropertyType.GETTER_SETTER:
        b.configure_property(obj, key, flags, kind, pick(draw, fns), pick(draw, fns))
    else:
        b.configure_property(obj, key, flags, kind, pick(draw, fns))


PROPERTY_FLAG_SETS = tuple(PropertyFlag(n) for n in range(8))


@generator("element_retrieval", family="properties", weight=20, inputs=(OBJECT,), source="CodeGenerators.swift:2051")
@st.composite
def element_retrieval(draw, b: Builder, obj: Var) -> Var:
    """`obj[i]`."""
    return b.get_element(obj, draw(INDEX_POOL))


@generator("element_assignment", family="properties", weight=20, inputs=(OBJECT,), source="CodeGenerators.swift:2058")
@st.composite
def element_assignment(draw, b: Builder, obj: Var) -> None:
    """`obj[i] = value`."""
    b.set_element(obj, draw(INDEX_POOL), draw(variable(b)))


@generator("element_update", family="properties", weight=7, inputs=(OBJECT,), source="CodeGenerators.swift:2066")
@st.composite
def element_update(draw, b: Builder, obj: Var) -> None:
    """`obj[i] op= value`."""
    b.update_element(obj, draw(INDEX_POOL), draw(variable(b, NUMBER)), draw(st.sampled_from(BinaryOperator)))


@generator("element_removal", family="properties", weight=5, inputs=(OBJECT,), source="CodeGenerators.swift:2078")
@st.composite
def element_removal(draw, b: Builder, obj: Var) -> Var:
    """`delete obj[i]`."""
    return b.delete_element(obj, draw(INDEX_POOL))


@generator("computed_property_retrieval", family="properties", weight=20, inputs=(OBJECT, ANY), source="CodeGenerators.swift:2120")
def computed_property_retrieval(b: Builder, obj: Var, key: Var) -> Var:
    """`obj[key]`."""
    return b.get_computed(obj, key)


@generator("computed_property_assignment", family="properties", weight=20, inputs=(OBJECT, ANY, ANY), source="CodeGenerators.swift:2128")
def computed_property_assignment(b: Builder, obj: Var, key: Var, value: Var) -> None:
    """`obj[key] = value`."""
    b.set_computed(obj, key, value)


@generator("computed_property_update", family="properties", weight=7, inputs=(OBJECT, ANY), source="CodeGenerators.swift:2137")
@st.composite
def computed_property_update(draw, b: Builder, obj: Var, key: Var) -> None:
    """`obj[key] op= value` (the compound and logical assignments on an element: a recorded Rhino bug area)."""
    b.update_computed(obj, key, draw(variable(b, NUMBER)), draw(st.sampled_from(BinaryOperator)))


@generator("computed_property_removal", family="properties", weight=5, inputs=(OBJECT, ANY), source="CodeGenerators.swift:2150")
def computed_property_removal(b: Builder, obj: Var, key: Var) -> Var:
    """`delete obj[key]`."""
    return b.delete_computed(obj, key)


@generator("prototype_access", family="properties", weight=10, inputs=(OBJECT,), source="CodeGenerators.swift:3159")
def prototype_access(b: Builder, obj: Var) -> Var:
    """`obj.__proto__`."""
    return b.get_property(obj, Keys.PROTO)


@generator("prototype_overwrite", family="properties", weight=10, inputs=(OBJECT, OBJECT), source="CodeGenerators.swift:3164")
@st.composite
def prototype_overwrite(draw, b: Builder, obj: Var, proto: Var) -> None:
    """`obj.__proto__ = proto` or `Object.setPrototypeOf(obj, proto)`."""
    if obj == proto:
        proto = b.construct(b.builtin(Builtins.OBJECT))
    if draw(maybe(0.5)):
        b.set_property(obj, Keys.PROTO, proto)
    else:
        b.call_method(b.builtin(Builtins.OBJECT), Methods.OBJECT_CTOR_SET_PROTOTYPE_OF, [obj, proto])


@generator("callback_property", family="properties", weight=10, inputs=(OBJECT, FUNCTION), source="CodeGenerators.swift:3171")
@st.composite
def callback_property(draw, b: Builder, obj: Var, fn: Var) -> None:
    """`obj.valueOf = fn` or `obj.toString = fn`."""
    b.set_property(obj, draw(st.sampled_from((Keys.VALUE_OF, Keys.TO_STRING))), fn)


@generator("length_change", family="properties", weight=5, inputs=(OBJECT,), source="CodeGenerators.swift:3274")
@st.composite
def length_change(draw, b: Builder, obj: Var) -> None:
    """`obj.length = n`."""
    b.set_property(obj, Keys.LENGTH, b.load_int(draw(st.sampled_from((0, 1, 2, 3, 10, 100, 2 ** 32 - 2)))))


@generator("object_integrity_level", family="properties", weight=1, inputs=(OBJECT,), source="CodeGenerators.swift:386")
@st.composite
def object_integrity_level(draw, b: Builder, obj: Var) -> None:
    """`Object.freeze / seal / preventExtensions (obj)`."""
    m = draw(st.sampled_from((Methods.OBJECT_CTOR_FREEZE, Methods.OBJECT_CTOR_SEAL, Methods.OBJECT_CTOR_PREVENT_EXTENSIONS)))
    b.call_method(b.builtin(Builtins.OBJECT), m, [obj])


@generator("object_static", family="properties", weight=4, inputs=(OBJECT,))
@st.composite
def object_static(draw, b: Builder, obj: Var) -> Var:
    """`Object.keys/values/entries/getOwnPropertyNames(obj)` or a JSON round trip."""
    kind = draw(st.integers(0, 4))
    o = b.builtin(Builtins.OBJECT)
    if kind == 4:
        json = b.builtin(Builtins.JSON)
        return b.call_method(json, Methods.JSON_PARSE, [b.call_method(json, Methods.JSON_STRINGIFY, [obj])])
    m = (Methods.OBJECT_CTOR_KEYS, Methods.OBJECT_CTOR_VALUES, Methods.OBJECT_CTOR_ENTRIES, Methods.OBJECT_CTOR_GET_OWN_PROPERTY_NAMES)[kind]
    return b.call_method(o, m, [obj])


# ------------------------------------------------------------------ destructuring
@generator("destruct_array", family="objects", weight=5, inputs=(ARRAY,), toggle="destructuring", source="CodeGenerators.swift:2493")
@st.composite
def destruct_array(draw, b: Builder, arr: Var) -> tuple[Var, ...]:
    """`var [a, , b, ...rest] = arr`."""
    elements = tuple(ArrayElement(Binding() if draw(maybe(0.7)) else None) for _ in range(draw(st.integers(1, 4))))
    rest = Binding() if syntax_ok("array-rest") and draw(maybe(0.3)) else None
    return b.destruct(arr, ArrayPattern(elements, rest), draw(st.sampled_from(DeclarationKind)))


@generator("destruct_object", family="objects", weight=5, inputs=(OBJECT,), toggle="destructuring", source="CodeGenerators.swift:2534")
@st.composite
def destruct_object(draw, b: Builder, obj: Var) -> tuple[Var, ...]:
    """`var {a, b, ...rest} = obj`."""
    keys = draw(st.lists(a_key(b, obj), min_size=1, max_size=3, unique=True))
    props = tuple(ObjectProperty(k, Binding()) for k in keys)
    return b.destruct(obj, ObjectPattern(props, has_rest=syntax_ok("object-rest") and draw(maybe(0.3))))


@generator("destruct_array_reassign", family="objects", weight=5, inputs=(ARRAY,), toggle="destructuring", source="CodeGenerators.swift:2513")
@st.composite
def destruct_array_reassign(draw, b: Builder, arr: Var) -> None:
    """`[a, b] = arr` into existing assignable variables."""
    targets = draw(st.lists(assignable(b), min_size=1, max_size=3))
    pattern = ArrayPattern(tuple(ArrayElement(Binding()) for _ in targets))
    b.destruct_and_reassign(arr, pattern, targets)


# ------------------------------------------------------------------ proxies, hierarchies
@generator("proxy", family="objects", weight=10, inputs=(OBJECT,), block=True, source="CodeGenerators.swift:3206")
@st.composite
def proxy(draw, b: Builder, target: Var) -> Var:
    """`new Proxy(target, handler)` with a handler of drawn traps bound to visible functions."""
    fns = b.pool(FUNCTION).exact
    traps = draw(st.lists(st.sampled_from(TRAP_KEYS), max_size=4, unique=True)) if fns else []
    with b.object_literal() as handler:
        for trap in traps:
            handler.add_property(trap, pick(draw, fns))
    return b.construct(b.builtin(Builtins.PROXY), [target, handler.result])


@generator("object_hierarchy", family="objects", weight=10, block=True, source="CodeGenerators.swift:3525")
@st.composite
def object_hierarchy(draw, b: Builder) -> None:
    """Four objects with nested property shapes {p0}, {p0,p1}, {p0,p1,p2}, {p0,p1,p3}."""
    keys = draw(st.lists(st.sampled_from(ORDINARY_KEYS), min_size=4, max_size=4, unique=True))
    objs = []
    for _ in range(4):
        with b.object_literal() as o:
            pass
        objs.append(o.result)
    shapes = ([0], [0, 1], [0, 1, 2], [0, 1, 3])
    for o, shape in zip(objs, shapes):
        for k in shape:
            b.set_property(o, keys[k], draw(variable(b)))


@generator("element_kind_change", family="properties", weight=5, inputs=(ARRAY,), source="CodeGenerators.swift:3290")
@st.composite
def element_kind_change(draw, b: Builder, arr: Var) -> None:
    """Store a value of another kind into an array element."""
    b.set_element(arr, draw(st.integers(0, 9)), draw(variable(b)))


# ------------------------------------------------------------------ super (scaffolding)
def _scaffold(draw, b: Builder, body) -> None:
    """Run `body` inside a method: directly if one is open, else inside an object literal with a
    prototype and one method, which is then called (design §7.2 scaffolding)."""
    if Context.METHOD in b.context:
        body()
        return
    proto_pool = b.pool(OBJECT).exact
    with b.object_literal() as o:
        if proto_pool:
            o.set_prototype(pick(draw, proto_pool))
        with o.method(Keys.M, Parameters(0)):
            body()
    b.call_computed_method(o.result, b.load_string(Keys.M.name), [])


@generator("super_property_retrieval", family="objects", weight=20, context=Context.METHOD, scaffolds=True, block=True,
           source="CodeGenerators.swift:2663")
@st.composite
def super_property_retrieval(draw, b: Builder) -> None:
    """`super.key` inside an object-literal method."""
    _scaffold(draw, b, lambda: b.get_super_property(draw(KEY_POOL)))


@generator("super_property_assignment", family="objects", weight=20, context=Context.METHOD, scaffolds=True, block=True,
           source="CodeGenerators.swift:2671")
@st.composite
def super_property_assignment(draw, b: Builder) -> None:
    """`super.key = value`."""
    _scaffold(draw, b, lambda: b.set_super_property(draw(KEY_POOL), draw(variable(b))))


@generator("super_property_update", family="objects", weight=10, context=Context.METHOD, scaffolds=True, block=True,
           source="CodeGenerators.swift:2700")
@st.composite
def super_property_update(draw, b: Builder) -> None:
    """`super.key op= value`."""
    _scaffold(draw, b, lambda: b.update_super_property(draw(KEY_POOL), draw(variable(b, NUMBER)), draw(st.sampled_from(BinaryOperator))))


@generator("computed_super_property", family="objects", weight=20, context=Context.METHOD, scaffolds=True, block=True,
           source="CodeGenerators.swift:2686")
@st.composite
def computed_super_property(draw, b: Builder) -> None:
    """`super[key]` and `super[key] = value`."""
    def body():
        key = draw(variable(b))
        if draw(st.booleans()):
            b.get_computed_super_property(key)
        else:
            b.set_computed_super_property(key, draw(variable(b)))
    _scaffold(draw, b, body)


@generator("super_method_call", family="objects", weight=20, context=Context.METHOD, scaffolds=True, block=True,
           source="CodeGenerators.swift:2585")
@st.composite
def super_method_call(draw, b: Builder) -> None:
    """`super.method(args)` for a method every prototype has (a drawn prototype rarely has `m`:
    the completion test showed "Cannot find function m")."""
    _scaffold(draw, b, lambda: b.call_super_method(draw(st.sampled_from((Keys.TO_STRING, Keys.VALUE_OF, Keys.HAS_OWN_PROPERTY))),
                                                    draw(st.lists(variable(b), max_size=2))))
