from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentgate.adapters.codex import RunResult
from agentgate.spec import CheckSpec, TaskSpec
from agentgate.status import write_marker
from agentgate.validation import ModelPair
from agentgate.workspace import Worktree


class CollectorError(RuntimeError):
    """Raised when a required artifact cannot be collected."""


class NoChangesError(CollectorError):
    """Raised when executor success produced no changes."""


@dataclass(frozen=True)
class CheckOutput:
    spec: CheckSpec
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _run_git(
    worktree: Path,
    args: list[str],
    *,
    timeout_s: float = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectorError(f"git artifact command failed: {exc}") from exc


def check_slug(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return normalized or "check"


def run_checks(task: TaskSpec, worktree: Path) -> tuple[CheckOutput, ...]:
    outputs: list[CheckOutput] = []
    timeout_s = task.budget.max_minutes_per_round * 60
    for check in task.checks:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                check.run,
                cwd=worktree,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
            outputs.append(
                CheckOutput(
                    spec=check,
                    exit_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    duration_s=time.monotonic() - started,
                )
            )
        except subprocess.TimeoutExpired as exc:
            outputs.append(
                CheckOutput(
                    spec=check,
                    exit_code=124,
                    stdout=_text(exc.stdout),
                    stderr=_text(exc.stderr),
                    duration_s=time.monotonic() - started,
                )
            )
        except OSError as exc:
            outputs.append(
                CheckOutput(
                    spec=check,
                    exit_code=-1,
                    stdout="",
                    stderr=f"{type(exc).__name__}: {exc}",
                    duration_s=time.monotonic() - started,
                )
            )
    return tuple(outputs)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def ensure_run_slot(runs_root: Path, task_id: str) -> Path:
    run_dir = runs_root / task_id
    round_dir = run_dir / "round-1"
    if round_dir.exists():
        raise CollectorError(f"Round directory already exists: {round_dir}")
    round_dir.mkdir(parents=True)
    return round_dir


def _executor_log(result: RunResult) -> str:
    return f"=== stdout ===\n{result.stdout}\n=== stderr ===\n{result.stderr}"


def _write_artifacts(
    round_dir: Path,
    checks: tuple[CheckOutput, ...],
) -> tuple[Path, ...]:
    artifacts = round_dir / "artifacts"
    artifacts.mkdir()
    paths: list[Path] = []
    for index, check in enumerate(checks, start=1):
        stem = f"{index:02d}-{check_slug(check.spec.name)}"
        files = {
            f"{stem}.stdout.txt": check.stdout,
            f"{stem}.stderr.txt": check.stderr,
            f"{stem}.exit.txt": f"{check.exit_code}\n",
        }
        for name, content in files.items():
            path = artifacts / name
            path.write_text(content, encoding="utf-8")
            paths.append(path)
    return tuple(paths)


def _git_artifacts(worktree: Worktree) -> tuple[str, str]:
    staged = _run_git(worktree.path, ["add", "-A"])
    if staged.returncode != 0:
        raise CollectorError(
            "git add -A failed: " + (staged.stderr.strip() or staged.stdout.strip())
        )
    head_diff = _run_git(worktree.path, ["diff", "HEAD"])
    if head_diff.returncode != 0:
        raise CollectorError(
            "git diff HEAD failed: "
            + (head_diff.stderr.strip() or head_diff.stdout.strip())
        )

    patch = head_diff.stdout
    commits = ""
    if worktree.base_commit:
        full_diff = _run_git(worktree.path, ["diff", worktree.base_commit])
        if full_diff.returncode != 0:
            raise CollectorError(
                "git diff base failed: "
                + (full_diff.stderr.strip() or full_diff.stdout.strip())
            )
        if full_diff.stdout:
            patch = full_diff.stdout
        log = _run_git(
            worktree.path,
            ["log", "--format=%h %s", f"{worktree.base_commit}..HEAD"],
        )
        if log.returncode == 0 and log.stdout.strip():
            commits = log.stdout.strip()

    status = _run_git(worktree.path, ["status", "--porcelain"])
    if status.returncode != 0:
        raise CollectorError(
            "git status failed: " + (status.stderr.strip() or status.stdout.strip())
        )
    if not patch.strip() and not status.stdout.strip():
        raise NoChangesError("no changes detected")

    if commits:
        patch = f"{patch}\n--- commits since base ---\n{commits}\n"
    return patch, status.stdout


def _fenced(content: str, language: str = "text") -> str:
    longest = max((len(match) for match in re.findall(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{content}\n{fence}"


def render_verifier_input(
    task: TaskSpec,
    diff: str,
    status: str,
    checks: tuple[CheckOutput, ...],
) -> str:
    check_lines = "\n".join(
        f"- `{check.name}`: `{check.run}`; expect: `{json.dumps(check.expect, ensure_ascii=False)}`"
        for check in task.checks
    )
    evidence_blocks: list[str] = []
    for check in checks:
        evidence_blocks.extend(
            (
                f"### {check.spec.name}",
                f"Command: `{check.spec.run}`",
                f"Exit code: `{check.exit_code}`",
                "stdout:",
                _fenced(check.stdout),
                "stderr:",
                _fenced(check.stderr),
            )
        )
    return "\n".join(
        (
            f"# Verification input: {task.id}",
            "",
            "## Goal",
            task.goal,
            "",
            "## Acceptance checks",
            check_lines,
            "",
            "## Diff",
            _fenced(diff, "diff"),
            "",
            "## Check evidence",
            *evidence_blocks,
            "",
            "## Changed file list",
            _fenced(status),
            "",
        )
    )


def _state_document(
    task: TaskSpec,
    worktree: Worktree,
    result: RunResult,
    pair: ModelPair,
    *,
    status: str,
    started_at: str,
    finished_at: str,
) -> dict[str, object]:
    return {
        "task_id": task.id,
        "repo": str(worktree.repo),
        "round": 1,
        "status": status,
        "worktree": str(worktree.path),
        "branch": worktree.branch,
        "executor": {
            "harness": task.executor.harness,
            "model": task.executor.model,
            "exit": result.exit_code,
            "duration_s": round(result.duration_s, 3),
            "error_class": result.error_class.value,
        },
        "verifier": {
            "harness": task.verifier.harness,
            "model": task.verifier.model,
            "readonly": True,
        },
        "model_pair": {
            "tier": pair.tier,
            "executor_family": pair.executor_family,
            "verifier_family": pair.verifier_family,
        },
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class CollectionResult:
    round_dir: Path
    state: dict[str, object]
    checks: tuple[CheckOutput, ...]


def collect_success(
    task: TaskSpec,
    worktree: Worktree,
    result: RunResult,
    pair: ModelPair,
    *,
    round_dir: Path,
    started_at: str,
) -> CollectionResult:
    diff, status = _git_artifacts(worktree)
    checks = run_checks(task, worktree.path)
    _write_artifacts(round_dir, checks)
    (round_dir / "executor.log").write_text(_executor_log(result), encoding="utf-8")

    diff_patch = f"{diff}\n--- git status --porcelain ---\n{status}"
    (round_dir / "diff.patch").write_text(diff_patch, encoding="utf-8")
    (round_dir / "changed_files.txt").write_text(status, encoding="utf-8")
    (round_dir / "verifier_input.md").write_text(
        render_verifier_input(task, diff_patch, status, checks),
        encoding="utf-8",
    )
    finished_at = utc_now()
    state = _state_document(
        task,
        worktree,
        result,
        pair,
        status="executed",
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_json(round_dir / "state.json", state)
    write_marker(round_dir.parent, "EXECUTED")
    return CollectionResult(round_dir=round_dir, state=state, checks=checks)


def collect_blocked(
    task: TaskSpec,
    worktree: Worktree,
    result: RunResult,
    pair: ModelPair,
    *,
    round_dir: Path,
    started_at: str,
    reason: str | None = None,
) -> CollectionResult:
    (round_dir / "executor.log").write_text(_executor_log(result), encoding="utf-8")
    finished_at = utc_now()
    state = _state_document(
        task,
        worktree,
        result,
        pair,
        status="blocked",
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_json(round_dir / "state.json", state)
    write_marker(
        round_dir.parent,
        "BLOCKED",
        reason or f"{result.error_class.value}: {result.stderr}",
    )
    return CollectionResult(round_dir=round_dir, state=state, checks=())
