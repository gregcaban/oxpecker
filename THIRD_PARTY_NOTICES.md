# Third-party notices

oxpecker re-implements ideas and specifications from the projects below. Their licences are
reproduced under `docs/licenses/`.

## Fuzzilli — Apache License 2.0

Copyright 2019 Google LLC. https://github.com/googleprojectzero/fuzzilli

The generator modules `oxpecker/gen/generators/fz_objects.py`, `fz_control.py`, `fz_exotic.py`,
`fz_api.py` and the templates in `oxpecker/gen/templates_fz.py` are Python ports of generators
and program templates from `Sources/Fuzzilli/CodeGen/CodeGenerators.swift`,
`ProgramTemplates.swift` and `JavaScriptEnvironment.swift`, adapted to emit JavaScript source
text through oxpecker's `ProgramBuilder` (whose method surface is modelled on Fuzzilli's
`ProgramBuilder.swift`) and restricted to what Rhino supports. Several generators in the other
`oxpecker/gen/generators/*.py` modules follow the same catalogue in spirit. No Swift source is
copied. Licence text: `docs/licenses/Apache-2.0.txt`.

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
