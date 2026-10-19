import pytest

from oxpecker import HARNESS_BIN
from oxpecker.harness.client import HarnessClient


@pytest.fixture(scope="session")
def harness():
    if not HARNESS_BIN.exists():
        pytest.skip("harness not built: cd harness-java && ./gradlew fatJar")
    with HarnessClient(quiet=True) as c:
        yield c


@pytest.fixture(scope="session")
def known(harness):
    """Finding signatures already captured as regressions, so the property test logs rather than
    fails on them. Extra signatures can be listed in tests/known_signatures.txt."""
    from pathlib import Path
    from oxpecker.harness.compare import compare_run

    here = Path(__file__).parent
    sigs: set[str] = set()
    for path in sorted((here / "regressions").glob("*.js")):
        run = harness.run(path.read_text(), source_name=path.name)
        sigs.add(compare_run(run).signature())
    extra = here / "known_signatures.txt"
    if extra.exists():
        sigs |= {ln.split("#", 1)[0].strip() for ln in extra.read_text().splitlines() if ln.split("#", 1)[0].strip()}
    return sigs
