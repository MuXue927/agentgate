from __future__ import annotations

import subprocess
from pathlib import Path

from fakes import scripted_executor, scripted_verifier

from agentgate.orchestrator import Usage, start_task
from agentgate.status import read_state
from agentgate.workspace import cleanup_worktree

TASK_ID = "fake-loop"

# A cmd builtin, not a Python one-liner: the mechanical checks are real
# commands in the worktree, and spawning an interpreter twice per round would
# dominate the runtime of these tests.
STATE_CHECK = 'findstr /x /c:"done" state.txt'
ALWAYS_FAILS = 'findstr /x /c:"never" state.txt'


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
    (repo / "tests").mkdir()
    (repo / "tests" / "test_keep.py").write_text("def test_keep():\n    pass\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    return repo


def make_task(
    tmp_path: Path,
    repo: Path,
    *,
    check_run: str,
    check_name: str = "state-ok",
    protected: str = "",
    max_rounds: int = 2,
) -> Path:
    task = tmp_path / "task.yaml"
    task.write_text(
        f"""\
id: {TASK_ID}
repo: {repo.as_posix()}
goal: Write state.txt with the word done.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints: []
{protected}checks:
  - name: {check_name}
    run: {check_run}
    expect: {{exit: 0}}
budget: {{max_rounds: {max_rounds}, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


def usage() -> Usage:
    return Usage(total_tokens=0, cost_usd=0.0)


def pass_verdict(check_name: str) -> dict:
    return {
        "verdict": "PASS",
        "summary": "state.txt says done.",
        "checks": [{"name": check_name, "status": "PASS", "evidence": "done"}],
        "defects": [],
    }


def write_state_file(text: str):
    def action(worktree: Path) -> None:
        (worktree / "state.txt").write_text(text + "\n", encoding="utf-8")

    return action


def test_fake_loop_reflows_fail_then_pass(tmp_path: Path) -> None:
    """R1 fails its own check, R2 fixes it, the model answers PASS."""

    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo, check_run=STATE_CHECK)
    executor = scripted_executor(
        {1: write_state_file("draft"), 2: write_state_file("done")}
    )
    verifier = scripted_verifier({2: pass_verdict("state-ok")})
    events: list[str] = []

    try:
        outcome = start_task(
            task,
            runs_root=tmp_path / "runs",
            executor=executor,
            verifier=verifier,
            usage_provider=usage,
            on_event=events.append,
        )
        run_dir = tmp_path / "runs" / TASK_ID

        assert outcome.state["status"] == "done"
        assert executor.calls == [1, 2]
        # Round 1 never reached the model: the mechanical gate owned it.
        assert verifier.calls == [1, 2]
        assert verifier.answered == [2]
        assert [item["status"] for item in outcome.state["rounds"]] == [
            "verified_fail",
            "verified_pass",
        ]
        assert (run_dir / "DONE").is_file()

        assert "Round 1: 机检未通过：state-ok" in events
        assert "Round 1: FAIL (1 defect) → continue" in events
        assert "Round 2: PASS → DONE" in events
        report = (run_dir / "report.md").read_text(encoding="utf-8")
        assert "- R1: FAIL（M1）" in report
        assert "- R2: PASS" in report
    finally:
        cleanup_worktree(repo, repo / "wt" / f"{TASK_ID}-r1", f"ag/{TASK_ID}-r1")
        cleanup_worktree(repo, repo / "wt" / f"{TASK_ID}-r2", f"ag/{TASK_ID}-r2")


def test_fake_loop_stops_on_protected_path(tmp_path: Path) -> None:
    """The executor edits a guarded test file; the gate stops the run."""

    repo = make_repo(tmp_path)
    task = make_task(
        tmp_path,
        repo,
        check_run=STATE_CHECK,
        protected="protected:\n  - tests/test_keep.py\n",
    )

    def loosen(worktree: Path) -> None:
        (worktree / "state.txt").write_text("done\n", encoding="utf-8")
        (worktree / "tests" / "test_keep.py").write_text(
            "# loosened on purpose\n",
            encoding="utf-8",
        )

    executor = scripted_executor({1: loosen})
    verifier = scripted_verifier()

    try:
        outcome = start_task(
            task,
            runs_root=tmp_path / "runs",
            executor=executor,
            verifier=verifier,
            usage_provider=usage,
        )
        run_dir = tmp_path / "runs" / TASK_ID

        assert outcome.state["status"] == "blocked"
        assert outcome.blocked_code is not None
        assert outcome.blocked_code.value == "protected-path"
        assert executor.calls == [1]
        # The gate runs before the model, so nothing was ever asked.
        assert verifier.calls == [1]
        assert verifier.answered == []

        state = read_state(run_dir)
        assert state["terminal_code"] == "protected-path"
        assert state["rounds"][0]["blocked_code"] == "protected-path"
        assert (run_dir / "BLOCKED").is_file()

        report = (run_dir / "report.md").read_text(encoding="utf-8")
        assert "BLOCKED [protected-path]" in report
        assert "- R1: BLOCKED [protected-path]" in report
    finally:
        cleanup_worktree(repo, repo / "wt" / f"{TASK_ID}-r1", f"ag/{TASK_ID}-r1")


def test_fake_loop_stops_on_no_progress(tmp_path: Path) -> None:
    """Two rounds, the same check failing: the second round is not judged."""

    repo = make_repo(tmp_path)
    task = make_task(
        tmp_path,
        repo,
        check_run=ALWAYS_FAILS,
        check_name="always-fails",
    )
    executor = scripted_executor(
        {1: write_state_file("first"), 2: write_state_file("second")}
    )
    verifier = scripted_verifier()

    try:
        outcome = start_task(
            task,
            runs_root=tmp_path / "runs",
            executor=executor,
            verifier=verifier,
            usage_provider=usage,
        )
        run_dir = tmp_path / "runs" / TASK_ID

        assert outcome.state["status"] == "failed"
        assert "no-progress" in str(outcome.state["terminal_reason"])
        assert executor.calls == [1, 2]
        # The verifier ran twice; the model was never reached in either round.
        assert verifier.calls == [1, 2]
        assert verifier.answered == []

        rounds = outcome.state["rounds"]
        assert rounds[-1]["failure_reason"] == "no-progress"
        assert rounds[-1]["no_progress"] == {
            "previous_round": 1,
            "failing_checks": ["always-fails"],
            "previous_failing_checks": ["always-fails"],
        }
        assert (run_dir / "FAILED").is_file()

        report = (run_dir / "report.md").read_text(encoding="utf-8")
        assert "无净进展" in report
        assert "- R2: no-progress" in report
    finally:
        cleanup_worktree(repo, repo / "wt" / f"{TASK_ID}-r1", f"ag/{TASK_ID}-r1")
        cleanup_worktree(repo, repo / "wt" / f"{TASK_ID}-r2", f"ag/{TASK_ID}-r2")
