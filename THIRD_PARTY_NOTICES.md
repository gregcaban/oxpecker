# Third-party notices

oxpecker re-implements ideas and specifications from the projects below. Their licences are
reproduced under `docs/licenses/`.

## Fuzzilli — Apache License 2.0

Copyright 2019 Google LLC. https://github.com/googleprojectzero/fuzzilli

oxpecker's generation layer is being rebuilt from Fuzzilli's design: its intermediate language
(FuzzIL: `Sources/Fuzzilli/FuzzIL/`), `ProgramBuilder`, code generators and program templates
(`Sources/Fuzzilli/CodeGen/`), JavaScript lifter (`Sources/Fuzzilli/Lifting/`) and mutators
(`Sources/Fuzzilli/Mutators/`), translated to Python and restricted to what Rhino supports. Every
file derived from Fuzzilli carries an attribution header naming the Fuzzilli source it follows.
Reference checkout: Fuzzilli commit `be2811d00282320381fc8007d64873745772011d` (2026-10-13). (The
earlier ports, `oxpecker/gen/generators/fz_*.py` and `templates_fz.py`, were removed on 2026-10-25
with the old generation layer.) Licence text: `docs/licenses/Apache-2.0.txt`.

## test262 — BSD licence

Copyright (c) 2012 Ecma International and contributors; individual files carry their own
copyright lines. https://github.com/tc39/test262

`tests/seeds/test262-*.js` are adaptations of two test262 files (each names its source and
original copyright in its header). `oxpecker corpus test262` reads Rhino's vendored copy of the
suite at run time; nothing else from it is vendored here. Licence text:
`docs/licenses/test262-LICENSE.txt`.

## Mozilla Rhino — MPL 2.0

https://github.com/mozilla/rhino. Used as a build-time dependency (`org.mozilla:rhino`, built from
source at the pinned commit) and not redistributed. The regression reproducers under
`tests/regressions/` derived from Rhino's own test suites (`nested-array-comprehension-*`,
`let-genexpr-*`, `compiled-yield-call-arg-*`, `compiled-relop-arg-*`, `string-doubling-*`,
`arrow-function-*`) are minimal snippets written for oxpecker, with the originating test named in
each header.

## JaCoCo — Eclipse Public License 2.0

https://www.jacoco.org/. Used at runtime as a Java agent and analysis library (Maven
dependencies), not redistributed.

## README photograph — CC0

`docs/oxpecker.jpg`: "Kruger Park, South Africa" by Joel Herzog, published on Unsplash and
mirrored on Wikimedia Commons
(https://commons.wikimedia.org/wiki/File:Kruger_Park,_South_Africa_(Unsplash_1xetOxStk_Y).jpg),
released under CC0 1.0 (public domain dedication). Credited in the README as a courtesy.
