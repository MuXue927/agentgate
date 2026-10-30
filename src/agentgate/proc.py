"""Bounded subprocess execution: a timeout that actually bounds wall clock.

Why this module exists
----------------------
On Windows ``codex`` is an npm ``.cmd`` shim, so our direct child is
``cmd.exe`` and the real worker (``node.exe``) is a *grandchild*. Killing only
the direct child leaves the grandchild alive still holding our stdout/stderr
pipes; ``subprocess.run``'s internal second ``communicate()`` then waits for
pipe EOF and the call returns long after the deadline. Measured on this
machine: ``timeout=2`` returned after 12.4s. The same shape applies to any
``.cmd`` wrapper (``codex``, ``uv``, ``npx``), so plain ``subprocess.run``
cannot be trusted for deadlines here.

``run_bounded`` starts the child in its own process group (POSIX session) and,
on timeout, kills the whole tree before draining the pipes — bounded by
``kill_grace_s``. It never waits indefinitely.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

#: Exit code reported when the deadline expired. Matches the convention GNU
#: ``timeout`` uses, which keeps 124 meaningful in reports and state files.
TIMEOUT_EXIT_CODE = 124


class BoundedResult(subprocess.CompletedProcess):
    """``CompletedProcess`` plus explicit timeout bookkeeping."""

    timed_out: bool = False
    duration_s: float = 0.0


def run_bounded(
    command: Sequence[str] | str,
    *,
    cwd: Path | None,
    stdin_text: str | None = None,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    shell: bool = False,
    kill_grace_s: float = 5.0,
) -> BoundedResult:
    """Run ``command`` and return once it has exited or been killed.

    Returns instead of raising on timeout: ``exit_code`` is
    :data:`TIMEOUT_EXIT_CODE` and ``timed_out`` is True, with whatever output
    was produced before the kill. ``OSError`` from spawning still propagates.
    """

    started = time.monotonic()
    kwargs: dict[str, object] = {
        "cwd": str(cwd) if cwd is not None else None,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": shell,
    }
    if env is not None:
        kwargs["env"] = dict(env)
    if os.name == "nt":
        # Windows does NOT get tree-killing from the process group:
        # CREATE_NEW_PROCESS_GROUP only detaches us from console Ctrl-C. The
        # actual kill is `taskkill /F /T`, which walks the parent chain (see
        # `_kill_tree`). Do not "optimise" this away expecting group semantics
        # -- there are none for TerminateProcess.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # POSIX: the group is the mechanism -- session leader, so killpg
        # reaches every descendant of the direct child.
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = process.communicate(input=stdin_text, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _kill_tree_and_drain(process, exc, kill_grace_s)
        result = BoundedResult(command, TIMEOUT_EXIT_CODE, stdout, stderr)
        result.timed_out = True
        result.duration_s = time.monotonic() - started
        return result

    result = BoundedResult(command, process.returncode, stdout, stderr)
    result.duration_s = time.monotonic() - started
    return result


def _kill_tree_and_drain(
    process: subprocess.Popen[str],
    exc: subprocess.TimeoutExpired,
    kill_grace_s: float,
) -> tuple[str, str]:
    """Kill the whole tree, then read what is left without waiting forever."""

    partial_stdout = _text(exc.stdout)
    partial_stderr = _text(exc.stderr)
    _kill_tree(process)
    try:
        stdout, stderr = process.communicate(timeout=kill_grace_s)
    except subprocess.TimeoutExpired:
        # Something outside the tree still holds the pipes. Give up on the
        # pipes rather than block the caller.
        try:
            process.kill()
        except OSError:
            pass
        return partial_stdout, partial_stderr
    return _text(stdout) or partial_stdout, _text(stderr) or partial_stderr


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
