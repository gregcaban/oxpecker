"""Templates ported from Fuzzilli's ProgramTemplates.swift: JIT1Function, JIT2Functions,
JITTrickyFunction and JSONFuzzer.

Rhino's compiler infers number types for function locals from their warm-up behaviour,
specialises direct calls to top-level functions, and links call sites per observed argument
type. These templates warm a function (or a call site) up with one shape, then change the
shape -- a different argument type, a redefined callee, a branch that only runs on a later
iteration -- so any divergence between the interpreter and the compiler shows up as a diff
rather than silently vanishing after deoptimization.

Registering this module (importing it) adds four templates to the registry: jit1_function,
jit2_functions, jit_tricky_function, json_fuzzer. It does not modify any existing module.
"""

from __future__ import annotations

from hypothesis import strategies as st

from .builder import ProgramBuilder, Var
from .generators.compiler import PERTURB, numeric_body
from .generators.values import INT32S, NUMBERS, STRINGS, literal
from .registry import template
from .templates import statements
from .types import T

# -- shared helpers -----------------------------------------------------------------------

#: Values used as JSON.stringify's `replacer` argument when it's a function.
_JSON_REPLACER_FNS = [
    "function(key, value) { return value; }",
    "function(key, value) { return typeof value === \"bigint\" ? value.toString() : value; }",
    "function(key, value) { return key === \"__proto__\" ? undefined : value; }",
    "function(key, value) { return typeof value === \"number\" ? value * 2 : value; }",
]
#: Values used as the `replacer` argument when it's an allow-list array.
_JSON_REPLACER_ARRS = ['["a", "b", "0", "length"]', "[]", '["z"]']
#: Values used as the `space` (indent) argument.
_JSON_INDENT_NUMS = ["2", "4", "0", "-1", "12"]
_JSON_INDENT_STRS = ['"\\t"', '"  "', '""']
#: Values used as JSON.parse's `reviver` argument.
_JSON_REVIVERS = [
    "function(key, value) { return value; }",
    "function(key, value) { return typeof value === \"number\" ? value + 0 : value; }",
    "function(key, value) { return key === \"__proto__\" ? undefined : value; }",
]
#: Hand-picked JSON strings that are known to be tricky for parsers: a poisoned __proto__ key,
#: an out-of-range exponent (parses to Infinity), a lone surrogate, a duplicate key, trailing
#: comma, a non-JSON NaN, a leading-zero number, a bare Infinity, and a very long string value.
_TRICKY_JSON_LITERALS = [
    '\'{"__proto__": 1}\'',
    "'[1e400]'",
    '\'"\\ud800"\'',
    '\'{"a":1,"a":2}\'',
    "'[1,2,]'",
    '\'{"a": NaN}\'',
    "'01'",
    "'Infinity'",
    '\'{"a":"\' + "x".repeat(5000) + \'"}\'',
]


def _call_args(b: ProgramBuilder, n: int, pool: list[str]) -> str:
    return ", ".join(b.choice(pool) for _ in range(n))


# -- jit1_function --------------------------------------------------------------------------

@template("jit1_function", weight=3)
def tpl_jit1_function(b: ProgramBuilder):
    """Port of JIT1Function: one function with a generated body, warmed up in a loop with one
    argument shape, then called with other argument types so a type-specialised compiled body
    either deoptimizes correctly or disagrees with the interpreter."""
    statements(b, 0, max(1, b.opts.max_statements // 4))

    nparams = b.rand_int(1, 3)
    with b.define_function(nparams, param_types=[T.NUMBER] * nparams) as (f, params):
        locals_ = numeric_body(b, params, b.rand_int(1, 3))
        b.run_some(b.rand_int(2, 5))
        ret = b.choice(locals_ + params) if b.maybe(0.6) else (b.pick(T.UNKNOWN) or params[0])
        b.emit(f"return {ret};")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    acc = b.fresh(T.ARRAY, "[]")

    # Warm the function up: call it enough times (2-10) with consistent int arguments.
    with b.for_loop(b.rand_int(2, 10)) as i:
        b.emit(f"{acc}.push({f}({_call_args(b, nparams, INT32S + [str(i)])}));")
    b.probe("warm")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    b.emit(f"{acc}.push({f}({_call_args(b, nparams, INT32S)}));")
    b.probe("post_warm")

    # Now call with other argument types: doubles, strings, objects, BigInt, NaN, -0 ...
    for _ in range(b.rand_int(1, 3)):
        b.emit(f"{acc}.push({f}({_call_args(b, nparams, PERTURB + NUMBERS + STRINGS)}));")
        b.probe("type_change")

    # Warm again (possibly a different shape now) to trigger recompilation.
    with b.for_loop(b.rand_int(2, 10)) as i:
        b.emit(f"{acc}.push({f}({_call_args(b, nparams, INT32S + [str(i)])}));")
    b.probe("recompile")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    b.emit(f"{acc}.push({f}({_call_args(b, nparams, INT32S)}));")
    b.probe("end")


# -- jit2_functions -------------------------------------------------------------------------

@template("jit2_functions", weight=3)
def tpl_jit2_functions(b: ProgramBuilder):
    """Port of JIT2Functions: f1 calls f2 directly (a top-level direct call site Rhino
    specialises), both are warmed up, then the callee is swapped -- either f2 itself is
    redefined, or the call goes through a variable that's repointed -- and warmed again."""
    statements(b, 0, max(1, b.opts.max_statements // 4))

    nparams2 = b.rand_int(1, 2)
    with b.define_function(nparams2, param_types=[T.NUMBER] * nparams2) as (f2, params2):
        locals2 = numeric_body(b, params2, b.rand_int(1, 2))
        b.run_some(b.rand_int(1, 3))
        b.emit(f"return {b.choice(locals2 + params2)};")

    use_alias = b.maybe(0.5)
    callee_name = f2
    if use_alias:
        # The call inside f1 goes through a variable rather than the literal f2 name, so later
        # we can repoint the variable instead of redefining f2 itself.
        callee = b.fresh(T.FUNCTION, f2.name)
        callee_name = callee

    nparams1 = b.rand_int(1, 3)
    with b.define_function(nparams1, param_types=[T.NUMBER] * nparams1) as (f1, params1):
        locals1 = numeric_body(b, params1, b.rand_int(1, 2))
        b.run_some(b.rand_int(1, 3))
        inner_args = _call_args(b, nparams2, [str(v) for v in (locals1 + params1)] + INT32S)
        inner = b.declare(T.NUMBER)
        b.emit(f"var {inner} = {callee_name}({inner_args});    // direct call to the other function")
        b.emit(f"return {inner} + {b.choice(locals1 + params1)};")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    acc = b.fresh(T.ARRAY, "[]")

    # Warm up f1 and f2 separately (each gets its own call site observing one argument shape).
    with b.for_loop(b.rand_int(2, 10)) as i:
        b.emit(f"{acc}.push({f1}({_call_args(b, nparams1, INT32S + [str(i)])}));")
    with b.for_loop(b.rand_int(2, 10)) as i:
        b.emit(f"{acc}.push({f2}({_call_args(b, nparams2, INT32S + [str(i)])}));")
    b.probe("warm")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    b.emit(f"{acc}.push({f2}({_call_args(b, nparams2, INT32S)}));")
    b.emit(f"{acc}.push({f1}({_call_args(b, nparams1, INT32S)}));")
    b.probe("pre_change")

    # Change the callee: redefine f2 in place, or (if f1 calls through a variable) repoint it.
    newbody = f"function({', '.join('p' + str(i) for i in range(nparams2))}) {{ return {b.choice(PERTURB)}; }}"
    b.emit(f"{callee_name} = {newbody};    // change the callee f1's direct call depends on")
    b.probe("changed")

    # Call again through both call sites to trigger recompilation / relinking.
    with b.for_loop(b.rand_int(2, 10)) as i:
        b.emit(f"{acc}.push({f1}({_call_args(b, nparams1, INT32S + [str(i)])}));")
    with b.for_loop(b.rand_int(2, 10)) as i:
        b.emit(f"{acc}.push({f2}({_call_args(b, nparams2, INT32S + [str(i)])}));")
    b.probe("recompile")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    b.emit(f"{acc}.push({f1}({_call_args(b, nparams1, INT32S)}));")
    b.emit(f"{acc}.push({f2}({_call_args(b, nparams2, INT32S)}));")
    b.probe("end")


# -- jit_tricky_function --------------------------------------------------------------------

@template("jit_tricky_function", weight=3)
def tpl_jit_tricky_function(b: ProgramBuilder):
    """Port of JITTrickyFunction: a function whose first parameter is an iteration counter; a
    branch guarded by that counter (`i == N` or `i % N == 0`) only runs on some calls, and only
    there does a numeric local get perturbed to a non-number. Most warm-up calls never take the
    branch, so the optimizer sees number-only locals until a later call flips one."""
    statements(b, 0, max(1, b.opts.max_statements // 4))

    nparams = b.rand_int(1, 2)
    use_mod = b.maybe(0.5)
    modulus = b.choice([2, 5, 10, 25]) if use_mod else None
    num_iterations = b.rand_int(20, 100)
    one_shot_target = b.rand_int(max(0, num_iterations - 10), num_iterations - 1)

    with b.define_function(nparams + 1, param_types=[T.NUMBER] * (nparams + 1)) as (f, params):
        iter_param = params[0]
        others = params[1:]
        b.run_some(b.rand_int(1, 3))
        locals_ = numeric_body(b, others, b.rand_int(1, 3))
        victim = b.choice(locals_)
        cond = f"{iter_param} % {modulus} == 0" if use_mod else f"{iter_param} == {one_shot_target}"
        with b.block(f"if ({cond}) {{"):
            b.emit(f"{victim} = {b.choice(PERTURB)};    // only reached on some calls")
            b.run_some(2)
        b.probe("inside")
        b.emit(f"return {' + '.join(str(v) for v in locals_)};")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    acc = b.fresh(T.ARRAY, "[]")
    arg_pool = INT32S + NUMBERS

    # Two warm-up passes (loop of loops, mirroring Fuzzilli) to potentially trigger
    # recompilation: most calls skip the branch, a handful near the end take it.
    for _ in range(2):
        with b.for_loop(num_iterations) as i:
            b.emit(f"{acc}.push({f}({i}, {_call_args(b, nparams, arg_pool)}));")
    b.probe("outside_warm")

    statements(b, 0, max(1, b.opts.max_statements // 4))

    # Call again, counter restarting from zero, so the branch triggers again on a fresh pass.
    with b.for_loop(num_iterations) as i2:
        b.emit(f"{acc}.push({f}({i2}, {_call_args(b, nparams, arg_pool)}));")
    b.probe("outside_recompile")

    # Finally, force the tricky branch directly (and with a non-numeric extra argument too).
    for _ in range(b.rand_int(1, 2)):
        trigger = modulus * b.rand_int(1, 3) if use_mod else one_shot_target
        b.emit(f"{acc}.push({f}({trigger}, {_call_args(b, nparams, PERTURB + NUMBERS)}));")
        b.probe("tricky_call")

    statements(b, 0, max(1, b.opts.max_statements // 4))
    b.probe("end")


# -- json_fuzzer ------------------------------------------------------------------------------

@template("json_fuzzer", weight=3)
def tpl_json_fuzzer(b: ProgramBuilder):
    """Port of JSONFuzzer: builds nested array/object graphs out of existing variables and
    JSON edge-case literals (-0, NaN, Infinity, undefined, functions, symbols, BigInt, long and
    unicode strings, numeric and __proto__ keys), stringifies them with and without a replacer
    / indent, parses the result back with and without a reviver, and separately feeds JSON.parse
    a handful of known-tricky literal strings. Every JSON call is guarded since BigInt values
    make JSON.stringify throw and malformed strings make JSON.parse throw."""
    statements(b, 0, max(1, b.opts.max_statements // 3))

    def leaf() -> str:
        return b.choice([
            "0", "-0", "NaN", "Infinity", "-Infinity", "undefined", "null", "true", "false",
            '"a"', '""', '"' + ("x" * 64) + '"', '"\\u00e9\\u03b1\\u03b2\\ud83d\\ude00"',
            "2147483647", "1e21", "function(){}", 'Symbol("s")', "10n", "9007199254740993n",
            b.expr(T.UNKNOWN, literal),
        ])

    def value(depth: int) -> str:
        if depth <= 0 or b.maybe(0.35):
            return leaf()
        if b.maybe(0.5):
            n = b.rand_int(0, 4)
            return "[" + ", ".join(value(depth - 1) for _ in range(n)) + "]"
        n = b.rand_int(0, 4)
        parts = []
        for _ in range(n):
            key = b.choice(['"a"', '"b"', '"0"', '"1"', '"__proto__"', '"length"', '"z"'])
            parts.append(f"{key}: {value(depth - 1)}")
        return "{" + ", ".join(parts) + "}"

    def guarded(expr: str) -> Var:
        """Runs `expr` inside try/catch, assigning the result (or the caught error's name)."""
        out = b.fresh(T.UNKNOWN, "undefined")
        tb = b.try_block()
        with tb.body():
            b.emit(f"{out} = {expr};")
        with tb.catch("e"):
            b.emit(f'{out} = "threw:" + (e && e.name);')
        tb.end()
        return out

    def stringify_call(value_expr: str) -> str:
        mode = b.choice(["plain", "fn", "arr", "indent_num", "indent_str"])
        if mode == "fn":
            return f"JSON.stringify({value_expr}, {b.choice(_JSON_REPLACER_FNS)})"
        if mode == "arr":
            return f"JSON.stringify({value_expr}, {b.choice(_JSON_REPLACER_ARRS)})"
        if mode == "indent_num":
            return f"JSON.stringify({value_expr}, null, {b.choice(_JSON_INDENT_NUMS)})"
        if mode == "indent_str":
            return f"JSON.stringify({value_expr}, null, {b.choice(_JSON_INDENT_STRS)})"
        return f"JSON.stringify({value_expr})"

    def parse_call(json_expr: str) -> str:
        if b.maybe(0.5):
            return f"JSON.parse({json_expr}, {b.choice(_JSON_REVIVERS)})"
        return f"JSON.parse({json_expr})"

    # A helper that flips one character of a JSON string: mostly fuzzes JSON.parse rather than
    # the stringify/object-building side. The flip index is a literal fraction baked in at
    # generation time (not Math.random()) so the program is deterministic across engines.
    with b.define_function(1, param_types=[T.STRING]) as (mutate_fn, mparams):
        j = mparams[0]
        xorv = b.rand_int(1, 127)
        frac = b.draw(st.floats(min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False))
        b.emit(f"if ({j}.length === 0) return {j};")
        idx = b.fresh(T.INT32, f"Math.floor({j}.length * {frac})")
        code = b.fresh(T.INT32, f"{j}.charCodeAt({idx}) ^ {xorv}")
        b.emit(f"return {j}.substring(0, {idx}) + String.fromCharCode({code}) + {j}.substring({idx} + 1);")

    payloads: list[Var] = []
    for _ in range(b.rand_int(1, 5)):
        s = guarded(stringify_call(value(b.rand_int(1, 3))))
        if b.maybe(0.25):
            s = b.fresh(T.STRING, f'typeof {s} === "string" ? {mutate_fn}({s}) : {s}')
        payloads.append(s)
    b.probe("stringified")

    # Parse the payloads back; may hit the same string more than once.
    for _ in range(len(payloads) * 2):
        s = b.choice(payloads)
        guarded(parse_call(f'typeof {s} === "string" ? {s} : String({s})'))
    b.probe("roundtrip")

    # JSON.parse of hand-picked tricky literal strings, independent of the object graph above.
    for _ in range(b.rand_int(1, 3)):
        guarded(parse_call(b.choice(_TRICKY_JSON_LITERALS)))
    b.probe("tricky_parse")

    statements(b, 0, max(1, b.opts.max_statements // 3))
    b.probe("end")
