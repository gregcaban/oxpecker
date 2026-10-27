"""Generator families. Importing this package registers every generator with the strategies
registry; `oxpecker.il.templates` imports it. Files translated from Fuzzilli carry the
attribution header; each generator's `source=` names the Swift definition it follows."""

from . import values, operations, control, calls, objects, compiler, java, continuations  # noqa: F401  (registration)
