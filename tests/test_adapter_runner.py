from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentgate import runner
from agentgate.adapters.codex import (
    Adapter,
    CodexAdapter,
    ErrorClass,
    classify_error,
    resolve_executable,
)
from agentgate.proc import BoundedResult
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
    # The prompt travels on stdin: an 18KB verifier input blows the Windows
    # argv limit ("input line is too long").
    assert "review this" not in command


def test_runner_passes_no_shell_and_records_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = load_task(ROOT / "templates" / "add-feature.task.yaml")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> BoundedResult:
        calls.append((command, kwargs))
        return BoundedResult(command, 0, "ok\n", "")

    # The runner goes through proc.run_bounded (tree-killing timeout), not
    # subprocess.run.
    monkeypatch.setattr(runner, "run_bounded", fake_run)
    result = runner.run(task, "do work", cwd=ROOT)

    assert result.exit_code == 0
    assert result.error_class is ErrorClass.OK
    assert result.stdout == "ok\n"
    assert result.duration_s >= 0
    assert Path(calls[0][0][0]).stem.lower() == "codex"
    assert "shell" not in calls[0][1]
    assert calls[0][1]["timeout_s"] == task.budget.max_minutes_per_round * 60


def test_runner_rejects_same_family_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = load_task(ROOT / "templates" / "add-feature.task.yaml")
    task = replace(task, verifier=replace(task.verifier, model="deepseek-v4-pro"))
    called = False

    def should_not_run(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "run_bounded", should_not_run)
    with pytest.raises(ModelPairError, match="同一模型族"):
        runner.run(task, "do work", cwd=ROOT)

    assert not called


def test_resolve_executable_finds_windows_shim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`codex` is an npm .cmd shim; CreateProcess cannot run it directly.

    Regression: build_cmd returned the bare name "codex", so
    subprocess.run(["codex", ...]) raised FileNotFoundError [WinError 2] even
    though "codex" worked in a shell.
    """
    import os

    shim = tmp_path / "faketool.cmd"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    resolved = resolve_executable(["faketool", "exec", "--flag"])

    assert Path(resolved[0]).name.lower() == "faketool.cmd"
    assert resolved[1:] == ["exec", "--flag"]


def test_resolve_executable_passthrough_for_absolute_and_unknown() -> None:
    absolute = str(ROOT / "pyproject.toml")
    assert resolve_executable([absolute, "x"]) == [absolute, "x"]
    assert resolve_executable(["definitely-not-a-real-binary-xyz", "y"])[0] == (
        "definitely-not-a-real-binary-xyz"
    )


def test_build_cmd_uses_explicit_sandbox() -> None:
    command = CodexAdapter().build_cmd(
        "prompt",
        "m",
        cwd=ROOT,
        readonly=True,
        schema=None,
        sandbox="danger-full-access",
    )

    assert command[command.index("-s") + 1] == "danger-full-access"


def test_build_cmd_defaults_to_read_only_for_verifier() -> None:
    command = CodexAdapter().build_cmd(
        "prompt", "m", cwd=ROOT, readonly=True, schema=None
    )

    assert command[command.index("-s") + 1] == "read-only"


def test_build_cmd_executor_default_has_no_sandbox_flag() -> None:
    command = CodexAdapter().build_cmd(
        "prompt", "m", cwd=ROOT, readonly=False, schema=None
    )

    assert "-s" not in command
