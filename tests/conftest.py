import pytest

from oxpecker import HARNESS_BIN
from oxpecker.harness.client import HarnessClient


@pytest.fixture(scope="session")
def harness():
    if not HARNESS_BIN.exists():
        pytest.skip("harness not built: cd harness-java && ./gradlew fatJar")
    with HarnessClient(quiet=True) as c:
        yield c
