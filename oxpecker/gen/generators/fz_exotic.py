"""Ported from Fuzzilli's CodeGenerators.swift: array element-kind/length transitions, objects
that imitate primitives/arrays/iterables, and `super` inside object-literal methods / generator
functions. Rhino has a NativeArrayLinker and dense/sparse array representations, so the element-
kind and length generators lean on that: far-beyond-length writes, length shrink, holes, and
non-index keys (negative/fractional) all force representation changes that interpreted and
compiled mode might disagree on."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal

FAMILY = "exotic-arrays"


@generator("element_kind_change", family=FAMILY, weight=4)
def gen_element_kind_change(b: ProgramBuilder):
    """An array created with one element kind (small ints, doubles, or mixed/holey) is read by a
    loop function, then transitioned to another kind/shape by assignment, push or splice --
    including a far-beyond-length write, a length shrink, a deleted hole, and a negative or
    fractional index (none of which are real array indices)."""
    kind = b.choice(["ints", "doubles", "mixed"])
    lit = {"ints": "[1, 2, 3, 4, 5]", "doubles": "[1.5, 2.5, 3.5, 4.5, 5.5]",
           "mixed": '[1, "a", {}, , 5]'}[kind]
    a = b.fresh(T.ARRAY, lit)
    log = b.fresh(T.ARRAY, "[]")
    f = b.declare(T.FUNCTION)
    with b.block(f"function {f}(arr) {{", kind="function"):
        b.scopes[-1].vars.append(Var("arr", T.ARRAY))
        acc = b.declare(T.ARRAY)
        b.emit(f"var {acc} = [];")
        with b.block("for (var i = 0; i < arr.length; i++) {"):
            b.emit(f"{acc}.push(arr[i]);")
        b.emit(f"return [{acc}, arr.length];")
    b.emit(f"{log}.push(JSON.stringify({f}({a})));")
    change = b.choice([
        f"{a}[{b.rand_int(0, 4)}] = {literal(b, T.NUMBER)};",
        f'{a}[1] = "x";',
        f"{a}.push({{}});",
        f"{a}.splice(1, 1, 1.5, 'q');",
        f"{a}[20] = 1;    // far beyond length: dense -> sparse",
        f"{a}.length = {b.rand_int(0, 2)};    // shrink",
        f"delete {a}[1];    // leaves a hole",
        f"{a}[-1] = 1;    // negative: not an array index",
        f"{a}[1.5] = 1;    // fractional: not an array index",
    ])
    b.emit(change)
    b.emit(f"{log}.push(JSON.stringify({f}({a})));")


@generator("length_change", family=FAMILY, weight=3)
def gen_length_change(b: ProgramBuilder):
    """.length is set larger, smaller or to an invalid value on either a real array or a plain
    array-like object; arrays truncate/holeify on shrink while plain objects just store an
    ordinary 'length' property, which this compares."""
    log = b.fresh(T.ARRAY, "[]")
    if b.maybe(0.5):
        o = b.pick_or_make(T.ARRAY)
    else:
        o = b.fresh(T.OBJECT, '{0: 1, 1: "a", 2: {}, length: 3}')
    new_len = b.choice(["0", "1", str(b.rand_int(2, 8)), "100", "-1", "1.5", "NaN", '"3"',
                        "4294967295", "4294967296"])
    tb = b.try_block()
    with tb.body():
        b.emit(f"{o}.length = {new_len};")
        b.emit(f"{log}.push([{o}.length, {o}[0], {o}[1], {o}[2]]);")
    with tb.catch("e"):
        b.emit(f"{log}.push('threw:' + e.name);")
    tb.end()


@generator("imitation", family=FAMILY, weight=4)
def gen_imitation(b: ProgramBuilder):
    """An object imitating a primitive (valueOf / Symbol.toPrimitive) or an array (length +
    indices + Symbol.iterator), then used wherever the imitated value would be: arithmetic,
    Array.prototype methods via .call, and spread (if the 'spread' toggle is enabled)."""
    log = b.fresh(T.ARRAY, "[]")
    tb = b.try_block()
    with tb.body():
        if b.maybe(0.5):
            orig = b.expr(T.NUMBER, literal)
            o = b.declare(T.OBJECT)
            if b.maybe(0.5):
                b.emit(f"var {o} = {{ valueOf() {{ {log}.push('valueOf'); return {orig}; }} }};")
            else:
                b.emit(f"var {o} = {{ [Symbol.toPrimitive](hint) {{ {log}.push(hint); return {orig}; }} }};")
            b.emit(f"{log}.push({o} + 1, {o} * 2, {o} == {orig});")
        else:
            items = [literal(b, T.NUMBER) for _ in range(b.rand_int(2, 4))]
            n = len(items)
            elems = ", ".join(f"{i}: {v}" for i, v in enumerate(items))
            o = b.declare(T.OBJECT)
            b.emit(f"var {o} = {{ length: {n}, {elems},")
            b.indent += 1
            b.emit("[Symbol.iterator]: function () { var idx = 0, self = this; "
                   "return { next: function () { "
                   "return idx < self.length ? {value: self[idx++], done: false} : {value: undefined, done: true}; } }; }")
            b.indent -= 1
            b.emit("};")
            b.emit(f"{log}.push(Array.prototype.join.call({o}, ','));")
            b.emit(f"{log}.push(Array.prototype.slice.call({o}, 1));")
            if "spread" in b.opts.toggles and b.maybe(0.6):
                b.emit(f"{log}.push([...{o}]);")
    with tb.catch("e"):
        b.emit(f"{log}.push('threw:' + e.name);")
    tb.end()


@generator("custom_iterator", family=FAMILY, weight=4, output=T.OBJECT)
def gen_custom_iterator(b: ProgramBuilder):
    """An object implementing Symbol.iterator whose next() returns a drawn {value, done}
    sequence, consumed by for-of (toggle 'for-of'), Array.from, spread (toggle 'spread') or
    manual next() calls; return() logs being invoked on an early break."""
    log = b.fresh(T.ARRAY, "[]")
    n = b.rand_int(2, 5)
    vals = [literal(b, T.NUMBER) for _ in range(n)]
    it = b.declare(T.OBJECT)
    b.emit(f"var {it} = {{")
    b.indent += 1
    b.emit("[Symbol.iterator]: function () {")
    b.indent += 1
    b.emit(f"var i = 0; var vals = [{', '.join(vals)}];")
    b.emit("return {")
    b.indent += 1
    b.emit("next: function () { return i < vals.length ? {value: vals[i++], done: false} : {value: undefined, done: true}; },")
    b.emit(f"return: function (v) {{ {log}.push('return:' + v); return {{value: v, done: true}}; }}")
    b.indent -= 1
    b.emit("};")
    b.indent -= 1
    b.emit("}")
    b.indent -= 1
    b.emit("};")

    modes = ["array_from", "manual_next"]
    if "for-of" in b.opts.toggles:
        modes.append("for_of")
    if "spread" in b.opts.toggles:
        modes.append("spread")
    mode = b.choice(modes)
    if mode == "for_of":
        with b.block(f"for (var v of {it}) {{"):
            b.emit(f"{log}.push(v);")
            if b.maybe(0.4):
                b.emit("break;")
    elif mode == "spread":
        b.emit(f"{log}.push([...{it}]);")
    elif mode == "array_from":
        b.emit(f"{log}.push(Array.from({it}));")
    else:
        itr = b.fresh(T.OBJECT, f"{it}[Symbol.iterator]()")
        with b.for_loop(b.rand_int(1, n + 1)):
            b.emit(f"{log}.push(JSON.stringify({itr}.next()));")


@generator("super_method_call", family=FAMILY, weight=3)
def gen_super_method_call(b: ProgramBuilder):
    """An object literal with __proto__ whose method calls super.method(args); the call is also
    repeated via .call/.apply with a different `this` and argument count."""
    proto = b.fresh(T.OBJECT, f"{{ tag: {literal(b, T.NUMBER)}, m(x, y) {{ return [this.tag, x, y]; }} }}")
    o = b.declare(T.OBJECT)
    b.emit(f"var {o} = {{")
    b.indent += 1
    b.emit(f"__proto__: {proto},")
    b.emit(f"tag: {literal(b, T.STRING)},")
    b.emit("m(x, y) { return super.m(x, y); }")
    b.indent -= 1
    b.emit("};")
    a1, a2 = b.expr(T.NUMBER, literal), b.expr(T.NUMBER, literal)
    b.fresh(T.ARRAY, f"{o}.m({a1}, {a2})")
    other_this = b.choice(["{tag: 'borrowed'}", "undefined", "null", str(o)])
    b.fresh(T.UNKNOWN, b.choice([f"{proto}.m.call({other_this}, {a1})", f"{proto}.m.apply({o}, [{a1}, {a2}, {a2}])"]))


@generator("super_property_get", family=FAMILY, weight=3)
def gen_super_property_get(b: ProgramBuilder):
    """super.x / super[expr] read from an object-literal method, where the prototype property is
    a getter that observes `this` (must be the receiving object, not the prototype)."""
    log = b.fresh(T.ARRAY, "[]")
    key = b.choice(["x", "y"])
    proto = b.fresh(T.OBJECT, f"{{ get {key}() {{ {log}.push(this.tag); return this.tag * 2; }}, z: 9 }}")
    o = b.declare(T.OBJECT)
    access = b.choice([f"super.{key}", f'super["{key}"]', "super[k]"])
    b.emit(f"var {o} = {{")
    b.indent += 1
    b.emit(f"__proto__: {proto},")
    b.emit(f"tag: {b.choice(['1', '2', '\"s\"'])},")
    b.emit(f"get(k) {{ return [{access}, super.z]; }}")
    b.indent -= 1
    b.emit("};")
    b.fresh(T.ARRAY, f"{o}.get({key!r})")
    b.fresh(T.ARRAY, f"[{log}.length, {o}.tag]")


@generator("super_property_set", family=FAMILY, weight=3)
def gen_super_property_set(b: ProgramBuilder):
    """super.x = v: when the prototype property is a plain data property this must create an
    own property on `this` (not overwrite the prototype's); when it is an accessor, the
    prototype's setter runs instead. Both `this` and the prototype are observed afterwards."""
    log = b.fresh(T.ARRAY, "[]")
    if b.maybe(0.5):
        proto = b.fresh(T.OBJECT, f"{{ get x() {{ return 'proto-get'; }}, set x(v) {{ {log}.push('proto-set:' + v); }} }}")
    else:
        proto = b.fresh(T.OBJECT, "{ x: 'proto-data' }")
    o = b.declare(T.OBJECT)
    b.emit(f"var {o} = {{")
    b.indent += 1
    b.emit(f"__proto__: {proto},")
    b.emit("set(v) { super.x = v; }")
    b.indent -= 1
    b.emit("};")
    v = b.expr(T.NUMBER, literal)
    b.emit(f"{o}.set({v});")
    b.fresh(T.ARRAY, f"[{o}.hasOwnProperty('x'), {o}.x, {proto}.x]")


@generator("super_property_update", family=FAMILY, weight=3)
def gen_super_property_update(b: ProgramBuilder):
    """super.x += / ++ / -- : read via [[Get]] on the prototype chain, write via [[Set]] with
    `this` as the receiver, with numeric and string operands."""
    proto = b.fresh(T.OBJECT, f"{{ x: {b.choice(['1', '2.5', '\"a\"'])} }}")
    o = b.declare(T.OBJECT)
    op = b.choice(["super.x += 1;", 'super.x += "z";', "super.x++;", "super.x--;", "super.x -= 2;"])
    b.emit(f"var {o} = {{")
    b.indent += 1
    b.emit(f"__proto__: {proto},")
    b.emit(f"upd() {{ {op} return this.x; }}")
    b.indent -= 1
    b.emit("};")
    log = b.fresh(T.ARRAY, f"[{o}.upd()]")
    b.emit(f"{log}.push([{o}.x, {proto}.x]);")


@generator("yield_patterns", family=FAMILY, weight=3, toggle="generators")
def gen_yield_patterns(b: ProgramBuilder):
    """yield* delegation into an array and into another generator, a generator's return value,
    yield inside try/finally, and driving next() with arguments that come back as the result of
    the previous yield."""
    log = b.fresh(T.ARRAY, "[]")
    inner = b.declare(T.FUNCTION)
    with b.block(f"function* {inner}() {{", kind="function"):
        b.emit(f"yield {literal(b, T.NUMBER)};")
        b.emit(f"yield {literal(b, T.NUMBER)};")
        b.emit(f"return {literal(b, T.STRING)};")
    g = b.declare(T.FUNCTION)
    with b.block(f"function* {g}() {{", kind="function"):
        shape = b.rand_int(0, 2)
        if shape == 0:
            b.emit(f"var r = yield* [{literal(b, T.NUMBER)}, {literal(b, T.NUMBER)}];")
            b.emit("return r;")
        elif shape == 1:
            b.emit(f"var r = yield* {inner}();")
            b.emit("return r;")
        else:
            b.emit(f"try {{ var va = yield 1; var vb = yield va; return va + vb; }} finally {{ {log}.push('finally'); }}")
    it = b.fresh(T.OBJECT, f"{g}()")
    for i in range(b.rand_int(2, 4)):
        arg = literal(b, T.NUMBER) if i else ""
        b.emit(f"{log}.push(JSON.stringify({it}.next({arg})));")
