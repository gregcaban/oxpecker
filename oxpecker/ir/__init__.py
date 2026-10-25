"""FuzzIL-style IR for generated programs (docs/proposal-hypothesis-ir.md).

A program is a tuple of `Instr` over integer SSA variables; blocks are matched Begin/End
instructions. `analysis` recomputes scopes and types from scratch (types are never stored),
`verify` checks the invariants every generator and mutator must keep, and `lift` turns a
program into JavaScript with probes, generator comments and an inlining policy.
"""
