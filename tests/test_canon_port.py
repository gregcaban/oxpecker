"""JavaCanon (OXPECKER_CANON=java) renders exactly what canon.js renders, and reaches user code
(proxy traps, getters, poisoned toString, Object.prototype) in the same order.

Each program logs what user code saw into print() output; the whole outcome (trace, java types,
soft messages, output, completion, thrown) must be equal under js and java, and `both` must not
report a canon-mismatch. The big parity run over the regressions and generated programs is
scripts/canon_parity.py.
"""

from __future__ import annotations

import os

import pytest

from oxpecker import HARNESS_BIN
from oxpecker.harness.client import HarnessClient

VOLATILE = {"ms", "newProbes", "newProbeIds", "requiredReached", "hitProbes"}

PROGRAMS = {
    "empty": "1;",
    "primitives": "__probe('p', undefined, null, true, 1, -0, 0, NaN, 1.5, 1e21, 'a\"b\\n', 'é', '\\ud800', 10n, Symbol('d'), Symbol(), Symbol.for('k'), Symbol.iterator);",
    "long-string": "__probe('p', 'x'.repeat(1000), 'é'.repeat(500)); 'y'.repeat(401)",
    "arrays": "var a = [1,,3]; a.x = 1; a[Symbol('s')] = 2; var big = new Array(300).fill(1); Object.defineProperty(a, 1, {get(){return 1}, enumerable:true});"
              " __probe('p', a, big, [], Object.freeze([1,2]), [1,2,3].concat());",
    "objects": "var o = {a:1, get g(){return 1}, set s(v){}, [Symbol.toStringTag]: 'T'}; Object.defineProperty(o,'h',{value:2});"
               " Object.preventExtensions(o); __probe('p', o, Object.create(null), Object.create(o), {a:{b:{c:{d:{e:{f:{g:{h:{i:{j:1}}}}}}}}}});",
    "cycles": "var a = {}; a.self = a; var b = [a, a, {x: a}]; b.push(b); __probe('p', a, b);",
    "node-limit": "var a = []; for (var i = 0; i < 700; i++) a.push({i: i}); __probe('p', a);",
    "boxed-date-regexp": "__probe('p', new Number(1), new String('s'), new Boolean(false), Object(10n), new Date(0), new Date(NaN), /a+/gi, Object(Symbol('q')));",
    "functions": "function f(a, b) { return 1 } var g = f.bind(null); var h = (x) => x; function K() {} K.prototype.m = function () {}; __probe('p', f, g, h, K, Math.max, Object, new K, function*(){});",
    "bound-poisoned-length": "var log = []; var b = Math.max.bind(null); Object.defineProperty(b, 'length', {value: {valueOf(){ log.push('vo'); return {}; }, toString(){ log.push('ts'); return '5'; }}});"
                             " Object.defineProperty(b, 'name', {value: 7}); __probe('p', b); print(log.join());",
    "errors": "var e = new RangeError('boom'); e.extra = 1; function MyErr(s) { this.message = s; } MyErr.prototype = Object.create(Error.prototype); var m = new MyErr('mine');"
              " __probe('p', e, m, new TypeError(), Object.create(Error.prototype), new Error('x', {cause: e}));",
    "error-global-overwritten": "var E = Error; this.Error = undefined; __probe('p', new E('a'), Object.create(E.prototype)); this.Error = E;",
    "error-global-deleted": "var E = Error; delete this.Error; __probe('p', new E('a')); this.Error = E;",
    "error-global-getter": "var log = []; var E = Error; delete this.Error; Object.defineProperty(this, 'Error', {get(){ log.push('g'); return E; }, configurable: true});"
                           " __probe('p', {a: new E('a')}); print(log.join());",
    "object-prototype-excluded": "Object.prototype.foo = 1; Object.prototype.zzz = 2; var e = new Error('m'); e.foo = 5; e.bar = 6; __probe('p', e);",
    "object-prototype-get": "var log = []; Object.defineProperty(Object.prototype, 'get', {get(){ log.push('get'); return undefined; }, configurable: true});"
                            " __probe('p', {a: 1, b: [1, 2]}); print(log.join());",
    "object-prototype-value-writable": "var log = []; Object.defineProperty(Object.prototype, 'writable', {get(){ log.push('w'); return true; }, configurable: true});"
                                       " Object.defineProperty(Object.prototype, 'enumerable', {get(){ log.push('e'); return false; }, configurable: true});"
                                       " __probe('p', {a: 1}, [5]); print(log.join());",
    "proxy-traps": "var log = []; var h = {};"
                   " ['getPrototypeOf','setPrototypeOf','isExtensible','preventExtensions','getOwnPropertyDescriptor','defineProperty','has','get','set','deleteProperty','ownKeys']"
                   ".forEach(function (t) { h[t] = function () { log.push(t); return Reflect[t].apply(null, arguments); }; });"
                   " var p = new Proxy({a: 1}, h); var q = Object.create(p); var r = {n: p, m: [p]}; var f = function () {}; delete f.name; Object.setPrototypeOf(f, p);"
                   " __probe('p', p, q, r, f); print(log.join());",
    "proxy-of-function-array": "var log = []; var h = {get(t, k){ log.push(String(k)); return t[k]; }}; __probe('p', new Proxy(function(){}, h), new Proxy([1], h)); print(log.join());",
    "proxy-throwing-gpo": "var log = []; var p = new Proxy({}, {getPrototypeOf(){ log.push('gpo'); throw {toString(){ log.push('ts'); return 'x'; }}; }});"
                          " __probe('p', Object.create(p)); print(log.join());",
    "revoked-proxy": "var r = Proxy.revocable({}, {}); r.revoke(); __probe('p', r.proxy, {a: r.proxy}, Object.create(r.proxy));",
    "tostringtag-throws": "var log = []; var o = {get [Symbol.toStringTag]() { log.push('tag'); throw new Error('no'); }}; __probe('p', o); print(log.join());",
    "tostringtag-weird": "__probe('p', {[Symbol.toStringTag]: 'Number'}, {[Symbol.toStringTag]: 'Map'}, {[Symbol.toStringTag]: 'Error'}, {[Symbol.toStringTag]: 'Date'});",
    "maps-sets": "var m = new Map([[1, 'a'], [{k: 1}, new Set([1, 2])], [NaN, m2 = new Map()]]); var s = new Set(['x', [1]]); m.set(m, m); __probe('p', m, s, new WeakMap(), new WeakSet());",
    "map-subclass-poisoned": "var log = []; var m = new Map([[1, 2]]); Object.defineProperty(m, 'size', {get() { log.push('size'); return 1; }}); m.forEach = function () { log.push('fe'); }; __probe('p', m); print(log.join());",
    "typed-arrays": "__probe('p', new Int8Array([1, -2, 3]), new Float64Array([0.5, NaN, -0]), new Uint8Array(300), new BigInt64Array(2), new ArrayBuffer(4), new DataView(new ArrayBuffer(2)), new Uint8ClampedArray(2));",
    "symbols": "var s = Symbol('d'); var o = {[s]: 1, [Symbol()]: 2, [Symbol.for('r')]: 3, [Symbol.iterator]: 4, [Symbol.hasInstance]: 5}; __probe('p', o, s);",
    "symbol-description-poisoned": "var log = []; var d = Object.getOwnPropertyDescriptor(Symbol.prototype, 'description'); Object.defineProperty(Symbol.prototype, 'description', {get(){ log.push('d'); return 'zz'; }, configurable: true});"
                                   " Symbol.keyFor = function () { log.push('kf'); }; JSON.stringify = function () { log.push('js'); return 'S'; };"
                                   " Object.getOwnPropertyDescriptor = function () { log.push('gopd'); }; Reflect.ownKeys = function () { log.push('ok'); return []; };"
                                   " __probe('p', {[Symbol('a')]: 1, k: [1, 2], s: 'x'.repeat(500)}); print(log.join());",
    "generators-promise": "function* g() { yield 1; } var it = g(); __probe('p', it, Promise.resolve(1), Object.getPrototypeOf(it), Object.getPrototypeOf(Object.getPrototypeOf(it)), [].values(), new Map().entries());",
    "builtin-protos": "__probe('p', Object.prototype, Array.prototype, Function.prototype, Map.prototype, Int8Array.prototype, Object.getPrototypeOf(Int8Array.prototype), Error.prototype, TypeError.prototype, globalThis, this);",
    "proto-unnamed": "function A() {} var a = new A(); var p = A.prototype; __probe('p', a, p, a); __probe('q', a, Object.create({x: 1}));",
    "arguments": "function f() { return arguments; } __probe('p', f(1, 2), (function () { 'use strict'; return arguments; })(1));",
    "array-like-exotic": "var a = [1, 2]; a.length = 5; a['01'] = 1; a['4294967295'] = 1; a[-1] = 2; a[2.5] = 3; __probe('p', a);",
    "getter-throws": "var o = {get a() { throw new Error('x'); }}; __probe('p', o);",
    "completion-throw": "throw {a: new Error('e'), b: [1]};",
    "completion-throw-error": "null.x;",
    "completion-throw-proxy-name": "throw new Proxy({}, {get(){ throw 1; }});",
    "completion-value": "({a: [1, 2, new Date(5)]})",
    "java": "__probe('p', J, J.toString(), typeof J, [J], {j: J});",
    "string-keys-numeric": "__probe('p', {'1': 1, 'a b': 2, '\\u2028': 3, 'é': 4, '': 5, '__proto__': 6});",
    "constructed": "function A() { this.x = 3; } Object.defineProperty(A.prototype, 'y', {get() { return 1; }}); __probe('p', new A(), A, A.prototype);",
    "label-and-with": "var o = {a: 1}; with (o) { __probe('p', a, o); }",
    "weird-numbers": "__probe('p', 1e-7, 123456789012345680000, 0.1 + 0.2, -1e-7, 2 ** 53, 5e-324, 1 / 3, 255, 1 << 31);",
}


@pytest.fixture(scope="module")
def clients():
    if not HARNESS_BIN.exists():
        pytest.skip("harness not built: cd harness-java && ./gradlew fatJar")
    started: dict[str, HarnessClient] = {}
    saved = os.environ.get("OXPECKER_CANON")
    try:
        for canon in ("js", "java", "both"):
            os.environ["OXPECKER_CANON"] = canon
            c = HarnessClient(quiet=True)
            c.start()
            started[canon] = c
    finally:
        if saved is None:
            os.environ.pop("OXPECKER_CANON", None)
        else:
            os.environ["OXPECKER_CANON"] = saved
    for canon, c in started.items():
        assert c.info()["canon"] == canon
    yield started
    for c in started.values():
        c.stop()


def comparable(o) -> dict:
    return {k: v for k, v in o.to_json().items() if k not in VOLATILE}


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_java_canon_matches_js_canon(clients, name):
    src = PROGRAMS[name]
    # canon.js is interpreted and spends the instruction budget (700 nodes exhaust 5M), JavaCanon
    # does not: a big budget keeps the comparison about the rendering.
    kw = dict(source_name=name + ".js", instruction_budget=500_000_000, wall_ms=30000)
    js = clients["js"].run(src, **kw)
    java = clients["java"].run(src, **kw)
    both = clients["both"].run(src, **kw)
    for mode in js.results:
        assert comparable(js.results[mode]) == comparable(java.results[mode]), (name, mode)
        mism = [s for s in both.results[mode].soft if s.startswith("canon-mismatch:")]
        assert not mism, (name, mode, mism[0][:500])
    # the program really rendered something (not a syntax error that makes both sides trivially equal)
    assert any(o.status in ("ok", "throw") for o in js.results.values()), (name, js.results["interpreted"].status, js.results["interpreted"].thrown, js.results["interpreted"].reason,
                java.results["interpreted"].status, java.results["interpreted"].reason, java.results["interpreted"].ms,
                java.results["interpreted"].engineException)


PATCH = ("var __join = Array.prototype.join; Array.prototype.join = function () { return 'J'; };"
         " var __slice = String.prototype.slice; String.prototype.slice = function () { return 'SL'; };"
         " Array.prototype.push = function () { return 0; }; Array.prototype.indexOf = function () { return 0; };"
         " String.prototype.indexOf = function () { return 0; };")
TARGET = "__probe('p', {k: [1, 2], s: 'x'.repeat(500), f: Math.max, e: new Error('m')});"


def test_java_canon_ignores_patched_array_and_string_methods(clients):
    """canon.js calls push/join/slice/indexOf of the program's own Array and String prototypes (it
    captures the object-model builtins only); the Java port does not, so patching them changes
    nothing in its rendering. A deliberate difference, kept visible here."""
    kw = dict(instruction_budget=500_000_000, wall_ms=30000)
    clean = clients["java"].run(TARGET, source_name="clean.js", **kw)
    patched = clients["java"].run(PATCH + TARGET, source_name="clean.js", **kw)
    for mode in clean.results:
        assert clean.results[mode].trace == patched.results[mode].trace
        assert clean.results[mode].trace and "SL" not in clean.results[mode].trace[0]
