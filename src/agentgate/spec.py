from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "task.schema.json"
VERDICT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "verdict.schema.json"


class SpecError(ValueError):
    """Raised when a task or verdict document violates its contract."""


def require_allowed_repo(task: TaskSpec, *, allow_self: bool) -> None:
    if task.repo.resolve() == PROJECT_ROOT.resolve() and not allow_self:
        raise SpecError(
            "task.yaml repo points to AgentGate itself. "
            "Choose the target repository or pass --allow-self explicitly."
        )


@dataclass(frozen=True)
class ModelSpec:
    harness: str
    model: str
    # Codex sandbox mode. None keeps the historical default
    # (executor: config default workspace-write, verifier: read-only).
    # Set explicitly to "danger-full-access" to work around the Codex Windows
    # sandbox defect documented in README "常见坑".
    sandbox: str | None = None


@dataclass(frozen=True)
class CheckSpec:
    name: str
    run: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class BudgetSpec:
    max_rounds: int
    max_minutes_per_round: float
    max_total_minutes: float


@dataclass(frozen=True)
class TaskSpec:
    path: Path
    id: str
    repo: Path
    goal: str
    executor: ModelSpec
    verifier: ModelSpec
    constraints: tuple[str, ...]
    checks: tuple[CheckSpec, ...]
    budget: BudgetSpec
    before_execute: bool
    before_merge: bool


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SpecError(f"Schema file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SpecError(f"Schema root must be an object: {path}")
    return value


def _format_path(parts: list[Any] | tuple[Any, ...]) -> str:
    if not parts:
        return "$"
    result = ""
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else str(part)
    return result


def validate_document(
    document: Any,
    schema_path: Path,
    *,
    source: str,
) -> None:
    schema = _load_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return

    messages: list[str] = []
    for error in errors:
        path = list(error.absolute_path)
        if error.validator == "required":
            match = re.search(r"'([^']+)' is a required property", error.message)
            if match:
                path.append(match.group(1))
                field = _format_path(path)
                messages.append(f"{field}: required field is missing")
                continue
        field = _format_path(path)
        messages.append(f"{field}: {error.message}")
    raise SpecError(
        f"Invalid {source}:\n" + "\n".join(f"- {message}" for message in messages)
    )


def load_task(path: Path) -> TaskSpec:
    path = path.expanduser().resolve()
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SpecError(f"Task file not found: {path}") from exc
    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SpecError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SpecError("Invalid task.yaml: root must be an object")

    validate_document(document, TASK_SCHEMA_PATH, source=f"task.yaml: {path}")

    check_names: set[str] = set()
    checks: list[CheckSpec] = []
    for index, item in enumerate(document["checks"]):
        name = item["name"]
        if name in check_names:
            raise SpecError(
                f"Invalid task.yaml:\n- checks[{index}].name: duplicate {name!r}"
            )
        check_names.add(name)
        regex = item["expect"].get("stdout_regex")
        if regex is not None:
            try:
                re.compile(regex)
            except re.error as exc:
                raise SpecError(
                    f"Invalid task.yaml:\n- checks[{index}].expect.stdout_regex: {exc}"
                ) from exc
        checks.append(
            CheckSpec(name=name, run=item["run"], expect=dict(item["expect"]))
        )

    budget = BudgetSpec(
        max_rounds=document["budget"]["max_rounds"],
        max_minutes_per_round=float(document["budget"]["max_minutes_per_round"]),
        max_total_minutes=float(document["budget"]["max_total_minutes"]),
    )
    if budget.max_minutes_per_round > budget.max_total_minutes:
        raise SpecError(
            "Invalid task.yaml:\n"
            "- budget.max_minutes_per_round: cannot exceed budget.max_total_minutes"
        )

    repo = Path(document["repo"]).expanduser()
    if not repo.is_absolute():
        repo = (path.parent / repo).resolve()
    else:
        repo = repo.resolve()

    return TaskSpec(
        path=path,
        id=document["id"],
        repo=repo,
        goal=document["goal"].strip(),
        executor=ModelSpec(
            harness=document["executor"]["harness"],
            model=document["executor"]["model"].strip(),
            sandbox=document["executor"].get("sandbox"),
        ),
        verifier=ModelSpec(
            harness=document["verifier"]["harness"],
            model=document["verifier"]["model"].strip(),
            sandbox=document["verifier"].get("sandbox"),
        ),
        constraints=tuple(document["constraints"]),
        checks=tuple(checks),
        budget=budget,
        before_execute=document["human_approval"]["before_execute"],
        before_merge=document["human_approval"]["before_merge"],
    )


def load_verdict(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SpecError(f"Verdict file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"Invalid JSON in {path}: {exc}") from exc
    validate_verdict_document(document, source=f"verdict.json: {path}")
    return document


def validate_verdict_document(document: Any, *, source: str) -> None:
    if isinstance(document, dict) and document.get("verdict") == "PASS":
        defects = document.get("defects")
        if isinstance(defects, list) and defects:
            raise SpecError(
                "Invalid verdict.json:\n"
                "- verdict.defects: PASS requires defects to be empty"
            )
    validate_document(document, VERDICT_SCHEMA_PATH, source=source)
    if isinstance(document, dict) and document.get("verdict") == "PASS":
        failed_checks = [
            check["name"]
            for check in document.get("checks", [])
            if check.get("status") != "PASS"
        ]
        if failed_checks:
            names = ", ".join(failed_checks)
            raise SpecError(
                "Invalid verdict.json:\n"
                f"- verdict.checks: PASS requires every check to pass ({names})"
            )
