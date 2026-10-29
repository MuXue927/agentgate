from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

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
DSH_CREDENTIAL_NOTE = (
    "DSH 验收方不可用：需设置 DEEPSEEK_API_KEY 环境变量"
    "（DSH GUI 里的 key 在 headless 下读不到）"
)
PI_CREDENTIAL_NOTE = (
    "Pi 验收方不可用：需配置智谱 key（ZHIPU_API_KEY），"
    "再重开终端并运行 `ag doctor`。"
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
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=probe.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=probe.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return ProbeResult(
            probe=probe,
            ok=False,
            exit_code=None,
            duration_s=time.monotonic() - started,
            stdout=stdout,
            stderr=stderr,
            error=f"timeout after {probe.timeout_s}s",
        )
    except OSError as exc:
        return ProbeResult(
            probe=probe,
            ok=False,
            exit_code=None,
            duration_s=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    return ProbeResult(
        probe=probe,
        ok=completed.returncode == 0,
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


def _repair_notes(project_dir: Path, results: Sequence[ProbeResult]) -> tuple[str, ...]:
    notes: list[str] = []
    by_key = {result.probe.key: result for result in results}
    codex_command = (
        f'codex exec --skip-git-repo-check -C "{project_dir}" '
        f'-m {EXECUTOR_MODEL} "reply OK"'
    )
    dsh_command = (
        f'$env:ELECTRON_RUN_AS_NODE="1"; & "{DSH_EXE}" --expose-internals '
        f'"{DSH_CLI}" headless "reply OK" --json'
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
    dsh = by_key["verifier_dsh"]
    if not dsh.ok:
        if "MISSING_CREDENTIAL" in f"{dsh.stdout}\n{dsh.stderr}":
            notes.append(DSH_CREDENTIAL_NOTE)
        notes.append(f"DSH 直接调用命令：{dsh_command}")
    if not by_key["verifier_pi"].ok:
        notes.append(PI_CREDENTIAL_NOTE)
    return tuple(notes)


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
        repair_notes=_repair_notes(project_dir, results),
        model_pair_error=model_pair_error,
    )


def _display_command(command: Sequence[str]) -> str:
    def quote(part: str) -> str:
        if not part or any(char.isspace() for char in part):
            return f'"{part}"'
        return part

    return " ".join(quote(part) for part in command)


def render_report(report: DoctorReport) -> str:
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
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        )

    lines = [
        format_row(headers),
        "-+-".join("-" * width for width in widths),
    ]
    lines.extend(format_row(row) for row in rows)

    failures = [result for result in report.results if not result.ok]
    if failures:
        lines.extend(("", "Probe failures (raw output):"))
        for result in failures:
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

    if report.repair_notes:
        lines.extend(("", "Repair commands / notes:"))
        lines.extend(f"- {note}" for note in report.repair_notes)

    if report.model_pair_error:
        lines.extend(("", f"Invalid model pair: {report.model_pair_error}"))
    lines.extend(("", f"Double-blind tier: {report.tier}"))
    return "\n".join(lines)
