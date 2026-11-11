"""Rhino targets: the Rhino versions oxpecker runs against, named in rhino-targets.toml.

A target is a published release (`maven = "1.9.2"`) or a commit of a git repository (`git`,
`ref`). Each has its own harness jar (harness-java/build/libs/harness-NAME.jar, built by
`build`), its own capability table (what the generators may draw from) and its own regression
expectations. OXPECKER_RHINO=NAME selects one for a whole process: bin/harness picks the jar,
the environment model the table. Unset, it is the registry's default, whose jar is the plain
`./gradlew fatJar` (oxpecker-harness-all.jar), so a checkout that never names a target works
as before.

The harness source is shared: version-specific Rhino calls live in RhinoCompat (reflection)
and Scopes (one class per API line, src/rhino1 and src/rhino2), see harness-java/build.gradle.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import ROOT

REGISTRY = ROOT / "rhino-targets.toml"
ENV = "OXPECKER_RHINO"
LIBS = ROOT / "harness-java" / "build" / "libs"
SOURCES = ROOT / "build" / "rhino"                    # git targets are checked out here, one directory each
CAPABILITIES = ROOT / "oxpecker" / "environment" / "capabilities"
_LOCAL_JDK = "/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"     # as harness-java/gradle.properties
# The JDK 21 Rhino and the harness build with: OXPECKER_JAVA_HOME, else the local Homebrew one, else JAVA_HOME (CI).
JAVA_HOME = os.environ.get("OXPECKER_JAVA_HOME") or (_LOCAL_JDK if os.path.isdir(_LOCAL_JDK) else os.environ.get("JAVA_HOME", ""))


@dataclass(frozen=True)
class Target:
    name: str
    maven: str | None = None                          # a version on Maven Central
    git: str | None = None                            # or a repository and a commit in it
    ref: str | None = None

    @property
    def version(self) -> str:
        """What the harness reports as its Rhino version (the jar manifest's Rhino-Version)."""
        return self.maven if self.maven else f"{self.name}@{(self.ref or '')[:10]}"

    @property
    def api(self) -> str:
        """The harness's Scopes source set for this Rhino: 1 for the 1.x API, 2 for 2.0 (master)."""
        return "1" if self.maven and self.maven.startswith("1.") else "2"


def registry() -> tuple[str, dict[str, Target]]:
    data = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    targets = {name: Target(name, **spec) for name, spec in data.get("targets", {}).items()}
    return data["default"], targets


def default() -> str:
    return registry()[0]


def current() -> str:
    """The target this process runs against: OXPECKER_RHINO, else the registry's default."""
    return os.environ.get(ENV) or default()


def get(name: str) -> Target:
    _, targets = registry()
    if name not in targets:
        raise KeyError(f"unknown Rhino target {name!r}; rhino-targets.toml has {', '.join(targets)}")
    return targets[name]


def jar(name: str) -> Path:
    """The harness jar of a target. The default target's is the plain fatJar's when it has none
    of its own, so CI and `./gradlew fatJar` keep working without naming a target."""
    own = LIBS / f"harness-{name}.jar"
    if not own.exists() and name == default():
        return LIBS / "oxpecker-harness-all.jar"
    return own


def built() -> list[str]:
    """The targets whose harness jar exists, the default first."""
    first, targets = registry()
    return [n for n in sorted(targets, key=lambda n: (n != first, n)) if jar(n).exists()]


def capabilities(name: str) -> Path:
    return CAPABILITIES / f"{name}.json"


def build(name: str, *, quiet: bool = False) -> Path:
    """Builds the harness jar of a target, checking out and building Rhino first for a git one."""
    t = get(name)
    env = dict(os.environ, JAVA_HOME=JAVA_HOME)
    props = [f"-PrhinoVersion={t.version}", f"-PrhinoApi={t.api}", f"-PharnessJar=harness-{name}.jar"]
    if t.git:
        props.append(f"-PrhinoJar={_rhino_jar(t, env, quiet)}")
    # gradle.properties pins org.gradle.java.home to a local path; the command line wins.
    _run(["./gradlew", "-q", f"-Dorg.gradle.java.home={JAVA_HOME}", "fatJar", *props], ROOT / "harness-java", env, quiet)
    return LIBS / f"harness-{name}.jar"


def _rhino_jar(t: Target, env: dict[str, str], quiet: bool) -> Path:
    src = SOURCES / t.name
    if not (src / ".git").exists():
        SOURCES.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", t.git, str(src)], ROOT, env, quiet)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    if head != t.ref:
        _run(["git", "fetch", "--quiet", "origin", t.ref], src, env, quiet)
        _run(["git", "checkout", "--quiet", "--detach", t.ref], src, env, quiet)
    _run(["./gradlew", "-q", ":rhino:jar"], src, env, quiet)
    jars = sorted((src / "rhino" / "build" / "libs").glob("rhino-*.jar"))
    jars = [j for j in jars if not j.name.endswith(("-sources.jar", "-javadoc.jar"))]
    if len(jars) != 1:
        raise RuntimeError(f"expected one Rhino jar in {src}/rhino/build/libs, found {[j.name for j in jars]}")
    return jars[0]


def _run(cmd: list[str], cwd: Path, env: dict[str, str], quiet: bool) -> None:
    if not quiet:
        print(f"  {' '.join(cmd)}  (in {cwd.relative_to(ROOT) if cwd.is_relative_to(ROOT) else cwd})")
    if shutil.which(cmd[0]) is None and not (cwd / cmd[0]).exists():
        raise RuntimeError(f"{cmd[0]} not found")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def expected_kind(source: str, target: str, default_kind: str = "hard") -> str:
    """The verdict kind a checked-in reproducer expects on a target, from its leading comment
    lines: `// oxpecker-expect: KIND` for every target, `// oxpecker-expect[NAME]: KIND` for one
    (which wins). Without either: `default_kind`, a finding."""
    general = specific = None
    for line in source.splitlines():
        s = line.strip()
        if not s.startswith("//"):
            break
        if "oxpecker-expect[" in s:
            name, kind = s.split("oxpecker-expect[", 1)[1].split("]:", 1)
            if name.strip() == target:
                specific = kind.strip()
        elif "oxpecker-expect:" in s:
            general = s.split("oxpecker-expect:", 1)[1].strip()
    return specific or general or default_kind
