# Translated from Fuzzilli (Copyright 2019-2025 Google LLC, Apache License 2.0),
# Sources/Fuzzilli/FuzzIL/JsOperations.swift, Operation.swift and Context.swift at commit
# be2811d00282320381fc8007d64873745772011d, restricted to what Rhino supports as decided in
# docs/design-generation-layer.md §3.3. Line numbers in comments are JsOperations.swift lines.
# See THIRD_PARTY_NOTICES.md and docs/licenses/Apache-2.0.txt.
"""Operations and the closed vocabularies they interpret.

Every construct a generator can build is one of the operation classes below; operators,
keywords and modes are enums; builtins, keys and methods are registry entries (environment.py).
The only `str` fields are the characters of a string literal (`LoadString.value`), a regexp
pattern (`LoadRegExp.pattern`) and the fixed parts of a template literal
(`CreateTemplateString.parts`). A guard test enumerates them."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, Flag, auto
from typing import TYPE_CHECKING, ClassVar

from .types import Key, Type

if TYPE_CHECKING:
    from .environment import Builtin, Method


# ------------------------------------------------------------------------------- vocabularies
class Attr(Flag):                                   # Operation.swift:99-152
    NONE = 0
    MUTABLE = auto()
    CALL = auto()
    BLOCK_START = auto()
    BLOCK_END = auto()
    JUMP = auto()
    VARIADIC = auto()
    SINGULAR = auto()
    PROPAGATES_CONTEXT = auto()
    RESUMES_CONTEXT = auto()


class Context(Flag):                                # Context.swift, minus wasm/async/class/module/worker; plus WITH
    EMPTY = 0
    JAVASCRIPT = auto()
    SUBROUTINE = auto()
    GENERATOR_FUNCTION = auto()
    METHOD = auto()
    LOOP = auto()
    OBJECT_LITERAL = auto()
    SWITCH_BLOCK = auto()
    SWITCH_CASE = auto()
    WITH = auto()


class UnaryOperator(Enum):                          # 1998-2018
    PRE_INC = "pre-inc"
    PRE_DEC = "pre-dec"
    POST_INC = "post-inc"
    POST_DEC = "post-dec"
    LOGICAL_NOT = "logical-not"
    BITWISE_NOT = "bitwise-not"
    PLUS = "plus"
    MINUS = "minus"

    @property
    def reassigns_input(self) -> bool:
        return self in INCREMENTS

    @property
    def is_postfix(self) -> bool:
        return self in (UnaryOperator.POST_INC, UnaryOperator.POST_DEC)


INCREMENTS = frozenset({UnaryOperator.PRE_INC, UnaryOperator.PRE_DEC, UnaryOperator.POST_INC, UnaryOperator.POST_DEC})


class BinaryOperator(Enum):                         # 2033-2052
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    MOD = "mod"
    BIT_AND = "bit-and"
    BIT_OR = "bit-or"
    LOGIC_AND = "logic-and"
    LOGIC_OR = "logic-or"
    XOR = "xor"
    LSHIFT = "lshift"
    RSHIFT = "rshift"
    EXP = "exp"
    U_RSHIFT = "u-rshift"
    NULL_COALESCE = "null-coalesce"


ARITHMETIC = frozenset({BinaryOperator.ADD, BinaryOperator.SUB, BinaryOperator.MUL, BinaryOperator.DIV,
                        BinaryOperator.MOD, BinaryOperator.EXP})
BITWISE = frozenset({BinaryOperator.BIT_AND, BinaryOperator.BIT_OR, BinaryOperator.XOR, BinaryOperator.LSHIFT,
                     BinaryOperator.RSHIFT, BinaryOperator.U_RSHIFT})
LOGICAL = frozenset({BinaryOperator.LOGIC_AND, BinaryOperator.LOGIC_OR, BinaryOperator.NULL_COALESCE})
OPERATOR_GROUPS: tuple[frozenset[BinaryOperator], ...] = (ARITHMETIC, BITWISE, LOGICAL)


class Comparator(Enum):                             # 2101-2114
    EQUAL = "equal"
    STRICT_EQUAL = "strict-equal"
    NOT_EQUAL = "not-equal"
    STRICT_NOT_EQUAL = "strict-not-equal"
    LESS = "less"
    LESS_EQUAL = "less-equal"
    GREATER = "greater"
    GREATER_EQUAL = "greater-equal"


EQUALITY = frozenset({Comparator.EQUAL, Comparator.STRICT_EQUAL, Comparator.NOT_EQUAL, Comparator.STRICT_NOT_EQUAL})
RELATIONAL = frozenset({Comparator.LESS, Comparator.LESS_EQUAL, Comparator.GREATER, Comparator.GREATER_EQUAL})


class RegExpFlag(Flag):                             # 281-365, minus unicodeSets and hasIndices (no `v`, no `d` in Rhino b95f75d)
    NONE = 0
    IGNORE_CASE = auto()
    GLOBAL = auto()
    MULTILINE = auto()
    DOT_ALL = auto()
    UNICODE = auto()
    STICKY = auto()


class PropertyFlag(Flag):                           # 1150-1169
    NONE = 0
    WRITABLE = auto()
    CONFIGURABLE = auto()
    ENUMERABLE = auto()


class PropertyType(Enum):                           # 1171-1176
    VALUE = "value"
    GETTER = "getter"
    SETTER = "setter"
    GETTER_SETTER = "getter-setter"


class DeclarationKind(Enum):
    VAR = "var"
    LET = "let"
    CONST = "const"


class NamedVariableMode(Enum):                      # 181-195, minus `none` (that is LoadBuiltin)
    GLOBAL = "global"
    VAR = "var"
    LET = "let"
    CONST = "const"


class ForEachKind(Enum):                            # 2672-2675
    OF = "of"
    IN = "in"


class Radix(Enum):
    DEC = 10
    HEX = 16
    OCT = 8
    BIN = 2


class FloatStyle(Enum):
    PLAIN = "plain"
    EXPONENT = "exponent"


class Lift(Enum):
    """How the lifter treats a value-producing instruction (design §3.6)."""
    INLINE = "inline"
    VAR = "var"
    LET = "let"
    CONST = "const"


class DirectiveKind(Enum):
    USE_STRICT = "use strict"


class ProbeLabel(Enum):
    STEP = "step"
    END = "end"
    WARM = "warm"
    CHANGED = "changed"
    CATCH = "catch"
    PAUSE = "pause"
    JAVA = "java"


# ------------------------------------------------------------------------------- destructuring
# JsOperations.swift:3571-3702. Targets: a fresh binding, a member of an existing object, or a nested pattern.
@dataclass(frozen=True, slots=True)
class Binding:
    """Declares (Destruct) or assigns (DestructAndReassign) a plain variable."""


@dataclass(frozen=True, slots=True)
class PropertyTarget:
    key: Key


@dataclass(frozen=True, slots=True)
class ElementTarget:
    index: int


@dataclass(frozen=True, slots=True)
class ComputedTarget:
    """`<obj>[<key>] = ...`; takes two inputs."""


@dataclass(frozen=True, slots=True)
class ObjectProperty:
    key: Key | None                 # None = computed key (one extra input)
    target: Target
    has_default: bool = False


@dataclass(frozen=True, slots=True)
class ObjectPattern:
    properties: tuple[ObjectProperty, ...]
    has_rest: bool = False          # `...rest`, always a fresh binding


@dataclass(frozen=True, slots=True)
class ArrayElement:
    target: Target | None           # None = hole
    has_default: bool = False


@dataclass(frozen=True, slots=True)
class ArrayPattern:
    elements: tuple[ArrayElement, ...]
    rest: Target | None = None


Target = Binding | PropertyTarget | ElementTarget | ComputedTarget | ObjectPattern | ArrayPattern
Pattern = ObjectPattern | ArrayPattern


def num_bindings(t: Target | None) -> int:
    if t is None or isinstance(t, (PropertyTarget, ElementTarget, ComputedTarget)):
        return 0
    if isinstance(t, Binding):
        return 1
    if isinstance(t, ObjectPattern):
        return sum(num_bindings(p.target) for p in t.properties) + (1 if t.has_rest else 0)
    return sum(num_bindings(e.target) for e in t.elements) + num_bindings(t.rest)


def _target_inputs(t: Target | None) -> int:
    if t is None or isinstance(t, Binding):
        return 0
    if isinstance(t, (PropertyTarget, ElementTarget)):
        return 1
    if isinstance(t, ComputedTarget):
        return 2
    return num_extra_inputs(t)


def num_extra_inputs(p: Pattern) -> int:
    """Inputs beyond the source: computed keys, default values and member-target objects, in
    depth-first left-to-right pattern order (JsOperations.swift:3668-3693)."""
    if isinstance(p, ObjectPattern):
        return sum((1 if q.key is None else 0) + (1 if q.has_default else 0) + _target_inputs(q.target) for q in p.properties)
    return sum((1 if e.has_default else 0) + _target_inputs(e.target) for e in p.elements) + _target_inputs(p.rest)


@dataclass(frozen=True, slots=True)
class Parameters:                                   # 1382-1431
    count: int
    has_rest: bool = False
    default_indices: tuple[int, ...] = ()
    destructuring: tuple[tuple[int, Pattern], ...] = ()

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("parameter count")
        if tuple(sorted(self.default_indices)) != self.default_indices:
            raise ValueError("default indices must be sorted")
        for i in self.default_indices:
            if not 0 <= i < self.count or (self.has_rest and i == self.count - 1):
                raise ValueError("default index out of range or on the rest parameter")
        for i, _ in self.destructuring:
            if not 0 <= i < self.count:
                raise ValueError("destructuring index out of range")

    @property
    def num_defaults(self) -> int:
        return len(self.default_indices)

    @property
    def num_bindings(self) -> int:
        """Inner outputs of the subroutine: one per plain parameter, the bindings of a destructured one."""
        return self.count - len(self.destructuring) + sum(num_bindings(p) for _, p in self.destructuring)


# ------------------------------------------------------------------------------- base
@dataclass(frozen=True)
class Operation:
    """Base of every operation. Subclass fields are the operation's parameters; the class
    attributes describe inputs, outputs, inner outputs and context (Operation.swift:19-98)."""
    num_inputs: ClassVar[int] = 0
    num_outputs: ClassVar[int] = 0
    num_inner_outputs: ClassVar[int] = 0
    first_variadic: ClassVar[int | None] = None
    attrs: ClassVar[Attr] = Attr.NONE
    required_context: ClassVar[Context] = Context.JAVASCRIPT
    context_opened: ClassVar[Context] = Context.EMPTY

    def inputs(self) -> int:
        return self.num_inputs

    def outputs(self) -> int:
        return self.num_outputs

    def inner_outputs(self) -> int:
        return self.num_inner_outputs

    def opened(self) -> Context:
        return self.context_opened

    def required(self) -> Context:
        return self.required_context

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    def is_block_start(self) -> bool:
        return bool(self.attrs & Attr.BLOCK_START)

    @property
    def is_block_end(self) -> bool:
        return bool(self.attrs & Attr.BLOCK_END)

    @property
    def is_block(self) -> bool:
        return bool(self.attrs & (Attr.BLOCK_START | Attr.BLOCK_END))

    @property
    def is_block_group_start(self) -> bool:
        return self.is_block_start and not self.is_block_end

    @property
    def is_block_group_end(self) -> bool:
        return self.is_block_end and not self.is_block_start

    @property
    def is_jump(self) -> bool:
        return bool(self.attrs & Attr.JUMP)

    @property
    def is_variadic(self) -> bool:
        return bool(self.attrs & Attr.VARIADIC)

    @property
    def is_call(self) -> bool:
        return bool(self.attrs & Attr.CALL)

    @property
    def is_mutable(self) -> bool:
        return bool(self.attrs & Attr.MUTABLE)

    @property
    def is_singular(self) -> bool:
        return bool(self.attrs & Attr.SINGULAR)

    @property
    def propagates_context(self) -> bool:
        return bool(self.attrs & Attr.PROPAGATES_CONTEXT)

    @property
    def resumes_context(self) -> bool:
        return bool(self.attrs & Attr.RESUMES_CONTEXT)


_BS = Attr.BLOCK_START
_BE = Attr.BLOCK_END
_P = Attr.PROPAGATES_CONTEXT
_JS = Context.JAVASCRIPT


# ------------------------------------------------------------------------------- literals
@dataclass(frozen=True)
class LoadInteger(Operation):                       # 70
    value: int
    radix: Radix = Radix.DEC
    num_outputs = 1
    attrs = Attr.MUTABLE

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("JavaScript has no negative literals: use UnaryOperation(MINUS, ...)")
        if self.value > 2 ** 53:
            raise ValueError("integer literal beyond 2**53; use LoadBigInt or LoadFloat")


@dataclass(frozen=True)
class LoadBigInt(Operation):                        # 83
    value: int
    num_outputs = 1
    attrs = Attr.MUTABLE

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("no negative literals")


@dataclass(frozen=True)
class LoadFloat(Operation):                         # 95
    value: float
    style: FloatStyle = FloatStyle.PLAIN
    num_outputs = 1
    attrs = Attr.MUTABLE

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or math.copysign(1.0, self.value) < 0:
            raise ValueError("float literals are finite and non-negative; NaN/Infinity are builtins, -x is a unary minus")


@dataclass(frozen=True)
class LoadString(Operation):                        # 106
    value: str                                      # the characters; the lifter quotes and escapes
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class LoadBoolean(Operation):                       # 119
    value: bool
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class LoadUndefined(Operation):                     # 130
    num_outputs = 1


@dataclass(frozen=True)
class LoadNull(Operation):                          # 138
    num_outputs = 1


@dataclass(frozen=True)
class LoadThis(Operation):                          # 146
    num_outputs = 1


@dataclass(frozen=True)
class LoadArguments(Operation):                     # 154
    num_outputs = 1
    required_context = _JS | Context.SUBROUTINE


@dataclass(frozen=True)
class LoadNewTarget(Operation):                     # 3046
    num_outputs = 1
    required_context = _JS | Context.SUBROUTINE


@dataclass(frozen=True)
class LoadRegExp(Operation):                        # 386
    pattern: str
    flags: RegExpFlag = RegExpFlag.NONE
    num_outputs = 1
    attrs = Attr.MUTABLE

    def __post_init__(self) -> None:
        if not self.pattern:
            raise ValueError("an empty regexp pattern would lift as a comment")


@dataclass(frozen=True)
class LoadBuiltin(Operation):
    """A global from the registry (replaces FuzzIL's CreateNamedVariable with mode `none`)."""
    builtin: Builtin
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class CreateNamedVariable(Operation):               # 214; always with an initial value here
    identifier: Key
    mode: NamedVariableMode
    num_inputs = 1
    num_outputs = 1
    attrs = Attr.MUTABLE

    def __post_init__(self) -> None:
        if not self.identifier.is_identifier:
            raise ValueError("a named variable needs an identifier name")


@dataclass(frozen=True)
class Declare(Operation):
    """`var|let|const v = <input>`: the declared, assignable class of variable (design §3.2).
    Replaces FuzzIL's Dup (2096)."""
    kind: DeclarationKind = DeclarationKind.VAR
    num_inputs = 1
    num_outputs = 1
    attrs = Attr.MUTABLE


# ------------------------------------------------------------------------------- object literals
@dataclass(frozen=True)
class BeginObjectLiteral(Operation):                # 428
    attrs = _BS
    context_opened = Context.OBJECT_LITERAL


@dataclass(frozen=True)
class ObjectLiteralAddProperty(Operation):          # 437
    key: Key
    num_inputs = 1
    attrs = Attr.MUTABLE
    required_context = Context.OBJECT_LITERAL


@dataclass(frozen=True)
class ObjectLiteralAddElement(Operation):           # 449
    index: int
    num_inputs = 1
    attrs = Attr.MUTABLE
    required_context = Context.OBJECT_LITERAL


@dataclass(frozen=True)
class ObjectLiteralAddComputedProperty(Operation):  # 461: inputs (key, value)
    num_inputs = 2
    required_context = Context.OBJECT_LITERAL


@dataclass(frozen=True)
class ObjectLiteralCopyProperties(Operation):       # 470: `...<input>`
    num_inputs = 1
    required_context = Context.OBJECT_LITERAL


@dataclass(frozen=True)
class ObjectLiteralSetPrototype(Operation):         # 479: `__proto__: <input>`
    num_inputs = 1
    attrs = Attr.SINGULAR
    required_context = Context.OBJECT_LITERAL


_METHOD_CTX = _JS | Context.SUBROUTINE | Context.METHOD


@dataclass(frozen=True)
class BeginObjectLiteralMethod(Operation):          # 489; inner outputs: this, then parameters
    key: Key
    parameters: Parameters = Parameters(0)
    param_types: tuple[Type, ...] = ()           # declared parameter types (a hint for the typer; () = all ANY)
    generator: bool = False
    attrs = _BS | Attr.MUTABLE
    required_context = Context.OBJECT_LITERAL

    def inputs(self) -> int:
        return self.parameters.num_defaults

    def inner_outputs(self) -> int:
        return 1 + self.parameters.num_bindings

    def opened(self) -> Context:
        return _METHOD_CTX | (Context.GENERATOR_FUNCTION if self.generator else Context.EMPTY)


@dataclass(frozen=True)
class EndObjectLiteralMethod(Operation):            # 513
    attrs = _BE


@dataclass(frozen=True)
class BeginObjectLiteralComputedMethod(Operation):  # 518; input 0 = key, then defaults
    parameters: Parameters = Parameters(0)
    param_types: tuple[Type, ...] = ()           # declared parameter types (a hint for the typer; () = all ANY)
    generator: bool = False
    attrs = _BS
    required_context = Context.OBJECT_LITERAL

    def inputs(self) -> int:
        return 1 + self.parameters.num_defaults

    def inner_outputs(self) -> int:
        return 1 + self.parameters.num_bindings

    def opened(self) -> Context:
        return _METHOD_CTX | (Context.GENERATOR_FUNCTION if self.generator else Context.EMPTY)


@dataclass(frozen=True)
class EndObjectLiteralComputedMethod(Operation):    # 539
    attrs = _BE


@dataclass(frozen=True)
class BeginObjectLiteralGetter(Operation):          # 544; inner output: this
    key: Key
    num_inner_outputs = 1
    attrs = _BS | Attr.MUTABLE
    required_context = Context.OBJECT_LITERAL
    context_opened = _METHOD_CTX


@dataclass(frozen=True)
class EndObjectLiteralGetter(Operation):            # 559
    attrs = _BE


@dataclass(frozen=True)
class BeginObjectLiteralComputedGetter(Operation):  # 563; input: key; inner: this
    num_inputs = 1
    num_inner_outputs = 1
    attrs = _BS
    required_context = Context.OBJECT_LITERAL
    context_opened = _METHOD_CTX


@dataclass(frozen=True)
class EndObjectLiteralComputedGetter(Operation):    # 576
    attrs = _BE


@dataclass(frozen=True)
class BeginObjectLiteralSetter(Operation):          # 581; inner outputs: this, value
    key: Key
    num_inner_outputs = 2
    attrs = _BS | Attr.MUTABLE
    required_context = Context.OBJECT_LITERAL
    context_opened = _METHOD_CTX


@dataclass(frozen=True)
class EndObjectLiteralSetter(Operation):            # 596
    attrs = _BE


@dataclass(frozen=True)
class BeginObjectLiteralComputedSetter(Operation):  # 600; input: key; inner: this, value
    num_inputs = 1
    num_inner_outputs = 2
    attrs = _BS
    required_context = Context.OBJECT_LITERAL
    context_opened = _METHOD_CTX


@dataclass(frozen=True)
class EndObjectLiteralComputedSetter(Operation):    # 613
    attrs = _BE


@dataclass(frozen=True)
class EndObjectLiteral(Operation):                  # 617: defines the object
    num_outputs = 1
    attrs = _BE
    required_context = Context.OBJECT_LITERAL


# ------------------------------------------------------------------------------- arrays, templates
@dataclass(frozen=True)
class CreateArray(Operation):                       # 1010
    num_elements: int = 0
    num_outputs = 1
    first_variadic = 0
    attrs = Attr.VARIADIC

    def inputs(self) -> int:
        return self.num_elements


@dataclass(frozen=True)
class CreateIntArray(Operation):                    # 1024
    values: tuple[int, ...]
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class CreateFloatArray(Operation):                  # 1035
    values: tuple[float, ...]
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class CreateArrayWithSpread(Operation):             # 1046
    spreads: tuple[bool, ...]
    num_outputs = 1
    first_variadic = 0
    attrs = Attr.VARIADIC | Attr.MUTABLE

    def inputs(self) -> int:
        return len(self.spreads)


@dataclass(frozen=True)
class CreateTemplateString(Operation):              # 1063: parts[0] v0 parts[1] v1 ... parts[n]
    parts: tuple[str, ...]
    num_outputs = 1
    first_variadic = 0
    attrs = Attr.VARIADIC | Attr.MUTABLE

    def __post_init__(self) -> None:
        if not self.parts:
            raise ValueError("a template has at least one part")

    def inputs(self) -> int:
        return len(self.parts) - 1


# ------------------------------------------------------------------------------- properties
@dataclass(frozen=True)
class GetProperty(Operation):                       # 1082
    key: Key
    receiver_optional: bool = False
    num_inputs = 1
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class SetProperty(Operation):                       # 1099: (object, value)
    key: Key
    num_inputs = 2
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class UpdateProperty(Operation):                    # 1117: `o.k op= v`
    key: Key
    op: BinaryOperator
    num_inputs = 2
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class DeleteProperty(Operation):                    # 1136
    key: Key
    receiver_optional: bool = False
    num_inputs = 1
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class ConfigureProperty(Operation):                 # 1180: (object, value | getter [, setter])
    key: Key
    flags: PropertyFlag
    type: PropertyType
    attrs = Attr.MUTABLE

    def inputs(self) -> int:
        return 3 if self.type is PropertyType.GETTER_SETTER else 2


@dataclass(frozen=True)
class GetElement(Operation):                        # 1195
    index: int
    receiver_optional: bool = False
    num_inputs = 1
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class SetElement(Operation):                        # 1212
    index: int
    num_inputs = 2
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class UpdateElement(Operation):                     # 1229
    index: int
    op: BinaryOperator
    num_inputs = 2
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class DeleteElement(Operation):                     # 1248
    index: int
    receiver_optional: bool = False
    num_inputs = 1
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class ConfigureElement(Operation):                  # 1265
    index: int
    flags: PropertyFlag
    type: PropertyType
    attrs = Attr.MUTABLE

    def inputs(self) -> int:
        return 3 if self.type is PropertyType.GETTER_SETTER else 2


@dataclass(frozen=True)
class GetComputedProperty(Operation):               # 1280: (object, key)
    receiver_optional: bool = False
    num_inputs = 2
    num_outputs = 1


@dataclass(frozen=True)
class SetComputedProperty(Operation):               # 1295: (object, key, value)
    num_inputs = 3


@dataclass(frozen=True)
class UpdateComputedProperty(Operation):            # 1310
    op: BinaryOperator
    num_inputs = 3
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class DeleteComputedProperty(Operation):            # 1327
    receiver_optional: bool = False
    num_inputs = 2
    num_outputs = 1


@dataclass(frozen=True)
class ConfigureComputedProperty(Operation):         # 1342: (object, key, value | getter [, setter])
    flags: PropertyFlag
    type: PropertyType
    attrs = Attr.MUTABLE

    def inputs(self) -> int:
        return 4 if self.type is PropertyType.GETTER_SETTER else 3


@dataclass(frozen=True)
class TypeOf(Operation):                            # 1355
    num_inputs = 1
    num_outputs = 1


@dataclass(frozen=True)
class Void(Operation):                              # 1363
    num_inputs = 1
    num_outputs = 1


@dataclass(frozen=True)
class TestInstanceOf(Operation):                    # 1371: (value, constructor)
    num_inputs = 2
    num_outputs = 1


@dataclass(frozen=True)
class TestIn(Operation):                            # 1379: (key, object)
    num_inputs = 2
    num_outputs = 1


# ------------------------------------------------------------------------------- functions
@dataclass(frozen=True)
class BeginPlainFunction(Operation):                # 1504 (BeginAnyFunction 1472)
    parameters: Parameters = Parameters(0)
    param_types: tuple[Type, ...] = ()           # declared parameter types (a hint for the typer; () = all ANY)
    named: bool = False             # a function statement with a name (direct-call target) where legal
    strict: bool = False
    num_outputs = 1
    attrs = _BS
    context_opened = _JS | Context.SUBROUTINE

    def inputs(self) -> int:
        return self.parameters.num_defaults

    def inner_outputs(self) -> int:
        return self.parameters.num_bindings


@dataclass(frozen=True)
class EndPlainFunction(Operation):                  # 1507
    attrs = _BE


@dataclass(frozen=True)
class BeginArrowFunction(Operation):                # 1527
    parameters: Parameters = Parameters(0)
    param_types: tuple[Type, ...] = ()           # declared parameter types (a hint for the typer; () = all ANY)
    num_outputs = 1
    attrs = _BS
    context_opened = _JS | Context.SUBROUTINE

    def inputs(self) -> int:
        return self.parameters.num_defaults

    def inner_outputs(self) -> int:
        return self.parameters.num_bindings


@dataclass(frozen=True)
class EndArrowFunction(Operation):                  # 1530
    attrs = _BE


@dataclass(frozen=True)
class BeginGeneratorFunction(Operation):            # 1535
    parameters: Parameters = Parameters(0)
    param_types: tuple[Type, ...] = ()           # declared parameter types (a hint for the typer; () = all ANY)
    named: bool = False
    strict: bool = False
    num_outputs = 1
    attrs = _BS
    context_opened = _JS | Context.SUBROUTINE | Context.GENERATOR_FUNCTION

    def inputs(self) -> int:
        return self.parameters.num_defaults

    def inner_outputs(self) -> int:
        return self.parameters.num_bindings


@dataclass(frozen=True)
class EndGeneratorFunction(Operation):              # 1544
    attrs = _BE


@dataclass(frozen=True)
class Directive(Operation):                         # 1616
    kind: DirectiveKind = DirectiveKind.USE_STRICT


@dataclass(frozen=True)
class Return(Operation):                            # 1631
    has_value: bool = True
    attrs = Attr.JUMP
    required_context = _JS | Context.SUBROUTINE

    def inputs(self) -> int:
        return 1 if self.has_value else 0


@dataclass(frozen=True)
class Yield(Operation):                             # 1647: output = the value sent in
    has_value: bool = True
    num_outputs = 1
    required_context = _JS | Context.GENERATOR_FUNCTION

    def inputs(self) -> int:
        return 1 if self.has_value else 0


@dataclass(frozen=True)
class YieldEach(Operation):                         # 1663
    num_inputs = 1
    required_context = _JS | Context.GENERATOR_FUNCTION


# ------------------------------------------------------------------------------- calls
@dataclass(frozen=True)
class CallFunction(Operation):                      # 1682: (callee, args...)
    num_arguments: int = 0
    call_optional: bool = False
    num_outputs = 1
    first_variadic = 1
    attrs = Attr.VARIADIC | Attr.CALL

    def inputs(self) -> int:
        return 1 + self.num_arguments


@dataclass(frozen=True)
class CallFunctionWithSpread(Operation):            # 1712
    spreads: tuple[bool, ...]
    call_optional: bool = False
    num_outputs = 1
    first_variadic = 1
    attrs = Attr.VARIADIC | Attr.CALL | Attr.MUTABLE

    def __post_init__(self) -> None:
        if not any(self.spreads):
            raise ValueError("use CallFunction when nothing is spread")

    def inputs(self) -> int:
        return 1 + len(self.spreads)


@dataclass(frozen=True)
class Construct(Operation):                         # 1749: (constructor, args...)
    num_arguments: int = 0
    num_outputs = 1
    first_variadic = 1
    attrs = Attr.VARIADIC | Attr.CALL

    def inputs(self) -> int:
        return 1 + self.num_arguments


@dataclass(frozen=True)
class ConstructWithSpread(Operation):               # 1771
    spreads: tuple[bool, ...]
    num_outputs = 1
    first_variadic = 1
    attrs = Attr.VARIADIC | Attr.CALL | Attr.MUTABLE

    def __post_init__(self) -> None:
        if not any(self.spreads):
            raise ValueError("use Construct when nothing is spread")

    def inputs(self) -> int:
        return 1 + len(self.spreads)


@dataclass(frozen=True)
class CallMethod(Operation):                        # 1799: (receiver, args...)
    method: Method
    num_arguments: int = 0
    receiver_optional: bool = False
    call_optional: bool = False
    num_outputs = 1
    first_variadic = 1
    attrs = Attr.VARIADIC | Attr.CALL | Attr.MUTABLE

    def inputs(self) -> int:
        return 1 + self.num_arguments


@dataclass(frozen=True)
class CallMethodWithSpread(Operation):              # 1847
    method: Method
    spreads: tuple[bool, ...]
    receiver_optional: bool = False
    call_optional: bool = False
    num_outputs = 1
    first_variadic = 1
    attrs = Attr.VARIADIC | Attr.CALL | Attr.MUTABLE

    def inputs(self) -> int:
        return 1 + len(self.spreads)


@dataclass(frozen=True)
class CallComputedMethod(Operation):                # 1902: (receiver, key, args...)
    num_arguments: int = 0
    receiver_optional: bool = False
    call_optional: bool = False
    num_outputs = 1
    first_variadic = 2
    attrs = Attr.VARIADIC | Attr.CALL

    def inputs(self) -> int:
        return 2 + self.num_arguments


@dataclass(frozen=True)
class CallComputedMethodWithSpread(Operation):      # 1947
    spreads: tuple[bool, ...]
    receiver_optional: bool = False
    call_optional: bool = False
    num_outputs = 1
    first_variadic = 2
    attrs = Attr.VARIADIC | Attr.CALL | Attr.MUTABLE

    def inputs(self) -> int:
        return 2 + len(self.spreads)


# ------------------------------------------------------------------------------- operators
@dataclass(frozen=True)
class UnaryOperation(Operation):                    # 2020; increments reassign their input
    op: UnaryOperator
    num_inputs = 1
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class BinaryOperation(Operation):                   # 2054
    op: BinaryOperator
    num_inputs = 2
    num_outputs = 1
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class TernaryOperation(Operation):                  # 2066: (condition, if-true, if-false)
    num_inputs = 3
    num_outputs = 1


@dataclass(frozen=True)
class Reassign(Operation):                          # 2075: (target, value)
    num_inputs = 2


@dataclass(frozen=True)
class Update(Operation):                            # 2084: `target op= value`; logical ops give `&&=` etc.
    op: BinaryOperator
    num_inputs = 2
    attrs = Attr.MUTABLE


@dataclass(frozen=True)
class Compare(Operation):                           # 2120
    op: Comparator
    num_inputs = 2
    num_outputs = 1
    attrs = Attr.MUTABLE


# ------------------------------------------------------------------------------- super (object-literal methods)
_SUPER_CTX = _JS | Context.METHOD


@dataclass(frozen=True)
class CallSuperMethod(Operation):                   # 2181
    key: Key
    num_arguments: int = 0
    num_outputs = 1
    first_variadic = 0
    attrs = Attr.VARIADIC | Attr.CALL | Attr.MUTABLE
    required_context = _SUPER_CTX

    def inputs(self) -> int:
        return self.num_arguments


@dataclass(frozen=True)
class GetSuperProperty(Operation):                  # 2393
    key: Key
    num_outputs = 1
    attrs = Attr.MUTABLE
    required_context = _SUPER_CTX


@dataclass(frozen=True)
class SetSuperProperty(Operation):                  # 2404
    key: Key
    num_inputs = 1
    attrs = Attr.MUTABLE
    required_context = _SUPER_CTX


@dataclass(frozen=True)
class SetComputedSuperProperty(Operation):          # 2415: (key, value)
    num_inputs = 2
    required_context = _SUPER_CTX


@dataclass(frozen=True)
class GetComputedSuperProperty(Operation):          # 2423: (key)
    num_inputs = 1
    num_outputs = 1
    required_context = _SUPER_CTX


@dataclass(frozen=True)
class UpdateSuperProperty(Operation):               # 2431
    key: Key
    op: BinaryOperator
    num_inputs = 1
    attrs = Attr.MUTABLE
    required_context = _SUPER_CTX


# ------------------------------------------------------------------------------- control flow
@dataclass(frozen=True)
class BeginIf(Operation):                           # 2446; inner output: block label
    inverted: bool = False
    num_inputs = 1
    num_inner_outputs = 1
    attrs = _BS | Attr.MUTABLE | _P
    context_opened = _JS


@dataclass(frozen=True)
class BeginElse(Operation):                         # 2462
    num_inner_outputs = 1
    attrs = _BE | _BS | _P
    context_opened = _JS


@dataclass(frozen=True)
class EndIf(Operation):                             # 2473
    attrs = _BE


@dataclass(frozen=True)
class BeginWhileLoopHeader(Operation):              # 2522
    attrs = _BS | _P
    context_opened = _JS


@dataclass(frozen=True)
class BeginWhileLoopBody(Operation):                # 2533: (condition); inner: loop label
    num_inputs = 1
    num_inner_outputs = 1
    attrs = _BS | _BE | _P
    context_opened = _JS | Context.LOOP


@dataclass(frozen=True)
class EndWhileLoop(Operation):                      # 2544
    attrs = _BE


@dataclass(frozen=True)
class BeginDoWhileLoopBody(Operation):              # 2553; inner: loop label
    num_inner_outputs = 1
    attrs = _BS | _P
    context_opened = _JS | Context.LOOP


@dataclass(frozen=True)
class BeginDoWhileLoopHeader(Operation):            # 2564
    attrs = _BS | _BE | _P
    context_opened = _JS


@dataclass(frozen=True)
class EndDoWhileLoop(Operation):                    # 2575: (condition)
    num_inputs = 1
    attrs = _BE


@dataclass(frozen=True)
class BeginForLoopInitializer(Operation):           # 2615
    attrs = _BS | _P
    context_opened = _JS


@dataclass(frozen=True)
class BeginForLoopCondition(Operation):             # 2624: inputs = initial values; inner = loop variables
    num_loop_variables: int = 1
    declaration: DeclarationKind = DeclarationKind.LET   # the keyword of the loop variables (oxpecker; FuzzIL has `let`)
    attrs = _BS | _BE | _P
    context_opened = _JS

    def inputs(self) -> int:
        return self.num_loop_variables

    def inner_outputs(self) -> int:
        return self.num_loop_variables


@dataclass(frozen=True)
class BeginForLoopAfterthought(Operation):          # 2639: input = condition; inner = loop variables
    num_loop_variables: int = 1
    num_inputs = 1
    attrs = _BS | _BE | _P
    context_opened = _JS

    def inner_outputs(self) -> int:
        return self.num_loop_variables


@dataclass(frozen=True)
class BeginForLoopBody(Operation):                  # 2655: inner = loop variables + label
    num_loop_variables: int = 1
    attrs = _BS | _BE | _P
    context_opened = _JS | Context.LOOP

    def inner_outputs(self) -> int:
        return self.num_loop_variables + 1


@dataclass(frozen=True)
class EndForLoop(Operation):                        # 2670
    attrs = _BE


@dataclass(frozen=True)
class BeginForEachLoop(Operation):
    """FuzzIL's `ForLoop` (2739): `for (x of|in <input>)`, with an optional destructuring header.
    Inner outputs: the bound variables (one, or the pattern's bindings), then the loop label.
    Ends with EndForEachLoop (FuzzIL reuses EndForLoop; a separate end is clearer to check)."""
    kind: ForEachKind
    pattern: Pattern | None = None
    declaration: DeclarationKind = DeclarationKind.VAR
    attrs = _BS | _P
    context_opened = Context.LOOP

    def __post_init__(self) -> None:
        if self.kind is ForEachKind.IN and self.pattern is not None:
            raise ValueError("for-in has no destructuring header")

    def inputs(self) -> int:
        return 1 + (num_extra_inputs(self.pattern) if self.pattern else 0)

    def inner_outputs(self) -> int:
        return (num_bindings(self.pattern) if self.pattern else 1) + 1


@dataclass(frozen=True)
class EndForEachLoop(Operation):
    attrs = _BE


@dataclass(frozen=True)
class BeginRepeatLoop(Operation):                   # 2795: `for (let i = 0; i < n; i++)`; inner: [counter,] label
    iterations: int
    exposes_counter: bool = True
    declaration: DeclarationKind = DeclarationKind.LET
    attrs = _BS | _P
    context_opened = _JS | Context.LOOP

    def __post_init__(self) -> None:
        if self.iterations < 0:
            raise ValueError("iterations")

    def inner_outputs(self) -> int:
        return 2 if self.exposes_counter else 1


@dataclass(frozen=True)
class EndRepeatLoop(Operation):                     # 2814
    attrs = _BE


@dataclass(frozen=True)
class LoopBreak(Operation):                         # 2822: optional input = label
    has_label: bool = False
    attrs = Attr.JUMP
    required_context = _JS | Context.LOOP

    def inputs(self) -> int:
        return 1 if self.has_label else 0


@dataclass(frozen=True)
class LoopContinue(Operation):                      # 2832
    has_label: bool = False
    attrs = Attr.JUMP
    required_context = _JS | Context.LOOP

    def inputs(self) -> int:
        return 1 if self.has_label else 0


@dataclass(frozen=True)
class BeginTry(Operation):                          # 2842
    attrs = _BS | _P


@dataclass(frozen=True)
class BeginCatch(Operation):                        # 2850; inner: the exception
    num_inner_outputs = 1
    attrs = _BS | _BE | _P


@dataclass(frozen=True)
class BeginFinally(Operation):                      # 2860
    attrs = _BS | _BE | _P


@dataclass(frozen=True)
class EndTryCatchFinally(Operation):                # 2868
    attrs = _BE


@dataclass(frozen=True)
class ThrowException(Operation):                    # 2876
    num_inputs = 1
    attrs = Attr.JUMP


@dataclass(frozen=True)
class BeginBlockStatement(Operation):               # 2902; inner: block label
    num_inner_outputs = 1
    attrs = _BS | _P
    context_opened = _JS


@dataclass(frozen=True)
class EndBlockStatement(Operation):                 # 2913
    attrs = _BE


@dataclass(frozen=True)
class BlockBreak(Operation):                        # 2921: input = label
    num_inputs = 1
    attrs = Attr.JUMP


@dataclass(frozen=True)
class BeginSwitch(Operation):                       # 2982: input = discriminant; inner: label
    num_inputs = 1
    num_inner_outputs = 1
    attrs = _BS
    context_opened = Context.SWITCH_BLOCK


@dataclass(frozen=True)
class BeginSwitchCase(Operation):                   # 2994: input = case value
    num_inputs = 1
    attrs = _BS | Attr.RESUMES_CONTEXT
    required_context = Context.SWITCH_BLOCK
    context_opened = Context.SWITCH_CASE | _JS


@dataclass(frozen=True)
class BeginSwitchDefaultCase(Operation):            # 3007
    attrs = _BS | Attr.RESUMES_CONTEXT | Attr.SINGULAR
    required_context = Context.SWITCH_BLOCK
    context_opened = Context.SWITCH_CASE | _JS


@dataclass(frozen=True)
class EndSwitchCase(Operation):                     # 3018
    falls_through: bool = False
    attrs = _BE


@dataclass(frozen=True)
class EndSwitch(Operation):                         # 3030
    attrs = _BE
    required_context = Context.SWITCH_BLOCK


@dataclass(frozen=True)
class SwitchBreak(Operation):                       # 3038
    attrs = Attr.JUMP
    required_context = _JS | Context.SWITCH_CASE


@dataclass(frozen=True)
class BeginWith(Operation):                         # 2149
    num_inputs = 1
    attrs = _BS | _P
    context_opened = _JS | Context.WITH


@dataclass(frozen=True)
class EndWith(Operation):                           # 2159
    attrs = _BE


@dataclass(frozen=True)
class BeginCodeString(Operation):                   # 2885: a nested program whose source is the output string
    num_outputs = 1
    attrs = _BS
    context_opened = _JS


@dataclass(frozen=True)
class EndCodeString(Operation):                     # 2893
    attrs = _BE


# ------------------------------------------------------------------------------- destructuring ops
@dataclass(frozen=True)
class Destruct(Operation):                          # 3770: declares the pattern's bindings
    pattern: Pattern
    kind: DeclarationKind = DeclarationKind.VAR
    attrs = Attr.MUTABLE

    def inputs(self) -> int:
        return 1 + num_extra_inputs(self.pattern)

    def outputs(self) -> int:
        return num_bindings(self.pattern)


@dataclass(frozen=True)
class DestructAndReassign(Operation):               # 3791: assigns into existing variables / members
    pattern: Pattern
    attrs = Attr.MUTABLE

    def inputs(self) -> int:
        return 1 + num_bindings(self.pattern) + num_extra_inputs(self.pattern)


# ------------------------------------------------------------------------------- oxpecker
@dataclass(frozen=True)
class Probe(Operation):
    """`__probe(label, <visible declared variables>)`; no inputs, so no mutation breaks it."""
    label: ProbeLabel = ProbeLabel.STEP
    number: int = 0


# ------------------------------------------------------------------------------- block matching
# Semantics.swift:62-: which end operations may close a begin operation.
END_OF: dict[type[Operation], tuple[type[Operation], ...]] = {
    BeginObjectLiteral: (EndObjectLiteral,),
    BeginObjectLiteralMethod: (EndObjectLiteralMethod,),
    BeginObjectLiteralComputedMethod: (EndObjectLiteralComputedMethod,),
    BeginObjectLiteralGetter: (EndObjectLiteralGetter,),
    BeginObjectLiteralComputedGetter: (EndObjectLiteralComputedGetter,),
    BeginObjectLiteralSetter: (EndObjectLiteralSetter,),
    BeginObjectLiteralComputedSetter: (EndObjectLiteralComputedSetter,),
    BeginPlainFunction: (EndPlainFunction,),
    BeginArrowFunction: (EndArrowFunction,),
    BeginGeneratorFunction: (EndGeneratorFunction,),
    BeginIf: (BeginElse, EndIf),
    BeginElse: (EndIf,),
    BeginWhileLoopHeader: (BeginWhileLoopBody,),
    BeginWhileLoopBody: (EndWhileLoop,),
    BeginDoWhileLoopBody: (BeginDoWhileLoopHeader,),
    BeginDoWhileLoopHeader: (EndDoWhileLoop,),
    BeginForLoopInitializer: (BeginForLoopCondition,),
    BeginForLoopCondition: (BeginForLoopAfterthought,),
    BeginForLoopAfterthought: (BeginForLoopBody,),
    BeginForLoopBody: (EndForLoop,),
    BeginForEachLoop: (EndForEachLoop,),
    BeginRepeatLoop: (EndRepeatLoop,),
    BeginTry: (BeginCatch, BeginFinally),
    BeginCatch: (BeginFinally, EndTryCatchFinally),
    BeginFinally: (EndTryCatchFinally,),
    BeginBlockStatement: (EndBlockStatement,),
    BeginSwitch: (EndSwitch,),
    BeginSwitchCase: (EndSwitchCase,),
    BeginSwitchDefaultCase: (EndSwitchCase,),
    BeginWith: (EndWith,),
    BeginCodeString: (EndCodeString,),
}


def matches_end(begin: Operation, end: Operation) -> bool:
    return isinstance(end, END_OF.get(type(begin), ()))


def all_operation_classes() -> tuple[type[Operation], ...]:
    """Every concrete operation class, in definition order."""
    return tuple(cls for cls in _subclasses(Operation) if cls is not Operation)


def _subclasses(cls: type) -> list[type]:
    out: list[type] = []
    for sub in cls.__subclasses__():
        out.append(sub)
        out.extend(_subclasses(sub))
    return out


SUBROUTINE_BEGINS: tuple[type[Operation], ...] = (
    BeginPlainFunction, BeginArrowFunction, BeginGeneratorFunction,
    BeginObjectLiteralMethod, BeginObjectLiteralComputedMethod,
    BeginObjectLiteralGetter, BeginObjectLiteralComputedGetter,
    BeginObjectLiteralSetter, BeginObjectLiteralComputedSetter,
)
FUNCTION_BEGINS: tuple[type[Operation], ...] = (BeginPlainFunction, BeginArrowFunction, BeginGeneratorFunction)
SPREAD_CALLS: tuple[type[Operation], ...] = (CallFunctionWithSpread, ConstructWithSpread, CallMethodWithSpread,
                                             CallComputedMethodWithSpread)
