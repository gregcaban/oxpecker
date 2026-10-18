"""Compiler-targeted generators: what Rhino's bytecode compiler does differently from the
interpreter (number-type inference, activation, direct calls, invokedynamic linkers, ConsString,
completion values, super in object literals). These get extra weight."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import INT32S, NUMBERS, literal

PERTURB = ['"7"', '"abc"', "undefined", "null", "{valueOf() { return 3; }}", "[2]", "true", "10n", "NaN", "-0",
           "2147483648", "0.5", "{toString() { return \"4\"; }}", "Symbol.iterator"]


def numeric_body(b: ProgramBuilder, params: list[Var], nlocals: int) -> list[Var]:
    """Locals that are provably numeric to the optimizer, plus arithmetic over them."""
    locals_: list[Var] = []
    for _ in range(nlocals):
        v = b.declare(T.NUMBER)
        b.emit(f"var {v} = {b.choice(INT32S + ['0', '1', '1.5', '2147483647'])};")
        locals_.append(v)
    pool = locals_ + params
    for _ in range(b.rand_int(1, 5)):
        t = b.choice(locals_)
        x, y = b.choice(pool), b.choice(pool)
        form = b.choice([f"{t} = {x} + {y};", f"{t} = {x} * {y};", f"{t} = {x} - {y};", f"{t} = {x} / {y};",
                         f"{t} = {x} % {y};", f"{t}++;", f"{t} += {x};", f"{t} = {x} | 0;", f"{t} = {x} >>> 0;",
                         f"{t} = ~{x};", f"{t} = {x} << 1;", f"{t} = {x} == {y} ? 1 : 0;", f"{t} = -{x};",
                         f"{t} = {x} ** 2;", f"{t} = Math.imul({x}, {y});", f"{t} = {x} < {y} ? {x} : {y};"])
        b.emit(form)
    return locals_


@generator("numeric_local_type_flip", family="compiler-targeted", weight=6, output=T.FUNCTION)
def gen_numeric_local_type_flip(b: ProgramBuilder):
    """Numeric-only function locals (optimizer infers number types), then one local is switched
    to a non-number late in the function, and the function is called with ints then doubles."""
    with b.define_function(2, param_types=[T.NUMBER, T.NUMBER]) as (f, params):
        locals_ = numeric_body(b, params, b.rand_int(1, 3))
        if b.maybe(0.8):
            victim = b.choice(locals_)
            b.emit(f"{victim} = {b.choice(PERTURB)};    // type perturbation")
            b.emit(f"{b.choice(locals_)} = {victim} + {b.choice(locals_ + params)};")
        b.emit(f"return {' + '.join(str(v) for v in locals_)};")
    b.fresh(T.NUMBER, f"{f}({b.choice(INT32S)}, {b.choice(INT32S)})")
    b.fresh(T.NUMBER, f"{f}({b.choice(NUMBERS)}, {b.choice(NUMBERS)})")
    b.fresh(T.NUMBER, f"{f}({b.choice(PERTURB)}, {b.choice(INT32S)})")


@generator("activation_pair", family="compiler-targeted", weight=5)
def gen_activation_pair(b: ProgramBuilder):
    """The same numeric body twice: once plain (optimizer runs) and once with an `arguments`
    read / eval / with inserted, which forces activation and disables the optimizer. Both results
    are compared."""
    with b.capture() as body:
        params = [Var("a0", T.NUMBER), Var("a1", T.NUMBER)]
        b.scopes.append(type(b.scopes[-1])("function", list(params)))
        b.indent += 1
        locals_ = numeric_body(b, params, 2)
        b.emit(f"return {locals_[0]} + {locals_[1]};")
        b.indent -= 1
        b.scopes.pop()
    fa = b.declare(T.FUNCTION)
    fb = b.declare(T.FUNCTION)
    activation = "var z = arguments.length;" if b.opts.strict else b.choice(
        ["var z = arguments.length;", "eval('1');", "with ({}) { }", "var z = arguments[0];"])
    b.emit(f"function {fa}(a0, a1) {{")
    b.emit_lines(body)
    b.emit("}")
    b.emit(f"function {fb}(a0, a1) {{")
    b.indent += 1
    b.emit(f"{activation}    // forces activation: optimizer skipped")
    b.indent -= 1
    b.emit_lines(body)
    b.emit("}")
    x, y = b.choice(NUMBERS + INT32S), b.choice(NUMBERS + INT32S + PERTURB)
    b.fresh(T.ARRAY, f"[{fa}({x}, {y}), {fb}({x}, {y})]")


@generator("direct_call_arity", family="compiler-targeted", weight=5)
def gen_direct_call_arity(b: ProgramBuilder):
    """A top-level function called directly with matching, fewer and extra arguments, then
    redefined between calls (the compiler specialises direct calls)."""
    f = b.declare(T.FUNCTION)
    with b.block(f"function {f}(a0, a1) {{", kind="function"):
        b.scopes[-1].vars.extend([Var("a0", T.UNKNOWN), Var("a1", T.UNKNOWN)])
        b.emit(f"return {b.choice(['a0 + a1', '[a0, a1]', 'a0 * 2 + (a1 | 0)', 'typeof a1', 'a0 === undefined ? -1 : a0'])};")
    res = b.fresh(T.ARRAY, "[]")
    b.emit(f"{res}.push({f}({b.choice(INT32S)}, {b.choice(INT32S)}));")
    b.emit(f"{res}.push({f}({b.choice(NUMBERS)}));")
    b.emit(f"{res}.push({f}({b.choice(INT32S)}, {b.choice(NUMBERS)}, {b.choice(PERTURB)}));")
    b.emit(f"{res}.push({f}());")
    if b.maybe(0.5):
        b.emit(f"{f} = function(a0, a1) {{ return {b.choice(['a1', '-1', 'a0 - a1'])}; }};    // redefine the direct-call target")
        b.emit(f"{res}.push({f}({b.choice(INT32S)}, {b.choice(INT32S)}));")


@generator("callsite_relink", family="compiler-targeted", weight=6)
def gen_callsite_relink(b: ProgramBuilder):
    """One call/property site hit repeatedly with one observed type (int, double, string,
    ConsString, dense array, constant), then with another: exercises invokedynamic relinking."""
    f = b.declare(T.FUNCTION)
    with b.block(f"function {f}(x, y) {{", kind="function"):
        b.scopes[-1].vars.extend([Var("x", T.UNKNOWN), Var("y", T.UNKNOWN)])
        b.emit(f"return {b.choice(['x + y', 'x - y', 'x * y', 'x < y', 'x == y', 'x.length', 'x[0]', 'x + \"\"', 'x | y', 'x / y', 'x % y', '-x'])};")
    acc = b.fresh(T.ARRAY, "[]")
    first = b.choice(["1", "1.5", '"s"', '"a" + "b"', "[1, 2]", "2147483647", "true"])
    second = b.choice(PERTURB + ["[3]", '"x" + 1', "0.1"])
    with b.for_loop(b.rand_int(2, 6)) as i:
        b.emit(f"{acc}.push({f}({first}, {i}));")
    b.emit(f"{acc}.push({f}({second}, {b.choice(INT32S)}));")
    b.emit(f"{acc}.push({f}({first}, {second}));")


@generator("property_site_shape_change", family="compiler-targeted", weight=4)
def gen_property_site_shape_change(b: ProgramBuilder):
    """A property get site warmed on one shape, then the object gains/loses the property, gets a
    new prototype, or the data property becomes an accessor."""
    key = b.choice(["x", "y", "0", "length"])
    o = b.fresh(T.OBJECT, f"{{{key}: {b.choice(INT32S)}}}")
    f = b.declare(T.FUNCTION)
    with b.block(f"function {f}(obj) {{", kind="function"):
        b.scopes[-1].vars.append(Var("obj", T.OBJECT))
        b.emit(f"return obj.{key} + 1;" if key != "0" else "return obj[0] + 1;")
    acc = b.fresh(T.ARRAY, "[]")
    with b.for_loop(3):
        b.emit(f"{acc}.push({f}({o}));")
    change = b.choice([
        f"delete {o}[{key!r}];",
        f"Object.defineProperty({o}, {key!r}, {{get() {{ return {b.choice(NUMBERS)}; }}}});",
        f"Object.setPrototypeOf({o}, {{{key}: {b.choice(PERTURB)}}}); delete {o}[{key!r}];",
        f"{o}[{key!r}] = {b.choice(PERTURB)};",
        f"{o} = {{{key}: {b.choice(NUMBERS)}, extra: 1}};",
        f"{o} = [{b.choice(INT32S)}];",
    ])
    b.emit(change)
    b.emit(f"{acc}.push({f}({o}));")


@generator("string_concat_chain", family="compiler-targeted", weight=4, output=T.STRING)
def gen_string_concat_chain(b: ProgramBuilder):
    """Long concatenation chains (ConsString), then used in comparison, +number, indexing."""
    s = b.fresh(T.STRING, b.choice(['""', '"a"', '"0"']))
    with b.for_loop(b.rand_int(1, 8)) as i:
        b.emit(f"{s} += {b.choice([str(i), '\"b\"', f'{s}.length', '1.5', 'null', '[]'])};")
    b.fresh(T.UNKNOWN, b.choice([f"{s} == {s}.length", f"{s} + 1", f"{s}[2]", f"{s} < \"b\"", f"{s}.charCodeAt(0)",
                                 f"{s} === {s} + \"\"", f"Number({s})", f"{s}.length", f"{s}.indexOf(\"b\")"]))


@generator("int32_boundary_arith", family="compiler-targeted", weight=4, output=T.NUMBER)
def gen_int32_boundary_arith(b: ProgramBuilder):
    """Arithmetic that overflows int32 inside a numeric-typed function."""
    with b.define_function(1, param_types=[T.NUMBER]) as (f, params):
        x = b.declare(T.NUMBER)
        b.emit(f"var {x} = {b.choice(['2147483647', '-2147483648', '4294967295', '1073741824', '65536'])};")
        b.emit(b.choice([f"{x} = {x} + {params[0]};", f"{x} = {x} * {params[0]};", f"{x}++;", f"{x} = {x} << 1;",
                         f"{x} = {x} - {params[0]};", f"{x} = ({x} + 1) | 0;", f"{x} = {x} * {x};"]))
        b.emit(f"return [{x}, {x} | 0, {x} >>> 0, {x} === ({x} | 0)];")
    b.fresh(T.ARRAY, f"{f}({b.choice(['1', '2', '-1', '0.5', '2147483647', '\"1\"'])})")


@generator("super_in_object_literal", family="compiler-targeted", weight=4)
def gen_super_in_object_literal(b: ProgramBuilder):
    """`super.x` / `super[k]` property access and compound updates from an object-literal method,
    with a key whose toString swaps the prototype (the interpreted-only test262 failure)."""
    proto = b.fresh(T.OBJECT, f"{{p: {b.choice(INT32S)}, get g() {{ return this.p * 2; }}}}")
    proto2 = b.fresh(T.OBJECT, f"{{p: {b.choice(NUMBERS)}}}")
    o = b.declare(T.OBJECT)
    op = b.choice(["return super.p;", "return ++super.p;", "return super.p++;", "return super[k] += 1;",
                   "return super.g;", "super.p = 5; return this.p;", "return super[k]--;", "return super.q;"])
    b.emit(f"var {o} = {{")
    b.indent += 1
    b.emit(f"__proto__: {proto},")
    b.emit(f"m(k) {{ {op} }}")
    b.indent -= 1
    b.emit("};")
    key = b.fresh(T.OBJECT, f"{{toString() {{ Object.setPrototypeOf({o}, {proto2}); return \"p\"; }}}}") \
        if b.maybe(0.5) else b.fresh(T.STRING, '"p"')
    b.fresh(T.UNKNOWN, f"{o}.m({key})")
    b.fresh(T.UNKNOWN, f"[{o}.p, {proto}.p, {proto2}.p]")


@generator("const_site", family="compiler-targeted", weight=2)
def gen_const_site(b: ProgramBuilder):
    """Operations on literal constants that the compiler may fold or link as constants."""
    x = b.choice(NUMBERS)
    b.fresh(T.UNKNOWN, b.choice([f"{x} + 1", f"{x} * 0", f"{x} / 0", f"{x} % 1", f"-({x})", f"{x} === {x}",
                                 f"{x} | 0", f"{x} >>> 0", f'"" + {x}', f"{x} ** 0.5", f"typeof {x}", f"{x} == \"{x}\""]))
