"""Every Python block of docs/repl-tutorial.md runs, in order, in one namespace, so the tutorial
cannot drift from the code. A block whose first line is `# needs the harness` runs only with the
harness fixture (and is skipped, not failed, without it)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TUTORIAL = Path(__file__).resolve().parents[2] / "docs" / "repl-tutorial.md"
HARNESS_MARK = "# needs the harness"


def blocks() -> list[tuple[int, str]]:
    text = TUTORIAL.read_text(encoding="utf-8")
    return [(text[: m.start()].count("\n") + 2, m.group(1)) for m in re.finditer(r"```python\n(.*?)```", text, re.S)]


def test_tutorial_has_blocks():
    assert len(blocks()) >= 10


@pytest.mark.slow
@pytest.mark.filterwarnings("ignore::hypothesis.errors.NonInteractiveExampleWarning")   # the tutorial shows .example()
def test_tutorial_blocks_run_in_order(request, capsys):
    ns: dict = {}
    for line, code in blocks():
        if code.startswith(HARNESS_MARK):
            request.getfixturevalue("harness")      # skips the whole test when the jar is missing
        try:
            exec(compile(code, f"{TUTORIAL.name}:{line}", "exec"), ns)
        except Exception as e:                      # noqa: BLE001  (report which block)
            raise AssertionError(f"tutorial block at line {line} failed: {type(e).__name__}: {e}") from e
    capsys.readouterr()
