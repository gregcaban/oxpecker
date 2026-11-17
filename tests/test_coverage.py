"""What a fuzzing run reports about coverage (oxpecker/coverage.py): JaCoCo's counters for the
Rhino jar, the coverage curve, and whether the run had plateaued."""

from __future__ import annotations

import json
import math

import pytest

from oxpecker import coverage
from oxpecker.fuzz import fuzz
from oxpecker.harness.client import HarnessClient
from oxpecker.il.options import GenOptions


def curve(f, minutes: int = 60, every_s: int = 30) -> list[dict]:
    return [{"t": float(t), "runs": t, "corpus": 0, "covered": round(f(t)), "findings": 0}
            for t in range(0, minutes * 60 + 1, every_s)]


def test_a_logarithmic_curve_predicts_what_doubling_the_run_adds():
    s = coverage.saturation(curve(lambda t: 10_000 + 800 * math.log(max(t, 1))))
    assert abs(s["doubling_adds"] - 800 * math.log(2)) < 5
    assert s["verdict"].startswith("slowing down")          # ~555 of ~16.5k: 3.4 %


def test_a_flat_curve_has_plateaued_and_a_steep_one_is_still_growing():
    assert coverage.saturation(curve(lambda t: 20_000 + min(t, 600)))["verdict"].startswith("plateaued")
    assert coverage.saturation(curve(lambda t: 1_000 + 2 * t))["verdict"].startswith("still growing")
    assert coverage.saturation(curve(lambda t: t, minutes=1))["verdict"] == "too short to tell"


def test_jar_coverage_sums_packages_and_leaves_the_unreachable_out_of_reachable():
    report = {"coveredProbes": 7, "buckets": {"*cumulative*": {"packages": {
        "org.mozilla.javascript": [5, 10, 4, 8, 50, 100],
        "org.mozilla.javascript.commonjs.module": [0, 4, 0, 2, 0, 20]}}}}
    c = coverage.jar_coverage(report)
    assert c["lines"] == [50, 120] and c["branches"] == [4, 10] and c["methods"] == [5, 14]
    assert c["reachable"]["lines"] == [50, 100]
    assert "lines  41.7% (50/120)" in coverage.format_summary(coverage.summarize(report, []))


def test_a_resumed_corpus_reads_back_only_the_latest_runs_curve(tmp_path):
    for start in (True, False, True, False):
        coverage.append_row(tmp_path, 0.0, 1, 1, 5, 0, start=start)
    assert len(coverage.read_curve(tmp_path)) == 2


def test_a_curve_is_rebuilt_from_an_old_fuzz_log(tmp_path):
    log = tmp_path / "fuzz.log"
    log.write_text("     79 runs   7.8/s  corpus 36  new probes 2401  findings 0  equal=35\n"
                   "    158 runs   7.9/s  corpus 60  new probes 3090  findings 1  equal=56\n"
                   "     10 runs   5.0/s  corpus 3  new probes 100  findings 0\n"      # a later run, appended
                   "     20 runs   5.0/s  corpus 5  new probes 150  findings 0\n")
    rows = coverage.curve_from_log(log)
    assert [r["covered"] for r in rows] == [0, 100, 150] and rows[-1]["t"] == 4.0


@pytest.fixture()
def cov_client():
    with HarnessClient(quiet=True, coverage=True) as c:
        if not c.info().get("coverage"):
            pytest.skip("coverage agent not attached")
        yield c


def test_a_harness_run_records_a_curve_and_the_jars_coverage(cov_client, tmp_path, monkeypatch):
    monkeypatch.setattr(coverage, "CURVE_EVERY_S", 0.0)       # a row every iteration
    summary = fuzz(corpus_dir=tmp_path / "corpus", options=GenOptions(), iterations=6, seed=3, client=cov_client,
                   findings_dir=tmp_path / "findings", shrink_budget=2, source_shrink_budget=0, quiet=True)
    rows = coverage.read_curve(tmp_path / "corpus")
    assert rows[0].get("start") and len(rows) >= 7
    assert [r["covered"] for r in rows] == sorted(r["covered"] for r in rows)      # never shrinks
    assert rows[-1]["covered"] == summary["coveredProbes"] == cov_client.coverage_count()
    cov = summary["coverage"]
    assert 0 < cov["lines"][0] < cov["lines"][1] and "org.mozilla.javascript.optimizer" in cov["packages"]
    assert cov["missed"] and all(l_cov < l_tot for _, l_cov, l_tot, _, _ in cov["missed"])
    assert json.loads((tmp_path / "corpus" / "last-run.json").read_text())["coverage"]["lines"] == cov["lines"]
