import pytest

from oxpecker import HARNESS_BIN
from oxpecker.harness.client import HarnessClient


@pytest.fixture(scope="session")
def harness():
    if not HARNESS_BIN.exists():
        pytest.skip("harness not built: cd harness-java && ./gradlew fatJar")
    # OXPECKER_COVERAGE=1 attaches JaCoCo so the property test can steer on new Rhino probes.
    import os
    with HarnessClient(quiet=True, coverage=bool(os.environ.get("OXPECKER_COVERAGE"))) as c:
        yield c


@pytest.fixture(scope="session")
def target_harness():
    """A harness client per Rhino target, started on first use: target_harness("master")."""
    from oxpecker import targets

    clients: dict[str, HarnessClient] = {}

    def get(name: str) -> HarnessClient:
        if name not in clients:
            if not targets.jar(name).exists():
                pytest.skip(f"no harness for Rhino target {name}: oxpecker build-harness {name}")
            clients[name] = HarnessClient(quiet=True, target=name)
            clients[name].start()
        return clients[name]
    yield get
    for c in clients.values():
        c.stop()


def pytest_generate_tests(metafunc):
    """`rhino_target` runs a test once per Rhino target with a built harness (rhino-targets.toml);
    only the current one (OXPECKER_RHINO, else the default) when none is built."""
    if "rhino_target" in metafunc.fixturenames:
        from oxpecker import targets
        metafunc.parametrize("rhino_target", targets.built() or [targets.current()])


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
