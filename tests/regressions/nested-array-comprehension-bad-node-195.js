// Rhino b95f75d: a JS 1.7 array comprehension nested as the element expression of another
// array comprehension (`[[... for (x in ..)] for (y in ..)]`) parses fine, but the inner
// comprehension's block-scope cleanup leaves a RESETVAR node (Token 195) in a position the
// code generators don't expect, so both evaluators throw a raw Java RuntimeException instead
// of producing/rejecting the expression:
//   interpreted: java.lang.RuntimeException: 195                     (CodeGenerator.badTree)
//   compiled:    java.lang.RuntimeException: Unexpected node type 195 (BodyCodegen, default case)
// Same root cause (confirmed standalone) as the generator-expression filter form
// `[x for (c in []) if ([0 for (x in [])])]`. Originating tests:
//   js1_5/Regress/regress-349648.js (nested array comprehension, decompilation)
//   js1_8/genexps/regress-380237-01.js (sudoku solver, nested genexprs w/ `if` filter)
//   js1_8_1/regress/regress-452498-119.js (comment #119: `uneval(new Function("[(x = x) for
//     (c in []) if ([{} for (x in [])])]"))`)
// Found by replaying Rhino's legacy Mozilla suite (oxpecker corpus rhino-mozilla), 2026-10-18.
// Replay: oxpecker diff tests/regressions/nested-array-comprehension-bad-node-195.js --language-version 250
function f() { [[0 for (x in [])] for (y in [])]; }
