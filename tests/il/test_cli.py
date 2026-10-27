"""The gen and list commands: cases are written deterministically from seeds and the listing
names every registered generator."""

from __future__ import annotations

import json
from pathlib import Path

import oxpecker.il.templates  # noqa: F401
from oxpecker.cli import main, write_cases
from oxpecker.il.options import GenOptions
from oxpecker.il.strategies import REGISTRY, TEMPLATES, generate


def test_gen_writes_the_four_files_per_case_deterministically(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    paths = write_cases(a, 3, 11, GenOptions(max_statements=6))
    write_cases(b, 3, 11, GenOptions(max_statements=6))
    assert [p.name for p in paths] == ["case_0000.js", "case_0001.js", "case_0002.js"]
    for k in range(3):
        stem = f"case_{k:04d}"
        for suffix in (".js", ".plain.js", ".il", ".meta.json"):
            assert (a / (stem + suffix)).read_text() == (b / (stem + suffix)).read_text()
        meta = json.loads((a / (stem + ".meta.json")).read_text())
        assert meta["seed"] == 11 + k and meta["template"] in TEMPLATES
        # case k is generate(seed + k): the shell and the CLI agree
        assert (a / (stem + ".js")).read_text() == generate(11 + k, max_statements=6).source
        assert "__probe" in (a / (stem + ".js")).read_text()
        assert "__probe" not in (a / (stem + ".plain.js")).read_text()


def test_gen_command_restricts_to_a_template_and_family(tmp_path: Path):
    out = tmp_path / "out"
    rc = main(["gen", "-o", str(out), "-n", "2", "--seed", "3", "--template", "plain", "--family", "values",
               "--family", "operations", "--without", "eval", "--max-statements", "5"])
    assert rc == 0
    for k in range(2):
        meta = json.loads((out / f"case_{k:04d}.meta.json").read_text())
        assert meta["template"] == "plain"
        assert set(meta["generators"]) <= {n for n, s in REGISTRY.items() if s.family in ("values", "operations")}
        assert "eval" not in meta["options"]["toggles"]


def test_list_names_every_generator_and_template(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    for name in REGISTRY:
        assert name in out
    assert main(["list", "--templates"]) == 0
    out = capsys.readouterr().out
    for name in TEMPLATES:
        assert name in out
