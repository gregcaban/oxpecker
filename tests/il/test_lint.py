"""Guard test 2: no JavaScript in the generation layer's sources.

An `ast` walk over the generator modules, templates and strategies fails on any string constant
that contains JavaScript syntax. String *contents* for JavaScript string values are allowed only
inside calls to `load_string(...)`; everything else (operators, keys, builtins) comes from enums
and registries. The allowlist is empty and stays empty."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "oxpecker" / "il"
LINTED = [ROOT / "strategies.py", ROOT / "templates.py", ROOT / "mutate.py", *sorted((ROOT / "generators").glob("*.py"))]

TOKENS = ("{", "}", "(", ")", ";", "=>", "===", "==", "!=", "+", "*", "/", "%", "<", ">", "=", "!", "&", "|", "^", "~", "?", "`")
MINUS_RE = re.compile(r"(^|[\s\d(\[])-|-(\d|\s|$)")   # `-1`, `a - b`, `-x`; not the hyphen in "compiler-targeted"
KEYWORDS = ("function", "new", "typeof", "instanceof", "var", "let", "const", "return", "if", "else", "for", "while",
            "this", "delete", "void", "in", "of", "yield", "throw", "try", "catch", "finally", "switch", "case", "break",
            "continue", "with", "null", "undefined", "true", "false", "class", "super", "arguments", "eval")
KEYWORD_RE = re.compile(r"\b(" + "|".join(KEYWORDS) + r")\b")
ALLOWLIST: tuple[str, ...] = ()   # stays empty


def looks_like_javascript(s: str) -> bool:
    if any(tok in s for tok in TOKENS) or MINUS_RE.search(s):
        return True
    return bool(KEYWORD_RE.search(s))


def string_constants(tree: ast.AST):
    """(node, value) for every string constant that is not a docstring, a decorator/registry
    argument (names, families, sources), or the contents of a string literal load."""
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                skip.add(id(node.body[0].value))
        if isinstance(node, ast.FunctionDef) and node.name in ("__repr__", "__str__"):
            for c in ast.walk(node):                     # a Python repr is not code
                if isinstance(c, ast.Constant):
                    skip.add(id(c))
        if isinstance(node, ast.Raise):                  # an error message is not code
            for c in ast.walk(node):
                if isinstance(c, ast.Constant):
                    skip.add(id(c))
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name in ("load_string", "generator", "value_maker", "template", "note", "sampled_from_strings"):
                for a in node.args:
                    for c in ast.walk(a):
                        if isinstance(c, ast.Constant):
                            skip.add(id(c))
                for kw in node.keywords:
                    for c in ast.walk(kw.value):
                        if isinstance(c, ast.Constant):
                            skip.add(id(c))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node, node.value


@pytest.mark.parametrize("path", LINTED, ids=lambda p: p.name)
def test_no_javascript_text_in_generator_sources(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not written yet")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = [(node.lineno, value) for node, value in string_constants(tree)
                 if looks_like_javascript(value) and value not in ALLOWLIST]
    assert not offenders, "JavaScript-looking string constants:\n" + "\n".join(f"  {path.name}:{ln}: {v!r}" for ln, v in offenders)


def test_the_lint_catches_the_old_habits():
    assert looks_like_javascript("throw {0}")
    assert looks_like_javascript("new Map()")
    assert looks_like_javascript("|0")
    assert looks_like_javascript("j == 1")
    assert looks_like_javascript("e instanceof TypeError")
    assert looks_like_javascript("function (x) { return x; }")
    assert looks_like_javascript("1/3")
    assert looks_like_javascript("-1") and looks_like_javascript("a - b") and looks_like_javascript("-Infinity")
    assert not looks_like_javascript("operations")
    assert not looks_like_javascript("compiler-targeted")


def test_the_lint_sees_through_the_sources():
    src = 'def g(draw, b):\n    """x = 1;"""\n    k = "a + b"\n    return b.load_string("x + y")\n'
    found = [v for _, v in string_constants(ast.parse(src))]
    assert found == ["a + b"]
