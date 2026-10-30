from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path

from agentgate.adapters.codex import (
    CodexAdapter,
    ErrorClass,
    RunResult,
    TokenUsage,
    resolve_executable,
)
from agentgate.proc import run_bounded
from agentgate.spec import TaskSpec
from agentgate.validation import require_different_models


def run(
    task: TaskSpec,
    prompt: str,
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    adapter: CodexAdapter | None = None,
) -> RunResult:
    """Run one executor turn with an argument list and no shell."""

    require_different_models(
        task.executor.model,
        task.verifier.model,
        executor_scaffold=task.executor.harness,
        verifier_scaffold=task.verifier.harness,
    )
    selected = adapter or CodexAdapter()
    command = resolve_executable(
        selected.build_cmd(
            prompt,
            task.executor.model,
            cwd=cwd,
            readonly=False,
            schema=None,
            sandbox=task.executor.sandbox,
        )
    )
    timeout_s = task.budget.max_minutes_per_round * 60
    started = time.monotonic()
    try:
        completed = run_bounded(
            command,
            cwd=cwd,
            stdin_text=prompt,
            timeout_s=timeout_s,
            env=env,
        )
    except OSError as exc:
        duration_s = time.monotonic() - started
        return RunResult(
            exit_code=-1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
            duration_s=duration_s,
            transcript_path=None,
            usage=TokenUsage(),
            error_class=ErrorClass.UNKNOWN,
        )

    if completed.timed_out:
        return RunResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_s=time.monotonic() - started,
            transcript_path=None,
            usage=None,
            error_class=ErrorClass.TIMEOUT,
        )
    completed.agentgate_duration_s = time.monotonic() - started  # type: ignore[attr-defined]
    return selected.parse(completed, workdir=cwd)
