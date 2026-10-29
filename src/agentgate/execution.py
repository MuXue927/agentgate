from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    prompt: str
    blocked: bool


def build_defect_prompt(
    task: TaskSpec,
    defects: list[dict[str, Any]],
    *,
    previous_round_dir: Path,
) -> str:
    constraints = "\n".join(f"- {item}" for item in task.constraints)
    defect_lines: list[str] = []
    for defect in defects:
        defect_lines.extend(
            (
                f"### {defect['id']}",
                f"where: {defect['where']}",
                f"why: {defect['why']}",
                f"reproduce: {defect['reproduce']}",
                f"expect: {defect['expect']}",
                f"actual: {defect['actual']}",
            )
        )
    defect_text = "\n".join(defect_lines)
    return (
        "You are the implementation executor continuing a failed AgentGate task.\n"
        f"Task id: {task.id}\n"
        f"Goal:\n{task.goal}\n\n"
        "Constraints:\n"
        f"{constraints}\n\n"
        "Previous-round defects (authoritative):\n"
        f"{defect_text}\n\n"
        f"Previous-round diff path: {previous_round_dir / 'diff.patch'}\n"
        "Fix the defects above. Do not change anything else.\n"
        "Do not run git commit or git add. Do not push or merge."
    )


def execute_round(
    task_path: Path,
    *,
    allow_self: bool = False,
    runs_root: Path | None = None,
    round_no: int = 1,
    base_ref: str | None = None,
    defects: list[dict[str, Any]] | None = None,
    previous_round_dir: Path | None = None,
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
    round_dir = ensure_run_slot(destination, task.id, round_no=round_no)
    task_snapshot = round_dir.parent / "task.yaml"
    if not task_snapshot.exists():
        shutil.copyfile(task.path, task_snapshot)
    worktree = create_worktree(
        task.repo,
        task.id,
        round_no=round_no,
        base_ref=base_ref,
    )
    started_at = utc_now()
    try:
        if defects is not None and previous_round_dir is not None:
            prompt = build_defect_prompt(
                task,
                defects,
                previous_round_dir=previous_round_dir,
            )
        else:
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
                round_no=round_no,
            )
            return ExecutionOutcome(
                task=task,
                pair=pair,
                collection=collection,
                result=result,
                prompt=prompt,
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
                round_no=round_no,
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
                round_no=round_no,
            )
            return ExecutionOutcome(
                task=task,
                pair=pair,
                collection=collection,
                result=result,
                prompt=prompt,
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
        prompt=prompt,
        blocked=False,
    )


__all__ = ["CollectorError", "ExecutionOutcome", "execute_round"]
