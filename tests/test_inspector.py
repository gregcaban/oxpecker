"""The inspector export writes what the UI reads (ui/lib/types.ts): a catalogue entry per
generator and template, one sample line per program with trace spans, metrics per focus,
findings by signature. Needs the harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oxpecker import inspector
from oxpecker.il.options import GenOptions
from oxpecker.il.strategies import REGISTRY, TEMPLATES


def test_catalogue_lists_every_generator_and_template_with_source():
    cat = inspector.catalogue()
    ids = {e["id"] for e in cat}
    assert set(REGISTRY) <= ids and set(TEMPLATES) <= ids
    gen = next(e for e in cat if e["id"] == "while_loop")
    assert gen["kind"] == "generator" and gen["family"] == "control" and gen["sourcePath"].startswith("oxpecker/il/generators/")
    assert "def while_loop" in gen["sourceSnippet"]
    tpl = next(e for e in cat if e["id"] == "plain")
    assert tpl["kind"] == "template" and tpl["description"]


def test_generator_trace_spans_follow_the_comments():
    src = "// [integer]\nvar n0 = 1;\n__probe(\"step\", n0);\n// [binary]\nvar n1 = n0 + 1;\n"
    assert inspector.generator_trace(src) == [{"gen": "integer", "lines": [0, 2]}, {"gen": "binary", "lines": [3, 4]}]


@pytest.mark.slow
def test_export_writes_the_run_directory(harness, tmp_path: Path):
    out = inspector.export(tmp_path, per_generator=1, per_template=1, seed=3, options=GenOptions(max_statements=5),
                           quiet=True, client=harness)
    assert (out / "catalogue.json").exists() and (out / "metrics.json").exists() and (out / "findings.json").exists()
    samples = [json.loads(ln) for ln in (out / "samples.jsonl").read_text().splitlines() if ln.strip()]
    assert samples, "no samples written"
    rec = samples[0]
    for key in ("id", "runId", "kind", "focus", "source", "sourcePlain", "seed", "template", "enabledGenerators",
                "generatorTrace", "stats", "results", "verdict"):
        assert key in rec, key
    assert {"interpreted", "compiled"} <= set(rec["results"])
    assert rec["stats"]["bytes"] == len(rec["source"].encode())
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["samples"] == len(REGISTRY) + len(TEMPLATES)
    row = metrics["perFocus"]["plain"]
    assert row["kind"] == "template" and row["samples"] == 1 and row["fired"] == 1
    fired = sum(r["fired"] for r in metrics["perFocus"].values())
    assert fired >= 0.8 * len(REGISTRY), "most generators should fire in their own sample"
