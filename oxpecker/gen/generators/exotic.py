"""Exotic objects and sloppy-mode constructs: Proxy, valueOf/toString/Symbol.toPrimitive
overrides, getters with side effects, `with`, direct eval."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal

VALUEOF_RESULTS = ["1", "2.5", '"3"', "NaN", "{}", "-0"]
TOSTRING_RESULTS = ['"4"', '"x"', "5", '""']
TOPRIMITIVE_RESULTS = ["1", '"2"', 'hint === "number" ? 3 : "s"', "true"]


@generator("proxy", family="exotic", weight=2, inputs=(T.OBJECT,), output=T.OBJECT)
def gen_proxy(b: ProgramBuilder, target: Var):
    """A Proxy with get/set/has traps that log into an array, then used like an object."""
    log = b.fresh(T.ARRAY, "[]")
    p = b.declare(T.OBJECT)
    b.emit(f"var {p} = new Proxy({target}, {{")
    b.indent += 1
    b.emit(f'get(t, k, r) {{ {log}.push("get:" + String(k)); return Reflect.get(t, k, r); }},')
    b.emit(f'set(t, k, v, r) {{ {log}.push("set:" + String(k)); return Reflect.set(t, k, v, r); }},')
    b.emit(f'has(t, k) {{ {log}.push("has:" + String(k)); return k in t; }},')
    b.indent -= 1
    b.emit("});")
    b.emit(f"{p}.{b.choice(['x', 'a', 'length'])} = {b.expr(T.UNKNOWN, literal)};")
    b.fresh(T.UNKNOWN, b.choice([f"{p}.x", f'"x" in {p}', f"{p}.x + 1", f"typeof {p}.a", f"Object.keys({p})"]))


@generator("to_primitive_override", family="exotic", weight=3, output=T.OBJECT)
def gen_to_primitive_override(b: ProgramBuilder):
    """An object with valueOf / toString / Symbol.toPrimitive, used in arithmetic, comparison
    and concatenation (the hint and call order are observable)."""
    log = b.fresh(T.ARRAY, "[]")
    o = b.declare(T.OBJECT)
    kind = b.rand_int(0, 2)
    b.emit(f"var {o} = {{")
    b.indent += 1
    if kind == 0:
        b.emit(f'valueOf() {{ {log}.push("valueOf"); return {b.choice(VALUEOF_RESULTS)}; }},')
        b.emit(f'toString() {{ {log}.push("toString"); return {b.choice(TOSTRING_RESULTS)}; }},')
    elif kind == 1:
        b.emit(f'[Symbol.toPrimitive](hint) {{ {log}.push(hint); return {b.choice(TOPRIMITIVE_RESULTS)}; }},')
    else:
        b.emit(f'valueOf() {{ {log}.push("valueOf"); return {{}}; }},')
        b.emit(f'toString() {{ {log}.push("toString"); return "9"; }},')
    b.indent -= 1
    b.emit("};")
    x = b.expr(T.NUMBER, literal)
    b.fresh(T.UNKNOWN, b.choice([f"{o} + {x}", f"{o} * {x}", f"{o} < {x}", f"{o} == {x}", f'{o} + ""', f"`${{{o}}}`",
                                 f"{o} - 0", f"+{o}", f"{x} + {o}", f"[{o}] + 1", f"{o} | 0", f"{o}++"]))


@generator("getter_side_effect", family="exotic", weight=3, output=T.OBJECT)
def gen_getter_side_effect(b: ProgramBuilder):
    """A getter that mutates another variable or the object itself when read, read twice."""
    counter = b.fresh(T.INT32, "0")
    o = b.declare(T.OBJECT)
    key = b.choice(["x", "0", "length"])
    self_ref = f"this.{key}" if key != "0" else "this[0]"
    effect = b.choice([f"{counter}++", f"delete {self_ref}", f"this.y = {counter}", f"Object.freeze(this)",
                       f"{counter} = \"s\""])
    b.emit(f"var {o} = {{ get {key}() {{ {effect}; return {counter}; }} }};")
    b.fresh(T.UNKNOWN, f"{o}.{key} + {o}.{key}" if key != "0" else f"{o}[0] + {o}[0]")
    b.fresh(T.UNKNOWN, b.choice([f"{o}.{key}", f"{counter}", f"Object.keys({o})", f"JSON.stringify({o})"])
            if key != "0" else f"{o}[0]")


@generator("with_statement", family="exotic", weight=2, sloppy_only=True, inputs=(T.OBJECT,))
def gen_with_statement(b: ProgramBuilder, obj: Var):
    """`with (obj) { ... }` resolving names against the object (forces activation)."""
    res = b.fresh(T.UNKNOWN, "0")
    b.depth += 1
    with b.block(f"with ({obj}) {{"):
        b.emit(f"{res} = typeof {b.choice(['x', 'a', 'length', 'toString', 'valueOf'])};")
        if b.maybe(0.5):
            b.emit(f"{b.choice(['x', 'a'])} = {b.expr(T.UNKNOWN, literal)};")
        b.run_some(1)
    b.depth -= 1
    b.fresh(T.UNKNOWN, f"[{res}, {obj}.x, {obj}.a]")


@generator("direct_eval", family="exotic", weight=2, sloppy_only=True)
def gen_direct_eval(b: ProgramBuilder):
    """Direct eval that reads and writes enclosing variables (forces activation)."""
    v = b.pick_or_make(T.NUMBER)
    code = b.choice([f"'{v} + 1'", f"'{v} = {v} * 2; {v}'", f"'var {v}; typeof {v}'", "'this'", f"'{v}++'",
                     f"'function g() {{ return {v}; }} g()'", "'1;;'"])
    b.fresh(T.UNKNOWN, f"eval({code})")


@generator("indirect_eval_function", family="exotic", weight=1)
def gen_indirect_eval_function(b: ProgramBuilder):
    """Indirect eval / new Function: compiled in the same mode as the enclosing script."""
    v = b.pick_or_make(T.NUMBER)
    form = b.choice([f"(0, eval)('{v} = 1; typeof {v}')", f"new Function('a', 'return a + 1')({v})",
                     f"Function('return this')() === this", f"new Function('return arguments.length')({v}, {v})"])
    b.fresh(T.UNKNOWN, form)


@generator("promise_then", family="exotic", weight=1)
def gen_promise_then(b: ProgramBuilder):
    """A resolved/rejected promise whose reactions run when the harness drains microtasks."""
    log = b.fresh(T.ARRAY, "[]")
    form = b.choice([f"Promise.resolve({b.expr(T.UNKNOWN, literal)})", f"Promise.reject({b.expr(T.UNKNOWN, literal)})",
                     f"new Promise(function (res) {{ res({b.expr(T.NUMBER, literal)}); }})"])
    b.emit(f'{form}.then(function (v) {{ {log}.push("ok:" + v); }}, function (e) {{ {log}.push("err:" + e); }});')
    b.emit(f'{log}.push("sync");')


@generator("string_methods_unicode", family="exotic", weight=2, output=T.STRING)
def gen_string_methods_unicode(b: ProgramBuilder):
    """String operations on unicode and surrogate halves (length, codePointAt, normalize, case)."""
    s = b.fresh(T.STRING, b.choice(['"\\ud83d\\ude00"', '"\\ud800"', '"ß"', '"İ"', '"Ⅻ"', '"\\u0130"', '"e\\u0301"']))
    b.fresh(T.UNKNOWN, b.choice([f"{s}.length", f"{s}.codePointAt(0)", f"{s}.toUpperCase()", f"{s}.toLowerCase()",
                                 f"{s}.normalize(\"NFD\").length", f"[...{s}].length", f"{s}.charCodeAt(1)",
                                 f"encodeURIComponent({s})", f"{s} < \"z\"", f"{s}.localeCompare(\"a\")"]))
