# Ported from Fuzzilli (https://github.com/googleprojectzero/fuzzilli), Sources/Fuzzilli/CodeGen/CodeGenerators.swift,
# Copyright 2019 Google LLC, licensed under the Apache License, Version 2.0
# (see docs/licenses/Apache-2.0.txt and THIRD_PARTY_NOTICES.md). Re-implemented in Python against
# oxpecker's ProgramBuilder and filtered to the language subset Rhino supports.
"""Control-flow generators ported from Fuzzilli's CodeGenerators.swift (family="control-ops").

Dup/Reassignment/ChainedReassignments/ComplexForLoop/Throw/ConditionalThrow/BlockBreak/
NumberComputation/TypeTest/MethodAsPropertyRetrieval/ComputedMethodCall/LoadNewTarget.
"""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .calls import METHODS, args_for
from .values import literal, number_literal

# Reassignment cycles a variable through a *different* representation each time, which is
# exactly the pattern that defeats a compiler's per-slot type specialization.
REASSIGN_CYCLE: dict[T, T] = {
    T.NUMBER: T.STRING, T.INT32: T.STRING, T.BIGINT: T.STRING,
    T.STRING: T.OBJECT,
    T.OBJECT: T.UNDEFINED, T.ARRAY: T.UNDEFINED, T.FUNCTION: T.UNDEFINED,
    T.REGEXP: T.UNDEFINED, T.MAP: T.UNDEFINED, T.SET: T.UNDEFINED, T.TYPEDARRAY: T.UNDEFINED,
    T.BOOLEAN: T.NUMBER, T.UNDEFINED: T.NUMBER, T.NULL: T.NUMBER, T.SYMBOL: T.NUMBER,
}

JS_TYPE_NAMES = ['"undefined"', '"boolean"', '"number"', '"string"', '"symbol"', '"object"',
                 '"function"', '"bigint"']
INSTANCEOF_CTORS = ["Object", "Array", "Function", "Error", "RegExp", "Map", "Set",
                     "Number", "String", "Boolean"]

NUM_BINOPS = ["+", "-", "*", "/", "%", "**", "&", "|", "^", "<<", ">>", ">>>"]
MATH_UNARY = ["abs", "floor", "ceil", "round", "trunc", "sign", "sqrt", "cbrt", "sin", "cos",
              "log", "exp", "fround", "clz32"]
MATH_BINARY = ["max", "min", "pow", "atan2", "imul", "hypot"]


@generator("dup", family="control-ops", weight=2)
def gen_dup(b: ProgramBuilder):
    """Copies an existing variable into a new one, then uses both (aliasing of the same value)."""
    v = b.pick_or_make(T.UNKNOWN)
    d = b.fresh(v.type, str(v))
    b.fresh(T.BOOLEAN, f"{v} === {d}")


@generator("reassign", family="control-ops", weight=4, inputs=(T.UNKNOWN,))
def gen_reassign(b: ProgramBuilder, v: Var):
    """Assigns an existing variable a value of a *different* type (number -> string -> object ->
    undefined, ...): stresses a compiler's per-variable type specialization."""
    new_t = REASSIGN_CYCLE.get(v.type, T.STRING)
    b.emit(f"{v} = {b.expr(new_t, literal)};")


@generator("chained_reassign", family="control-ops", weight=3)
def gen_chained_reassign(b: ProgramBuilder):
    """`a = b = c = expr;` across several existing variables, occasionally ending in a property
    target, creating heavy aliasing between loop-carried variables."""
    n = b.rand_int(2, 4)
    targets = [str(b.pick_or_make(T.UNKNOWN)) for _ in range(n)]
    if b.maybe(0.4):
        obj = b.pick_or_make(T.OBJECT)
        targets.append(f"{obj}.{b.choice(['a', 'x', 'y', 'value'])}")
    value = b.expr(T.UNKNOWN, literal)
    b.emit(" = ".join(targets) + f" = {value};")


@generator("complex_for_loop", family="control-ops", weight=3)
def gen_complex_for_loop(b: ProgramBuilder):
    """A for-loop with a comma-separated init/update and two loop variables, a non-trivial
    condition, and at most 8 iterations (the condition always ANDs a hard bound on `i`)."""
    bound = b.rand_int(1, 8)
    i = b.new_name(T.INT32)
    j = b.new_name(T.INT32)
    cond = b.choice([
        f"{i} < {bound}",
        f"{i} < {bound} && {j} > 0",
        f"{i} < {bound} && {i} < {j}",
    ])
    update = b.choice([f"{i}++, {j}--", f"{i}++, {j} += 1", f"{i} += 1, {j} -= 2"])
    b.depth += 1
    with b.block(f"for (var {i} = 0, {j} = {bound}; {cond}; {update}) {{"):
        b.scopes[-1].vars.extend([Var(i, T.INT32), Var(j, T.INT32)])
        b.run_some()
    b.depth -= 1


@generator("throw_value", family="control-ops", weight=3, output=T.FUNCTION)
def gen_throw_value(b: ProgramBuilder):
    """A function that unconditionally throws a value of a drawn kind; the caller catches it
    and inspects typeof/instanceof on the caught value."""
    f = b.declare(T.FUNCTION)
    if b.maybe(0.5):
        val = b.expr(b.choice([T.NUMBER, T.STRING, T.OBJECT, T.ARRAY, T.UNDEFINED, T.NULL, T.BOOLEAN]), literal)
    else:
        val = b.choice(['new Error("boom")', 'new TypeError("bad")', 'new RangeError("oob")'])
    with b.block(f"function {f}() {{", kind="function"):
        b.emit(f"throw {val};")
    out = b.fresh(T.STRING, '""')
    tb = b.try_block()
    with tb.body():
        b.emit(f"{f}();")
    with tb.catch("e"):
        b.emit(f"{out} = {b.choice(['typeof e', 'e instanceof Error', 'e instanceof TypeError', 'String(e)'])};")
    tb.end()
    return f


@generator("conditional_throw", family="control-ops", weight=3, inputs=(T.BOOLEAN,))
def gen_conditional_throw(b: ProgramBuilder, cond: Var):
    """Throws an existing value only when a (possibly negated) condition on an existing boolean
    variable holds; wrapped in try/catch so the program continues either way."""
    v = b.pick_or_make(T.UNKNOWN)
    c = f"!{cond}" if b.maybe(0.5) else str(cond)
    out = b.pick_or_make(T.STRING)
    tb = b.try_block()
    with tb.body():
        with b.block(f"if ({c}) {{"):
            b.emit(f"throw {v};")
    with tb.catch("e"):
        b.emit(f"{out} += typeof e;")
    tb.end()


@generator("block_break", family="control-ops", weight=2)
def gen_block_break(b: ProgramBuilder):
    """A labelled block with a `break` out of it, optionally nested inside another labelled
    block that breaks out of either the inner or the outer one."""
    acc = b.fresh(T.INT32, "0")
    outer, inner = b.choice([("OUTER", "INNER"), ("L1", "L2"), ("A", "B")])
    with b.block(f"{outer}: {{"):
        b.emit(f"{acc} += 1;")
        if b.maybe(0.5):
            with b.block(f"{inner}: {{"):
                b.emit(f"{acc} += 10;")
                with b.block(f"if ({b.choice(['true', 'false', f'{acc} > 5'])}) {{"):
                    b.emit(f"break {b.choice([inner, outer])};")
                b.emit(f"{acc} += 100;")
        with b.block(f"if ({b.choice(['true', 'false', f'{acc} < 50'])}) {{"):
            b.emit(f"break {outer};")
        b.emit(f"{acc} += 1000;")


@generator("number_computation", family="control-ops", weight=4, output=T.NUMBER)
def gen_number_computation(b: ProgramBuilder):
    """A chain of 3-6 arithmetic/bitwise/Math.* operations mixing ints, doubles, -0, NaN and the
    2**31 boundaries, keeping every intermediate result in its own variable."""
    vals = [b.fresh(T.NUMBER, number_literal(b)) for _ in range(b.rand_int(1, 3))]
    for _ in range(b.rand_int(3, 6)):
        kind = b.rand_int(0, 2)
        if kind == 0:
            left, right = str(b.choice(vals)), str(b.choice(vals))
            op = b.choice(NUM_BINOPS)
            if op == "**":
                left = f"({left})"      # `-1 ** 2` is a SyntaxError
            vals.append(b.fresh(T.NUMBER, f"{left} {op} {right}"))
        elif kind == 1:
            vals.append(b.fresh(T.NUMBER, f"Math.{b.choice(MATH_UNARY)}({b.choice(vals)})"))
        else:
            vals.append(b.fresh(T.NUMBER, f"Math.{b.choice(MATH_BINARY)}({b.choice(vals)}, {b.choice(vals)})"))
    return vals[-1]


@generator("type_test", family="control-ops", weight=3, inputs=(T.UNKNOWN,), output=T.BOOLEAN)
def gen_type_test(b: ProgramBuilder, val: Var):
    """typeof / instanceof / Array.isArray / Number.isInteger / Object.prototype.toString.call
    on an existing variable. None of these can throw, so no guard is needed."""
    kind = b.rand_int(0, 4)
    if kind == 0:
        b.fresh(T.BOOLEAN, f"typeof {val} === {b.choice(JS_TYPE_NAMES)}")
    elif kind == 1:
        b.fresh(T.BOOLEAN, f"{val} instanceof {b.choice(INSTANCEOF_CTORS)}")
    elif kind == 2:
        b.fresh(T.BOOLEAN, f"Array.isArray({val})")
    elif kind == 3:
        b.fresh(T.BOOLEAN, f"Number.isInteger({val})")
    else:
        b.fresh(T.STRING, f"Object.prototype.toString.call({val})")


@generator("method_as_property", family="control-ops", weight=3, inputs=(T.OBJECT,))
def gen_method_as_property(b: ProgramBuilder, obj: Var):
    """`var f = obj.method;` then calls it unbound (losing `this`) and bound via `f.call(obj)` -
    the two can observably differ."""
    name = b.choice(["toString", "valueOf", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable"])
    f = b.fresh(T.FUNCTION, f"{obj}.{name}")
    tb = b.try_block()
    with tb.body():
        b.fresh(T.UNKNOWN, f"{f}()")
    with tb.catch("e"):
        b.emit("// unbound call failed (e.g. `this` coercion)")
    tb.end()
    b.fresh(T.UNKNOWN, f"{f}.call({obj})")


@generator("computed_method_call", family="control-ops", weight=3, inputs=(T.OBJECT,), output=T.UNKNOWN)
def gen_computed_method_call(b: ProgramBuilder, obj: Var):
    """`obj[name](args)` where `name` comes from a variable or a string expression - the
    computed-call path for a builtin receiver."""
    methods = METHODS.get(obj.type, METHODS[T.OBJECT])
    name, types = b.choice(methods)
    name_expr = b.fresh(T.STRING, f'"{name}"') if b.maybe(0.6) else f'"{name}"'
    tb = b.try_block()
    with tb.body():
        b.fresh(T.UNKNOWN, f"{obj}[{name_expr}]({args_for(b, types)})")
    with tb.catch("e"):
        b.emit("// computed method lookup/call failed")
    tb.end()


@generator("new_target", family="control-ops", weight=2, output=T.STRING)
def gen_new_target(b: ProgramBuilder):
    """A function reading `new.target`, invoked once as a plain call and once via `new`; each
    `typeof new.target` result is recorded as a static property of the function so both call
    styles are independently observable."""
    f = b.declare(T.FUNCTION)
    with b.block(f"function {f}() {{", kind="function"):
        b.emit(f"{f}.lastNewTarget = typeof new.target;")
    b.emit(f"{f}();")
    b.fresh(T.STRING, f"{f}.lastNewTarget")
    b.emit(f"new {f}();")
    return b.fresh(T.STRING, f"{f}.lastNewTarget")
