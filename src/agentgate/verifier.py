from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentgate.adapters.codex import CodexAdapter, resolve_executable
from agentgate.collector import CheckOutput, check_slug, render_verifier_input
from agentgate.spec import (
    PROJECT_ROOT,
    TaskSpec,
    load_task,
    validate_verdict_document,
)
from agentgate.status import clear_markers, set_status, write_marker
from agentgate.validation import require_different_models


class VerificationError(RuntimeError):
    """Raised for recoverable verification input or backend errors."""


class VerdictParseError(ValueError):
    """Raised when verifier output cannot produce a valid verdict."""


@dataclass(frozen=True)
class AttemptResult:
    number: int
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    raw_stdout: str
    stderr: str
    duration_s: float


@dataclass(frozen=True)
class VerificationOutcome:
    task_id: str
    round_dir: Path
    verdict: dict[str, Any] | None
    attempts: tuple[AttemptResult, ...]
    blocked_reason: str = ""
    before_status: str = ""
    after_status: str = ""


CommandRunner = Callable[
    [tuple[str, ...], Path, float, str],
    subprocess.CompletedProcess[str],
]


def _run_command(
    command: tuple[str, ...],
    cwd: Path,
    timeout_s: float,
    stdin_text: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        resolve_executable(command),
        cwd=cwd,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )


def _git_status(worktree: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise VerificationError(
            "git status --porcelain failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return completed.stdout


def _balanced_object(text: str, start: int) -> tuple[dict[str, Any], int]:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    document = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise VerdictParseError(f"invalid JSON object: {exc}") from exc
                if not isinstance(document, dict):
                    raise VerdictParseError("top-level JSON value must be an object")
                return document, index

    raise VerdictParseError("unbalanced JSON object")


def extract_first_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start < 0:
        raise VerdictParseError("no JSON object found")
    document, _ = _balanced_object(text, start)
    return document


def extract_verdict_json(text: str) -> dict[str, Any]:
    """Return the first balanced object whose top-level key is verdict."""

    index = 0
    errors: list[str] = []
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            candidate, end = _balanced_object(text, start)
        except VerdictParseError as exc:
            errors.append(str(exc))
            index = start + 1
            continue
        if candidate.get("verdict") in {"PASS", "FAIL", "BLOCKED"}:
            return candidate
        index = end + 1
    detail = f": {'; '.join(errors)}" if errors else ""
    raise VerdictParseError(f"no verdict JSON object found{detail}")


def _split_patch(round_dir: Path) -> tuple[str, str]:
    patch_path = round_dir / "diff.patch"
    if not patch_path.is_file():
        raise VerificationError(f"Missing artifact: {patch_path}")
    content = patch_path.read_text(encoding="utf-8")
    marker = "\n--- git status --porcelain ---\n"
    if marker in content:
        patch, status = content.rsplit(marker, 1)
        return patch, status

    changed_path = round_dir / "changed_files.txt"
    if not changed_path.is_file():
        raise VerificationError(
            "diff.patch has no status section and changed_files.txt is missing"
        )
    return content, changed_path.read_text(encoding="utf-8")


def _stored_checks(task: TaskSpec, round_dir: Path) -> tuple[CheckOutput, ...]:
    artifacts = round_dir / "artifacts"
    checks: list[CheckOutput] = []
    for index, check in enumerate(task.checks, start=1):
        stem = f"{index:02d}-{check_slug(check.name)}"
        stdout_path = artifacts / f"{stem}.stdout.txt"
        stderr_path = artifacts / f"{stem}.stderr.txt"
        exit_path = artifacts / f"{stem}.exit.txt"
        missing = [
            path for path in (stdout_path, stderr_path, exit_path) if not path.is_file()
        ]
        if missing:
            names = ", ".join(str(path) for path in missing)
            raise VerificationError(f"Missing artifact files: {names}")
        try:
            exit_code = int(exit_path.read_text(encoding="utf-8").strip())
        except ValueError as exc:
            raise VerificationError(f"Invalid exit code file: {exit_path}") from exc
        checks.append(
            CheckOutput(
                spec=check,
                exit_code=exit_code,
                stdout=stdout_path.read_text(encoding="utf-8"),
                stderr=stderr_path.read_text(encoding="utf-8"),
                duration_s=0.0,
            )
        )
    return tuple(checks)


def regenerate_verifier_input(
    task: TaskSpec,
    round_dir: Path,
) -> str:
    patch, status = _split_patch(round_dir)
    checks = _stored_checks(task, round_dir)
    content = render_verifier_input(
        task,
        f"{patch}\n--- git status --porcelain ---\n{status}",
        status,
        checks,
    )
    (round_dir / "verifier_input.md").write_text(content, encoding="utf-8")
    return content


def _prompt(
    verifier_input: str,
    *,
    previous_error: str = "",
) -> str:
    retry = ""
    if previous_error:
        retry = (
            "\nYour previous response was invalid. Correct it.\n"
            f"Previous error:\n{previous_error}\n"
        )
    return (
        "You are a read-only verifier. Do not modify any file.\n"
        "Do not call tools, run commands, or ask questions. "
        "All evidence is already supplied below.\n"
        "Return only the verdict JSON object. Do not repeat or quote the schema.\n"
        "It must validate against this schema file:\n"
        f"{PROJECT_ROOT / 'schemas' / 'verdict.schema.json'}\n"
        "Top-level keys are exactly verdict, summary, checks, defects.\n"
        "verdict is PASS, FAIL, or BLOCKED. checks items have "
        "name, status (PASS/FAIL), evidence. defects items have "
        "id, severity, where, why, reproduce, expect, actual.\n"
        "PASS requires every check to be PASS and defects to be empty.\n"
        "FAIL requires at least one defect object. BLOCKED may include defects.\n"
        "Every defect must include a directly executable reproduce command.\n"
        f"{retry}\n"
        "Verification input:\n"
        f"{verifier_input}"
    )


def _save_attempt(round_dir: Path, attempt: AttemptResult) -> None:
    stem = f"verifier-attempt-{attempt.number}"
    (round_dir / f"{stem}.stdout.txt").write_text(
        attempt.stdout,
        encoding="utf-8",
    )
    (round_dir / f"{stem}.raw-stdout.txt").write_text(
        attempt.raw_stdout,
        encoding="utf-8",
    )
    (round_dir / f"{stem}.stderr.txt").write_text(
        attempt.stderr,
        encoding="utf-8",
    )
    (round_dir / f"{stem}.exit.txt").write_text(
        f"{attempt.exit_code}\n",
        encoding="utf-8",
    )


def _run_attempt(
    *,
    number: int,
    task: TaskSpec,
    worktree: Path,
    prompt: str,
    round_dir: Path,
    adapter: CodexAdapter,
    command_runner: CommandRunner,
) -> AttemptResult:
    command_list = adapter.build_cmd(
        prompt,
        task.verifier.model,
        cwd=worktree,
        readonly=True,
        schema=PROJECT_ROOT / "schemas" / "verdict.schema.json",
        sandbox=task.verifier.sandbox,
    )
    last_message = round_dir / f"verifier-attempt-{number}.last.txt"
    # append, not insert-before-last: the prompt no longer sits at the end of
    # argv (it goes over stdin), so inserting at -1 would land between
    # "-m" and its model value.
    command_list.extend(["-o", str(last_message)])
    command = tuple(command_list)
    started = time.monotonic()
    try:
        completed = command_runner(
            command,
            worktree,
            task.budget.max_minutes_per_round * 60,
            prompt,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr)
        return AttemptResult(
            number=number,
            command=command,
            exit_code=124,
            stdout=stdout,
            raw_stdout=stdout,
            stderr=stderr or "verifier timed out",
            duration_s=time.monotonic() - started,
        )
    raw_stdout = completed.stdout
    final_stdout = (
        last_message.read_text(encoding="utf-8")
        if last_message.is_file()
        else raw_stdout
    )
    return AttemptResult(
        number=number,
        command=command,
        exit_code=completed.returncode,
        stdout=final_stdout,
        raw_stdout=raw_stdout,
        stderr=completed.stderr,
        duration_s=time.monotonic() - started,
    )


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _mark_blocked(round_dir: Path, reason: str) -> None:
    run_dir = round_dir.parent
    write_marker(run_dir, "BLOCKED", reason)
    set_status(run_dir, "blocked")


def _clear_previous_outcome(run_dir: Path, round_dir: Path) -> None:
    clear_markers(run_dir)
    verdict = round_dir / "verdict.json"
    if verdict.exists():
        verdict.unlink()
    for stale in round_dir.glob("verifier-attempt-*"):
        stale.unlink()


def verify_round(
    task_id: str,
    *,
    round_no: int = 1,
    runs_root: Path | None = None,
    adapter: CodexAdapter | None = None,
    command_runner: CommandRunner = _run_command,
    force: bool = False,
    manage_fail_status: bool = True,
) -> VerificationOutcome:
    if not task_id or Path(task_id).name != task_id:
        raise VerificationError(f"Invalid task id: {task_id!r}")
    root_runs = runs_root or (PROJECT_ROOT / "runs")
    run_dir = root_runs / task_id
    round_dir = run_dir / f"round-{round_no}"
    task_path = run_dir / "task.yaml"
    state_path = round_dir / "state.json"
    if not task_path.is_file():
        raise VerificationError(f"Missing task snapshot: {task_path}")
    if not state_path.is_file():
        raise VerificationError(f"Missing state artifact: {state_path}")
    existing_verdict_path = round_dir / "verdict.json"
    existing_verdict: dict[str, Any] | None = None
    if existing_verdict_path.is_file():
        loaded = json.loads(existing_verdict_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing_verdict = loaded
    if not force and (
        (run_dir / "DONE").is_file()
        or (existing_verdict is not None and existing_verdict.get("verdict") == "PASS")
    ):
        raise VerificationError(
            "Run already passed verification; use `ag verify --force` to rerun."
        )

    task = load_task(task_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("task_id") != task_id:
        raise VerificationError("state.json task_id does not match run directory")
    if state.get("status") != "executed":
        raise VerificationError("state.json does not describe an executed round")
    try:
        repo = Path(state["repo"]).resolve(strict=True)
        worktree = Path(state["worktree"]).resolve(strict=True)
    except (KeyError, OSError) as exc:
        raise VerificationError(f"Invalid state workspace paths: {exc}") from exc
    expected_parent = (repo / "wt").resolve()
    if worktree.parent != expected_parent:
        raise VerificationError(f"State worktree is outside repo/wt: {worktree}")
    if not worktree.is_dir():
        raise VerificationError(f"Worktree is missing: {worktree}")
    if task.repo.resolve() != repo:
        raise VerificationError("task.yaml repo does not match state.json repo")
    if task.verifier.harness != "codex":
        raise VerificationError("v0.2 step 4 supports Codex verifier only")
    require_different_models(
        task.executor.model,
        task.verifier.model,
        executor_scaffold=task.executor.harness,
        verifier_scaffold=task.verifier.harness,
    )

    verifier_input = regenerate_verifier_input(task, round_dir)
    _clear_previous_outcome(run_dir, round_dir)
    before_status = _git_status(worktree)
    selected = adapter or CodexAdapter()
    attempts: list[AttemptResult] = []
    parse_error = ""
    verdict: dict[str, Any] | None = None

    for attempt_number in (1, 2):
        attempt = _run_attempt(
            number=attempt_number,
            task=task,
            worktree=worktree,
            prompt=_prompt(verifier_input, previous_error=parse_error),
            round_dir=round_dir,
            adapter=selected,
            command_runner=command_runner,
        )
        attempts.append(attempt)
        _save_attempt(round_dir, attempt)
        after_status = _git_status(worktree)
        if after_status != before_status:
            reason = "verifier modified the worktree"
            _mark_blocked(round_dir, reason)
            return VerificationOutcome(
                task_id=task_id,
                round_dir=round_dir,
                verdict=None,
                attempts=tuple(attempts),
                blocked_reason=reason,
                before_status=before_status,
                after_status=after_status,
            )
        if attempt.exit_code != 0:
            reason = f"verifier exited {attempt.exit_code}: {attempt.stderr}"
            _mark_blocked(round_dir, reason)
            return VerificationOutcome(
                task_id=task_id,
                round_dir=round_dir,
                verdict=None,
                attempts=tuple(attempts),
                blocked_reason=reason,
                before_status=before_status,
                after_status=after_status,
            )
        try:
            candidate = extract_verdict_json(attempt.stdout)
            validate_verdict_document(candidate, source="verifier output")
            verdict = candidate
            break
        except (VerdictParseError, ValueError) as exc:
            parse_error = str(exc)

    if verdict is None:
        reason = f"verdict extraction failed twice: {parse_error}"
        _mark_blocked(round_dir, reason)
        return VerificationOutcome(
            task_id=task_id,
            round_dir=round_dir,
            verdict=None,
            attempts=tuple(attempts),
            blocked_reason=reason,
            before_status=before_status,
            after_status=before_status,
        )

    (round_dir / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if verdict["verdict"] == "PASS":
        write_marker(run_dir, "DONE")
        set_status(run_dir, "done")
    elif verdict["verdict"] == "BLOCKED":
        _mark_blocked(round_dir, verdict.get("summary", "verifier returned BLOCKED"))
    elif manage_fail_status:
        set_status(run_dir, "failed")
    return VerificationOutcome(
        task_id=task_id,
        round_dir=round_dir,
        verdict=verdict,
        attempts=tuple(attempts),
        before_status=before_status,
        after_status=before_status,
    )


def verify_run(
    task_id: str,
    *,
    runs_root: Path | None = None,
    adapter: CodexAdapter | None = None,
    command_runner: CommandRunner = _run_command,
    force: bool = False,
) -> VerificationOutcome:
    return verify_round(
        task_id,
        round_no=1,
        runs_root=runs_root,
        adapter=adapter,
        command_runner=command_runner,
        force=force,
    )
