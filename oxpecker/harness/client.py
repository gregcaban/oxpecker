"""HarnessClient: spawns the Java harness, sends cases as JSON lines, restarts it on death."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from .. import HARNESS_BIN

DEFAULT_MODES = ("interpreted", "compiled")


@dataclass
class Outcome:
    mode: str
    status: str
    compiledActually: bool | None = None
    value: str | None = None
    valueJavaTypes: str | None = None
    thrown: dict[str, Any] | None = None
    trace: list[str] = field(default_factory=list)
    traceJavaTypes: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    engineException: dict[str, Any] | None = None
    reason: str | None = None
    ms: int = 0

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Outcome":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)

    def to_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class RunResult:
    id: str
    results: dict[str, Outcome]
    restarted: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "results": {m: o.to_json() for m, o in self.results.items()},
                "restarted": self.restarted}


class HarnessError(RuntimeError):
    pass


class HarnessClient:
    """One long-lived JVM. Not thread-safe; use one client per worker."""

    def __init__(self, harness_bin: os.PathLike[str] | str = HARNESS_BIN, *, recycle_every: int = 5000,
                 hard_timeout_s: float = 60.0, quiet: bool = False):
        self.harness_bin = str(harness_bin)
        self.recycle_every = recycle_every
        self.hard_timeout_s = hard_timeout_s
        self.quiet = quiet
        self.proc: subprocess.Popen[str] | None = None
        self.cases_since_start = 0
        self.restarts = 0

    # -- process management --------------------------------------------------------------
    def start(self) -> None:
        if not os.path.exists(self.harness_bin):
            raise HarnessError(f"harness launcher not found: {self.harness_bin} (build with: cd harness-java && ./gradlew fatJar)")
        self.proc = subprocess.Popen(
            [self.harness_bin, "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None if not self.quiet else subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
        )
        self.cases_since_start = 0

    def stop(self) -> None:
        p = self.proc
        self.proc = None
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.write(json.dumps({"cmd": "exit"}) + "\n")
                p.stdin.flush()
            p.wait(timeout=2)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            p.kill()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def restart(self) -> None:
        self.stop()
        self.restarts += 1
        self.start()

    def __enter__(self) -> "HarnessClient":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def info(self) -> dict[str, Any]:
        return self._request({"cmd": "info"})["info"]

    # -- requests -------------------------------------------------------------------------
    def _request(self, req: dict[str, Any]) -> dict[str, Any]:
        if self.proc is None:
            self.start()
        assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
        line = json.dumps(req, ensure_ascii=False)
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise HarnessError(f"harness died while sending: {e}") from e

        # A hard deadline beyond anything the harness's own watchdog should allow.
        timer = threading.Timer(self.hard_timeout_s, self._kill)
        timer.start()
        try:
            resp_line = self.proc.stdout.readline()
        finally:
            timer.cancel()
        if not resp_line:
            raise HarnessError("harness died (no response)")
        try:
            return json.loads(resp_line)
        except json.JSONDecodeError as e:
            raise HarnessError(f"bad response from harness: {resp_line[:200]!r}") from e

    def _kill(self) -> None:
        if self.proc is not None:
            self.proc.kill()

    def run(self, source: str, *, modes: tuple[str, ...] = DEFAULT_MODES, language_version: int = 200,
            strict: bool = False, instruction_budget: int = 5_000_000, wall_ms: int = 5000, seed: int = 1,
            id: str | None = None, source_name: str = "case.js") -> RunResult:
        """Runs one case in every mode. If the JVM dies or asks to be restarted, the missing modes
        are reported as crash/timeout and the JVM is restarted."""
        case_id = id or uuid.uuid4().hex[:12]
        req = {"id": case_id, "source": source, "modes": list(modes), "languageVersion": language_version,
               "strict": strict, "instructionBudget": instruction_budget, "wallMs": wall_ms, "seed": seed,
               "sourceName": source_name}
        if self.cases_since_start >= self.recycle_every:
            self.restart()
        self.cases_since_start += 1
        restarted = False
        try:
            resp = self._request(req)
        except HarnessError as e:
            if not self.quiet:
                print(f"[harness] {e}; restarting JVM", file=sys.stderr)
            self.restart()
            results = {m: Outcome(mode=m, status="crash", reason="jvm-died",
                                  engineException={"clazz": "JVM", "message": str(e), "stack": ""}) for m in modes}
            return RunResult(case_id, results, restarted=True)
        if "error" in resp:
            raise HarnessError(resp["error"])
        results = {m: Outcome.from_json(o) for m, o in resp.get("results", {}).items()}
        for m in modes:
            if m not in results:
                # The harness stopped before running this mode (watchdog / OOM in an earlier mode).
                results[m] = Outcome(mode=m, status="crash", reason="not-run")
        if resp.get("restart"):
            restarted = True
            self.restart()
        return RunResult(case_id, results, restarted=restarted)
