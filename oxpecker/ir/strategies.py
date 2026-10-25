"""`programs(opts)`: the Hypothesis strategy for whole IR programs, lifted into the same `Program`
record the string generator produces (`gen/strategies.py`), so everything downstream -- the
harness, `findings`, `oxpecker run`, the inspector -- works unchanged.
"""

from __future__ import annotations

from hypothesis import strategies as st

from ..gen.builder import GenOptions
from ..gen.strategies import Program, options_dict
from .analysis import verify
from .builder import Builder
from .lift import lift
from .ops import Program as IRProgram
from .registry import available_generators, available_templates, weighted


@st.composite
def ir_programs(draw, opts: GenOptions = GenOptions()) -> tuple[IRProgram, dict]:
    """The IR program and its generation metadata (template, enabled generators, usage)."""
    gens = available_generators(opts)
    tpls = available_templates(opts)
    if not gens or not tpls:
        raise ValueError("no IR generators/templates enabled")
    if opts.swarm and len(gens) > 3:
        # Swarm testing: a random subset of generators per program. One boolean per generator in
        # a fixed order meaning "excluded", so shrinking (towards False) re-enables everything
        # and the pool -- and with it the meaning of every generator index -- returns to the
        # full fixed list (proposal §3.8 rule 3).
        excluded = [draw(st.booleans()) for _ in gens]
        subset = [g for g, x in zip(gens, excluded) if not x] or gens
    else:
        subset = gens
    tpl = draw(st.sampled_from(tpls))
    b = Builder(opts, weighted(subset))
    with b.drawing(draw):
        b.current_gen = tpl.name
        tpl.fn(b)
        b.current_gen = None
    prog = b.program()
    verify(prog)                     # a failure is a generator bug, never filtered
    meta = {"template": tpl.name, "enabled": sorted(g.name for g in subset), "usage": dict(b.gen_usage)}
    return prog, meta


def to_program(prog: IRProgram, meta: dict, opts: GenOptions) -> Program:
    header = (f"template: {meta['template']}", "generators: " + ", ".join(meta["enabled"]))
    full = lift(prog, probes=opts.probes, comments=opts.comments, probe_width=opts.probe_width, header=header)
    plain = lift(prog, probes=False, comments=False)
    return Program(source=full.source, source_plain=plain.source, template=meta["template"],
                   enabled_generators=meta["enabled"], generator_usage=meta["usage"],
                   generator_spans=full.spans, stats=full.stats, options=options_dict(opts) | {"ir": True},
                   ir=prog)


@st.composite
def programs(draw, opts: GenOptions = GenOptions()) -> Program:
    prog, meta = draw(ir_programs(opts))
    return to_program(prog, meta, opts)
