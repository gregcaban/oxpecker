"""The lifter: inlining policy (FuzzIL's worked examples), per-instruction lift choices, Rhino
spellings, every construct lifts, probes and comments, code strings."""

from __future__ import annotations

import pytest

from oxpecker.il import ops
from oxpecker.il.builder import Builder
from oxpecker.il.environment import Builtins, Keys, Methods
from oxpecker.il.lift import LiftOptions, lift, spell_float, spell_integer, spell_string
from oxpecker.il.ops import BinaryOperator as B
from oxpecker.il.ops import Comparator as C
from oxpecker.il.ops import DeclarationKind as DK
from oxpecker.il.ops import ForEachKind, Lift, Parameters, PropertyFlag, PropertyType, Radix, FloatStyle, RegExpFlag
from oxpecker.il.ops import UnaryOperator as U
from oxpecker.il.types import Key, KeyKind, NUMBER

PLAIN = LiftOptions(probes=False, comments=False)


def js(b: Builder, **kw) -> str:
    return lift(b.finish(), LiftOptions(probes=False, comments=False, **kw))


def lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def norm(text_or_lines) -> list[str]:
    """Lines with the numbers in generated names replaced by '#', so tests state structure and
    naming scheme without depending on variable numbering."""
    import re
    ls = lines(text_or_lines) if isinstance(text_or_lines, str) else list(text_or_lines)
    return [re.sub(r"(?<=[A-Za-z])\d+", "#", ln) for ln in ls]


# ------------------------------------------------------------------ inlining (lifter-notes §1.9)
def test_example_a_everything_inlines_into_one_call():
    b = Builder()
    f = b.builtin(Builtins.PARSE_INT)
    x = b.binary(b.load_int(3), b.load_int(4), B.ADD)
    b.call_function(f, [x])
    assert lines(js(b)) == ["parseInt(3 + 4);"]


def test_example_b_two_uses_are_declared_and_literals_are_duplicated():
    b = Builder()
    f = b.builtin(Builtins.PARSE_INT)
    three = b.load_int(3)
    x = b.binary(three, b.load_int(4), B.ADD)
    b.call_function(f, [x])
    b.call_function(f, [x])
    b.call_function(f, [three])
    assert lines(js(b)) == ["var n3 = 3 + 4;", "parseInt(n3);", "parseInt(n3);", "parseInt(3);"]


def test_example_c_an_intervening_statement_flushes_the_pending_expression():
    b = Builder()
    f = b.builtin(Builtins.PARSE_INT)
    g = b.builtin(Builtins.PARSE_FLOAT)
    o = b.declare(b.builtin(Builtins.MATH))
    v1 = b.call_function(f, [b.load_string("1")])
    b.set_property(o, Keys.X, b.load_int(5))
    b.call_function(g, [v1])
    assert lines(js(b)) == ["var o3 = Math;", 'var n5 = parseInt("1");', "o3.x = 5;", "parseFloat(n5);"]


def test_effectful_expressions_inline_only_in_order():
    b = Builder()
    f = b.builtin(Builtins.PARSE_INT)
    g = b.builtin(Builtins.PARSE_FLOAT)
    a = b.call_function(f, [b.load_string("1")])
    c = b.call_function(g, [b.load_string("2")])
    b.create_array([a, c])                    # in order: both inline
    a2 = b.call_function(f, [b.load_string("3")])
    c2 = b.call_function(g, [b.load_string("4")])
    b.create_array([c2, a2])                  # reversed: both are flushed first
    text = lines(js(b))
    assert text[0] == '[parseInt("1"), parseFloat("2")];'
    assert text[1] == 'var n8 = parseInt("3");' and text[2] == 'var n10 = parseFloat("4");' and text[3] == "[n10, n8];"


def test_lift_choice_keeps_a_value_as_its_own_statement():
    b = Builder()
    x = b.emit(ops.LoadInteger(1), lift=Lift.CONST).output
    y = b.emit(ops.BinaryOperation(B.MUL), [x, x], lift=Lift.LET).output
    b.typeof(y)
    assert lines(js(b)) == ["const n0 = 1;", "let n1 = n0 * n0;", "typeof n1;"]


def test_declared_variables_are_statements_and_never_inlined():
    b = Builder()
    n = b.declare(b.load_int(1), DK.LET)
    b.reassign(n, b.binary(n, b.load_int(2), B.ADD))
    b.update(n, b.load_int(3), B.MUL)
    b.unary(U.POST_INC, n)
    b.unary(U.PRE_DEC, n)
    assert lines(js(b)) == ["let n1 = 1;", "n1 = n1 + 2;", "n1 *= 3;", "n1++;", "--n1;"]


def test_constructs_are_never_inlined_and_unused_effectful_values_become_statements():
    b = Builder()
    m = b.construct(b.builtin(Builtins.MAP))
    b.call_method(m, Methods.MAP_SET, [b.load_int(1), b.load_int(2)])
    assert lines(js(b)) == ["var o1 = new Map();", "o1.set(1, 2);"]


# ------------------------------------------------------------------ spellings
def test_number_spellings():
    assert spell_integer(255, Radix.HEX) == "0xff" and spell_integer(8, Radix.OCT) == "0o10" and spell_integer(5, Radix.BIN) == "0b101"
    assert spell_float(1e21) == "1e+21" and spell_float(0.1) == "0.1" and spell_float(1.0) == "1.0"
    assert spell_float(1500.0, FloatStyle.EXPONENT) == "1.5e3"
    assert spell_float(5e-324) == "5e-324"
    b = Builder()
    b.typeof(b.unary(U.MINUS, b.load_int(0)))
    b.typeof(b.unary(U.MINUS, b.unary(U.MINUS, b.load_int(1))))
    b.typeof(b.binary(b.unary(U.MINUS, b.load_int(2)), b.load_int(2), B.EXP))
    b.typeof(b.load_bigint(5))
    # same-precedence right-associative unaries are bracketed on the left (Expression.swift:69-80)
    assert lines(js(b)) == ["typeof (-0);", "typeof (-(-1));", "typeof ((-2) ** 2);", "typeof 5n;"]


def test_string_escaping():
    assert spell_string('a"b\\c\n') == '"a\\"b\\\\c\\n"'
    assert spell_string("\ud800") == '"\\ud800"'
    assert spell_string(" é") == '"\\u2028é"'


def test_precedence_and_brackets():
    b = Builder()
    x, y, z = b.load_int(1), b.load_int(2), b.load_int(3)
    s = b.binary(b.binary(x, y, B.ADD), z, B.MUL)            # nested binaries are always bracketed
    b.typeof(s)
    b.call_method(b.load_int(3), Methods.NUMBER_TO_STRING, [])   # number literal receiver
    b.typeof(b.ternary(b.compare(x, y, C.LESS), x, y))
    b.typeof(b.unary(U.LOGICAL_NOT, b.compare(x, y, C.EQUAL)))
    assert lines(js(b)) == ["typeof ((1 + 2) * 3);", "(3).toString();", "typeof (1 < 2 ? 1 : 2);", "typeof (!(1 == 2));"]


def test_keys_and_members():
    b = Builder()
    o = b.declare(b.builtin(Builtins.MATH))
    b.typeof(b.get_property(o, Keys.PI))
    b.typeof(b.get_property(o, Key("a-b")))
    b.typeof(b.get_property(o, Key("3", KeyKind.INDEX)))
    b.typeof(b.get_property(o, Keys.A, optional=True))
    b.typeof(b.get_element(o, -1))
    b.set_computed(o, b.load_string("k"), b.load_int(1))
    b.typeof(b.delete_property(o, Keys.X))
    assert lines(js(b)) == ["var o1 = Math;", "typeof o1.PI;", 'typeof o1["a-b"];', "typeof o1[3];", "typeof o1?.a;",
                            "typeof o1[-1];", 'o1["k"] = 1;', "typeof (delete o1.x);"]


# ------------------------------------------------------------------ constructs
def test_functions_declarations_and_expressions():
    b = Builder()
    with b.plain_function(Parameters(2, has_rest=True), named=True) as (f, (p, q)):
        b.do_return(b.get_property(q, Keys.LENGTH))
    with b.plain_function(Parameters(1, default_indices=(0,)), defaults=[b.load_int(7)]) as (g, (p,)):
        with b.plain_function(named=True) as (h, _):      # nested named: still a statement (function body, depth 0)
            b.do_return()
        with b.if_(b.load_bool(True)):
            with b.plain_function(named=True) as (k, _):  # inside a block: expression form
                pass
    with b.arrow_function(Parameters(1)) as (a, (p,)):
        b.do_return(p)
    with b.generator_function(named=True, strict=True) as (gen, _):
        b.yield_(b.load_int(1))
        b.yield_each(b.create_array([]))
    b.call_function(f, [b.load_int(1), b.load_int(2)])
    text = norm(js(b))
    assert text == [
        "function f#(p#, ...p#) {", "    return p#.length;", "}",
        "var f# = function (p# = 7) {",
        "    function f#() {", "        return;", "    }",
        "    if (true) {", "        var f# = function () {", "        };", "    }",
        "};",
        "var f# = (p#) => {", "    return p#;", "};",
        "function* f#() {", '    "use strict";', "    yield 1;", "    yield* [];", "}",
        "f#(1, 2);",
    ]


def test_object_literal_forms():
    b = Builder()
    one = b.load_int(1)
    with b.object_literal() as o:
        o.add_property(Keys.A, one)
        o.add_element(2, one)
    b.typeof(o.result)
    k = b.load_string("k")
    proto = b.load_null()
    with b.object_literal() as o2:
        o2.add_computed(k, one)
        o2.set_prototype(proto)
        with o2.method(Keys.M, Parameters(1)) as (this, (p,)):
            b.do_return(b.binary(b.get_super_property(Keys.A), p, B.ADD))
        with o2.getter(Keys.G) as this:
            b.do_return(this)
        with o2.setter(Keys.G) as (this, v):
            b.set_property(this, Keys.B, v)
    b.call_method(o2.result, Methods.OBJECT_HAS_OWN_PROPERTY, [b.load_string("a")])
    with b.object_literal() as o3:
        pass
    b.typeof(o3.result)
    text = norm(js(b))
    assert text == [
        "typeof { a: 1, 2: 1 };",
        "var o# = {", '    ["k"]: 1,', "    __proto__: null,",
        "    m(p#) {", "        return super.a + p#;", "    },",
        "    get g() {", "        return this;", "    },",
        "    set g(p#) {", "        this.b = p#;", "    },",
        "};",
        'o#.hasOwnProperty("a");',
        "typeof {};",
    ]


def test_loops_and_control_flow():
    b = Builder()
    n = b.declare(b.load_int(0))
    with b.while_loop() as loop:
        c = b.compare(n, b.load_int(3), C.LESS)
        with loop.body(c) as label:
            b.unary(U.POST_INC, n)
            b.break_(label)
    with b.do_while_loop() as loop:
        b.continue_()
        with loop.header() as h:
            h.done(b.load_bool(False))
    with b.for_loop() as loop:
        with loop.condition([b.load_int(0)]) as (i,):
            cond = b.compare(i, b.load_int(2), C.LESS)
        with loop.afterthought(cond) as (i2,):
            b.unary(U.POST_INC, i2)
        with loop.body() as ((i3,), label):
            b.update(n, i3, B.ADD)
    with b.for_each(ForEachKind.OF, b.create_array([n]), declaration=DK.CONST) as ((e,), label):
        b.typeof(e)
    with b.for_each(ForEachKind.IN, b.builtin(Builtins.MATH)) as ((k,), label):
        b.block_break(label)
    with b.repeat(5) as (i, label):
        with b.if_(b.compare(i, b.load_int(2), C.EQUAL), inverted=True) as br:
            b.continue_(label)
            with br.else_():
                b.break_()
    with b.try_() as t:
        b.throw(b.load_string("x"))
        with t.catch() as e:
            b.typeof(e)
        with t.finally_():
            b.typeof(n)
    one, two = b.load_int(1), b.load_int(2)
    with b.switch(n) as sw:
        with sw.case(one):
            b.switch_break()
        with sw.case(two, falls_through=True):
            b.typeof(n)
        with sw.default():
            b.typeof(n)
    with b.block() as label:
        b.block_break(label)
    with b.with_(b.builtin(Builtins.MATH)):
        b.typeof(n)
    text = norm(js(b))
    expected = [
        "var n# = 0;",
        "L#: while (n# < 3) {", "    n#++;", "    break L#;", "}",
        "do {", "    continue;", "} while (false);",
        "for (let i# = 0; i# < 2; i#++) {", "    n# += i#;", "}",
        "for (const i# of [n#]) {", "    typeof i#;", "}",
        "L#: for (var i# in Math) {", "    break L#;", "}",
        "L#: for (let i# = 0; i# < 5; i#++) {", "    if (!(i# == 2)) {", "        continue L#;", "    } else {",
        "        break;", "    }", "}",
        "try {", '    throw "x";', "} catch (e#) {", "    typeof e#;", "} finally {", "    typeof n#;", "}",
        "switch (n#) {", "    case 1:", "        break;", "        break;", "    case 2:", "        typeof n#;", "    default:",
        "        typeof n#;", "        break;", "}",
        "L#: {", "    break L#;", "}",
        "with (Math) {", "    typeof n#;", "}",
    ]
    assert text == expected


def test_destructuring():
    b = Builder()
    arr = b.create_int_array([1, 2, 3])
    a, rest = b.destruct(arr, ops.ArrayPattern((ops.ArrayElement(ops.Binding()), ops.ArrayElement(None)), rest=ops.Binding()))
    o = b.declare(b.builtin(Builtins.MATH))
    pat = ops.ObjectPattern((ops.ObjectProperty(Keys.PI, ops.Binding()), ops.ObjectProperty(None, ops.Binding(), has_default=True)), has_rest=True)
    x, y, r = b.destruct(o, pat, DK.CONST, extra=[b.load_string("E"), b.load_int(0)])
    b.destruct_and_reassign(arr, ops.ArrayPattern((ops.ArrayElement(ops.Binding()),)), [rest])
    text = lines(js(b))
    assert text[0] == "var arr0 = [1, 2, 3];" and text[1] == "var [n1, , ...v2] = arr0;"   # v2: later reassigned an integer
    assert text[3] == 'const {PI: v7, ["E"]: v8 = 0, ...o9} = o4;'
    assert text[4] == "[v2] = arr0;"


def test_probes_comments_and_strict():
    b = Builder()
    b.current_gen = "int"
    n = b.declare(b.load_int(1))
    b.probe()
    b.current_gen = "string"
    s = b.emit(ops.LoadString("x"), lift=Lift.VAR).output
    t = b.load_string("inlined")
    b.probe(ops.ProbeLabel.END)
    b.probe()                                   # directly after another probe: dropped
    code = b.finish()
    text = lift(code, LiftOptions(strict=True))
    assert lines(text) == ['"use strict";', "// [int]", "var n1 = 1;", '__probe("step", n1);', "// [string]", 'var s2 = "x";',
                           '__probe("end", s2, n1);']
    assert lines(lift(code, PLAIN)) == ["var n1 = 1;", 'var s2 = "x";']


def test_code_strings_are_lifted_and_escaped():
    b = Builder()
    n = b.declare(b.load_int(1))
    with b.code_string() as cs:
        b.reassign(n, b.load_string('a"b'))
    b.call_function(b.builtin(Builtins.EVAL), [cs.result])
    text = lines(js(b))
    assert text == ["var v1 = 1;", 'var s2 = "v1 = \\"a\\\\\\"b\\";\\n";', "eval(s2);"]


def test_every_operation_class_has_a_lift_rule():
    """A structural guard: the dispatcher names every operation class."""
    import inspect
    from oxpecker.il import lift as lift_module
    src = inspect.getsource(lift_module)
    for cls in ops.all_operation_classes():
        assert f"O.{cls.__name__}" in src or cls in ops.FUNCTION_BEGINS, cls.__name__


def test_configure_property_and_templates_and_regexps():
    b = Builder()
    o = b.declare(b.builtin(Builtins.MATH))
    with b.arrow_function() as (g, _):
        b.do_return(b.load_int(1))
    b.configure_property(o, Keys.X, PropertyFlag.CONFIGURABLE | PropertyFlag.ENUMERABLE, PropertyType.GETTER, g)
    b.configure_element(o, 0, PropertyFlag.WRITABLE, PropertyType.VALUE, b.load_int(2))
    b.typeof(b.create_template(("a", "b`${c", ""), [b.load_int(1), b.load_int(2)]))
    b.typeof(b.load_regexp("a+", RegExpFlag.GLOBAL | RegExpFlag.IGNORE_CASE))
    text = lines(js(b))
    assert text[4] == 'Object.defineProperty(o1, "x", { configurable: true, enumerable: true, get: f2 });'
    assert text[5] == "Object.defineProperty(o1, 0, { writable: true, value: 2 });"
    assert text[6] == "typeof `a${1}b\\`\\${c${2}`;"
    assert text[7] == "typeof /a+/gi;"
