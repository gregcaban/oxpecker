"""Function definition and call generators."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal

# Builtin methods per receiver type: (name, argument types). Kept to methods Rhino implements.
METHODS: dict[T, list[tuple[str, tuple[T, ...]]]] = {
    T.ARRAY: [("push", (T.UNKNOWN,)), ("pop", ()), ("shift", ()), ("unshift", (T.UNKNOWN,)),
              ("slice", (T.INT32,)), ("splice", (T.INT32, T.INT32)), ("indexOf", (T.UNKNOWN,)),
              ("join", (T.STRING,)), ("reverse", ()), ("sort", ()), ("concat", (T.ARRAY,)),
              ("includes", (T.UNKNOWN,)), ("fill", (T.NUMBER,)), ("toString", ()),
              ("map", (T.FUNCTION,)), ("filter", (T.FUNCTION,)), ("reduce", (T.FUNCTION, T.NUMBER)),
              ("forEach", (T.FUNCTION,)), ("some", (T.FUNCTION,)), ("every", (T.FUNCTION,)),
              ("find", (T.FUNCTION,)), ("findIndex", (T.FUNCTION,)), ("flat", ()), ("at", (T.INT32,))],
    T.STRING: [("charAt", (T.INT32,)), ("charCodeAt", (T.INT32,)), ("codePointAt", (T.INT32,)),
               ("indexOf", (T.STRING,)), ("slice", (T.INT32,)), ("substring", (T.INT32, T.INT32)),
               ("split", (T.STRING,)), ("toUpperCase", ()), ("toLowerCase", ()), ("trim", ()),
               ("repeat", (T.INT32,)), ("padStart", (T.INT32, T.STRING)), ("concat", (T.STRING,)),
               ("replace", (T.STRING, T.STRING)), ("includes", (T.STRING,)), ("localeCompare", (T.STRING,)),
               ("normalize", ()), ("at", (T.INT32,)), ("startsWith", (T.STRING,))],
    T.NUMBER: [("toString", ()), ("toFixed", (T.INT32,)), ("valueOf", ())],
    T.MAP: [("get", (T.UNKNOWN,)), ("set", (T.UNKNOWN, T.UNKNOWN)), ("has", (T.UNKNOWN,)),
            ("delete", (T.UNKNOWN,)), ("clear", ())],
    T.SET: [("add", (T.UNKNOWN,)), ("has", (T.UNKNOWN,)), ("delete", (T.UNKNOWN,))],
    T.REGEXP: [("test", (T.STRING,)), ("exec", (T.STRING,))],
    T.TYPEDARRAY: [("fill", (T.NUMBER,)), ("subarray", (T.INT32,)), ("indexOf", (T.NUMBER,)),
                   ("join", (T.STRING,)), ("reverse", ()), ("slice", (T.INT32,)), ("set", (T.ARRAY,))],
    T.OBJECT: [("hasOwnProperty", (T.STRING,)), ("toString", ()), ("valueOf", ()),
               ("propertyIsEnumerable", (T.STRING,)), ("isPrototypeOf", (T.OBJECT,))],
    T.FUNCTION: [("call", (T.UNKNOWN, T.UNKNOWN)), ("apply", (T.UNKNOWN, T.ARRAY)), ("bind", (T.UNKNOWN,)),
                 ("toString", ())],
}
GLOBAL_FUNCTIONS = [("Math.max", (T.NUMBER, T.NUMBER)), ("Math.min", (T.NUMBER, T.NUMBER)),
                    ("Math.floor", (T.NUMBER,)), ("Math.round", (T.NUMBER,)), ("Math.trunc", (T.NUMBER,)),
                    ("Math.abs", (T.NUMBER,)), ("Math.sign", (T.NUMBER,)), ("Math.imul", (T.NUMBER, T.NUMBER)),
                    ("Math.pow", (T.NUMBER, T.NUMBER)), ("Math.sqrt", (T.NUMBER,)), ("Math.fround", (T.NUMBER,)),
                    ("Math.clz32", (T.NUMBER,)), ("Math.hypot", (T.NUMBER, T.NUMBER)),
                    ("isNaN", (T.UNKNOWN,)), ("isFinite", (T.UNKNOWN,)), ("Number.isInteger", (T.UNKNOWN,)),
                    ("Number.isSafeInteger", (T.NUMBER,)), ("String.fromCharCode", (T.NUMBER,)),
                    ("Array.isArray", (T.UNKNOWN,)), ("Array.of", (T.UNKNOWN, T.UNKNOWN)),
                    ("Array.from", (T.ARRAY,)), ("Object.is", (T.UNKNOWN, T.UNKNOWN)),
                    ("JSON.stringify", (T.UNKNOWN,)), ("Boolean", (T.UNKNOWN,)), ("String", (T.UNKNOWN,)),
                    ("Number", (T.UNKNOWN,)), ("encodeURIComponent", (T.STRING,)), ("escape", (T.STRING,))]


def args_for(b: ProgramBuilder, types: tuple[T, ...]) -> str:
    n = len(types)
    r = b.rand_int(0, 9)
    if r == 0 and n:
        n -= 1          # fewer arguments
    elif r == 1:
        n += 1          # extra argument
    return ", ".join(b.expr(types[i] if i < len(types) else T.UNKNOWN, literal) for i in range(n))


@generator("function_def", family="calls", weight=5, output=T.FUNCTION)
def gen_function_def(b: ProgramBuilder):
    """A function declaration with a generated body and a return value."""
    with b.define_function() as (f, params):
        b.run_some()
        if b.maybe(0.85):
            b.emit(f"return {b.expr(T.UNKNOWN, literal)};")
    return f


@generator("arrow_closure", family="calls", weight=2, output=T.FUNCTION)
def gen_arrow_closure(b: ProgramBuilder):
    """An arrow function closing over an outer variable and mutating it."""
    outer = b.pick_or_make(T.NUMBER)
    with b.define_function(1, kind="arrow") as (f, params):
        b.emit(f"{outer} += {params[0]};")
        b.emit(f"return {outer};")
    b.fresh(T.NUMBER, f"{f}({b.expr(T.NUMBER, literal)})")


@generator("call", family="calls", weight=5, inputs=(T.FUNCTION,), output=T.UNKNOWN)
def gen_call(b: ProgramBuilder, f: Var):
    """Call an existing function with 0-3 arguments, guarded against non-callables."""
    tb = b.try_block()
    with tb.body():
        b.fresh(T.UNKNOWN, f"{f}({args_for(b, tuple(T.UNKNOWN for _ in range(b.rand_int(0, 3))))})")
    with tb.catch("e"):
        b.emit("// not callable or threw")
    tb.end()


@generator("method_call", family="calls", weight=5, output=T.UNKNOWN)
def gen_method_call(b: ProgramBuilder):
    """Call a builtin method on a typed receiver (Array/String/Map/...)."""
    t = b.choice(list(METHODS.keys()))
    recv = b.pick_or_make(t)
    name, types = b.choice(METHODS[t])
    b.fresh(T.UNKNOWN, f"{recv}.{name}({args_for(b, types)})")


@generator("global_call", family="calls", weight=4, output=T.UNKNOWN)
def gen_global_call(b: ProgramBuilder):
    """Call a global/builtin static function (Math.*, Number.*, Array.*, JSON...)."""
    name, types = b.choice(GLOBAL_FUNCTIONS)
    out = T.NUMBER if name.startswith("Math.") or name == "Number" else T.UNKNOWN
    b.fresh(out, f"{name}({args_for(b, types)})")


@generator("construct", family="calls", weight=3, output=T.OBJECT)
def gen_construct(b: ProgramBuilder):
    """`new` on a builtin or a generated function."""
    r = b.rand_int(0, 3)
    if r == 0:
        f = b.pick(T.FUNCTION)
        if f is not None:
            tb = b.try_block()
            with tb.body():
                b.fresh(T.OBJECT, f"new {f}({args_for(b, (T.UNKNOWN,))})")
            with tb.catch("e"):
                b.emit("// not a constructor")
            tb.end()
            return
    ctor = b.choice([("Object", (T.UNKNOWN,)), ("Array", (T.INT32,)), ("Number", (T.UNKNOWN,)),
                     ("String", (T.UNKNOWN,)), ("Boolean", (T.UNKNOWN,)), ("Date", (T.NUMBER,)),
                     ("Error", (T.STRING,)), ("RegExp", (T.STRING,)), ("Map", ()), ("Set", ())])
    tb = b.try_block()
    with tb.body():
        b.fresh(T.OBJECT, f"new {ctor[0]}({args_for(b, ctor[1])})")
    with tb.catch("e"):
        b.emit("// e.g. new Array(-1), invalid RegExp")
    tb.end()


@generator("call_apply_bind", family="calls", weight=2, inputs=(T.FUNCTION,), output=T.UNKNOWN)
def gen_call_apply_bind(b: ProgramBuilder, f: Var):
    """f.call / f.apply / f.bind with varying this and arguments."""
    this = b.expr(b.choice([T.OBJECT, T.NULL, T.UNDEFINED, T.NUMBER]), literal)
    form = b.choice([f"{f}.call({this}, {args_for(b, (T.UNKNOWN,))})",
                     f"{f}.apply({this}, {b.expr(T.ARRAY, literal)})",
                     f"{f}.bind({this})({args_for(b, (T.UNKNOWN,))})",
                     f"Reflect.apply({f}, {this}, {b.expr(T.ARRAY, literal)})"])
    tb = b.try_block()
    with tb.body():
        b.fresh(T.UNKNOWN, form)
    with tb.catch("e"):
        b.emit("// not callable / bad arguments list")
    tb.end()


@generator("arguments_object", family="calls", weight=3, output=T.FUNCTION)
def gen_arguments_object(b: ProgramBuilder):
    """A function reading `arguments` (forces activation, disables the optimizer), then called."""
    with b.define_function(2) as (f, params):
        kind = b.rand_int(0, 3)
        if kind == 0:
            b.emit("return arguments.length;")
        elif kind == 1:
            b.emit(f"return arguments[{b.choice(['0', '1', '2', '5'])}];")
        elif kind == 2:
            b.emit(f"{params[0]} = {b.expr(T.NUMBER, literal)};")
            b.emit("return arguments[0];      // mapped arguments in sloppy mode")
        else:
            b.emit("return Array.prototype.slice.call(arguments);")
    b.fresh(T.UNKNOWN, f"{f}({args_for(b, (T.NUMBER, T.NUMBER))})")


@generator("default_rest_params", family="calls", weight=2, output=T.FUNCTION)
def gen_default_rest_params(b: ProgramBuilder):
    """Default parameter values and rest parameters."""
    f = b.declare(T.FUNCTION)
    dflt = b.expr(T.UNKNOWN, literal)
    with b.block(f"function {f}(a0 = {dflt}, ...rest) {{", kind="function"):
        b.scopes[-1].vars.extend([Var("a0", T.UNKNOWN), Var("rest", T.ARRAY)])
        b.emit("return [a0, rest.length, rest[0]];")
    b.fresh(T.ARRAY, f"{f}({args_for(b, (T.UNKNOWN, T.UNKNOWN))})")


@generator("recursion", family="calls", weight=2, output=T.FUNCTION)
def gen_recursion(b: ProgramBuilder):
    """Bounded recursion through a direct call to a top-level function."""
    f = b.declare(T.FUNCTION)
    with b.block(f"function {f}(n) {{", kind="function"):
        b.scopes[-1].vars.append(Var("n", T.NUMBER))
        base = b.expr(T.NUMBER, literal)
        step = b.choice(["n - 1", "n - 2", "n / 2 | 0"])
        combine = b.choice([f"{f}({step}) + 1", f"{f}({step}) * 2", f"1 + {f}({step})", f"[{f}({step}), n]"])
        b.emit(f"if (n {b.choice(['<=', '===', '==', '<'])} 0) return {base};")
        b.emit(f"return {combine};")
    b.fresh(T.UNKNOWN, f"{f}({b.rand_int(0, 12)})")
