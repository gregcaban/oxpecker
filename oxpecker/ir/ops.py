"""Operations and instructions.

Operations are small frozen dataclasses: their fields are the instruction's *parameters*
(constants, operators, property names), which the operation mutator changes. Inputs and outputs
are variable ids on `Instr`, which the input mutator rewires.

Literals keep their JavaScript *spelling* (`"0x7fffffff"`, `"1/3"`, `"-0"`), because the spelling
is what Rhino's parser sees; the pools in `gen/generators/values.py` are reused as they are.

Two classes of variables (decision D15). **Temporaries** are the outputs of expressions: defined
once, never assigned, and the lifter may inline them. **Declared variables** -- `DeclareVar`
outputs, function parameters, catch variables, function names -- have a storage kind and may be
assigned any number of times (`Reassign`, `CompoundAssign`, `Update`); they are what Rhino's
optimizer specialises, so the IR keeps them as statements. FuzzIL draws the same line.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, fields

from ..gen.types import T

ARITH = ("+", "-", "*", "/", "%", "**")
BITWISE = ("&", "|", "^", "<<", ">>", ">>>")
COMPARE = ("<", "<=", ">", ">=", "==", "!=", "===", "!==")
LOGICAL = ("&&", "||", "??")
RELATION = ("in", "instanceof")
BINARY_OPS = ARITH + BITWISE + COMPARE + LOGICAL + RELATION
UNARY_OPS = ("-", "+", "!", "~", "typeof", "void")
COMPOUND_OPS = ARITH + BITWISE
UPDATE_OPS = ("++", "--")

PRIMITIVE_TYPES = frozenset({T.NUMBER, T.INT32, T.BIGINT, T.STRING, T.BOOLEAN, T.UNDEFINED, T.NULL})


# ---------------------------------------------------------------------------- values

@dataclass(frozen=True)
class LoadLiteral:
    """A literal by its spelling (a temporary: primitive ones are spelled out at their uses unless
    the instruction is marked `keep`)."""
    type: T
    text: str


@dataclass(frozen=True)
class DeclareVar:
    """`kind name = in0;` -- a declared, assignable variable (kind var | let | const; `const` is
    never assigned). Where it sits decides its storage: top level, function local, block."""
    kind: str = "var"


@dataclass(frozen=True)
class LoadBuiltin:
    """A global binding by name (`Math`, `Object`, `Array.prototype`, `globalThis`, ...)."""
    name: str
    type: T = T.OBJECT


@dataclass(frozen=True)
class CreateArray:
    """`[in0, in1, ...]`, with holes at the given element positions; inputs whose index is in
    `spread` are spread (`...in2`)."""
    holes: tuple[int, ...] = ()
    spread: tuple[int, ...] = ()


@dataclass(frozen=True)
class CreateObject:
    """`{k0: in0, k1: in1, ...}`."""
    keys: tuple[str, ...] = ()


# ---------------------------------------------------------------------------- expressions

@dataclass(frozen=True)
class Unary:
    op: str


@dataclass(frozen=True)
class Binary:
    op: str


@dataclass(frozen=True)
class Conditional:
    """`in0 ? in1 : in2`."""


@dataclass(frozen=True)
class GetProperty:
    name: str


@dataclass(frozen=True)
class SetProperty:
    """`in0.name = in1;`"""
    name: str


@dataclass(frozen=True)
class GetElement:
    """`in0[in1]`."""


@dataclass(frozen=True)
class SetElement:
    """`in0[in1] = in2;`"""


@dataclass(frozen=True)
class DeleteProperty:
    name: str


@dataclass(frozen=True)
class CallFunction:
    """`in0(in1, ...)`; any argument count, whatever the callee's arity. With `discard` a statement
    (no output): results nothing captured before are not turned into probed variables. Arguments
    whose position (0 = first argument) is in `spread` are spread (`f(...a)`)."""
    discard: bool = False
    spread: tuple[int, ...] = ()


@dataclass(frozen=True)
class CallMethod:
    """`in0.name(in1, ...)`; `discard` and `spread` as for CallFunction."""
    name: str
    discard: bool = False
    spread: tuple[int, ...] = ()


@dataclass(frozen=True)
class Construct:
    """`new in0(in1, ...)`; `discard` and `spread` as for CallFunction."""
    discard: bool = False
    spread: tuple[int, ...] = ()


@dataclass(frozen=True)
class Reassign:
    """`in0 = in1;` (in0 must be a declared variable; FuzzIL's Reassign)."""


@dataclass(frozen=True)
class CompoundAssign:
    """`in0 op= in1;` (FuzzIL's Update)."""
    op: str


@dataclass(frozen=True)
class Update:
    """`out = in0++` (or `++in0` with prefix); with `value=False` a statement (`in0++;`, no output)."""
    op: str
    prefix: bool = False
    value: bool = True


@dataclass(frozen=True)
class Eval:
    """Escape hatch for constructs not modelled yet (Fuzzilli has the same instruction):
    `code` with `{0}`, `{1}`, ... standing for the inputs. With an output type it is an
    expression (`var out = code;`), without one a statement (`code;`)."""
    code: str
    out: T | None = None
    pure: bool = False      # no side effects: the lifter may inline it


# ---------------------------------------------------------------------------- blocks

@dataclass(frozen=True)
class BeginFunction:
    """outputs: the function; inner: its parameters. `style` is decl | expr | arrow | generator
    (`function*`, may contain Yield); a declaration inside an if/loop/try block is lifted as an
    expression (no Annex B semantics)."""
    arity: int
    style: str = "decl"
    strict: bool = False


@dataclass(frozen=True)
class EndFunction:
    pass


@dataclass(frozen=True)
class Return:
    """`return in0;`, or `return;` without inputs."""


@dataclass(frozen=True)
class BeginIf:
    """`if (in0) {`"""


@dataclass(frozen=True)
class BeginElse:
    pass


@dataclass(frozen=True)
class EndIf:
    pass


@dataclass(frozen=True)
class BeginFor:
    """`for (var i = 0; i < count; i++) {` -- always bounded; inner: the counter, which nothing
    may reassign. An optional `label` (`L: for ...`) is a target for labelled break/continue."""
    count: int
    label: str | None = None


@dataclass(frozen=True)
class EndFor:
    pass


@dataclass(frozen=True)
class BeginWhile:
    """A bounded `while` / `do ... while`: inner is a counter the loop itself decrements, so every
    loop terminates whatever its body does -- `var c = count; while (c > 0) { c--; ...}` or
    `var c = count; do { ... } while (c-- > 0);`. Nothing may assign the counter."""
    count: int
    do: bool = False
    label: str | None = None


@dataclass(frozen=True)
class EndWhile:
    pass


@dataclass(frozen=True)
class BeginForEach:
    """`for (var x of in0) {` (kind "of") or `for (var k in in0) {` (kind "in"); inner: the
    element/key variable (assignable like a parameter). Terminates when the iterable does; a
    user-defined infinite iterator is stopped by the harness's instruction budget."""
    kind: str = "of"
    label: str | None = None


@dataclass(frozen=True)
class EndForEach:
    pass


@dataclass(frozen=True)
class BeginSwitch:
    """`switch (in0) {`; its body is a sequence of Case blocks, nothing else."""


@dataclass(frozen=True)
class Case:
    """`case in0:` -- or `default:` without inputs; each case body is its own scope in the IR."""


@dataclass(frozen=True)
class EndSwitch:
    pass


@dataclass(frozen=True)
class BeginWith:
    """`with (in0) {` -- sloppy mode only."""


@dataclass(frozen=True)
class EndWith:
    pass


@dataclass(frozen=True)
class BeginBlock:
    """A bare `{`, or with a `label` a labelled block (`L: {`), a target for `break L`."""
    label: str | None = None


@dataclass(frozen=True)
class EndBlock:
    pass


@dataclass(frozen=True)
class Break:
    """`break;` / `break label;` -- inside a loop or switch, or (labelled) inside the labelled
    statement."""
    label: str | None = None


@dataclass(frozen=True)
class Continue:
    """`continue;` / `continue label;` -- inside a loop (a labelled one when labelled)."""
    label: str | None = None


@dataclass(frozen=True)
class Yield:
    """`yield in0` / `yield* in0` (`delegate`) / bare `yield` -- only directly inside a generator
    function. With `value` the received value is an output (`var r = yield x`), else a statement."""
    delegate: bool = False
    value: bool = True


@dataclass(frozen=True)
class BeginTry:
    pass


@dataclass(frozen=True)
class BeginCatch:
    """inner: the exception variable."""


@dataclass(frozen=True)
class BeginFinally:
    pass


@dataclass(frozen=True)
class EndTry:
    pass


@dataclass(frozen=True)
class Throw:
    pass


@dataclass(frozen=True)
class Probe:
    """`__probe("pN", <visible variables>)`: the lifter fills in the variables."""
    label: str = ""


BEGIN = (BeginFunction, BeginIf, BeginFor, BeginWhile, BeginForEach, BeginSwitch, BeginWith, BeginBlock, BeginTry)
MIDDLE = (BeginElse, BeginCatch, BeginFinally, Case)
END = (EndFunction, EndIf, EndFor, EndWhile, EndForEach, EndSwitch, EndWith, EndBlock, EndTry)
END_OF = {BeginFunction: EndFunction, BeginIf: EndIf, BeginFor: EndFor, BeginWhile: EndWhile,
          BeginForEach: EndForEach, BeginSwitch: EndSwitch, BeginWith: EndWith, BeginBlock: EndBlock,
          BeginTry: EndTry}
LOOPS = (BeginFor, BeginWhile, BeginForEach)

Op = (LoadLiteral | DeclareVar | LoadBuiltin | CreateArray | CreateObject | Unary | Binary | Conditional | GetProperty
      | SetProperty | GetElement | SetElement | DeleteProperty | CallFunction | CallMethod | Construct
      | Reassign | CompoundAssign | Update | Eval | BeginFunction | EndFunction | Return | BeginIf
      | BeginElse | EndIf | BeginFor | EndFor | BeginWhile | EndWhile | BeginForEach | EndForEach | BeginSwitch
      | Case | EndSwitch | BeginWith | EndWith | BeginBlock | EndBlock | Break | Continue | Yield | BeginTry
      | BeginCatch | BeginFinally | EndTry | Throw | Probe)


@dataclass(frozen=True)
class Instr:
    op: Op
    inputs: tuple[int, ...] = ()
    outputs: tuple[int, ...] = ()     # visible after the instruction (BeginFunction: the function)
    inner: tuple[int, ...] = ()       # visible only inside the block it opens (parameters, counter, e)
    keep: bool | None = None          # lifting: keep as its own statement (True), may inline (False);
                                      # None = undecided, drawn by `lifting_choices` (D16)
    hint: str | None = None           # lifting: a role name for the output (`acc`, `ctor`), else by type
    gen: str | None = None            # provenance: the generator that emitted it (comments, inspector)
    origin: int = 0                   # provenance: which generator invocation

    def replace(self, **kw) -> "Instr":
        return dataclasses.replace(self, **kw)

    def renamed(self, rn) -> "Instr":
        f = lambda vs: tuple(rn(v) for v in vs)
        return dataclasses.replace(self, inputs=f(self.inputs), outputs=f(self.outputs), inner=f(self.inner))


Program = tuple[Instr, ...]


def params(op: Op) -> dict:
    return {f.name: getattr(op, f.name) for f in fields(op)}


def signature(ins: Instr) -> tuple:
    """The instruction without variable ids or provenance (locality metrics, dedup). `keep` is part
    of it: it changes the lifted program."""
    return (type(ins.op).__name__, *map(repr, params(ins.op).values()), len(ins.inputs), ins.keep)
