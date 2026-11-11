"""Rhino targets (rhino-targets.toml): the registry, which jar and capability table a target uses,
and the per-target expectations of checked-in reproducers. No harness needed."""

from __future__ import annotations

from oxpecker import targets


def test_the_registry_names_a_default_and_each_target_its_api_line():
    first, ts = targets.registry()
    assert first in ts
    assert ts["1.9.2"].maven == "1.9.2" and ts["1.9.2"].api == "1"
    assert ts["master"].git and len(ts["master"].ref) == 40 and ts["master"].api == "2"
    assert ts["master"].version == f"master@{ts['master'].ref[:10]}"


def test_oxpecker_rhino_selects_the_target_and_its_capability_table(monkeypatch):
    monkeypatch.delenv(targets.ENV, raising=False)
    assert targets.current() == targets.default()
    monkeypatch.setenv(targets.ENV, "master")
    assert targets.current() == "master"
    assert targets.capabilities("master").name == "master.json"
    assert targets.capabilities(targets.default()).exists()


def test_the_default_target_falls_back_to_the_plain_fatjar(tmp_path, monkeypatch):
    monkeypatch.setattr(targets, "LIBS", tmp_path)
    assert targets.jar(targets.default()).name == "oxpecker-harness-all.jar"
    assert targets.jar("master").name == "harness-master.jar"
    (tmp_path / f"harness-{targets.default()}.jar").write_bytes(b"")
    assert targets.jar(targets.default()).name == f"harness-{targets.default()}.jar"


def test_expectation_headers_per_target_win_over_the_general_one():
    src = "// oxpecker-expect: java-types\n// oxpecker-expect[master]: equal\n// a note\nvar x = 1;\n"
    assert targets.expected_kind(src, "master") == "equal"
    assert targets.expected_kind(src, "1.9.2") == "java-types"
    assert targets.expected_kind("var x = 1;\n", "master") == "hard"
    # only the leading comment lines count
    assert targets.expected_kind("var x;\n// oxpecker-expect[master]: equal\n", "master") == "hard"
