from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentgate import execution
from agentgate.adapters.codex import ErrorClass, RunResult
from agentgate.workspace import cleanup_worktree


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "AgentGate Test")
    git(repo, "config", "user.email", "agentgate@example.invalid")
    (repo / ".gitignore").write_text("wt/\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    return repo


def make_task(tmp_path: Path, repo: Path) -> Path:
    task = tmp_path / "task.yaml"
    task.write_text(
        f"""\
id: execution-fixture
repo: {repo.as_posix()}
goal: Create hello.txt with content ok.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints:
  - Do not push.
checks:
  - name: hello-exists
    run: echo check-output
    expect: {{exit: 0, stdout_contains: check-output}}
budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


def result(exit_code: int = 0, *, stderr: str = "") -> RunResult:
    return RunResult(
        exit_code=exit_code,
        stdout="EXECUTOR NATURAL SUMMARY: I have finished.",
        stderr=stderr,
        duration_s=0.25,
        transcript_path=None,
        usage=None,
        error_class=ErrorClass.OK if exit_code == 0 else ErrorClass.UNKNOWN,
    )


def test_success_collects_only_artifacts_for_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)

    def fake_executor(*args: object, **kwargs: object) -> RunResult:
        worktree = kwargs["cwd"]
        assert isinstance(worktree, Path)
        (worktree / "hello.txt").write_text("ok\n", encoding="utf-8")
        return result()

    monkeypatch.setattr(execution.runner, "run", fake_executor)
    outcome = execution.execute_round(task, runs_root=tmp_path / "runs")
    round_dir = outcome.collection.round_dir
    try:
        assert not outcome.blocked
        assert round_dir == tmp_path / "runs" / "execution-fixture" / "round-1"
        assert (round_dir / "diff.patch").is_file()
        assert (round_dir / "executor.log").is_file()
        assert (round_dir / "state.json").is_file()
        assert (round_dir / "verifier_input.md").is_file()
        assert (round_dir.parent / "EXECUTED").is_file()
        assert list((round_dir / "artifacts").glob("*.stdout.txt"))

        diff = (round_dir / "diff.patch").read_text(encoding="utf-8")
        assert "hello.txt" in diff
        assert "+ok" in diff
        assert "?? hello.txt" in diff

        executor_log = (round_dir / "executor.log").read_text(encoding="utf-8")
        verifier_input = (round_dir / "verifier_input.md").read_text(encoding="utf-8")
        assert "EXECUTOR NATURAL SUMMARY" in executor_log
        assert "EXECUTOR NATURAL SUMMARY" not in verifier_input
        assert "I have" not in verifier_input
        assert "已完成" not in verifier_input
        assert "check-output" in verifier_input
        assert "## Goal" in verifier_input
        assert "## Acceptance checks" in verifier_input
        assert "## Diff" in verifier_input
        assert "## Check evidence" in verifier_input
        assert "## Changed file list" in verifier_input

        state = json.loads((round_dir / "state.json").read_text(encoding="utf-8"))
        assert state["status"] == "executed"
        assert state["executor"]["exit"] == 0
        assert state["model_pair"]["tier"] == "★★"
        assert outcome.task.repo == repo
    finally:
        cleanup_worktree(
            repo,
            outcome.task.repo / "wt" / "execution-fixture-r1",
            "ag/execution-fixture-r1",
        )


def test_nonzero_executor_writes_blocked_without_verifier_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)

    monkeypatch.setattr(
        execution.runner,
        "run",
        lambda *args, **kwargs: result(1, stderr="raw unknown failure"),
    )
    outcome = execution.execute_round(task, runs_root=tmp_path / "blocked-runs")
    round_dir = outcome.collection.round_dir
    try:
        assert outcome.blocked
        assert (round_dir.parent / "BLOCKED").is_file()
        assert not (round_dir / "verifier_input.md").exists()
        assert not (round_dir / "diff.patch").exists()
        log = (round_dir / "executor.log").read_text(encoding="utf-8")
        assert "raw unknown failure" in log
        state = json.loads((round_dir / "state.json").read_text(encoding="utf-8"))
        assert state["status"] == "blocked"
        assert state["executor"]["error_class"] == "UNKNOWN"
    finally:
        cleanup_worktree(
            repo,
            outcome.task.repo / "wt" / "execution-fixture-r1",
            "ag/execution-fixture-r1",
        )
