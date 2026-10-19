"""Program construction: `build_program(rand, opts)` draws a template and a swarm subset of
generators through any `Rand`; `programs(opts)` is the Hypothesis strategy over it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hypothesis import strategies as st

from .builder import GenOptions, ProgramBuilder
from .rand import HypothesisRand, Rand
from .registry import (available_generators, available_templates, load_all, make_value, weighted)
from .templates import run_template


@dataclass
class Program:
    source: str                     # with probes and comments, as configured
    source_plain: str               # no probes, no comments: the reproducer form
    template: str
    enabled_generators: list[str]
    generator_usage: dict[str, int]
    generator_spans: list[dict[str, Any]]
    stats: dict[str, int]
    options: dict[str, Any] = field(default_factory=dict)

    def meta(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "enabledGenerators": self.enabled_generators,
            "generatorUsage": self.generator_usage,
            "generatorTrace": self.generator_spans,
            "stats": self.stats,
            "options": self.options,
        }


def options_dict(opts: GenOptions) -> dict[str, Any]:
    return {
        "minStatements": opts.min_statements, "maxStatements": opts.max_statements,
        "probes": opts.probes, "probeEvery": opts.probe_every, "comments": opts.comments,
        "strict": opts.strict, "toggles": sorted(opts.toggles), "swarm": opts.swarm,
        "maxDepth": opts.max_depth,
    }


def build_program(rand: Rand, opts: GenOptions = GenOptions()) -> Program:
    load_all()
    gens = available_generators(opts)
    if not gens:
        raise ValueError("no generators enabled")
    templates = available_templates(opts)
    if not templates:
        raise ValueError("no templates enabled")

    if opts.swarm and len(gens) > 3:
        # Swarm testing: each program sees a random non-empty subset of the generators.
        names = sorted(g.name for g in gens)
        chosen = set(rand.sample_unique(names, max(3, len(names) // 4), len(names)))
        subset = [g for g in gens if g.name in chosen] or gens
    else:
        subset = gens
    tpl = rand.choice(sorted(templates, key=lambda t: t.name))

    b = ProgramBuilder(rand, opts, pool=weighted(subset))
    b.make_value = make_value
    run_template(tpl, b)

    header = [f"template: {tpl.name}", "generators: " + ", ".join(sorted(g.name for g in subset))]
    source = b.render(header=header)
    return Program(
        source=source,
        source_plain=b.render(probes=False, comments=False),
        template=tpl.name,
        enabled_generators=sorted(g.name for g in subset),
        generator_usage=dict(b.gen_usage),
        generator_spans=b.generator_spans(header_lines=len(header)),
        stats=b.stats(),
        options=options_dict(opts),
    )


@st.composite
def programs(draw, opts: GenOptions = GenOptions()) -> Program:
    return build_program(HypothesisRand(draw), opts)
