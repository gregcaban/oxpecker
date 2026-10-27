# Follow-up work after the generation-layer rebuild

Written 2026-10-27 from the measurements of the first campaigns (`oxpecker fuzz`, ten minutes
and one hour; corpora in the session scratchpad, numbers reproduced below). Each item says what
was observed, what the design should be, and why. Decisions already taken are in
`docs/generation-layer-decisions.md`; the implementation is described in
`docs/implementation-generation-layer.md`.

## 1. Extension re-draws the whole lineage: cache the parent

**Observation.** A corpus entry is the Hypothesis choice sequence of a lineage (G35). Extending
it (`fuzz.extend`) replays that sequence as a prefix and draws the new steps after it. Replaying
re-executes everything: the base program's generation (builder, typer, verify, lift choices) and
every earlier mutation step, each with its own analysis, verify and lift of the whole program.
Measured on the hour-long run's corpus (median entry: 277 choices, 80 instructions, one to two
steps):

| stage | time |
|---|---|
| fresh lineage (generate, verify, lift choices) | 97 ms |
| replay an entry from its choices | 250 ms |
| extend an entry | 287 ms |
| one mutation step alone (analysis, mutator, verify, lift) | 30–40 ms |
| lift to JavaScript | 17 ms |
| Rhino, both modes, with coverage | 201 ms |

So generation plus mutation already cost as much as Rhino per run, and the cost of an
extension is linear in the depth of the lineage it extends.

**Design.** Keep the parent lineage object. The loop already replays an entry to get its
program; cache `choices -> Lineage` (an LRU of a few hundred entries, or the lineages of the
entries sampled this round). To extend, draw only the new steps:

```python
data = seeded_data(rng=rng)                                   # fresh choices only
with BuildContext(data, wrapped_test=lambda: None):
    child = data.draw(evolved(options, steps, base=parent_lineage.program))   # draws no base
entry_choices = parent_choices[:-1] + (True,) + tuple(data.choices)
```

`evolved(base=...)` draws nothing for the base, so the choices it consumes are exactly the ones
a full replay consumes after the parent's stop flag is flipped to `True`. The stored sequence is
therefore the same as today's and replays with `ConjectureData.for_choices` unchanged; shrinking
(which replays from the stored sequence) is unaffected. Extension drops from ~290 ms to the cost
of the new steps (~40 ms each) plus a dictionary lookup. One subtlety: `evolved(base=program)`
must start its step numbering where the parent stopped (`step=len(parent.steps)+1`), and the
cached lineage's `rejected` count must be carried over, because the replayed lineage's "one
more?" loop counts rejected steps against the cap.

**Test.** For each of fifty corpus entries: extend through the cache, then replay the stored
sequence from scratch and assert the two programs have the same `signature`.

## 2. The step cap makes corpus entries dead ends: rebase

**Observation.** `steps` (default 5) caps a lineage's total depth, not the number of steps per
extension round. An entry at the cap cannot be extended (`extend` returns None: its last choice
is not the stop flag), and the loop falls back to a fresh program. This happened for 221 of
1 716 extension attempts in the ten-minute run and 47 of 454 in the hour (`extend-failed` in
`last-run.json`). Depth distribution of the ten-minute corpus: 166 entries with 0 steps, 112
with 1, 72 with 2, 58 with 3, 47 with 4, 36 with 5. The corpus never evolves more than five
mutations from a generated base, and the deepest entries, which are the most evolved, are the
ones that stop evolving.

Raising `steps` is cheap in buffer terms (entries used at most 2 317 of the 8 192 serialised
bytes Hypothesis allows per test case, median ~550; a step adds 250–400) but with item 1 undone
it multiplies the replay cost, and Hypothesis's shrinker gets slower with the sequence length.

**Design: rebasing** (proposal §5.3's escape hatch). When a parent is at the cap, make its
final program the new base of a fresh lineage: `evolved(options, steps, base=parent.program)`
drawn from fresh choices. The new entry records `rebased_from: parent.id` and its own choices
(the new steps only). Replay is recursive: replay the parent, take its program, then replay the
new choices on top. Everything in the rebased prefix is outside the child's choice sequence
(form B of proposal §3.5): Hypothesis can shrink the new steps but not the base. That is
acceptable because findings are shrunk a second time at statement level (`shrink_lifted`,
G40), which does not care where the program came from. Bound the chain length (a rebase of a
rebase ...) at, say, four, and count rebases in the summary.

The alternative, a per-round cap (Fuzzilli's `consecutiveMutations`) with an unbounded lineage,
keeps everything shrinkable but needs item 1 first and still grows the choice sequence without
bound; the corpus would need compaction (G45 says compaction is too expensive to run on insert)
or a rebase once the sequence nears the buffer anyway.

## 3. `combine` dominates what reaches new coverage

**Observation.** Of the 798 mutation steps recorded in the ten-minute run's corpus entries,
`combine` accounts for 212 and `codegen` for 154; `number_flip` for 11. The mutators are drawn
uniformly, so `combine` is three times as productive as its share. It appends a whole fresh
program, so it reaches new probes almost by construction and drags the corpus towards large
programs (the largest entry has 333 instructions; the median 89). Large programs run slower,
shrink slower and spend more of their behaviour on code that is not under mutation.

**Design.** Either give `combine` (and `codegen`) a lower weight in `MUTATORS` (the list is
flat today; a weights tuple as for generators), or credit coverage per added instruction when
deciding whether a child enters the corpus (new probes divided by the instructions the step
added, with a threshold), or cap the instruction count of corpus entries. Measure with the
mutator-usage count above after each change; the number to watch is new probes per run.

## 4. Mutation rejections are not aggregated

`Lineage.rejected` counts the steps a lineage skipped because the child did not verify; the run
summary does not sum them. Add `stats["rejected-steps"]` and a per-mutator breakdown (which
mutator's children fail verification most) so the 35 % rejection bound of design §10 item 8 is
observed in campaigns, not only in the unit test.

## 5. Compaction as a separate pass

G45: compaction on insert made the loop twenty times slower for less coverage, because early in
a campaign nearly every run adds an entry. If a smaller corpus is wanted (for sharing, or to
keep replay cheap), compact as a separate command over an existing corpus, oldest entries first,
with the same `Shrinker` and a per-entry budget; the loop itself never compacts.

## 6. Smaller items

- The `operations` family's remaining throws are BigInt mixed with Number and Symbol in
  arithmetic, which are correct TypeErrors; `operand()` (G46) keeps them to ~15 % of draws.
  The next completion-rate item is the `calls` family at 75 %.
- `number_flip` is rarely applicable (it needs a number variable in scope) and rarely
  productive; a variant that first inserts a number-var function and then flips would make
  the compiled-mode bug shape (`compiled-bigint-in-number-typed-local.js`) reachable by
  mutation, not only by the `numeric_local_type_flip` generator.
- Splice donors from the corpus were decided against (G44); revisit with item 2 in place, since
  a rebased base is already a constant and a donor could be treated the same way.
