from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agentgate.doctor import DSH_CLI, DSH_EXE
from agentgate.spec import TaskSpec, load_task
from agentgate.validation import ModelPair, evaluate_model_pair
from agentgate.workspace import Worktree, cleanup_worktree, create_worktree


@dataclass(frozen=True)
class DryRunResult:
    task: TaskSpec
    pair: ModelPair
    worktree: Worktree
    executor_command: tuple[str, ...]
    verifier_command: tuple[str, ...]

    def render(self) -> str:
        return "\n".join(
            (
                f"Task: {self.task.id}",
                (
                    f"Model pair: {self.pair.tier} "
                    f"({self.pair.executor_family} -> {self.pair.verifier_family})"
                ),
                f"Worktree: {self.worktree.path}",
                f"Branch: {self.worktree.branch}",
                "",
                "Executor command:",
                shell_join(self.executor_command),
                "",
                "Verifier command:",
                shell_join(self.verifier_command),
                "",
                "No model API process was started (dry-run).",
            )
        )


def _bullet_lines(values: Iterable[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _execution_prompt(task: TaskSpec, worktree: Worktree) -> str:
    return (
        "You are the implementation executor. Work only inside this worktree:\n"
        f"{worktree.path}\n\n"
        f"Task id: {task.id}\n"
        f"Goal:\n{task.goal}\n\n"
        "Constraints:\n"
        f"{_bullet_lines(task.constraints)}\n\n"
        "Acceptance checks (the verifier will run these independently):\n"
        f"{_bullet_lines(f'{check.name}: {check.run}' for check in task.checks)}\n\n"
        "Do not push or merge. Do not change files outside the task scope."
    )


def _verifier_prompt(task: TaskSpec) -> str:
    return (
        "Act only as a read-only verifier. Do not modify any file.\n"
        "Judge the supplied artifacts, never an executor narrative.\n"
        f"Task id: {task.id}\n"
        f"Goal:\n{task.goal}\n\n"
        "Acceptance checks:\n"
        f"{_bullet_lines(f'{check.name}: {check.run}' for check in task.checks)}\n\n"
        "Return JSON conforming to schemas/verdict.schema.json. "
        "Every defect must include a reproducible command."
    )


def build_executor_command(task: TaskSpec, worktree: Worktree) -> tuple[str, ...]:
    return (
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-C",
        str(worktree.path),
        "-m",
        task.executor.model,
        _execution_prompt(task, worktree),
    )


def build_verifier_command(task: TaskSpec, worktree: Worktree) -> tuple[str, ...]:
    prompt = _verifier_prompt(task)
    if task.verifier.harness == "codex":
        return (
            "codex",
            "exec",
            "--skip-git-repo-check",
            "-C",
            str(worktree.path),
            "-s",
            "read-only",
            "-m",
            task.verifier.model,
            prompt,
        )
    if task.verifier.harness == "pi":
        return (
            "pi",
            "-p",
            "--mode",
            "json",
            "--model",
            task.verifier.model,
            "--tools",
            "read,grep,find,ls",
            "--no-session",
            prompt,
        )
    if task.verifier.harness == "dsh":
        return (
            str(DSH_EXE),
            "--expose-internals",
            str(DSH_CLI),
            "headless",
            prompt,
            "--json",
        )
    raise ValueError(f"Unsupported verifier harness: {task.verifier.harness}")


def shell_join(command: tuple[str, ...]) -> str:
    def quote(value: str) -> str:
        if value and not any(char.isspace() for char in value):
            return value
        return "'" + value.replace("'", "''") + "'"

    return " ".join(quote(value) for value in command)


def prepare_dry_run(task_path: Path) -> DryRunResult:
    task = load_task(task_path)
    if task.executor.harness != "codex":
        raise ValueError("executor.harness: v0.2 requires Codex as the executor")
    pair = evaluate_model_pair(
        task.executor.model,
        task.verifier.model,
        executor_scaffold=task.executor.harness,
        verifier_scaffold=task.verifier.harness,
    )
    worktree = create_worktree(task.repo, task.id, round_no=1)
    try:
        executor = build_executor_command(task, worktree)
        verifier = build_verifier_command(task, worktree)
    except Exception:
        cleanup_worktree(task.repo, worktree.path, worktree.branch)
        raise
    return DryRunResult(
        task=task,
        pair=pair,
        worktree=worktree,
        executor_command=executor,
        verifier_command=verifier,
    )
