"""The source of randomness behind ProgramBuilder.

Two backends with one interface, so the same generators serve both search strategies:

- `HypothesisRand` wraps a Hypothesis `draw`: the property tests get shrinking and the example
  database for free.
- `ChoiceRand` is a recorded choice sequence (a list of ints). Replaying it rebuilds the same
  program; mutating it (change/delete/insert/splice values) yields a *related* program that is
  still valid by construction, because every value is coerced into the range the builder asks for
  at that point. This is what the coverage-guided corpus loop (`oxpecker fuzz`) mutates, the same
  way Fuzzilli mutates FuzzIL and AFL mutates bytes.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Any


class Rand:
    def rand_int(self, lo: int, hi: int) -> int:
        raise NotImplementedError

    def choice(self, seq: Sequence[Any]) -> Any:
        seq = list(seq)
        return seq[self.rand_int(0, len(seq) - 1)]

    def maybe(self, p: float = 0.5) -> bool:
        return self.rand_int(0, 99) < int(p * 100)

    def sample_list(self, seq: Sequence[Any], lo: int, hi: int) -> list[Any]:
        """`lo..hi` elements drawn with replacement."""
        n = self.rand_int(lo, hi)
        return [self.choice(seq) for _ in range(n)]

    def sample_unique(self, seq: Sequence[Any], lo: int, hi: int) -> list[Any]:
        """A subset of `seq` of size lo..hi (order preserved), without replacement."""
        seq = list(seq)
        hi = min(hi, len(seq))
        lo = min(lo, hi)
        n = self.rand_int(lo, hi)
        picked: set[int] = set()
        for _ in range(n * 3):
            if len(picked) >= n:
                break
            picked.add(self.rand_int(0, len(seq) - 1))
        return [x for i, x in enumerate(seq) if i in picked]


class HypothesisRand(Rand):
    def __init__(self, draw: Callable[[Any], Any]):
        from hypothesis import strategies as st
        self.draw = draw
        self.st = st

    def rand_int(self, lo: int, hi: int) -> int:
        return self.draw(self.st.integers(lo, hi))

    def choice(self, seq: Sequence[Any]) -> Any:
        return self.draw(self.st.sampled_from(list(seq)))

    def sample_list(self, seq: Sequence[Any], lo: int, hi: int) -> list[Any]:
        # One list draw, so Hypothesis's shrinker can delete elements directly.
        return self.draw(self.st.lists(self.st.sampled_from(list(seq)), min_size=lo, max_size=hi))

    def sample_unique(self, seq: Sequence[Any], lo: int, hi: int) -> list[Any]:
        seq = list(seq)
        hi = min(hi, len(seq))
        chosen = self.draw(self.st.lists(self.st.sampled_from(seq), min_size=min(lo, hi), max_size=hi, unique=True))
        chosen_set = set(chosen)
        return [x for x in seq if x in chosen_set]


class ChoiceRand(Rand):
    """Replays `choices`; once they run out, draws from `rng` and appends, so a mutated or
    truncated sequence always completes into a full program. `choices` holds every draw made."""

    def __init__(self, choices: Sequence[int] = (), *, seed: int | None = None):
        self.choices: list[int] = list(choices)
        self.pos = 0
        self.rng = random.Random(seed)

    def rand_int(self, lo: int, hi: int) -> int:
        if lo >= hi:
            return lo
        if self.pos < len(self.choices):
            v = self.choices[self.pos]
            if v < lo or v > hi:
                # A mutated or re-contextualised value: fold it into the range the builder asks for.
                v = lo + (v - lo) % (hi - lo + 1)
                self.choices[self.pos] = v      # store the canonical (absolute) value
        else:
            v = self.rng.randint(lo, hi)
            self.choices.append(v)
        self.pos += 1
        return v

    @property
    def consumed(self) -> int:
        return self.pos


# --------------------------------------------------------------------------- mutations
def mutate(choices: Sequence[int], rng: random.Random, *, others: Sequence[Sequence[int]] = ()) -> list[int]:
    """One random mutation of a choice sequence. Values are arbitrary ints: the builder reduces
    them modulo the range it needs, so any list is a valid program."""
    c = list(choices)
    if not c:
        return [rng.randint(0, 255) for _ in range(rng.randint(5, 40))]
    op = rng.choice(["point", "point", "point", "block-delete", "block-insert", "block-dup", "splice", "havoc", "truncate"])
    n = len(c)
    if op == "point":
        for _ in range(rng.randint(1, 3)):
            i = rng.randrange(n)
            c[i] = rng.choice([c[i] + 1, c[i] - 1, rng.randint(0, 255), 0, c[i] ^ (1 << rng.randint(0, 7))])
    elif op == "block-delete" and n > 4:
        i = rng.randrange(n)
        j = min(n, i + rng.randint(1, max(1, n // 4)))
        del c[i:j]
    elif op == "block-insert":
        i = rng.randrange(n + 1)
        c[i:i] = [rng.randint(0, 255) for _ in range(rng.randint(1, 8))]
    elif op == "block-dup" and n > 2:
        i = rng.randrange(n)
        j = min(n, i + rng.randint(1, max(1, n // 4)))
        c[j:j] = c[i:j]
    elif op == "splice" and others:
        o = list(rng.choice(others))
        if o:
            i = rng.randrange(n)
            k = rng.randrange(len(o))
            c = c[:i] + o[k:]
    elif op == "havoc":
        for _ in range(rng.randint(3, 10)):
            c = mutate(c, rng, others=others)
    elif op == "truncate" and n > 4:
        c = c[: rng.randint(2, n - 1)]
    else:
        i = rng.randrange(n)
        c[i] = rng.randint(0, 255)
    return c
