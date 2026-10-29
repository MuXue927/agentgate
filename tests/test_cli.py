from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from agentgate.cli import app
from agentgate.doctor import DoctorReport
from agentgate.spec import PROJECT_ROOT


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
        round_dir = PROJECT_ROOT / "runs" / "fixture" / "round-1"
        outcome = SimpleNamespace(
            blocked=False,
            collection=SimpleNamespace(round_dir=round_dir),
        )
        with patch("agentgate.cli.execute_round", return_value=outcome) as execute:
            result = CliRunner().invoke(
                app,
                ["run", "templates/add-feature.task.yaml"],
            )

        assert result.exit_code == 0
        assert "Round 1 executed" in result.output
        execute.assert_called_once()

    def test_blocked_run_prints_raw_stderr(self) -> None:
        round_dir = PROJECT_ROOT / "runs" / "fixture" / "round-1"
        outcome = SimpleNamespace(
            blocked=True,
            collection=SimpleNamespace(
                round_dir=round_dir,
                state={"executor": {"error_class": "UNKNOWN"}},
            ),
            result=SimpleNamespace(stderr="raw executor failure"),
        )
        with patch("agentgate.cli.execute_round", return_value=outcome):
            result = CliRunner().invoke(
                app,
                ["run", "templates/add-feature.task.yaml"],
            )

        assert result.exit_code == 2
        assert "Round 1 BLOCKED" in result.output
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
