from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agentgate.expectations import CheckFailure, MechanicalResult
from agentgate.orchestrator import _save
from agentgate.status import (
    StateError,
    create_state,
    load_state,
    read_state,
    update_state,
    write_state,
)
from agentgate.subject import Subject
from agentgate.verifier import VerificationError, _read_round_state, _record_no_progress

HEAD = "b" * 40


def seed_run(run_dir: Path) -> dict:
    """A run whose round 1 has been executed and not yet judged."""

    state = {
        "repo": "/tmp/target",
        "effective_max_rounds": 3,
        "status": "executed",
        "rounds": [
            {
                "round": 1,
                "status": "executed",
                "worktree": "/tmp/target/wt/x-r1",
                "branch": "ag/x-r1",
                "base_commit": "a" * 40,
                "head_commit": HEAD,
                "verdict": None,
                "defect_fingerprints": [],
            }
        ],
    }
    write_state(run_dir, state)
    return state


def failed_checks() -> MechanicalResult:
    return MechanicalResult(
        passed=(),
        failed=(
            CheckFailure(
                name="ci-runner",
                reason="exit 1",
                reproduce="pytest",
                expect="exit 0",
                actual="exit 1",
            ),
        ),
    )


def test_verifier_write_then_orchestrator_save_does_not_lose_fields(
    tmp_path: Path,
) -> None:
    """The P1-3 bug, reproduced by interleaving the two writers.

    The verifier records the comparison straight to disk; `ag run` then saves
    the snapshot it took before that happened. Writing the snapshot back
    replaces the round entry and drops every field this process never knew
    about -- `no_progress` in P1-3, `subject` here. The fix has to be in the
    write path, not in a list of fields to copy back by hand.
    """

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    memory = seed_run(run_dir)

    subject = Subject(
        base_commit="a" * 40,
        head_sha=HEAD,
        diff_sha256="c" * 64,
        changed_files=("hello.txt",),
    )
    detail = _record_no_progress(
        run_dir,
        1,
        "no-progress: round 1 fails the same 1 check(s) as round 0 (ci-runner)",
        previous_round=1,
        subject=subject,
        mechanical=failed_checks(),
        previous_failed=("ci-runner",),
    )

    # What `_drive` does at that moment: mark the round failed on its own
    # snapshot and save. It knows nothing about `no_progress` or `subject`.
    memory["rounds"][0]["status"] = "failed"
    memory["rounds"][0]["failure_reason"] = "no-progress"
    _save(run_dir, memory)

    entry = read_state(run_dir)["rounds"][0]
    assert entry["status"] == "failed"
    assert entry["no_progress"] == detail
    assert entry["subject"]["head_sha"] == HEAD


def test_write_state_is_atomic_on_crash(tmp_path: Path) -> None:
    """A crash at the rename leaves the previous document, not half of one."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_run(run_dir)
    before = (run_dir / "state.json").read_text(encoding="utf-8")

    state = read_state(run_dir)
    state["status"] = "done"
    with (
        patch("agentgate.status.os.replace", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        write_state(run_dir, state)

    assert (run_dir / "state.json").read_text(encoding="utf-8") == before
    assert read_state(run_dir)["status"] == "executed"


def test_state_has_no_temp_file_after_write(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_run(run_dir)
    update_state(run_dir, lambda state: state.update(status="running"))

    assert sorted(item.name for item in run_dir.iterdir()) == ["state.json"]


def test_two_writers_both_persist(tmp_path: Path) -> None:
    """Neither writer saw the other's document, and neither erases it."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_run(run_dir)

    update_state(run_dir, lambda state: state.update(final_branch="ag/x-r1"))
    update_state(
        run_dir,
        lambda state: state["rounds"][0].update(blocked_reason="from the verifier"),
    )

    state = read_state(run_dir)
    assert state["final_branch"] == "ag/x-r1"
    assert state["rounds"][0]["blocked_reason"] == "from the verifier"


def test_corrupt_state_is_reported_not_repaired(tmp_path: Path) -> None:
    """Both readers name the file and leave it alone."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    broken = run_dir / "state.json"
    broken.write_text('{"rounds": [', encoding="utf-8")

    with pytest.raises(StateError) as run_error:
        load_state(run_dir)
    assert str(broken) in str(run_error.value)

    round_state = run_dir / "round-1-state.json"
    round_state.write_text("not json at all", encoding="utf-8")
    with pytest.raises(VerificationError) as round_error:
        _read_round_state(round_state)
    assert str(round_state) in str(round_error.value)

    assert broken.read_text(encoding="utf-8") == '{"rounds": ['
    assert round_state.read_text(encoding="utf-8") == "not json at all"


def test_update_state_returns_mutator_result(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_run(run_dir)

    assert update_state(run_dir, lambda state: len(state["rounds"])) == 1
    assert update_state(run_dir, lambda state: state.update(status="done")) is None


def test_create_state_refuses_to_replace(tmp_path: Path) -> None:
    """Starting a run over an existing state is `resume --force`'s job."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_run(run_dir)

    with pytest.raises(StateError) as error:
        create_state(run_dir, {"repo": "/tmp/target"})

    assert "already exists" in str(error.value)
    assert read_state(run_dir)["status"] == "executed"


def test_save_of_one_round_keeps_another_rounds_fields(tmp_path: Path) -> None:
    """The merge locates the entry by `round` number, not by position."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    memory = seed_run(run_dir)

    def add_second_round(state: dict) -> None:
        state["rounds"].append({"round": 2, "status": "executed", "verdict": None})

    update_state(run_dir, add_second_round)
    memory["rounds"].append({"round": 2, "status": "verified_fail"})
    memory["rounds"][0]["subject"] = {"head_sha": HEAD}
    _save(run_dir, memory)

    rounds = read_state(run_dir)["rounds"]
    assert [entry["round"] for entry in rounds] == [1, 2]
    assert rounds[1]["status"] == "verified_fail"
    assert json.dumps(rounds[0]["subject"]) == json.dumps({"head_sha": HEAD})
