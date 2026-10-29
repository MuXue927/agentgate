from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from agentgate.cli import app
from agentgate.doctor import DoctorReport


class TestCli:
    def test_help_lists_doctor(self) -> None:
        result = CliRunner().invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "doctor" in result.output
        assert "run" in result.output
        assert "clean" in result.output

    def test_doctor_route_returns_zero_when_required_probes_pass(self) -> None:
        report = DoctorReport(
            results=(),
            tier="★★",
            required_ok=True,
        )
        with patch("agentgate.cli.collect_report", return_value=report):
            result = CliRunner().invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "backend" in result.output
        assert "Double-blind tier: ★★" in result.output

    def test_run_without_dry_run_dispatches_execution(self) -> None:
        outcome = SimpleNamespace(
            state={
                "status": "done",
                "rounds": [{"round": 1}],
                "elapsed_seconds": 1.0,
                "final_branch": "ag/fixture-r1",
            },
            events=("Round 1: PASS → DONE",),
        )
        with patch("agentgate.cli.start_task", return_value=outcome) as execute:
            result = CliRunner().invoke(
                app,
                ["run", "templates/add-feature.task.yaml"],
            )

        assert result.exit_code == 0
        assert "Round 1: PASS → DONE" in result.output
        assert "DONE + 1 轮" in result.output
        execute.assert_called_once()

    def test_blocked_run_prints_terminal_reason(self) -> None:
        outcome = SimpleNamespace(
            state={
                "status": "blocked",
                "rounds": [],
                "elapsed_seconds": 1.0,
            },
            events=("BLOCKED: raw executor failure",),
        )
        with patch("agentgate.cli.start_task", return_value=outcome):
            result = CliRunner().invoke(
                app,
                ["run", "templates/add-feature.task.yaml"],
            )

        assert result.exit_code == 2
        assert "raw executor failure" in result.output

    def test_verify_fail_is_exit_zero_and_prints_defect(self) -> None:
        verdict = {
            "verdict": "FAIL",
            "summary": "bad content",
            "checks": [],
            "defects": [
                {
                    "id": "D1",
                    "severity": "blocking",
                    "where": "hello.txt:1",
                    "why": "wrong",
                    "reproduce": "type hello.txt",
                    "expect": "ok2",
                    "actual": "ok",
                }
            ],
        }
        outcome = SimpleNamespace(verdict=verdict, blocked_reason="")
        with patch("agentgate.cli.verify_run", return_value=outcome):
            result = CliRunner().invoke(app, ["verify", "fixture"])

        assert result.exit_code == 0
        assert "Verdict: FAIL" in result.output
        assert "reproduce: type hello.txt" in result.output

    def test_verify_blocked_is_exit_two(self) -> None:
        outcome = SimpleNamespace(
            verdict=None,
            blocked_reason="verdict extraction failed twice",
        )
        with patch("agentgate.cli.verify_run", return_value=outcome):
            result = CliRunner().invoke(app, ["verify", "fixture"])

        assert result.exit_code == 2
        assert "BLOCKED: verdict extraction failed twice" in result.output
