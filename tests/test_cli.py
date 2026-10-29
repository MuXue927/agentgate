from __future__ import annotations

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

    def test_run_without_dry_run_is_refused(self) -> None:
        result = CliRunner().invoke(
            app,
            ["run", "templates/add-feature.task.yaml"],
        )

        assert result.exit_code == 2
        assert "Real execution is not implemented" in result.output
