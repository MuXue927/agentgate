"""Scripted model backends for loop tests.

Test-only, and deliberately so: nothing in `src/` imports this module and the
CLI cannot select it. What is faked is exactly the boundary that would
otherwise start a process -- `runner.run` for the executor, and the verifier's
`command_runner`. Everything between those two points is the real thing:
worktree creation, collection, the mechanical checks, every gate, the state
writes, the markers and the report.

That is the point of P1-5. A fake that replaced the loop as well would only
prove that the fake works.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agentgate.adapters.codex import ErrorClass, RunResult
from agentgate.execution import ExecutionOutcome, execute_round
from agentgate.verifier import VerificationOutcome, verify_round


def scripted_executor(
    rounds: Mapping[int, Callable[[Path], None]],
) -> Callable[..., ExecutionOutcome]:
    """The real `execute_round`, with the model replaced by a shell action.

    `rounds[n](worktree)` is what the executor "did" -- ordinary file writes.
    The prompt, the diff, the checks and the artifacts are all produced by the
    real code from that tree.
    """

    calls: list[int] = []

    def executor(task_path: Path, **kwargs: Any) -> ExecutionOutcome:
        round_no = int(kwargs["round_no"])
        calls.append(round_no)
        action = rounds[round_no]

        def fake_run(task: Any, prompt: str, *, cwd: Path, **_: Any) -> RunResult:
            action(cwd)
            return RunResult(
                exit_code=0,
                stdout=f"scripted executor, round {round_no}",
                stderr="",
                duration_s=0.0,
                transcript_path=None,
                usage=None,
                error_class=ErrorClass.OK,
            )

        with patch("agentgate.runner.run", side_effect=fake_run):
            return execute_round(task_path, **kwargs)

    executor.calls = calls  # type: ignore[attr-defined]
    return executor


def scripted_verifier(
    verdicts: Mapping[int, dict[str, Any]] | None = None,
) -> Callable[..., VerificationOutcome]:
    """The real `verify_round`, with the model answering from `verdicts`.

    A round with no scripted answer is a round some gate has to stop before the
    model is reached; if the model is reached anyway the runner raises, so a
    test can never pass by accident on a verdict nobody scripted.

    `subject` is echoed in at call time: the schema requires it and it only
    exists once the verifier has frozen the round's evidence.
    """

    scripted = dict(verdicts or {})
    calls: list[int] = []
    answered: list[int] = []

    def verifier(task_id: str, **kwargs: Any) -> VerificationOutcome:
        round_no = int(kwargs["round_no"])
        calls.append(round_no)
        runs_root = Path(kwargs["runs_root"])

        def answer(
            command: tuple[str, ...],
            cwd: Path,
            timeout: float,
            stdin_text: str,
        ) -> subprocess.CompletedProcess[str]:
            if round_no not in scripted:
                raise AssertionError(
                    f"the verifier model was reached in round {round_no}, "
                    "but no verdict was scripted for that round"
                )
            answered.append(round_no)
            document = dict(scripted[round_no])
            document["subject"] = _frozen_subject(runs_root, task_id, round_no)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(document),
                "",
            )

        return verify_round(task_id, command_runner=answer, **kwargs)

    # `calls` includes rounds that a gate stopped (the verifier still runs);
    # `answered` counts the ones the model actually had to answer.
    verifier.calls = calls  # type: ignore[attr-defined]
    verifier.answered = answered  # type: ignore[attr-defined]
    return verifier


def _frozen_subject(runs_root: Path, task_id: str, round_no: int) -> dict[str, Any]:
    path = runs_root / task_id / f"round-{round_no}" / "subject.json"
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["scripted_executor", "scripted_verifier"]
