from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgate.spec import SpecError, load_task, load_verdict

ROOT = Path(__file__).resolve().parents[1]


def test_all_templates_validate() -> None:
    templates = sorted((ROOT / "templates").glob("*.task.yaml"))

    # 断言"四个必备模板都在"，而不是"正好四个"：新增模板（如 minimal）
    # 不该让这条测试变红。
    names = {path.name for path in templates}
    required = {
        "add-feature.task.yaml",
        "fix-bug.task.yaml",
        "write-tests.task.yaml",
        "update-docs.task.yaml",
    }
    assert required <= names
    assert [load_task(path).id for path in templates]
    assert all("<your-repo>" in path.read_text(encoding="utf-8") for path in templates)


def test_missing_budget_field_reports_exact_path() -> None:
    fixture = ROOT / "tests" / "fixtures" / "invalid-missing-budget.task.yaml"

    with pytest.raises(SpecError, match=r"budget\.max_rounds") as error:
        load_task(fixture)

    assert "required field is missing" in str(error.value)


def test_verdict_pass_with_defects_is_rejected() -> None:
    fixture = ROOT / "tests" / "fixtures" / "invalid-verdict-pass-with-defect.json"

    with pytest.raises(SpecError, match=r"verdict\.defects") as error:
        load_verdict(fixture)

    assert "PASS requires defects to be empty" in str(error.value)


def test_fail_without_defects_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "fail-empty.json"
    fixture.write_text(
        json.dumps(
            {
                "verdict": "FAIL",
                "summary": "failed",
                "checks": [],
                "defects": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SpecError, match=r"defects.*non-empty"):
        load_verdict(fixture)


def test_task_yaml_parses_explicit_sandbox() -> None:
    """Windows Codex sandbox is broken; tasks pin danger-full-access explicitly."""

    root = Path(__file__).resolve().parents[1]
    task = load_task(root / "tasks" / "agentgate-docs.task.yaml")

    assert task.executor.sandbox == "danger-full-access"
    assert task.verifier.sandbox == "danger-full-access"


def test_sandbox_defaults_to_none(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "templates" / "fix-bug.task.yaml").read_text(encoding="utf-8")
    task_path = tmp_path / "no-sandbox.task.yaml"
    task_path.write_text(source, encoding="utf-8")

    task = load_task(task_path)

    assert task.executor.sandbox is None
    assert task.verifier.sandbox is None


# --- P1-1: holdout_checks is an optional second list, 1-3 items -----------


def _holdout_task(tmp_path: Path, *, checks: str, holdout: str) -> Path:
    task_path = tmp_path / "holdout.task.yaml"
    task_path.write_text(
        f"""\
id: holdout-schema
repo: {tmp_path.as_posix()}
goal: Exercise the holdout_checks contract.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints: []
{checks}{holdout}budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task_path


def test_holdout_without_public_checks_is_rejected(tmp_path: Path) -> None:
    """Held-out alone has no loop to run, so `checks` stays required."""

    task_path = _holdout_task(
        tmp_path,
        checks="",
        holdout=(
            "holdout_checks:\n"
            "  - name: hidden-1\n"
            "    run: printf ok\n"
            "    expect: {exit: 0}\n"
        ),
    )

    with pytest.raises(SpecError, match=r"checks: required field is missing"):
        load_task(task_path)


def test_holdout_is_limited_to_three(tmp_path: Path) -> None:
    """A holdout list is a detector, not a second exam."""

    items = "".join(
        f"  - name: hidden-{index}\n    run: printf ok\n    expect: {{exit: 0}}\n"
        for index in range(1, 5)
    )
    task_path = _holdout_task(
        tmp_path,
        checks="checks:\n  - name: public\n    run: printf ok\n    expect: {exit: 0}\n",
        holdout=f"holdout_checks:\n{items}",
    )

    with pytest.raises(SpecError, match=r"holdout_checks") as error:
        load_task(task_path)

    assert "too long" in str(error.value)


def test_holdout_checks_parse_alongside_checks(tmp_path: Path) -> None:
    task_path = _holdout_task(
        tmp_path,
        checks="checks:\n  - name: public\n    run: printf ok\n    expect: {exit: 0}\n",
        holdout=(
            "holdout_checks:\n"
            "  - name: hidden-1\n"
            "    run: printf hidden\n"
            "    expect: {exit: 0, stdout_contains: hidden}\n"
        ),
    )

    task = load_task(task_path)

    assert [check.name for check in task.checks] == ["public"]
    assert [check.name for check in task.holdout_checks] == ["hidden-1"]
    assert task.holdout_checks[0].expect == {"exit": 0, "stdout_contains": "hidden"}
