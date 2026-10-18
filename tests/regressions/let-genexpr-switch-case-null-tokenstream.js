// Rhino b95f75d: a `switch` case value that is a JS 1.7 generator expression whose per-element
// expression is a `let (...) expr` parses in a way that leaves the parser's TokenStream (`this.ts`)
// null when some later parse step reads it back, crashing the engine instead of reporting a
// syntax error (E4X and nested comprehensions are not required - this is just `let` + a genexpr
// used as a `case` value):
//   interpreted: java.lang.NullPointerException: Cannot read field "cursor" because "this.ts" is null
//   compiled:    java.lang.NullPointerException: Cannot read field "cursor" because "this.ts" is null
// Originating tests: js1_7/regress/regress-352870-01.js and js1_7/regress/regress-352870-02.js
// (both combine this with E4X and nested array comprehensions, which are incidental).
// Found by replaying Rhino's legacy Mozilla suite (oxpecker corpus rhino-mozilla), 2026-10-18.
// Replay: oxpecker diff tests/regressions/let-genexpr-switch-case-null-tokenstream.js --language-version 250
switch (0) { case (let (a) 0 for (c in 0)): }
