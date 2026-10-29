from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentgate.spec import SpecError, load_task, load_verdict

ROOT = Path(__file__).resolve().parents[1]


def test_all_templates_validate() -> None:
    templates = sorted((ROOT / "templates").glob("*.task.yaml"))

    assert len(templates) == 4
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
