"""Control-flow generators: bounded loops, switch, try/catch/finally, labels."""

from __future__ import annotations

from ..builder import ProgramBuilder, Var
from ..registry import generator
from ..types import T
from .values import literal


@generator("for_loop", family="control", weight=4)
def gen_for_loop(b: ProgramBuilder):
    """A bounded for loop whose body is a few nested statements."""
    acc = b.pick_or_make(T.NUMBER)
    with b.for_loop() as i:
        b.emit(f"{acc} += {i};")
        b.run_some()


@generator("while_loop", family="control", weight=2)
def gen_while_loop(b: ProgramBuilder):
    """A while / do-while loop counting down from a small bound."""
    c = b.fresh(T.INT32, str(b.rand_int(0, 5)))
    b.depth += 1
    if b.maybe(0.5):
        with b.block(f"while ({c} > 0) {{"):
            b.emit(f"{c}--;")
            b.run_some()
    else:
        with b.block("do {", footer=f"}} while ({c}-- > 0);"):
            b.run_some()
    b.depth -= 1


@generator("if_else", family="control", weight=3)
def gen_if_else(b: ProgramBuilder):
    """if / else on a drawn condition."""
    cond = b.expr(b.choice([T.BOOLEAN, T.NUMBER, T.STRING, T.UNKNOWN]), literal)
    b.depth += 1
    with b.block(f"if ({cond}) {{", footer="} else {" if b.maybe(0.6) else "}"):
        b.run_some()
    if b.lines[-1].text == "} else {":
        b.indent += 1
        b.run_some()
        b.indent -= 1
        b.emit("}")
    b.depth -= 1


@generator("switch", family="control", weight=2)
def gen_switch(b: ProgramBuilder):
    """switch with fallthrough, default in the middle, and mixed-type case labels."""
    disc = b.expr(b.choice([T.NUMBER, T.STRING, T.INT32]), literal)
    out = b.fresh(T.STRING, '""')
    labels = [literal(b, b.choice([T.NUMBER, T.STRING])) for _ in range(b.rand_int(1, 3))]
    with b.block(f"switch ({disc}) {{"):
        for i, lab in enumerate(labels):
            b.emit(f"case {lab}:")
            b.indent += 1
            b.emit(f'{out} += "c{i}";')
            if b.maybe(0.6):
                b.emit("break;")
            b.indent -= 1
            if i == 0 and b.maybe(0.4):
                b.emit("default:")
                b.indent += 1
                b.emit(f'{out} += "d";')
                b.indent -= 1
        if "default:" not in [ln.text for ln in b.lines[-8:]]:
            b.emit("default:")
            b.indent += 1
            b.emit(f'{out} += "d";')
            b.indent -= 1


@generator("try_catch", family="control", weight=3)
def gen_try_catch(b: ProgramBuilder):
    """try/catch around nested statements and a possible throw; the catch inspects the error."""
    out = b.fresh(T.UNKNOWN, "0")
    tb = b.try_block()
    with tb.body():
        b.run_some()
        if b.maybe(0.5):
            b.emit(f"throw {b.expr(b.choice([T.NUMBER, T.STRING, T.OBJECT]), literal)};")
        elif b.maybe(0.5):
            b.emit(f"{b.pick_or_make(T.NULL)}.x;")
    with tb.catch("e"):
        b.emit(f"{out} = {b.choice(['e', 'e instanceof TypeError', 'typeof e', 'e && e.name', 'String(e)'])};")
    if b.maybe(0.5):
        with tb.finally_():
            b.emit(f'{out} = [{out}, "finally"];')
    tb.end()


@generator("finally_abrupt", family="control", weight=3, output=T.FUNCTION)
def gen_finally_abrupt(b: ProgramBuilder):
    """A function whose try/catch/finally uses return/throw/break in the finally block, which
    overrides the try/catch completion. Mirrors the compiled-only test262 failure."""
    f = b.declare(T.FUNCTION)
    count = b.fresh(T.INT32, "0")
    with b.block(f"function {f}() {{", kind="function"):
        try_c = b.choice(["return 'try';", "throw 'try';", f"{count} += 1;"])
        catch_c = b.choice(["return 'catch';", "throw 'catch';", f"{count} += 10;"])
        fin_c = b.choice(["return 'finally';", "throw 'finally';", f"{count} += 100;"])
        if b.maybe(0.5):
            b.emit("for (var k = 0; k < 2; k++) {")
            b.indent += 1
            fin_c = b.choice([fin_c, "break;", "continue;"])
        b.emit("try {")
        b.indent += 1
        b.emit(try_c)
        b.indent -= 1
        b.emit("} catch (e) {")
        b.indent += 1
        b.emit(catch_c)
        b.indent -= 1
        b.emit("} finally {")
        b.indent += 1
        b.emit(fin_c)
        b.indent -= 1
        b.emit("}")
        if b.lines[-9].text.startswith("for (var k"):
            b.indent -= 1
            b.emit("}")
        b.emit("return 'end';")
    res = b.fresh(T.UNKNOWN, "null")
    tb = b.try_block()
    with tb.body():
        b.emit(f"{res} = {f}();")
    with tb.catch("e"):
        b.emit(f'{res} = "threw:" + e;')
    tb.end()


@generator("labelled_break", family="control", weight=2)
def gen_labelled_break(b: ProgramBuilder):
    """Nested loops with a labelled break or continue."""
    acc = b.fresh(T.INT32, "0")
    label = b.choice(["outer", "L"])
    b.emit(f"{label}: for (var i = 0; i < {b.rand_int(1, 4)}; i++) {{")
    b.indent += 1
    b.scopes.append(type(b.scopes[-1])("block", [Var("i", T.INT32)]))
    b.emit(f"for (var j = 0; j < {b.rand_int(1, 4)}; j++) {{")
    b.indent += 1
    b.emit(f"{acc} += i * 10 + j;")
    b.emit(f"if ({b.choice(['j == 1', 'i == j', f'{acc} > 15'])}) {b.choice(['break', 'continue'])} {label};")
    b.indent -= 1
    b.emit("}")
    b.scopes.pop()
    b.indent -= 1
    b.emit("}")


@generator("completion_value", family="control", weight=2)
def gen_completion_value(b: ProgramBuilder):
    """eval() of a statement list whose completion value depends on loops/try/switch."""
    body = b.choice([
        "'1; try { 2 } finally { 3 }'",
        "'4; for (var q = 0; q < 2; q++) { q }'",
        "'5; switch (1) { case 1: 6; break; }'",
        "'7; if (false) { 8 }'",
        "'9; do { 10; break; } while (false)'",
        "'11; try { throw 12 } catch (e) { e }'",
        "'13; L: { 14; break L; 15 }'",
        "'16; while (true) { 17; break; }'",
    ])
    if b.opts.strict:
        b.fresh(T.UNKNOWN, f"(0, eval)({body})")
    else:
        b.fresh(T.UNKNOWN, f"eval({body})")
