"""Program templates (port of `gen/templates.py`): the overall shape of a program. A template drives
the top-level statement list (one `st.lists` draw) and places probes."""

from __future__ import annotations

from .builder import Builder
from .registry import template


def statements(b: Builder, lo: int | None = None, hi: int | None = None) -> None:
    """The top-level statement list, with a probe after each statement when probing is on."""
    lo = b.opts.min_statements if lo is None else lo
    hi = b.opts.max_statements if hi is None else hi
    b.statement_list(lo, hi, probe=bool(b.opts.probe_every))


@template("plain", weight=6)
def tpl_plain(b: Builder):
    """A flat list of generated statements with a probe after each."""
    statements(b)
    b.probe("end")
