from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_execution import result
from test_orchestrator import make_repo, make_task, usage
from test_verifier import completed, git, pass_json, prepare_run, write_run_state

from agentgate import execution
from agentgate.blocked import BlockedCode, blocked_line
from agentgate.orchestrator import start_task
from agentgate.report import render_report
from agentgate.spec import load_task
from agentgate.status import read_state
from agentgate.verifier import verify_run
from agentgate.workspace import cleanup_worktree

RUN = "verifier-fixture"
BRANCH = "ag/verifier-fixture-r1"


def _assert_coded(
    outcome,
    runs_root: Path,
    repo: Path,
    code: BlockedCode,
) -> None:
    """The outcome, the state on disk and the report must name the same cause."""

    assert outcome.blocked_code == code

    state = read_state(runs_root / RUN)
    assert state is not None
    assert state["status"] == "blocked"
    assert state["terminal_code"] == code.value
    assert state["rounds"][0]["blocked_code"] == code.value

    task = load_task(runs_root / RUN / "task.yaml")
    report = render_report(task, state, repo=repo)
    assert blocked_line(code, state["terminal_reason"]) in report


def _never_called(*args: object, **kwargs: object) -> object:
    raise AssertionError("the verifier must not be called")


def test_stale_subject_carries_stale_subject_code(tmp_path: Path) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    write_run_state(runs_root, worktree, rounds=1)
    # The round recorded one head; the tree then moves on. Judging the frozen
    # diff now would bind a verdict to a tree nobody will merge.
    git(worktree, "commit", "--allow-empty", "-m", "move head after collection")

    try:
        outcome = verify_run(RUN, runs_root=runs_root, command_runner=_never_called)
        _assert_coded(
            outcome, runs_root, tmp_path / "target", BlockedCode.STALE_SUBJECT
        )
        assert "stale-subject" in outcome.blocked_reason
    finally:
        cleanup_worktree(tmp_path / "target", worktree, BRANCH)


def test_protected_hit_carries_protected_path_code(tmp_path: Path) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    write_run_state(runs_root, worktree, rounds=1)
    # A tracked file modified by the round. (An added file is allowed on
    # purpose: `protected` guards against loosening what already exists.)
    (worktree / "README.md").write_text(
        "fixture\nloosened\n",
        encoding="utf-8",
    )
    # The run's own task snapshot is what `verify_round` loads.
    snapshot = runs_root / RUN / "task.yaml"
    snapshot.write_text(
        snapshot.read_text(encoding="utf-8").replace(
            "constraints:",
            "protected:\n  - README.md\nconstraints:",
        ),
        encoding="utf-8",
    )

    try:
        outcome = verify_run(RUN, runs_root=runs_root, command_runner=_never_called)
        _assert_coded(
            outcome,
            runs_root,
            tmp_path / "target",
            BlockedCode.PROTECTED_PATH,
        )
    finally:
        cleanup_worktree(tmp_path / "target", worktree, BRANCH)


def test_verifier_exit_nonzero_carries_backend_error_code(tmp_path: Path) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    write_run_state(runs_root, worktree, rounds=1)

    def failing(
        command: tuple[str, ...],
        cwd: Path,
        timeout: float,
        stdin_text: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, "", "backend exploded")

    try:
        outcome = verify_run(RUN, runs_root=runs_root, command_runner=failing)
        _assert_coded(
            outcome,
            runs_root,
            tmp_path / "target",
            BlockedCode.BACKEND_ERROR,
        )
        assert "backend exploded" in outcome.blocked_reason
    finally:
        cleanup_worktree(tmp_path / "target", worktree, BRANCH)


def test_verifier_says_blocked_carries_verifier_blocked_code(tmp_path: Path) -> None:
    runs_root, worktree = prepare_run(tmp_path)
    write_run_state(runs_root, worktree, rounds=1)

    def declining(
        command: tuple[str, ...],
        cwd: Path,
        timeout: float,
        stdin_text: str,
    ) -> subprocess.CompletedProcess[str]:
        document = json.loads(pass_json(runs_root))
        document["verdict"] = "BLOCKED"
        document["summary"] = "the evidence does not settle this"
        return completed(json.dumps(document))

    try:
        outcome = verify_run(RUN, runs_root=runs_root, command_runner=declining)
        assert outcome.verdict is not None
        assert outcome.verdict["verdict"] == "BLOCKED"
        _assert_coded(
            outcome,
            runs_root,
            tmp_path / "target",
            BlockedCode.VERIFIER_BLOCKED,
        )
    finally:
        cleanup_worktree(tmp_path / "target", worktree, BRANCH)


def test_no_changes_carries_no_changes_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real executor + real loop: an executor that changes nothing.

    Not a fake: the collector raises `NoChangesError`, `execute_round` turns it
    into a blocked outcome with a code, and the orchestrator relays that code
    into `terminal_code` instead of guessing one.
    """

    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo, max_rounds=2)
    monkeypatch.setattr(execution.runner, "run", lambda *a, **k: result())

    outcome = start_task(
        task,
        runs_root=tmp_path / "runs",
        executor=execution.execute_round,
        verifier=_never_called,
        usage_provider=usage,
    )
    run_dir = tmp_path / "runs" / "loop-fixture"
    try:
        assert outcome.blocked_code == BlockedCode.NO_CHANGES
        state = read_state(run_dir)
        assert state is not None
        assert state["status"] == "blocked"
        assert state["terminal_code"] == "no-changes"
        assert state["rounds"] == []

        assert blocked_line(
            BlockedCode.NO_CHANGES,
            state["terminal_reason"],
        ) in (run_dir / "report.md").read_text(encoding="utf-8")
        # The marker stays a human sentence, exactly as before.
        assert "no changes detected" in (run_dir / "BLOCKED").read_text(
            encoding="utf-8"
        )
    finally:
        cleanup_worktree(
            repo,
            repo / "wt" / "loop-fixture-r1",
            "ag/loop-fixture-r1",
        )


def test_unreadable_state_carries_code_without_repairing(tmp_path: Path) -> None:
    """The one ending that cannot record its code in the state it cannot read.

    The document stays exactly as it was found -- repairing it here would mean
    guessing which rounds happened, which is what batch I's `StateError` exists
    to prevent. The code reaches the caller and the marker instead.
    """

    runs_root, worktree = prepare_run(tmp_path)
    write_run_state(runs_root, worktree, rounds=1)
    run_dir = runs_root / RUN
    broken = run_dir / "state.json"
    broken.write_text('{"rounds": [', encoding="utf-8")

    try:
        outcome = verify_run(RUN, runs_root=runs_root, command_runner=_never_called)

        assert outcome.verdict is None
        assert outcome.blocked_code == BlockedCode.UNREADABLE_STATE
        assert str(broken) in outcome.blocked_reason
        assert broken.read_text(encoding="utf-8") == '{"rounds": ['
        assert (run_dir / "BLOCKED").is_file()
    finally:
        cleanup_worktree(tmp_path / "target", worktree, BRANCH)


def test_unreadable_state_carries_code_without_repairing(tmp_path: Path) -> None:
    """The one ending that cannot record its code in the state it cannot read.

    The document stays exactly as it was found -- repairing it here would mean
    guessing which rounds happened, which is what batch I's `StateError` exists
    to prevent. The code reaches the caller and the marker instead.
    """

    runs_root, worktree = prepare_run(tmp_path)
    write_run_state(runs_root, worktree, rounds=1)
    run_dir = runs_root / RUN
    broken = run_dir / "state.json"
    broken.write_text('{"rounds": [', encoding="utf-8")

    try:
        outcome = verify_run(RUN, runs_root=runs_root, command_runner=_never_called)

        assert outcome.verdict is None
        assert outcome.blocked_code == BlockedCode.UNREADABLE_STATE
        assert str(broken) in outcome.blocked_reason
        assert broken.read_text(encoding="utf-8") == '{"rounds": ['
        assert (run_dir / "BLOCKED").is_file()
    finally:
        cleanup_worktree(tmp_path / "target", worktree, BRANCH)
