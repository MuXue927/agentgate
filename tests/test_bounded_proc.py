from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from agentgate.proc import TIMEOUT_EXIT_CODE, run_bounded


def _shim_with_slow_grandchild(tmp_path: Path, sleep_s: float) -> list[str]:
    """A launcher whose *grandchild* does the sleeping.

    This is the shape that made ``subprocess.run(timeout=)`` useless: the
    direct child (cmd.exe / sh) is killed, the worker keeps our pipes open.
    """

    code = f"import time; time.sleep({sleep_s})"
    if os.name == "nt":
        script = tmp_path / "outer.cmd"
        script.write_text(
            f'@echo off\r\n"{sys.executable}" -c "{code}"\r\n',
            encoding="ascii",
        )
        return [str(script)]
    script = tmp_path / "outer.sh"
    script.write_text(f'#!/bin/sh\n"{sys.executable}" -c "{code}"\necho done\n')
    script.chmod(0o755)
    return [str(script)]


def test_run_bounded_kills_grandchild_and_returns_promptly(tmp_path: Path) -> None:
    """Regression: timeout=2 on a 12s grandchild must not take 12 seconds."""

    command = _shim_with_slow_grandchild(tmp_path, 12)
    started = time.monotonic()
    result = run_bounded(command, cwd=tmp_path, timeout_s=2)
    elapsed = time.monotonic() - started

    assert result.returncode == TIMEOUT_EXIT_CODE
    assert result.timed_out is True
    assert elapsed < 6, f"wall clock was not bounded by the timeout: {elapsed:.1f}s"


def test_run_bounded_normal_path_returns_output(tmp_path: Path) -> None:
    code = "import sys; data = sys.stdin.read(); print('got:' + data)"
    result = run_bounded(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        stdin_text="payload",
        timeout_s=30,
    )

    assert result.returncode == 0
    assert result.timed_out is False
    assert result.stdout.strip() == "got:payload"
    assert result.stderr == ""


def test_run_bounded_timeout_keeps_partial_output(tmp_path: Path) -> None:
    code = "import time; print('start', flush=True); time.sleep(12)"
    started = time.monotonic()
    result = run_bounded([sys.executable, "-c", code], cwd=tmp_path, timeout_s=2)
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.returncode == TIMEOUT_EXIT_CODE
    assert "start" in result.stdout
    assert elapsed < 6, f"wall clock was not bounded by the timeout: {elapsed:.1f}s"


def test_run_bounded_shell_mode_reports_exit_code(tmp_path: Path) -> None:
    """check.run in task.yaml is a shell string, so the shell path is real."""

    result = run_bounded("exit 3", cwd=tmp_path, timeout_s=30, shell=True)

    assert result.returncode == 3
    assert result.timed_out is False
