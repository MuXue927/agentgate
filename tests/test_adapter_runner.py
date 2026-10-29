from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentgate import runner
from agentgate.adapters.codex import Adapter, CodexAdapter, ErrorClass, classify_error
from agentgate.spec import load_task
from agentgate.validation import ModelPairError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("exit_code", "stderr", "expected"),
    (
        (0, "insufficient_quota", ErrorClass.OK),
        (1, "insufficient_quota", ErrorClass.QUOTA),
        (1, "HTTP 401 invalid api key", ErrorClass.AUTH),
        (1, "rate limit exceeded (429)", ErrorClass.RATE_LIMIT),
        (1, "connect ECONNREFUSED", ErrorClass.NETWORK),
        (1, "ETIMEDOUT while connecting", ErrorClass.NETWORK),
        (1, "tunnel error", ErrorClass.NETWORK),
        (1, "some unfamiliar failure", ErrorClass.UNKNOWN),
    ),
)
def test_error_classification(
    exit_code: int,
    stderr: str,
    expected: ErrorClass,
) -> None:
    assert classify_error(exit_code, stderr) is expected


def test_codex_command_uses_argument_list_and_readonly_flag(tmp_path: Path) -> None:
    adapter = CodexAdapter()
    assert isinstance(adapter, Adapter)
    command = adapter.build_cmd(
        "review this",
        "nvidia/nemotron-3.5-lightning:free",
        cwd=tmp_path,
        readonly=True,
        schema=None,
    )

    assert command[:4] == ["codex", "exec", "--skip-git-repo-check", "-C"]
    assert command[4] == str(tmp_path)
    assert command[5:7] == ["-s", "read-only"]
    assert command[-1] == "review this"


def test_runner_passes_no_shell_and_records_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = load_task(ROOT / "templates" / "add-feature.task.yaml")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.run(task, "do work", cwd=ROOT)

    assert result.exit_code == 0
    assert result.error_class is ErrorClass.OK
    assert result.stdout == "ok\n"
    assert result.duration_s >= 0
    assert calls[0][0][0] == "codex"
    assert "shell" not in calls[0][1]
    assert calls[0][1]["timeout"] == task.budget.max_minutes_per_round * 60


def test_runner_rejects_same_family_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = load_task(ROOT / "templates" / "add-feature.task.yaml")
    task = replace(task, verifier=replace(task.verifier, model="deepseek-v4-pro"))
    called = False

    def should_not_run(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner.subprocess, "run", should_not_run)
    with pytest.raises(ModelPairError, match="同一模型族"):
        runner.run(task, "do work", cwd=ROOT)

    assert not called
