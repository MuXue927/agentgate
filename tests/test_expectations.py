from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentgate.adapters.codex import ErrorClass, RunResult
from agentgate.collector import CheckOutput, collect_success, utc_now
from agentgate.expectations import evaluate_expect
from agentgate.spec import CheckSpec, load_task
from agentgate.validation import evaluate_model_pair
from agentgate.verifier import verify_run
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


def make_task(
    tmp_path: Path,
    repo: Path,
    *,
    check_run: str,
    expect: str,
) -> Path:
    task = tmp_path / "task.yaml"
    task.write_text(
        f"""\
id: expect-fixture
repo: {repo.as_posix()}
goal: Create hello.txt with content ok.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints:
  - Do not push.
checks:
  - name: hello-content
    run: {check_run}
    expect: {expect}
budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


def prepare(
    tmp_path: Path,
    *,
    check_run: str = "type hello.txt",
    expect: str = "{exit: 0, stdout_contains: ok}",
) -> tuple[Path, Path, str, str]:
    repo = make_repo(tmp_path)
    task_path = make_task(tmp_path, repo, check_run=check_run, expect=expect)
    task = load_task(task_path)
    pair = evaluate_model_pair(task.executor.model, task.verifier.model)
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
    return runs_root, worktree.path, task.id, f"ag/{task.id}-r1"


def cleanup(tmp_path: Path, worktree: Path, branch: str) -> None:
    cleanup_worktree(tmp_path / "target", worktree, branch)


def completed(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["codex"], 0, text, "")


def model_verdict(verdict: str) -> str:
    if verdict == "PASS":
        return json.dumps(
            {
                "verdict": "PASS",
                "summary": "The file matches the goal.",
                "checks": [
                    {"name": "hello-content", "status": "PASS", "evidence": "ok"}
                ],
                "defects": [],
            }
        )
    return json.dumps(
        {
            "verdict": "FAIL",
            "summary": "The file content does not match.",
            "checks": [
                {"name": "hello-content", "status": "FAIL", "evidence": "ok"}
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


def test_mechanical_fail_overrides_model_pass(tmp_path: Path) -> None:
    """A model PASS must never rescue a check that really exited non-zero."""

    runs_root, worktree, task_id, branch = prepare(
        tmp_path,
        check_run="type missing.txt",
        expect="{exit: 0}",
    )
    calls: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...],
        cwd: Path,
        timeout: float,
        stdin_text: str,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed(model_verdict("PASS"))

    try:
        outcome = verify_run(task_id, runs_root=runs_root, command_runner=runner)

        assert calls == [], "the verifier model must not be called"
        assert outcome.verdict is not None
        assert outcome.verdict["verdict"] == "FAIL"
        assert outcome.attempts == ()
        assert outcome.mechanical is not None and not outcome.mechanical.ok
        assert (outcome.round_dir / "mechanical.json").is_file()
        assert (outcome.round_dir / "verdict.json").is_file()
    finally:
        cleanup(tmp_path, worktree, branch)


def test_all_checks_pass_still_requires_model_pass(tmp_path: Path) -> None:
    """Green checks are necessary, not sufficient: both sides must pass."""

    runs_root, worktree, task_id, branch = prepare(tmp_path)
    try:
        outcome = verify_run(
            task_id,
            runs_root=runs_root,
            command_runner=lambda command, cwd, timeout, stdin_text: completed(
                model_verdict("FAIL")
            ),
        )

        assert outcome.mechanical is not None and outcome.mechanical.ok
        assert outcome.verdict is not None
        assert outcome.verdict["verdict"] == "FAIL"
        assert outcome.verdict["defects"][0]["where"] == "hello.txt:1"
    finally:
        cleanup(tmp_path, worktree, branch)


def test_command_timeout_is_mechanical_fail(tmp_path: Path) -> None:
    """Exit 124 is a failed check, not an infrastructure BLOCKED."""

    runs_root, worktree, task_id, branch = prepare(tmp_path)
    round_dir = runs_root / task_id / "round-1"
    (round_dir / "artifacts" / "01-hello-content.exit.txt").write_text(
        "124\n",
        encoding="utf-8",
    )

    def runner(
        command: tuple[str, ...],
        cwd: Path,
        timeout: float,
        stdin_text: str,
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("the verifier model must not be called")

    try:
        outcome = verify_run(task_id, runs_root=runs_root, command_runner=runner)

        assert outcome.verdict is not None
        assert outcome.verdict["verdict"] == "FAIL"
        assert outcome.attempts == ()
        assert "exit expected 0 actual 124" in outcome.verdict["defects"][0]["why"]
        assert not (round_dir.parent / "BLOCKED").is_file()
    finally:
        cleanup(tmp_path, worktree, branch)


def test_mechanical_verdict_is_distinguishable(tmp_path: Path) -> None:
    """Synthetic defects must be attributable to the machine, not the judge."""

    runs_root, worktree, task_id, branch = prepare(
        tmp_path,
        check_run="type missing.txt",
        expect="{exit: 0}",
    )
    try:
        outcome = verify_run(
            task_id,
            runs_root=runs_root,
            command_runner=lambda command, cwd, timeout, stdin_text: completed(
                model_verdict("PASS")
            ),
        )

        assert outcome.verdict is not None
        defect = outcome.verdict["defects"][0]
        assert defect["where"] == "mechanical:hello-content"
        assert defect["severity"] == "blocking"
        assert defect["reproduce"] == "type missing.txt"
        assert defect["id"] == "M1"
        assert outcome.verdict["checks"][0]["status"] == "FAIL"
        assert outcome.verdict["summary"].startswith("mechanical checks failed:")
    finally:
        cleanup(tmp_path, worktree, branch)


@pytest.mark.parametrize(
    ("expect", "stdout", "exit_code", "expected_ok"),
    [
        ({"exit": 0, "stdout_contains": "ok"}, "ok\n", 1, False),
        ({"exit": 0, "stdout_contains": "ok"}, "no\n", 0, False),
        ({"exit": 0, "stdout_not_contains": "failed"}, "failed 1\n", 0, False),
        ({"exit": 0, "stdout_regex": r"\d+ passed"}, "8 passed", 0, True),
    ],
)
def test_every_declared_key_is_evaluated(
    expect: dict[str, object],
    stdout: str,
    exit_code: int,
    expected_ok: bool,
) -> None:
    """No short-circuit: one failing key is never hidden by a passing one."""

    spec = CheckSpec(name="sample", run="cmd", expect=expect)
    output = CheckOutput(
        spec=spec,
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_s=0.1,
    )

    ok, reason = evaluate_expect(spec, output)

    assert ok is expected_ok
    if not expected_ok:
        assert reason
