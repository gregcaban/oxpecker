"""Program templates (port of `gen/templates.py`): the overall shape of a program, as strategies.
A template drives the top-level statement list (one `st.lists` draw) and places probes."""

from __future__ import annotations

from hypothesis import strategies as st

from .builder import Builder, statements
from .registry import template


def top_level(b: Builder, lo: int | None = None, hi: int | None = None) -> st.SearchStrategy:
    """The top-level statement list, with a probe after each statement when probing is on."""
    lo = b.opts.min_statements if lo is None else lo
    hi = b.opts.max_statements if hi is None else hi
    return statements(b, lo, hi, probe=bool(b.opts.probe_every))


@template("plain", weight=6)
@st.composite
def plain(draw, b: Builder):
    """A flat list of generated statements with a probe after each."""
    draw(top_level(b))
    b.probe("end")
