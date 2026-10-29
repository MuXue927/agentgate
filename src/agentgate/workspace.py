from __future__ import annotations

import re
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
    base_commit: str | None = None


@dataclass(frozen=True)
class CleanResult:
    worktrees: tuple[Path, ...]
    branches: tuple[str, ...]


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


def _listed_worktrees(repo: Path) -> tuple[Worktree, ...]:
    result = _run_git(repo, ["worktree", "list", "--porcelain"])
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise WorkspaceError(f"git worktree list failed:\n{stderr}")

    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines() + [""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value

    return tuple(
        Worktree(
            repo=repo,
            path=Path(record["worktree"]).resolve(),
            branch=record.get("branch", "").removeprefix("refs/heads/"),
        )
        for record in records
    )


def _agent_branches(repo: Path, task_id: str | None) -> set[str]:
    result = _run_git(
        repo,
        ["for-each-ref", "--format=%(refname:short)", "refs/heads/ag/"],
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise WorkspaceError(f"git branch listing failed:\n{stderr}")
    branches = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    if task_id is None:
        return branches
    pattern = re.compile(rf"^ag/{re.escape(task_id)}-r\d+$")
    return {branch for branch in branches if pattern.fullmatch(branch)}


def clean_agent_worktrees(
    repo: Path,
    *,
    task_id: str | None = None,
    all_tasks: bool = False,
    force: bool = False,
) -> CleanResult:
    """Remove only AgentGate worktrees under wt/ and ag/* branches."""

    if bool(task_id) == all_tasks:
        raise WorkspaceError("Choose exactly one of <task-id> or --all")

    root = _repo_root(repo)
    wt_root = (root / "wt").resolve()
    listed = _listed_worktrees(root)
    branch_set = _agent_branches(root, None if all_tasks else task_id)

    selected: list[Worktree] = []
    for entry in listed:
        if entry.path == root or entry.path.parent != wt_root:
            continue
        if not all_tasks and entry.branch not in branch_set:
            continue
        selected.append(entry)

    if not selected and not branch_set:
        return CleanResult(worktrees=(), branches=())

    if not force:
        dirty: list[Path] = []
        for entry in selected:
            if not entry.path.exists():
                continue
            status = _run_git(entry.path, ["status", "--porcelain"])
            if status.returncode != 0:
                raise WorkspaceError(
                    f"Cannot inspect worktree: {entry.path}\n{status.stderr.strip()}"
                )
            if status.stdout.strip():
                dirty.append(entry.path)
        if dirty:
            paths = "\n".join(f"- {path}" for path in dirty)
            raise WorkspaceError(
                "AgentGate worktree has uncommitted changes; "
                "rerun with --force to delete:\n" + paths
            )

    removed_paths: list[Path] = []
    for entry in selected:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(entry.path))
        removed = _run_git(root, args)
        if removed.returncode != 0:
            stderr = removed.stderr.strip() or removed.stdout.strip()
            raise WorkspaceError(f"git worktree remove failed: {entry.path}\n{stderr}")
        removed_paths.append(entry.path)

    removed_branches: list[str] = []
    active_branches = {
        entry.branch for entry in _listed_worktrees(root) if entry.branch
    }
    for branch in sorted(branch_set):
        if branch in active_branches:
            continue
        deleted = _run_git(root, ["branch", "-D", branch])
        if deleted.returncode != 0:
            stderr = deleted.stderr.strip() or deleted.stdout.strip()
            raise WorkspaceError(f"git branch delete failed: {branch}\n{stderr}")
        removed_branches.append(branch)

    _run_git(root, ["worktree", "prune"])
    return CleanResult(
        worktrees=tuple(removed_paths),
        branches=tuple(removed_branches),
    )


def create_worktree(repo: Path, task_id: str, *, round_no: int = 1) -> Worktree:
    root = _repo_root(repo)
    path = _internal_worktree_path(root, task_id, round_no)
    branch = f"ag/{task_id}-r{round_no}"
    if path.exists():
        raise WorkspaceError(f"Worktree path already exists: {path}")
    if _branch_exists(root, branch):
        raise WorkspaceError(f"Branch already exists: {branch}")

    base = _run_git(root, ["rev-parse", "HEAD"])
    if base.returncode != 0:
        stderr = base.stderr.strip() or base.stdout.strip()
        raise WorkspaceError(f"Cannot resolve worktree base commit:\n{stderr}")
    base_commit = base.stdout.strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git(
        root,
        ["worktree", "add", "-b", branch, str(path)],
    )
    if result.returncode != 0:
        cleanup_worktree(root, path, branch)
        stderr = result.stderr.strip() or result.stdout.strip()
        raise WorkspaceError(f"git worktree add failed:\n{stderr}")
    return Worktree(
        repo=root,
        path=path,
        branch=branch,
        base_commit=base_commit,
    )


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
