"""oxpecker: differential testing of Mozilla Rhino, interpreted vs compiled."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS_BIN = ROOT / "bin" / "harness"
