from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentgate.workspace import (
    WorkspaceError,
    clean_agent_worktrees,
    create_worktree,
)


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
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "AgentGate Test")
    git(repo, "config", "user.email", "agentgate@example.invalid")
    (repo / ".gitignore").write_text("wt/\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    git(repo, "branch", "keep-me")
    return repo


def test_clean_all_refuses_dirty_then_force_is_idempotent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    first = create_worktree(repo, "one")
    second = create_worktree(repo, "two")
    (first.path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="rerun with --force"):
        clean_agent_worktrees(repo, all_tasks=True)
    assert first.path.exists()
    assert second.path.exists()

    result = clean_agent_worktrees(repo, all_tasks=True, force=True)
    assert set(result.worktrees) == {first.path, second.path}
    assert not first.path.exists()
    assert not second.path.exists()
    assert git(repo, "branch", "--list", "ag/*").stdout.strip() == ""
    assert git(repo, "branch", "--list", "keep-me").stdout.strip()

    again = clean_agent_worktrees(repo, all_tasks=True, force=True)
    assert again.worktrees == ()
    assert again.branches == ()


def test_clean_task_only_removes_matching_agent_worktree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    selected = create_worktree(repo, "wanted")
    kept = create_worktree(repo, "other")

    result = clean_agent_worktrees(repo, task_id="wanted")

    assert result.worktrees == (selected.path,)
    assert not selected.path.exists()
    assert kept.path.exists()
    assert git(repo, "branch", "--list", "ag/other-r1").stdout.strip()
