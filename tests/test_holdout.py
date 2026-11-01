"""Held-out checks: acceptance criteria the executor never sees (P1-1)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentgate.adapters.codex import ErrorClass, RunResult
from agentgate.collector import collect_success, utc_now
from agentgate.report import render_report
from agentgate.spec import load_task
from agentgate.validation import evaluate_model_pair
from agentgate.verifier import verify_run
from agentgate.workspace import cleanup_worktree, create_worktree

RUN_ID = "holdout-fixture"
PY = f'"{sys.executable}"'

#: One line, because the report is allowed to quote the tail of the output and
#: nothing else.
FAILING_HOLDOUT = (
    "import sys; sys.stderr.write('AssertionError: empty input'); sys.exit(1)"
)
#: If this ever runs, it leaves a file behind -- which is how the "skipped when
#: the public checks fail" test proves the command was never executed.
MARKER_HOLDOUT = (
    "import pathlib; pathlib.Path('holdout-ran.txt').write_text('x', encoding='utf-8')"
)
PUBLIC_CHECK = (
    "import pathlib,sys; "
    "sys.exit(0 if pathlib.Path('hello.txt').read_text(encoding='utf-8').strip()"
    "=='ok' else 1)"
)


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


def make_task(tmp_path: Path, repo: Path, *, holdout: str | None) -> Path:
    block = ""
    if holdout is not None:
        block = (
            "holdout_checks:\n"
            "  - name: hidden-empty\n"
            "    run: >-\n"
            f'      {PY} -c "{holdout}"\n'
            "    expect: {exit: 0}\n"
        )
    task = tmp_path / "task.yaml"
    task.write_text(
        f"""\
id: {RUN_ID}
repo: {repo.as_posix()}
goal: Create hello.txt with content ok.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints:
  - Do not push.
checks:
  - name: hello-content
    run: >-
      {PY} -c "{PUBLIC_CHECK}"
    expect: {{exit: 0}}
{block}budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


def prepare_run(
    tmp_path: Path,
    *,
    holdout: str | None = FAILING_HOLDOUT,
) -> tuple[Path, Path, Path]:
    """Collect round 1 with a green public check and a failing held-out one."""

    repo = make_repo(tmp_path)
    task_path = make_task(tmp_path, repo, holdout=holdout)
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
    # The orchestrator owns the run-level state; the verifier only writes back
    # into an existing round entry.
    (round_dir.parent / "state.json").write_text(
        json.dumps(
            {
                "status": "executed",
                "rounds": [{"round": 1, "status": "executed", "branch": "ag/x-r1"}],
            }
        ),
        encoding="utf-8",
    )
    return runs_root, worktree.path, repo


def completed(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["codex"], 0, text, "")


def subject_of(runs_root: Path) -> dict:
    path = runs_root / RUN_ID / "round-1" / "subject.json"
    return json.loads(path.read_text(encoding="utf-8"))


def pass_json(runs_root: Path) -> str:
    return json.dumps(
        {
            "verdict": "PASS",
            "summary": "The file matches the goal.",
            "checks": [
                {"name": "hello-content", "status": "PASS", "evidence": "ok"},
            ],
            "defects": [],
            "subject": subject_of(runs_root),
        }
    )


def never_called(calls: list[tuple[str, ...]]):
    def runner(command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str):
        calls.append(command)
        raise AssertionError("the verifier model must not be called")

    return runner


def test_holdout_failure_is_terminal_without_verifier(tmp_path: Path) -> None:
    """A hidden check the executor never saw fails: FAILED, model 0 calls."""

    runs_root, worktree, _ = prepare_run(tmp_path)
    calls: list[tuple[str, ...]] = []
    try:
        outcome = verify_run(
            RUN_ID,
            runs_root=runs_root,
            command_runner=never_called(calls),
        )

        run_dir = runs_root / RUN_ID
        assert calls == []
        assert outcome.attempts == ()
        assert outcome.verdict is None
        assert outcome.failure_reason == "holdout-failed"
        assert not (run_dir / "BLOCKED").exists()
        assert (run_dir / "FAILED").is_file()
        assert not (run_dir / "DONE").exists()
        assert not (outcome.round_dir / "verdict.json").exists()
        # The public gate really was green before the hidden one ran.
        assert outcome.mechanical is not None
        assert outcome.mechanical.ok
        assert outcome.mechanical.passed == ("hello-content",)

        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        entry = state["rounds"][0]
        assert state["status"] == "failed"
        assert entry["status"] == "failed"
        assert entry["failure_reason"] == "holdout-failed"
        assert entry["holdout"]["checked"] == 1
        assert entry["holdout"]["failed"] == [
            {"index": 1, "name": "hidden-empty", "tail": "AssertionError: empty input"}
        ]
        assert entry["subject"]["changed_files"] == ["hello.txt"]
        assert "verdict" not in entry
    finally:
        cleanup_worktree(tmp_path / "target", worktree, "ag/holdout-fixture-r1")


def test_holdout_failure_reports_index_not_command(tmp_path: Path) -> None:
    """The report may quote #1 and its output tail; never the command."""

    runs_root, worktree, repo = prepare_run(tmp_path)
    try:
        outcome = verify_run(
            RUN_ID,
            runs_root=runs_root,
            command_runner=never_called([]),
        )
        run_dir = runs_root / RUN_ID
        task = load_task(run_dir / "task.yaml")
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))

        report = render_report(task, state, repo=repo, run_dir=run_dir)

        assert (
            '留出判据未通过：#1（输出尾部 "AssertionError: empty input"），未调用验收方。'
            in report
        )
        assert "- R1: holdout-failed" in report
        assert "hidden-empty" not in report
        assert "sys.exit(1)" not in report
        assert outcome.round_dir == run_dir / "round-1"
    finally:
        cleanup_worktree(tmp_path / "target", worktree, "ag/holdout-fixture-r1")


def test_holdout_skipped_when_public_checks_fail(tmp_path: Path) -> None:
    """Public red: the hidden check is not even executed."""

    runs_root, worktree, _ = prepare_run(tmp_path, holdout=MARKER_HOLDOUT)
    try:
        exit_file = (
            runs_root / RUN_ID / "round-1" / "artifacts" / "01-hello-content.exit.txt"
        )
        exit_file.write_text("1\n", encoding="utf-8")

        outcome = verify_run(
            RUN_ID,
            runs_root=runs_root,
            command_runner=never_called([]),
        )

        assert outcome.verdict is not None
        assert outcome.verdict["verdict"] == "FAIL"
        assert outcome.failure_reason == ""
        assert outcome.holdout is None
        assert not (outcome.round_dir / "holdout.json").exists()
        assert not (worktree / "holdout-ran.txt").exists()
    finally:
        cleanup_worktree(tmp_path / "target", worktree, "ag/holdout-fixture-r1")


def test_holdout_artifact_written_with_full_command(tmp_path: Path) -> None:
    """holdout.json is the only place the command survives."""

    runs_root, worktree, _ = prepare_run(tmp_path)
    try:
        outcome = verify_run(
            RUN_ID,
            runs_root=runs_root,
            command_runner=never_called([]),
        )
        declared = load_task(runs_root / RUN_ID / "task.yaml").holdout_checks[0]

        document = json.loads(
            (outcome.round_dir / "holdout.json").read_text(encoding="utf-8")
        )

        assert document["checked"] == 1
        entry = document["checks"][0]
        assert entry["index"] == 1
        assert entry["name"] == "hidden-empty"
        assert entry["run"] == declared.run
        assert entry["expect"] == {"exit": 0}
        assert entry["exit_code"] == 1
        assert entry["status"] == "FAIL"
        assert "AssertionError: empty input" in entry["stderr"]
    finally:
        cleanup_worktree(tmp_path / "target", worktree, "ag/holdout-fixture-r1")


def test_no_holdout_field_behaves_as_before(tmp_path: Path) -> None:
    """No `holdout_checks`: identical artifacts, identical PASS path."""

    runs_root, worktree, _ = prepare_run(tmp_path, holdout=None)
    try:
        outcome = verify_run(
            RUN_ID,
            runs_root=runs_root,
            command_runner=lambda command, cwd, timeout, stdin_text: completed(
                pass_json(runs_root)
            ),
        )

        assert outcome.verdict is not None
        assert outcome.verdict["verdict"] == "PASS"
        assert outcome.holdout is None
        assert not (outcome.round_dir / "holdout.json").exists()
        state = json.loads(
            (runs_root / RUN_ID / "state.json").read_text(encoding="utf-8")
        )
        assert "holdout" not in state["rounds"][0]
    finally:
        cleanup_worktree(tmp_path / "target", worktree, "ag/holdout-fixture-r1")
