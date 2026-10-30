from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentgate.proc import run_bounded
from agentgate.validation import ModelPairError, evaluate_model_pair

DSH_EXE = Path(r"D:\Software\DeepSeek Harness\DeepSeek Harness.exe")
DSH_CLI = Path(
    r"D:\Software\DeepSeek Harness\resources\app.asar\dsh\node_modules"
    r"\@deepseek-ai\dsh-desktop-host\lib\cli.js"
)
EXECUTOR_MODEL = "deepseek-v4-flash"
CODEX_VERIFIER_MODEL = "nvidia/nemotron-3.5-lightning:free"
PI_VERIFIER_MODEL = "bigmodel/glm-4.7-flash"
PROBE_PROMPT = "reply OK"
# A prompt that can only be satisfied by actually running a shell command.
# "reply OK" passes even when the Codex sandbox cannot start a process at all,
# which is exactly the Windows defect we need to surface here.
TOOL_PROBE_TEXT = "agentgate-probe"
TOOL_PROBE_PROMPT = f"Run this shell command and then reply with its exact output: echo {TOOL_PROBE_TEXT}"
TOOL_PROBE_NOTE = (
    "Codex 无法执行任何 shell 命令（Windows 沙箱缺陷，见 README「常见坑」）。"
    "任务需显式设置 `sandbox: danger-full-access`；若这样仍失败，说明 Codex 侧"
    "完全不可用，参考 §5.3 的三个上游 issue。"
)
DSH_CREDENTIAL_NOTE = (
    "DSH 验收方不可用：需设置 DEEPSEEK_API_KEY 环境变量"
    "（DSH GUI 里的 key 在 headless 下读不到）"
)
PI_CREDENTIAL_NOTE = (
    "Pi 验收方不可用：需配置智谱 key（ZHIPU_API_KEY），再重开终端并运行 `ag doctor`。"
)


@dataclass(frozen=True)
class Probe:
    key: str
    backend: str
    role: str
    scaffold: str
    model: str
    required: bool
    command: tuple[str, ...]
    timeout_s: int
    env: dict[str, str] | None = None
    # When set, the probe only counts as OK if this text appears in stdout.
    # Needed for tool-execution probes: a model blocked by the sandbox still
    # exits 0 after explaining that it could not run anything.
    expect_stdout: str = ""
    # The Codex Windows sandbox is intermittent, so tool probes retry once
    # before being declared broken.
    attempts: int = 1


@dataclass(frozen=True)
class ProbeResult:
    probe: Probe
    ok: bool
    exit_code: int | None
    duration_s: float
    stdout: str = ""
    stderr: str = ""
    error: str = ""


@dataclass(frozen=True)
class DoctorReport:
    results: tuple[ProbeResult, ...]
    tier: str
    required_ok: bool
    repair_notes: tuple[str, ...] = field(default_factory=tuple)
    model_pair_error: str = ""


ProbeRunner = Callable[[Probe, Path], ProbeResult]


def _windows_executable(command: Sequence[str], *, cwd: Path) -> list[str]:
    resolved = list(command)
    executable = resolved[0]
    if Path(executable).is_absolute():
        return resolved

    found = shutil.which(executable, path=os.environ.get("PATH"))
    if found:
        resolved[0] = found
    return resolved


def run_probe(probe: Probe, cwd: Path) -> ProbeResult:
    command = _windows_executable(probe.command, cwd=cwd)
    started = time.monotonic()
    result: ProbeResult | None = None
    for attempt in range(1, max(1, probe.attempts) + 1):
        result = _run_probe_once(probe, command, cwd, started)
        if result.ok or attempt >= probe.attempts:
            break
    assert result is not None
    return result


def _run_probe_once(
    probe: Probe,
    command: list[str],
    cwd: Path,
    started: float,
) -> ProbeResult:
    try:
        completed = run_bounded(
            command,
            cwd=cwd,
            timeout_s=probe.timeout_s,
            env=probe.env,
        )
    except OSError as exc:
        return ProbeResult(
            probe=probe,
            ok=False,
            exit_code=None,
            duration_s=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    if completed.timed_out:
        return ProbeResult(
            probe=probe,
            ok=False,
            exit_code=None,
            duration_s=time.monotonic() - started,
            stdout=completed.stdout,
            stderr=completed.stderr,
            error=f"timeout after {probe.timeout_s}s",
        )

    return ProbeResult(
        probe=probe,
        ok=completed.returncode == 0
        and (not probe.expect_stdout or probe.expect_stdout in completed.stdout),
        exit_code=completed.returncode,
        duration_s=time.monotonic() - started,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def build_probes(project_dir: Path) -> tuple[Probe, ...]:
    project = str(project_dir)
    codex_executor = (
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-C",
        project,
        "-m",
        EXECUTOR_MODEL,
        PROBE_PROMPT,
    )
    dsh_verifier = (
        str(DSH_EXE),
        "--expose-internals",
        str(DSH_CLI),
        "headless",
        PROBE_PROMPT,
        "--json",
    )
    codex_verifier = (
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-C",
        project,
        "-s",
        "read-only",
        "-m",
        CODEX_VERIFIER_MODEL,
        PROBE_PROMPT,
    )
    codex_tools = (
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-C",
        project,
        "-s",
        "danger-full-access",
        "-m",
        EXECUTOR_MODEL,
        TOOL_PROBE_PROMPT,
    )
    pi_verifier = (
        "pi",
        "-p",
        "--no-tools",
        "--no-skills",
        "--no-session",
        "--model",
        PI_VERIFIER_MODEL,
        "--mode",
        "text",
        PROBE_PROMPT,
    )

    dsh_env = os.environ.copy()
    dsh_env["ELECTRON_RUN_AS_NODE"] = "1"
    pi_env = os.environ.copy()
    return (
        Probe(
            key="executor_codex",
            backend="Codex (executor)",
            role="required",
            scaffold="codex",
            model=EXECUTOR_MODEL,
            required=True,
            command=codex_executor,
            timeout_s=60,
        ),
        Probe(
            key="verifier_codex",
            backend="Codex (default verifier, read-only)",
            role="required",
            scaffold="codex",
            model=CODEX_VERIFIER_MODEL,
            required=True,
            command=codex_verifier,
            timeout_s=45,
        ),
        Probe(
            key="codex_tools",
            backend="Codex (tool execution, danger-full-access)",
            role="optional",
            scaffold="codex",
            model=EXECUTOR_MODEL,
            required=False,
            command=codex_tools,
            timeout_s=90,
            expect_stdout=TOOL_PROBE_TEXT,
            attempts=2,
        ),
        Probe(
            key="verifier_dsh",
            backend="DSH (optional verifier)",
            role="optional",
            scaffold="dsh",
            model=EXECUTOR_MODEL,
            required=False,
            command=dsh_verifier,
            timeout_s=30,
            env=dsh_env,
        ),
        Probe(
            key="verifier_pi",
            backend="Pi (optional verifier)",
            role="optional",
            scaffold="pi",
            model=PI_VERIFIER_MODEL,
            required=False,
            command=pi_verifier,
            timeout_s=30,
            env=pi_env,
        ),
    )


def _required_repair_notes(
    project_dir: Path, results: Sequence[ProbeResult]
) -> tuple[str, ...]:
    notes: list[str] = []
    by_key = {result.probe.key: result for result in results}
    codex_command = (
        f'codex exec --skip-git-repo-check -C "{project_dir}" '
        f'-m {EXECUTOR_MODEL} "reply OK"'
    )

    if not by_key["executor_codex"].ok:
        notes.extend(
            (
                "Codex executor failed. Check the CLI and proxy with: where.exe codex; ocx status",
                f"Then retry: {codex_command}",
            )
        )
    if not by_key["verifier_codex"].ok:
        notes.append(
            "Default Codex verifier failed. Configure its non-DeepSeek provider with "
            "ocx, verify with `ocx status`, then retry `ag doctor`."
        )
    return tuple(notes)


def _optional_failure_summary(result: ProbeResult) -> str:
    raw = f"{result.stdout}\n{result.stderr}"
    if result.probe.key == "codex_tools":
        if "helper_unknown_error" in raw or "blocked by policy" in raw:
            return TOOL_PROBE_NOTE
        return "Codex 未能执行 shell 命令；真实运行会因 0 改动而 BLOCKED。"
    if result.probe.key == "verifier_dsh" and "MISSING_CREDENTIAL" in raw:
        return "需 DEEPSEEK_API_KEY"
    if result.probe.key == "verifier_pi":
        return "需智谱 key（ZHIPU_API_KEY）"
    if result.error:
        return result.error
    first_line = next(
        (line.strip() for line in raw.splitlines() if line.strip()),
        "探测失败",
    )
    return first_line[:160]


def _tier(results: Sequence[ProbeResult]) -> str:
    by_key = {result.probe.key: result for result in results}
    executor = by_key["executor_codex"]
    default_verifier = by_key["verifier_codex"]
    if not executor.ok or not default_verifier.ok:
        return "unavailable"

    try:
        default_pair = evaluate_model_pair(
            executor.probe.model,
            default_verifier.probe.model,
            executor_scaffold=executor.probe.scaffold,
            verifier_scaffold=default_verifier.probe.scaffold,
        )
        tier = default_pair.tier
    except ModelPairError:
        return "unavailable (same model)"

    pi = by_key["verifier_pi"]
    if pi.ok:
        try:
            pi_pair = evaluate_model_pair(
                executor.probe.model,
                pi.probe.model,
                executor_scaffold=executor.probe.scaffold,
                verifier_scaffold=pi.probe.scaffold,
            )
            tier = pi_pair.tier
        except ModelPairError:
            pass
    return tier


def collect_report(
    project_dir: Path,
    *,
    probe_runner: ProbeRunner = run_probe,
) -> DoctorReport:
    results = tuple(
        probe_runner(probe, project_dir) for probe in build_probes(project_dir)
    )
    by_key = {result.probe.key: result for result in results}
    model_pair_error = ""
    try:
        evaluate_model_pair(
            by_key["executor_codex"].probe.model,
            by_key["verifier_codex"].probe.model,
        )
    except ModelPairError as exc:
        model_pair_error = str(exc)

    required_ok = (
        all(result.ok for result in results if result.probe.required)
        and not model_pair_error
    )
    return DoctorReport(
        results=results,
        tier=_tier(results),
        required_ok=required_ok,
        repair_notes=_required_repair_notes(project_dir, results),
        model_pair_error=model_pair_error,
    )


def _display_command(command: Sequence[str]) -> str:
    def quote(part: str) -> str:
        if not part or any(char.isspace() for char in part):
            return f'"{part}"'
        return part

    return " ".join(quote(part) for part in command)


def render_report(report: DoctorReport, *, verbose: bool = False) -> str:
    headers = ("backend", "role", "command", "result", "time")
    rows = []
    for result in report.results:
        result_text = "PASS" if result.ok else "FAIL"
        rows.append(
            (
                result.probe.backend,
                result.probe.role,
                _display_command(result.probe.command),
                result_text,
                f"{result.duration_s:.2f}s",
            )
        )

    widths = [
        max([len(headers[index]), *(len(row[index]) for row in rows)])
        for index in range(len(headers))
    ]

    def format_row(row: Sequence[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    lines = [
        format_row(headers),
        "-+-".join("-" * width for width in widths),
    ]
    lines.extend(format_row(row) for row in rows)

    failures = [result for result in report.results if not result.ok]
    if failures:
        lines.extend(("", "Probe failures:"))
        for result in failures:
            if not result.probe.required and not verbose:
                lines.append(
                    f"[{result.probe.backend}] FAIL — "
                    f"{_optional_failure_summary(result)}"
                )
                continue
            status = (
                f"exit={result.exit_code}"
                if result.exit_code is not None
                else "no exit code"
            )
            lines.extend(
                (
                    f"[{result.probe.backend}] {status}",
                    "stdout:",
                    result.stdout.rstrip() or "<empty>",
                    "stderr:",
                    result.stderr.rstrip() or "<empty>",
                )
            )
            if result.error:
                lines.append(f"error: {result.error}")

    if verbose:
        dsh = next(
            (result for result in report.results if result.probe.key == "verifier_dsh"),
            None,
        )
        pi = next(
            (result for result in report.results if result.probe.key == "verifier_pi"),
            None,
        )
        optional_notes: list[str] = []
        if dsh is not None and not dsh.ok:
            optional_notes.append(DSH_CREDENTIAL_NOTE)
        if pi is not None and not pi.ok:
            optional_notes.append(PI_CREDENTIAL_NOTE)
        if optional_notes:
            lines.extend(("", "Optional verifier notes:"))
            lines.extend(f"- {note}" for note in optional_notes)

    if report.repair_notes:
        lines.extend(("", "Repair commands / notes:"))
        lines.extend(f"- {note}" for note in report.repair_notes)

    if report.model_pair_error:
        lines.extend(("", f"Invalid model pair: {report.model_pair_error}"))
    lines.extend(("", f"Double-blind tier: {report.tier}"))
    return "\n".join(lines)
