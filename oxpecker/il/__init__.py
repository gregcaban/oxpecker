"""The generation layer: a FuzzIL-style intermediate language for JavaScript programs, generated
and mutated as Hypothesis strategies and lifted to source only by `lift.py`.

Design: docs/design-generation-layer.md; decisions: docs/generation-layer-decisions.md.
Modules derived from Fuzzilli carry an attribution header (THIRD_PARTY_NOTICES.md).

Every layer is one call from a Python shell; each module exposes its step:

    >>> from oxpecker.il.builder import Builder          # hand-written IL
    >>> from oxpecker.il.ops import *
    >>> b = Builder(); n = b.declare(b.load_int(1)); b.update(n, b.load_int(2), BinaryOperator.ADD)
    >>> from oxpecker.il.program import dump; print(dump(b))
    >>> from oxpecker.il.lift import js; print(js(b))      # JavaScript
    >>> from oxpecker.il.analysis import analyse, explain, verify
    >>> print(explain(b))                                  # types, storage, the Rhino model
    >>> from oxpecker.il.strategies import generate, run_generator, draw, generators
    >>> g = generate(seed=3); print(g); print(dump(g)); print(explain(g))
    >>> s = run_generator("while_loop", seed=1); print(s)  # one generator after a small prefix
    >>> from oxpecker.il.mutate import mutate, lineage, mutators
    >>> m = mutate(g, seed=1); print(m.diff()); print(m)   # one mutation: the IL diff, the JavaScript
    >>> l = lineage(seed=2, steps=4); l.steps; print(l)    # a base program and its mutation steps
    >>> from oxpecker.harness.compare import run_and_compare
    >>> run_and_compare(g.source)                          # both Rhino modes, compared
"""
