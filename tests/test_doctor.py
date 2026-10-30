from __future__ import annotations

import unittest
from pathlib import Path

from agentgate.doctor import (
    DSH_CREDENTIAL_NOTE,
    DSH_EXE,
    Probe,
    ProbeResult,
    build_probes,
    collect_report,
    render_report,
)
from agentgate.validation import ModelPairError, require_different_models

PROJECT = Path(r"D:\Projects\agentgate")


def result(
    probe: Probe,
    ok: bool,
    *,
    stdout: str = "",
    stderr: str = "",
) -> ProbeResult:
    return ProbeResult(
        probe=probe,
        ok=ok,
        exit_code=0 if ok else 1,
        duration_s=0.1,
        stdout=stdout or ("OK" if ok else ""),
        stderr=stderr or ("" if ok else "raw failure"),
    )


class DoctorTests(unittest.TestCase):
    def test_tier_uses_model_difference_and_scaffold(self) -> None:
        cases = (
            (
                {
                    "executor_codex": True,
                    "codex_tools": True,
                    "verifier_dsh": False,
                    "verifier_codex": True,
                    "verifier_pi": True,
                },
                "★★★",
            ),
            (
                {
                    "executor_codex": True,
                    "codex_tools": True,
                    "verifier_dsh": False,
                    "verifier_codex": True,
                    "verifier_pi": False,
                },
                "★★",
            ),
        )
        for statuses, expected_prefix in cases:
            with self.subTest(expected_prefix=expected_prefix):
                report = collect_report(
                    PROJECT,
                    probe_runner=lambda probe, cwd, statuses=statuses: result(
                        probe,
                        statuses[probe.key],
                    ),
                )
                self.assertTrue(report.tier.startswith(expected_prefix))

    def test_required_failure_marks_tier_unavailable(self) -> None:
        statuses = {
            "executor_codex": False,
            "codex_tools": True,
            "verifier_dsh": True,
            "verifier_codex": True,
            "verifier_pi": False,
        }
        report = collect_report(
            PROJECT,
            probe_runner=lambda probe, cwd: result(probe, statuses[probe.key]),
        )
        self.assertFalse(report.required_ok)
        self.assertEqual(report.tier, "unavailable")

    def test_render_preserves_raw_failure_and_repair_command(self) -> None:
        statuses = {
            "executor_codex": False,
            "codex_tools": True,
            "verifier_dsh": False,
            "verifier_codex": False,
            "verifier_pi": False,
        }
        report = collect_report(
            PROJECT,
            probe_runner=lambda probe, cwd: result(probe, statuses[probe.key]),
        )
        output = render_report(report)
        self.assertIn("raw failure", output)
        self.assertIn("where.exe codex", output)
        self.assertIn("Double-blind tier: unavailable", output)

    def test_dsh_is_optional_and_reports_missing_credential(self) -> None:
        statuses = {
            "executor_codex": True,
            "codex_tools": True,
            "verifier_dsh": False,
            "verifier_codex": True,
            "verifier_pi": False,
        }

        def probe_result(probe: Probe, cwd: Path) -> ProbeResult:
            ok = statuses[probe.key]
            raw_error = (
                "MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "
                '"deepseek-official"'
                if probe.key == "verifier_dsh"
                else ""
            )
            return result(probe, ok, stderr=raw_error)

        report = collect_report(PROJECT, probe_runner=probe_result)
        output = render_report(report)

        self.assertTrue(report.required_ok)
        self.assertIn("DSH (optional verifier)", output)
        self.assertIn("需 DEEPSEEK_API_KEY", output)
        self.assertNotIn("MISSING_CREDENTIAL: llm-deepseek", output)

        verbose_output = render_report(report, verbose=True)
        self.assertIn("MISSING_CREDENTIAL: llm-deepseek", verbose_output)
        self.assertIn(DSH_CREDENTIAL_NOTE, verbose_output)

    def test_same_model_pair_is_rejected(self) -> None:
        with self.assertRaisesRegex(ModelPairError, "same model family"):
            require_different_models("deepseek-v4-flash", "deepseek-v4-flash")

    def test_dsh_probe_bypasses_cmd_and_is_optional(self) -> None:
        probes = {probe.key: probe for probe in build_probes(PROJECT)}
        dsh = probes["verifier_dsh"]

        self.assertFalse(dsh.required)
        self.assertEqual(dsh.command[0], str(DSH_EXE))
        self.assertIn("--expose-internals", dsh.command)
        self.assertEqual(dsh.env["ELECTRON_RUN_AS_NODE"], "1")


if __name__ == "__main__":
    unittest.main()
