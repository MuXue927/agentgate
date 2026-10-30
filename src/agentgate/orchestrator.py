from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentgate.execution import ExecutionOutcome, execute_round
from agentgate.report import write_report
from agentgate.spec import PROJECT_ROOT, TaskSpec, load_task, require_allowed_repo
from agentgate.status import (
    clear_markers,
    read_state,
    write_marker,
    write_state,
)
from agentgate.validation import evaluate_model_pair
from agentgate.verifier import VerificationOutcome, verify_round
from agentgate.workspace import clean_agent_worktrees

TERMINAL_STATUSES = {"done", "failed", "blocked"}


class OrchestratorError(RuntimeError):
    """Raised when a run cannot start or resume."""


@dataclass(frozen=True)
class Usage:
    total_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class RunOutcome:
    task: TaskSpec
    run_dir: Path
    state: dict[str, Any]
    events: tuple[str, ...]


ExecutorCallable = Callable[..., ExecutionOutcome]
VerifierCallable = Callable[..., VerificationOutcome]
UsageProvider = Callable[[], Usage]
EventSink = Callable[[str], None]


def _now_iso() -> str:
    from agentgate.collector import utc_now

    return utc_now()


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _normalize_why(why: object) -> str:
    text = _normalize_text(why)
    expected = re.search(
        r"(?:expects?|expected)\s+(?:exit\s+code\s+)?([-+]?\d+(?:\.\d+)?)",
        text,
    )
    actual = re.search(
        r"(?:actual\s+(?:exit\s+code\s+)?(?:was\s+)?|"
        r"(?:exits|exited|returns|returned)\s+(?:with\s+)?(?:code\s+)?)"
        r"([-+]?\d+(?:\.\d+)?)",
        text,
    )
    if expected and actual and ("code" in text or "exit" in text):
        return f"exit-code expected={expected.group(1)} actual={actual.group(1)}"
    return text


def _normalize_where(where: object) -> str:
    text = _normalize_text(where)
    if "verification" in text:
        return "verification-check"
    return text


def _numeric_value(value: object) -> str:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    return match.group(0) if match else _normalize_text(value)


def defect_fingerprint(defect: dict[str, Any]) -> str:
    why = _normalize_why(defect.get("why", ""))
    if not why.startswith("exit-code expected=") and ("exit" in why and "code" in why):
        why = (
            f"exit-code expected={_numeric_value(defect.get('expect'))} "
            f"actual={_numeric_value(defect.get('actual'))}"
        )
    normalized = "\0".join((_normalize_where(defect.get("where", "")), why))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _defect_map(verdict: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not verdict:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for defect in verdict.get("defects", []):
        result[defect_fingerprint(defect)] = defect
    return result


def _usage_from_ocx() -> Usage:
    executable = shutil.which("ocx")
    if executable is None:
        raise OrchestratorError("ocx executable not found")
    completed = subprocess.run(
        [executable, "usage", "--range", "today", "--surface", "codex", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise OrchestratorError(
            "ocx usage failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    summary = json.loads(completed.stdout)["summary"]
    return Usage(
        total_tokens=int(summary["totalTokens"]),
        cost_usd=float(summary["estimatedCostUsd"]),
    )


def _safe_usage(provider: UsageProvider) -> dict[str, float | int] | None:
    try:
        usage = provider()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, OrchestratorError):
        return None
    return {"total_tokens": usage.total_tokens, "cost_usd": usage.cost_usd}


def _usage_after_run(
    provider: UsageProvider,
    before: object,
) -> dict[str, float | int] | None:
    current = _safe_usage(provider)
    if provider is not _usage_from_ocx or not isinstance(before, dict):
        return current
    for _ in range(6):
        if current is not None and current != before:
            return current
        time.sleep(2)
        current = _safe_usage(provider)
    return current


def _checkpoint_round(worktree: Path, *, round_no: int) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if status.returncode != 0:
        raise OrchestratorError(
            "git status failed: " + (status.stderr.strip() or status.stdout.strip())
        )
    if status.stdout.strip():
        added = subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if added.returncode != 0:
            raise OrchestratorError(
                "git add -A failed: " + (added.stderr.strip() or added.stdout.strip())
            )
        committed = subprocess.run(
            [
                "git",
                "-c",
                "user.name=AgentGate",
                "-c",
                "user.email=agentgate@local.invalid",
                "commit",
                "-m",
                f"ag: round {round_no}",
            ],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if committed.returncode != 0:
            raise OrchestratorError(
                "git commit failed: "
                + (committed.stderr.strip() or committed.stdout.strip())
            )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if head.returncode != 0:
        raise OrchestratorError(
            "git rev-parse failed: " + (head.stderr.strip() or head.stdout.strip())
        )
    return head.stdout.strip()


def _run_dir(runs_root: Path, task_id: str) -> Path:
    return runs_root / task_id


def _task_for_state(run_dir: Path) -> TaskSpec:
    return load_task(run_dir / "task.yaml")


def _new_state(
    task: TaskSpec,
    *,
    effective_max_rounds: int,
    usage_before: dict[str, float | int] | None,
) -> dict[str, Any]:
    pair = evaluate_model_pair(
        task.executor.model,
        task.verifier.model,
        executor_scaffold=task.executor.harness,
        verifier_scaffold=task.verifier.harness,
    )
    return {
        "version": 1,
        "task_id": task.id,
        "repo": str(task.repo),
        "task_snapshot": "task.yaml",
        "status": "running",
        "effective_max_rounds": effective_max_rounds,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "elapsed_seconds": 0.0,
        "tier": pair.tier,
        "executor_family": pair.executor_family,
        "verifier_family": pair.verifier_family,
        "usage_before": usage_before,
        "usage_after": None,
        "cost_usd": None,
        "cost_tokens": None,
        "rounds": [],
        "final_branch": None,
        "terminal_reason": None,
    }


def _save(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    write_state(run_dir, state)


def _finish(
    task: TaskSpec,
    run_dir: Path,
    state: dict[str, Any],
    status: str,
    *,
    reason: str,
    usage_provider: UsageProvider,
) -> dict[str, Any]:
    state["status"] = status
    state["terminal_reason"] = reason
    usage_before = state.get("usage_before")
    usage_after = _usage_after_run(usage_provider, usage_before)
    state["usage_after"] = usage_after
    if isinstance(usage_before, dict) and usage_after is not None:
        state["cost_tokens"] = int(usage_after["total_tokens"]) - int(
            usage_before["total_tokens"]
        )
        state["cost_usd"] = float(usage_after["cost_usd"]) - float(
            usage_before["cost_usd"]
        )
    _save(run_dir, state)
    marker = {"done": "DONE", "failed": "FAILED", "blocked": "BLOCKED"}[status]
    write_marker(run_dir, marker, reason)
    write_report(task, state, run_dir=run_dir, repo=task.repo)
    return state


def _stage_elapsed(clock: Callable[[], float], started: float) -> float:
    return max(0.0, clock() - started)


def _drive(
    task: TaskSpec,
    *,
    run_dir: Path,
    state: dict[str, Any],
    executor: ExecutorCallable,
    verifier: VerifierCallable,
    usage_provider: UsageProvider,
    clock: Callable[[], float],
    on_event: EventSink | None,
    allow_self: bool = False,
) -> RunOutcome:
    events: list[str] = []

    def push(message: str) -> None:
        events.append(message)
        if on_event is not None:
            on_event(message)

    clear_markers(run_dir)
    state["status"] = "running"
    _save(run_dir, state)

    while True:
        if float(state["elapsed_seconds"]) > task.budget.max_total_minutes * 60:
            reason = (
                f"max_total_minutes exceeded: {state['elapsed_seconds']:.1f}s "
                f"> {task.budget.max_total_minutes * 60:.0f}s"
            )
            state = _finish(
                task,
                run_dir,
                state,
                "blocked",
                reason=reason,
                usage_provider=usage_provider,
            )
            push(f"BLOCKED: {reason}")
            break

        rounds: list[dict[str, Any]] = state["rounds"]
        round_no = len(rounds) + 1
        resumed_executed = bool(rounds and rounds[-1].get("status") == "executed")

        if not resumed_executed:
            if rounds and rounds[-1].get("status") != "verified_fail":
                raise OrchestratorError("Run state has no resumable round")
            if rounds and round_no > int(state["effective_max_rounds"]):
                reason = f"max_rounds exhausted after {len(rounds)} round(s)"
                state = _finish(
                    task,
                    run_dir,
                    state,
                    "failed",
                    reason=reason,
                    usage_provider=usage_provider,
                )
                push(f"FAILED: {reason}")
                break

            defects: list[dict[str, Any]] | None = None
            base_ref: str | None = None
            previous_round_dir: Path | None = None
            if rounds:
                previous = rounds[-1]
                previous_verdict = previous.get("verdict")
                defects = (
                    list(previous_verdict.get("defects", []))
                    if isinstance(previous_verdict, dict)
                    else None
                )
                if not defects:
                    reason = "previous round failed without defect details"
                    state = _finish(
                        task,
                        run_dir,
                        state,
                        "blocked",
                        reason=reason,
                        usage_provider=usage_provider,
                    )
                    push(f"BLOCKED: {reason}")
                    break
                base_ref = previous["branch"]
                previous_round_dir = run_dir / f"round-{previous['round']}"

            stage_started = clock()
            try:
                execution = executor(
                    run_dir / "task.yaml",
                    allow_self=allow_self,
                    runs_root=run_dir.parent,
                    round_no=round_no,
                    base_ref=base_ref,
                    defects=defects,
                    previous_round_dir=previous_round_dir,
                )
            except Exception as exc:  # noqa: BLE001
                state["elapsed_seconds"] += _stage_elapsed(clock, stage_started)
                reason = f"executor failed: {type(exc).__name__}: {exc}"
                state = _finish(
                    task,
                    run_dir,
                    state,
                    "blocked",
                    reason=reason,
                    usage_provider=usage_provider,
                )
                push(f"BLOCKED: {reason}")
                break

            state["elapsed_seconds"] += _stage_elapsed(clock, stage_started)
            if float(state["elapsed_seconds"]) > task.budget.max_total_minutes * 60:
                reason = (
                    f"max_total_minutes exceeded: {state['elapsed_seconds']:.1f}s "
                    f"> {task.budget.max_total_minutes * 60:.0f}s"
                )
                state = _finish(
                    task,
                    run_dir,
                    state,
                    "blocked",
                    reason=reason,
                    usage_provider=usage_provider,
                )
                push(f"BLOCKED: {reason}")
                break
            round_dir = execution.collection.round_dir
            (round_dir / "executor_prompt.txt").write_text(
                execution.prompt,
                encoding="utf-8",
            )
            if execution.blocked:
                reason = (
                    (run_dir / "BLOCKED").read_text(encoding="utf-8")
                    if (run_dir / "BLOCKED").is_file()
                    else "executor returned BLOCKED"
                )
                state = _finish(
                    task,
                    run_dir,
                    state,
                    "blocked",
                    reason=reason,
                    usage_provider=usage_provider,
                )
                push(f"BLOCKED: {reason}")
                break

            try:
                head = _checkpoint_round(
                    Path(execution.collection.state["worktree"]),
                    round_no=round_no,
                )
            except OrchestratorError as exc:
                state = _finish(
                    task,
                    run_dir,
                    state,
                    "blocked",
                    reason=str(exc),
                    usage_provider=usage_provider,
                )
                push(f"BLOCKED: {exc}")
                break

            rounds.append(
                {
                    "round": round_no,
                    "status": "executed",
                    "worktree": execution.collection.state["worktree"],
                    "branch": execution.collection.state["branch"],
                    "base_commit": execution.collection.state.get("base_commit"),
                    "head_commit": head,
                    "verdict": None,
                    "defect_fingerprints": [],
                }
            )
            if rounds[-1]["base_commit"] is None:
                head_parent = subprocess.run(
                    ["git", "rev-parse", f"{head}^"],
                    cwd=task.repo,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                rounds[-1]["base_commit"] = head_parent.stdout.strip()
            state["status"] = "executed"
            _save(run_dir, state)
            write_marker(run_dir, "EXECUTED")

        current_round = state["rounds"][-1]
        round_no = int(current_round["round"])
        stage_started = clock()
        verification = verifier(
            task.id,
            round_no=round_no,
            runs_root=run_dir.parent,
            force=False,
            manage_fail_status=False,
        )
        state["elapsed_seconds"] += _stage_elapsed(clock, stage_started)
        if float(state["elapsed_seconds"]) > task.budget.max_total_minutes * 60:
            reason = (
                f"max_total_minutes exceeded: {state['elapsed_seconds']:.1f}s "
                f"> {task.budget.max_total_minutes * 60:.0f}s"
            )
            state = _finish(
                task,
                run_dir,
                state,
                "blocked",
                reason=reason,
                usage_provider=usage_provider,
            )
            push(f"BLOCKED: {reason}")
            break

        if verification.verdict is None:
            reason = verification.blocked_reason or "verification returned BLOCKED"
            state = _finish(
                task,
                run_dir,
                state,
                "blocked",
                reason=reason,
                usage_provider=usage_provider,
            )
            push(f"BLOCKED: {reason}")
            break

        verdict = verification.verdict
        current_round["verdict"] = verdict
        current_round["defect_fingerprints"] = sorted(_defect_map(verdict))
        if verdict["verdict"] == "PASS":
            current_round["status"] = "verified_pass"
            state["final_branch"] = current_round["branch"]
            state = _finish(
                task,
                run_dir,
                state,
                "done",
                reason="verification passed",
                usage_provider=usage_provider,
            )
            push(f"Round {round_no}: PASS → DONE")
            break

        current_round["status"] = "verified_fail"
        defect_count = len(verdict.get("defects", []))
        prior = rounds[-2] if len(rounds) >= 2 else None
        repeated = (
            set(current_round["defect_fingerprints"])
            & set(prior.get("defect_fingerprints", []))
            if prior is not None
            else set()
        )
        if repeated:
            fingerprint = min(repeated)
            current_map = _defect_map(verdict)
            prior_map = _defect_map(prior.get("verdict"))
            defect = current_map[fingerprint]
            previous_defect = prior_map.get(fingerprint, {})
            reason = (
                "same defect persisted across consecutive rounds: "
                f"fingerprint={fingerprint}; id={defect.get('id')}; "
                f"where={defect.get('where')}; why={defect.get('why')}; "
                f"R{prior['round']} actual={previous_defect.get('actual')}; "
                f"R{round_no} actual={defect.get('actual')}"
            )
            state["status"] = "failed"
            state = _finish(
                task,
                run_dir,
                state,
                "failed",
                reason=reason,
                usage_provider=usage_provider,
            )
            push(f"Round {round_no}: FAIL ({defect_count} defect) → FAILED")
            break

        if round_no >= int(state["effective_max_rounds"]):
            reason = f"max_rounds exhausted after {round_no} round(s)"
            state["status"] = "failed"
            state = _finish(
                task,
                run_dir,
                state,
                "failed",
                reason=reason,
                usage_provider=usage_provider,
            )
            push(f"Round {round_no}: FAIL ({defect_count} defect) → FAILED")
            break

        state["status"] = "running"
        _save(run_dir, state)
        push(f"Round {round_no}: FAIL ({defect_count} defect) → continue")

    return RunOutcome(
        task=task,
        run_dir=run_dir,
        state=state,
        events=tuple(events),
    )


def start_task(
    task_path: Path,
    *,
    max_rounds: int | None = None,
    allow_self: bool = False,
    runs_root: Path | None = None,
    executor: ExecutorCallable = execute_round,
    verifier: VerifierCallable = verify_round,
    usage_provider: UsageProvider = _usage_from_ocx,
    clock: Callable[[], float] = time.monotonic,
    on_event: EventSink | None = None,
) -> RunOutcome:
    task = load_task(task_path)
    require_allowed_repo(task, allow_self=allow_self)
    effective = max_rounds if max_rounds is not None else task.budget.max_rounds
    if effective < 1:
        raise OrchestratorError("--rounds must be at least 1")

    root = runs_root or (PROJECT_ROOT / "runs")
    run_dir = _run_dir(root, task.id)
    if run_dir.exists():
        raise OrchestratorError(
            f"Run already exists: {run_dir}; use `ag resume {task.id}`"
        )
    run_dir.mkdir(parents=True)
    shutil.copyfile(task.path, run_dir / "task.yaml")
    clear_markers(run_dir)
    usage_before = _safe_usage(usage_provider)
    state = _new_state(
        task,
        effective_max_rounds=effective,
        usage_before=usage_before,
    )
    write_state(run_dir, state)
    return _drive(
        task,
        run_dir=run_dir,
        state=state,
        executor=executor,
        verifier=verifier,
        usage_provider=usage_provider,
        allow_self=allow_self,
        clock=clock,
        on_event=on_event,
    )


def resume_task(
    task_id: str,
    *,
    force: bool = False,
    allow_self: bool = False,
    runs_root: Path | None = None,
    executor: ExecutorCallable = execute_round,
    verifier: VerifierCallable = verify_round,
    usage_provider: UsageProvider = _usage_from_ocx,
    clock: Callable[[], float] = time.monotonic,
    on_event: EventSink | None = None,
) -> RunOutcome:
    root = runs_root or (PROJECT_ROOT / "runs")
    run_dir = _run_dir(root, task_id)
    state = read_state(run_dir)
    if state is None:
        raise OrchestratorError(f"Run state not found: {run_dir / 'state.json'}")
    task = _task_for_state(run_dir)
    if state.get("status") in TERMINAL_STATUSES and not force:
        raise OrchestratorError(
            f"Run is already terminal ({state.get('status')}); use --force to rerun."
        )
    if state.get("status") in TERMINAL_STATUSES and force:
        repo = Path(state["repo"])
        original_task_path = task.path
        clean_agent_worktrees(repo, task_id=task_id, force=True)
        shutil.rmtree(run_dir)
        return start_task(
            original_task_path,
            max_rounds=int(state["effective_max_rounds"]),
            allow_self=allow_self,
            runs_root=root,
            executor=executor,
            verifier=verifier,
            usage_provider=usage_provider,
            clock=clock,
            on_event=on_event,
        )
    return _drive(
        task,
        run_dir=run_dir,
        state=state,
        executor=executor,
        verifier=verifier,
        usage_provider=usage_provider,
        clock=clock,
        on_event=on_event,
        allow_self=allow_self,
    )
