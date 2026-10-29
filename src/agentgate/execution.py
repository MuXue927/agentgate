from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from agentgate import runner
from agentgate.adapters.codex import RunResult
from agentgate.collector import (
    CollectionResult,
    CollectorError,
    NoChangesError,
    collect_blocked,
    collect_success,
    ensure_run_slot,
    utc_now,
)
from agentgate.dryrun import build_execution_prompt
from agentgate.spec import PROJECT_ROOT, TaskSpec, load_task, require_allowed_repo
from agentgate.validation import ModelPair, evaluate_model_pair
from agentgate.workspace import create_worktree


@dataclass(frozen=True)
class ExecutionOutcome:
    task: TaskSpec
    pair: ModelPair
    collection: CollectionResult
    result: RunResult
    blocked: bool


def execute_round(
    task_path: Path,
    *,
    allow_self: bool = False,
    runs_root: Path | None = None,
) -> ExecutionOutcome:
    task = load_task(task_path)
    require_allowed_repo(task, allow_self=allow_self)
    pair = evaluate_model_pair(
        task.executor.model,
        task.verifier.model,
        executor_scaffold=task.executor.harness,
        verifier_scaffold=task.verifier.harness,
    )
    if task.executor.harness != "codex":
        raise ValueError("executor.harness: v0.2 requires Codex as the executor")

    destination = runs_root or (PROJECT_ROOT / "runs")
    round_dir = ensure_run_slot(destination, task.id)
    shutil.copyfile(task.path, round_dir.parent / "task.yaml")
    worktree = create_worktree(task.repo, task.id, round_no=1)
    started_at = utc_now()
    try:
        prompt = build_execution_prompt(task, worktree)
        result = runner.run(task, prompt, cwd=worktree.path)
        if result.exit_code != 0:
            collection = collect_blocked(
                task,
                worktree,
                result,
                pair,
                round_dir=round_dir,
                started_at=started_at,
            )
            return ExecutionOutcome(
                task=task,
                pair=pair,
                collection=collection,
                result=result,
                blocked=True,
            )
        try:
            collection = collect_success(
                task,
                worktree,
                result,
                pair,
                round_dir=round_dir,
                started_at=started_at,
            )
        except NoChangesError as exc:
            collection = collect_blocked(
                task,
                worktree,
                result,
                pair,
                round_dir=round_dir,
                started_at=started_at,
                reason=str(exc),
            )
            return ExecutionOutcome(
                task=task,
                pair=pair,
                collection=collection,
                result=result,
                blocked=True,
            )
    except Exception:
        if not (round_dir.parent / "BLOCKED").exists():
            (round_dir.parent / "BLOCKED").write_text(
                "Collection failed; inspect executor.log and worktree.",
                encoding="utf-8",
            )
        raise
    return ExecutionOutcome(
        task=task,
        pair=pair,
        collection=collection,
        result=result,
        blocked=False,
    )


__all__ = ["CollectorError", "ExecutionOutcome", "execute_round"]
