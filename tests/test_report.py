from __future__ import annotations

from pathlib import Path

from agentgate.report import render_report
from agentgate.spec import load_task

ROOT = Path(__file__).resolve().parents[1]
TASK = load_task(ROOT / "templates" / "add-feature.task.yaml")


def defect(
    *,
    where: str = "number.txt",
    expect: str = "3",
    actual: str = "2",
) -> dict[str, str]:
    return {
        "id": "D1",
        "severity": "blocking",
        "where": where,
        "why": "mismatch",
        "reproduce": "type number.txt",
        "expect": expect,
        "actual": actual,
    }


def round_state(
    number: int,
    *,
    kind: str,
    defects: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "round": number,
        "status": "verified_pass" if kind == "PASS" else "verified_fail",
        "branch": f"ag/report-fixture-r{number}",
        "base_commit": "base",
        "verdict": {
            "verdict": kind,
            "summary": "MACHINE SUMMARY MUST NOT APPEAR",
            "checks": [],
            "defects": defects or [],
        },
    }


def summary(path: str, added: int, deleted: int = 0) -> dict:
    return {
        "source": "state",
        "files": [
            {
                "path": path,
                "status": "A",
                "added": added,
                "deleted": deleted,
            }
        ],
    }


def conclusion(report: str) -> list[str]:
    section = report.split("## 结论\n", 1)[1].split("\n\n## 每轮", 1)[0]
    return section.strip().splitlines()


def test_done_conclusion_snapshot_uses_round_facts_and_diff() -> None:
    state = {
        "status": "done",
        "terminal_reason": "verification passed",
        "elapsed_seconds": 120,
        "cost_usd": 0.006,
        "tier": "★★",
        "rounds": [
            round_state(1, kind="FAIL", defects=[defect()]),
            round_state(2, kind="PASS"),
        ],
        "change_summary": summary("number.txt", 1),
    }

    report = render_report(TASK, state, repo=ROOT)

    assert conclusion(report) == [
        "R1 FAIL（number.txt：实际 2，期望 3）；R2 PASS。",
        "最终分支仅新增 number.txt（+1）。",
        "建议合并。",
    ]
    assert "MACHINE SUMMARY" not in report
    assert "Verification passed" not in report


def test_failed_conclusion_snapshot_is_actionable() -> None:
    state = {
        "status": "failed",
        "terminal_reason": "same defect persisted across consecutive rounds",
        "elapsed_seconds": 180,
        "cost_usd": 0.01,
        "tier": "★★",
        "rounds": [
            round_state(
                1,
                kind="FAIL",
                defects=[
                    defect(
                        where="retry.txt verification",
                        expect="0",
                        actual="1",
                    )
                ],
            ),
            round_state(
                2,
                kind="FAIL",
                defects=[
                    defect(
                        where="retry.txt verification",
                        expect="0",
                        actual="1",
                    )
                ],
            ),
        ],
        "change_summary": summary("retry.txt", 2),
    }

    report = render_report(TASK, state, repo=ROOT)

    assert conclusion(report) == [
        (
            "R1 FAIL（retry.txt verification：实际 1，期望 0）；"
            "R2 FAIL（retry.txt verification：实际 1，期望 0）。"
        ),
        "最终分支仅新增 retry.txt（+2）。",
        ("不建议合并；需先处理：same defect persisted across consecutive rounds。"),
    ]
    assert "Verification did not converge" not in report


def test_blocked_conclusion_snapshot_is_actionable() -> None:
    state = {
        "status": "blocked",
        "terminal_reason": "verdict extraction failed twice",
        "elapsed_seconds": 60,
        "cost_usd": 0.003,
        "tier": "★★",
        "rounds": [
            {
                "round": 1,
                "status": "executed",
                "branch": "ag/report-fixture-r1",
                "base_commit": "base",
                "verdict": None,
            }
        ],
        "change_summary": summary("limit.txt", 1),
    }

    report = render_report(TASK, state, repo=ROOT)

    assert conclusion(report) == [
        "R1 已执行但尚未验收。",
        "最终分支仅新增 limit.txt（+1）。",
        "暂不合并；阻塞原因：verdict extraction failed twice。",
    ]
    assert "The run stopped before convergence" not in report
