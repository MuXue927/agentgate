from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


def resolve_executable(argv: Sequence[str]) -> list[str]:
    """Resolve argv[0] through PATH so Windows can spawn npm shims.

    `codex` from npm is a `.cmd` wrapper. CreateProcess cannot execute a .cmd
    directly, so `subprocess.run(["codex", ...])` fails with
    FileNotFoundError [WinError 2] even though `codex` works in a shell.
    shutil.which honours PATHEXT and returns the real `codex.CMD` path.
    """

    resolved = list(argv)
    if not resolved:
        return resolved
    first = resolved[0]
    if Path(first).is_absolute():
        return resolved
    found = shutil.which(first)
    if found:
        resolved[0] = found
    return resolved


class ErrorClass(StrEnum):
    OK = "OK"
    QUOTA = "QUOTA"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH = "AUTH"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    TOOL_ERROR = "TOOL_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    transcript_path: Path | None
    usage: TokenUsage | None
    error_class: ErrorClass


@runtime_checkable
class Adapter(Protocol):
    name: str

    def capability(self) -> dict[str, bool]: ...

    def build_cmd(
        self,
        prompt: str,
        model: str,
        *,
        cwd: Path,
        readonly: bool,
        schema: Path | None,
    ) -> list[str]: ...

    def parse(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        workdir: Path,
    ) -> RunResult: ...


class CodexAdapter:
    name = "codex"

    def capability(self) -> dict[str, bool]:
        return {
            "json_output": False,
            "structured_schema": False,
            "resume": False,
            "readonly": True,
        }

    def build_cmd(
        self,
        prompt: str,
        model: str,
        *,
        cwd: Path,
        readonly: bool,
        schema: Path | None,
    ) -> list[str]:
        command = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "-C",
            str(cwd),
        ]
        if readonly:
            command.extend(("-s", "read-only"))
        if schema is not None:
            command.extend(("--output-schema", str(schema)))
        command.extend(("-m", model, prompt))
        return command

    def parse(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        workdir: Path,
    ) -> RunResult:
        duration_s = float(getattr(proc, "agentgate_duration_s", 0.0))
        return RunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_s=duration_s,
            transcript_path=None,
            usage=None,
            error_class=classify_error(proc.returncode, proc.stderr),
        )


def classify_error(exit_code: int, stderr: str) -> ErrorClass:
    if exit_code == 0:
        return ErrorClass.OK

    normalized = stderr.casefold()
    if "insufficient_quota" in normalized:
        return ErrorClass.QUOTA
    if re.search(r"\b401\b|invalid api key", normalized):
        return ErrorClass.AUTH
    if re.search(r"rate.?limit|\b429\b", normalized):
        return ErrorClass.RATE_LIMIT
    if re.search(r"econnrefused|etimedout|tunnel", normalized):
        return ErrorClass.NETWORK
    return ErrorClass.UNKNOWN
