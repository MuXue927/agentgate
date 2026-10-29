from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(RuntimeError):
    """Raised when a safe internal worktree cannot be created."""


@dataclass(frozen=True)
class Worktree:
    repo: Path
    path: Path
    branch: str


def _run_git(
    repo: Path,
    args: list[str],
    *,
    timeout_s: float = 20,
) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        raise WorkspaceError("git executable not found")
    try:
        return subprocess.run(
            [git, *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git command timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise WorkspaceError(f"git failed to start: {exc}") from exc


def _repo_root(repo: Path) -> Path:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise WorkspaceError(f"Target repo does not exist: {repo}")
    result = _run_git(repo, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "not a Git repo"
        raise WorkspaceError(f"Target repo is not a Git worktree: {repo}\n{stderr}")
    root = Path(result.stdout.strip()).resolve()
    if root != repo:
        raise WorkspaceError(
            f"task.yaml repo must be the Git root, not a subdirectory: {root}"
        )
    return root


def _internal_worktree_path(repo: Path, task_id: str, round_no: int) -> Path:
    wt_root = (repo / "wt").resolve()
    path = (wt_root / f"{task_id}-r{round_no}").resolve()
    if path.parent != wt_root:
        raise WorkspaceError(f"Unsafe worktree path: {path}")
    return path


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _run_git(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
    return result.returncode == 0


def create_worktree(repo: Path, task_id: str, *, round_no: int = 1) -> Worktree:
    root = _repo_root(repo)
    path = _internal_worktree_path(root, task_id, round_no)
    branch = f"ag/{task_id}-r{round_no}"
    if path.exists():
        raise WorkspaceError(f"Worktree path already exists: {path}")
    if _branch_exists(root, branch):
        raise WorkspaceError(f"Branch already exists: {branch}")

    path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git(
        root,
        ["worktree", "add", "-b", branch, str(path)],
    )
    if result.returncode != 0:
        cleanup_worktree(root, path, branch)
        stderr = result.stderr.strip() or result.stdout.strip()
        raise WorkspaceError(f"git worktree add failed:\n{stderr}")
    return Worktree(repo=root, path=path, branch=branch)


def cleanup_worktree(repo: Path, path: Path, branch: str) -> None:
    """Remove one AgentGate worktree and branch; safe to call repeatedly."""

    root = _repo_root(repo)
    path = path.expanduser().resolve()
    if path.parent != (root / "wt").resolve():
        raise WorkspaceError(f"Refusing to clean outside repo/wt: {path}")

    if path.exists():
        removed = _run_git(root, ["worktree", "remove", "--force", str(path)])
        if removed.returncode != 0 and path.exists():
            shutil.rmtree(path)
    if _branch_exists(root, branch):
        _run_git(root, ["branch", "-D", branch])
