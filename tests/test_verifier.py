from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentgate.adapters.codex import ErrorClass, RunResult
from agentgate.collector import collect_success, utc_now
from agentgate.spec import load_task
from agentgate.status import MARKERS
from agentgate.validation import evaluate_model_pair
from agentgate.verifier import (
    VerificationError,
    extract_first_json_object,
    extract_verdict_json,
    verify_run,
)
from agentgate.workspace import cleanup_worktree, create_worktree


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "AgentGate Test")
    git(repo, "config", "user.email", "agentgate@example.invalid")
    (repo / ".gitignore").write_text("wt/\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    return repo


def make_task(tmp_path: Path, repo: Path) -> Path:
    task = tmp_path / "task.yaml"
    task.write_text(
        f"""\
id: verifier-fixture
repo: {repo.as_posix()}
goal: Create hello.txt with content ok.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints:
  - Do not push.
checks:
  - name: hello-content
    run: type hello.txt
    expect: {{exit: 0, stdout_contains: ok}}
budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


def prepare_run(tmp_path: Path) -> tuple[Path, Path]:
    repo = make_repo(tmp_path)
    task_path = make_task(tmp_path, repo)
    task = load_task(task_path)
    pair = evaluate_model_pair(
        task.executor.model,
        task.verifier.model,
    )
    worktree = create_worktree(repo, task.id)
    (worktree.path / "hello.txt").write_text("ok\n", encoding="utf-8")
    runs_root = tmp_path / "runs"
    round_dir = runs_root / task.id / "round-1"
    round_dir.mkdir(parents=True)
    result = RunResult(
        exit_code=0,
        stdout="EXECUTOR NATURAL SUMMARY",
        stderr="",
        duration_s=1.0,
        transcript_path=None,
        usage=None,
        error_class=ErrorClass.OK,
    )
    collect_success(
        task,
        worktree,
        result,
        pair,
        round_dir=round_dir,
        started_at=utc_now(),
    )
    (round_dir.parent / "task.yaml").write_text(
        task_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return runs_root, worktree.path


def completed(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["codex"], 0, text, "")


def pass_json() -> str:
    return json.dumps(
        {
            "verdict": "PASS",
            "summary": "The file matches the goal.",
            "checks": [
                {
                    "name": "hello-content",
                    "status": "PASS",
                    "evidence": "ok",
                }
            ],
            "defects": [],
        }
    )


def fail_json() -> str:
    return json.dumps(
        {
            "verdict": "FAIL",
            "summary": "The file content does not match.",
            "checks": [
                {
                    "name": "hello-content",
                    "status": "FAIL",
                    "evidence": "ok",
                }
            ],
            "defects": [
                {
                    "id": "D1",
                    "severity": "blocking",
                    "where": "hello.txt:1",
                    "why": "Expected different content.",
                    "reproduce": "type hello.txt",
                    "expect": "ok2",
                    "actual": "ok",
                }
            ],
        }
    )


def test_extracts_fenced_json_with_extra_text() -> None:
    text = f"analysis first\n```json\n{pass_json()}\n```\nend"

    verdict = extract_first_json_object(text)

    assert verdict["verdict"] == "PASS"


def test_extraction_balances_braces_inside_strings() -> None:
    text = pass_json().replace("The file matches the goal.", "brace } and { text")

    verdict = extract_first_json_object(f"prefix {text} suffix")

    assert verdict["summary"] == "brace } and { text"


def test_extraction_skips_schema_object_for_verdict() -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "schemas" / "verdict.schema.json"
        ).read_text(encoding="utf-8")
    )

    verdict = extract_verdict_json(
        f"{json.dumps(schema)}\nActual answer:\n{pass_json()}"
    )

    assert verdict["verdict"] == "PASS"


def test_packaged_schema_validation_catches_pass_with_defects() -> None:
    document = json.loads(fail_json())
    document["verdict"] = "PASS"
    with pytest.raises(ValueError, match="PASS requires defects to be empty"):
        from agentgate.spec import validate_verdict_document

        validate_verdict_document(document, source="test")


def test_pass_writes_verdict_and_done(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    try:
        outcome = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=lambda command, cwd, timeout, stdin_text: completed(
                f"extra text\n```json\n{pass_json()}\n```"
            ),
        )

        assert outcome.verdict["verdict"] == "PASS"
        assert outcome.before_status == outcome.after_status
        assert (outcome.round_dir / "verdict.json").is_file()
        assert (outcome.round_dir.parent / "DONE").is_file()
        assert [
            name for name in MARKERS if (outcome.round_dir.parent / name).exists()
        ] == ["DONE"]
        assert "EXECUTOR NATURAL SUMMARY" not in (
            outcome.round_dir / "verifier_input.md"
        ).read_text(encoding="utf-8")
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )


def test_fail_is_normal_result_and_keeps_run(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    try:
        outcome = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=lambda command, cwd, timeout, stdin_text: completed(
                fail_json()
            ),
        )

        assert outcome.verdict["verdict"] == "FAIL"
        assert outcome.verdict["defects"][0]["reproduce"] == "type hello.txt"
        assert (outcome.round_dir / "verdict.json").is_file()
        assert not (outcome.round_dir.parent / "DONE").exists()
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )


def test_two_invalid_outputs_block_without_verdict(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    calls = 0

    def invalid(command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str):
        nonlocal calls
        calls += 1
        return completed("not valid JSON")

    try:
        outcome = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=invalid,
        )

        assert calls == 2
        assert outcome.verdict is None
        assert "failed twice" in outcome.blocked_reason
        assert (outcome.round_dir.parent / "BLOCKED").is_file()
        assert not (outcome.round_dir / "verdict.json").exists()
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )


def test_one_invalid_then_valid_retries_once(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    prompts: list[str] = []
    outcomes = iter(("bad JSON", pass_json()))

    def retry(command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str):
        prompts.append(stdin_text)
        return completed(next(outcomes))

    try:
        outcome = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=retry,
        )

        assert len(prompts) == 2
        assert "Previous error" in prompts[1]
        assert outcome.verdict["verdict"] == "PASS"
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )


def test_verifier_modification_invalidates_result(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)

    def modify(command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str):
        (cwd / "verifier-dirty.txt").write_text("dirty\n", encoding="utf-8")
        return completed(pass_json())

    try:
        outcome = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=modify,
        )

        assert outcome.verdict is None
        assert outcome.blocked_reason == "verifier modified the worktree"
        assert outcome.before_status != outcome.after_status
        assert not (outcome.round_dir / "verdict.json").exists()
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )


def test_verifier_command_is_readonly(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    commands: list[tuple[str, ...]] = []

    def capture(command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str):
        commands.append((command, stdin_text))
        return completed(pass_json())

    try:
        verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=capture,
        )

        command, stdin_text = commands[0]
        assert stdin_text.startswith("You are a read-only verifier")
        assert "-s" in command
        assert command[command.index("-s") + 1] == "read-only"
        schema_flag = command.index("--output-schema")
        assert command[schema_flag + 1].endswith("verdict.schema.json")
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )


def test_output_last_message_file_is_authoritative(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)

    def write_last_message(
        command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str
    ):
        flag = command.index("-o")
        Path(command[flag + 1]).write_text(pass_json(), encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            '[{"tool":"exec_command"}]',
            "",
        )

    try:
        outcome = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=write_last_message,
        )

        assert outcome.verdict["verdict"] == "PASS"
        assert "tool" in (
            outcome.round_dir / "verifier-attempt-1.raw-stdout.txt"
        ).read_text(encoding="utf-8")
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )


def test_passed_run_requires_force_for_reverify(
    tmp_path: Path,
) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    calls = 0

    def passing(command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str):
        nonlocal calls
        calls += 1
        return completed(pass_json())

    try:
        first = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=passing,
        )
        assert first.verdict["verdict"] == "PASS"

        with pytest.raises(VerificationError, match="already passed"):
            verify_run(
                "verifier-fixture",
                runs_root=runs_root,
                command_runner=passing,
            )
        assert calls == 1

        forced = verify_run(
            "verifier-fixture",
            runs_root=runs_root,
            command_runner=passing,
            force=True,
        )
        assert forced.verdict["verdict"] == "PASS"
        assert calls == 2
    finally:
        cleanup_worktree(
            tmp_path / "target",
            worktree,
            "ag/verifier-fixture-r1",
        )
