from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentgate import dryrun, workspace
from agentgate.dryrun import prepare_dry_run
from agentgate.spec import SpecError
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
id: safe-test-task
repo: {repo.as_posix()}
goal: Test the dry-run path.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints:
  - Do not push.
checks:
  - name: tests
    run: printf ok
    expect: {{exit: 0, stdout_contains: ok}}
budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


def test_dry_run_builds_internal_worktree_and_only_calls_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)
    commands: list[list[str]] = []
    original_run = workspace.subprocess.run

    def record_run(command: list[str], *args: object, **kwargs: object):
        commands.append(command)
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(workspace.subprocess, "run", record_run)
    result = prepare_dry_run(task)

    assert result.worktree.path == repo / "wt" / "safe-test-task-r1"
    assert result.pair.tier == "★★"
    assert "--tools" in result.verifier_command or "-s" in result.verifier_command
    assert all(Path(command[0]).name.lower().startswith("git") for command in commands)
    assert "Executor command:" in result.render()
    assert "Verifier command:" in result.render()

    cleanup_worktree(result.worktree.repo, result.worktree.path, result.worktree.branch)


def test_cleanup_is_idempotent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)
    result = prepare_dry_run(task)

    cleanup_worktree(result.worktree.repo, result.worktree.path, result.worktree.branch)
    cleanup_worktree(result.worktree.repo, result.worktree.path, result.worktree.branch)

    assert not result.worktree.path.exists()
    branch = git(repo, "branch", "--list", result.worktree.branch).stdout.strip()
    assert branch == ""


def test_failure_after_worktree_creation_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)

    def fail_preview(task_spec, worktree):
        raise RuntimeError("preview failure")

    monkeypatch.setattr(dryrun, "build_verifier_command", fail_preview)
    with pytest.raises(RuntimeError, match="preview failure"):
        prepare_dry_run(task)

    path = repo / "wt" / "safe-test-task-r1"
    assert not path.exists()
    branch = git(repo, "branch", "--list", "ag/safe-test-task-r1").stdout.strip()
    assert branch == ""


def test_same_family_rejected_before_worktree_creation(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    task = tmp_path / "same-family.yaml"
    task.write_text(
        f"""\
id: same-family
repo: {repo.as_posix()}
goal: This must never reach worktree creation.
executor: {{harness: codex, model: deepseek-v4-pro}}
verifier: {{harness: codex, model: deepseek-flash}}
constraints: []
checks:
  - name: tests
    run: printf ok
    expect: {{exit: 0}}
budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="同一模型族"):
        prepare_dry_run(task)

    assert not (repo / "wt" / "same-family-r1").exists()


def test_self_repo_requires_explicit_allow(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)
    self_root = Path(__file__).resolve().parents[1]
    updated = task.read_text(encoding="utf-8").replace(
        repo.as_posix(),
        self_root.as_posix(),
    )
    updated = updated.replace("safe-test-task", "self-check")
    task.write_text(updated, encoding="utf-8")

    with pytest.raises(SpecError, match="points to AgentGate itself"):
        prepare_dry_run(task)

    result = prepare_dry_run(task, allow_self=True)
    try:
        assert result.worktree.path == self_root / "wt" / "self-check-r1"
    finally:
        cleanup_worktree(
            result.worktree.repo,
            result.worktree.path,
            result.worktree.branch,
        )
