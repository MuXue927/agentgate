from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentgate.adapters.codex import ErrorClass, RunResult
from agentgate.collector import CheckOutput, CollectionResult
from agentgate.execution import ExecutionOutcome, build_defect_prompt
from agentgate.orchestrator import (
    OrchestratorError,
    Usage,
    defect_fingerprint,
    resume_task,
    start_task,
)
from agentgate.spec import load_task
from agentgate.status import MARKERS
from agentgate.validation import evaluate_model_pair
from agentgate.verifier import VerificationOutcome
from agentgate.workspace import cleanup_worktree, create_worktree


class SimulatedKill(BaseException):
    pass


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
    max_rounds: int = 3,
    max_total_minutes: int = 10,
) -> Path:
    task = tmp_path / "task.yaml"
    task.write_text(
        f"""\
id: loop-fixture
repo: {repo.as_posix()}
goal: Create files across rounds.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints:
  - Do not modify unrelated files.
  - Do not push.
checks:
  - name: fixture
    run: echo ok
    expect: {{exit: 0}}
budget: {{max_rounds: {max_rounds}, max_minutes_per_round: 5, max_total_minutes: {max_total_minutes}}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


def verdict(kind: str, *, defect: dict[str, str] | None = None) -> dict:
    defects = [defect] if defect else []
    return {
        "verdict": kind,
        "summary": "fixture verdict",
        "checks": [
            {
                "name": "fixture",
                "status": "PASS" if kind == "PASS" else "FAIL",
                "evidence": "fixture",
            }
        ],
        "defects": defects,
    }


def defect(where: str = "a.txt", why: str = "a.txt is wrong") -> dict[str, str]:
    return {
        "id": "D1",
        "severity": "blocking",
        "where": where,
        "why": why,
        "reproduce": "type a.txt",
        "expect": "right",
        "actual": "wrong",
    }


class FakeRoundTools:
    def __init__(
        self,
        repo: Path,
        runs_root: Path,
        *,
        verdicts: list[dict],
        fail_on_execution: int | None = None,
        fail_on_verification: int | None = None,
    ) -> None:
        self.repo = repo
        self.runs_root = runs_root
        self.verdicts = verdicts
        self.fail_on_execution = fail_on_execution
        self.fail_on_verification = fail_on_verification
        self.execution_calls: list[dict] = []
        self.verification_calls: list[int] = []

    def executor(self, task_path: Path, **kwargs: object) -> ExecutionOutcome:
        self.execution_calls.append(kwargs)
        round_no = int(kwargs["round_no"])
        if self.fail_on_execution == round_no:
            raise SimulatedKill("simulated kill")
        base_ref = kwargs.get("base_ref")
        task = load_task(task_path)
        worktree = create_worktree(
            self.repo,
            task.id,
            round_no=round_no,
            base_ref=str(base_ref) if base_ref else None,
        )
        (worktree.path / f"round-{round_no}.txt").write_text(
            f"round {round_no}\n",
            encoding="utf-8",
        )
        round_dir = self.runs_root / task.id / f"round-{round_no}"
        round_dir.mkdir(parents=True)
        (round_dir / "diff.patch").write_text(
            f"round {round_no}\n",
            encoding="utf-8",
        )
        prompt = (
            build_defect_prompt(
                task,
                list(kwargs["defects"]),
                previous_round_dir=Path(kwargs["previous_round_dir"]),
            )
            if kwargs.get("defects")
            else "initial prompt"
        )
        state = {
            "worktree": str(worktree.path),
            "branch": worktree.branch,
            "base_commit": worktree.base_commit,
            "executor": {"exit": 0},
        }
        return ExecutionOutcome(
            task=task,
            pair=evaluate_model_pair(task.executor.model, task.verifier.model),
            collection=CollectionResult(
                round_dir=round_dir,
                state=state,
                checks=(CheckOutput(task.checks[0], 0, "ok", "", 0.0),),
            ),
            result=RunResult(0, "", "", 0.01, None, None, ErrorClass.OK),
            prompt=prompt,
            blocked=False,
        )

    def verifier(self, task_id: str, **kwargs: object) -> VerificationOutcome:
        round_no = int(kwargs["round_no"])
        self.verification_calls.append(round_no)
        if self.fail_on_verification == round_no:
            raise SimulatedKill("simulated verification kill")
        result = self.verdicts[round_no - 1]
        round_dir = self.runs_root / task_id / f"round-{round_no}"
        (round_dir / "verdict.json").write_text(
            json.dumps(result) + "\n",
            encoding="utf-8",
        )
        return VerificationOutcome(
            task_id=task_id,
            round_dir=round_dir,
            verdict=result,
            attempts=(),
            before_status="",
            after_status="",
        )


def usage() -> Usage:
    return Usage(total_tokens=100, cost_usd=0.01)


def test_two_round_fail_then_pass_creates_done_report_and_exclusive_marker(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)
    tools = FakeRoundTools(
        repo,
        tmp_path / "runs",
        verdicts=[verdict("FAIL", defect=defect()), verdict("PASS")],
    )

    outcome = start_task(
        task,
        runs_root=tmp_path / "runs",
        executor=tools.executor,
        verifier=tools.verifier,
        usage_provider=usage,
    )
    run_dir = tmp_path / "runs" / "loop-fixture"
    try:
        assert outcome.state["status"] == "done"
        assert len(tools.execution_calls) == 2
        assert tools.execution_calls[1]["base_ref"] == "ag/loop-fixture-r1"
        assert [name for name in MARKERS if (run_dir / name).exists()] == ["DONE"]
        assert (run_dir / "report.md").is_file()
        report = (run_dir / "report.md").read_text(encoding="utf-8")
        assert "DONE" in report.splitlines()[0]
        assert "双盲 ★★" in report.splitlines()[0]
        assert "## 结论" in report
        prompt = (run_dir / "round-2" / "executor_prompt.txt").read_text(
            encoding="utf-8"
        )
        assert "Do not modify unrelated files." in prompt
        assert "a.txt is wrong" in prompt
        assert (run_dir / "round-1" / "diff.patch").exists()
        assert "Fixture" not in prompt
    finally:
        for number in (1, 2):
            cleanup_worktree(
                repo,
                repo / "wt" / f"loop-fixture-r{number}",
                f"ag/loop-fixture-r{number}",
            )


def test_rounds_override_one_ends_failed_without_retry(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo, max_rounds=3)
    tools = FakeRoundTools(
        repo,
        tmp_path / "runs",
        verdicts=[verdict("FAIL", defect=defect())],
    )
    try:
        outcome = start_task(
            task,
            max_rounds=1,
            runs_root=tmp_path / "runs",
            executor=tools.executor,
            verifier=tools.verifier,
            usage_provider=usage,
        )

        assert outcome.state["status"] == "failed"
        assert len(tools.execution_calls) == 1
        assert (tmp_path / "runs" / "loop-fixture" / "FAILED").is_file()
    finally:
        cleanup_worktree(
            repo,
            repo / "wt" / "loop-fixture-r1",
            "ag/loop-fixture-r1",
        )


def test_same_defect_fingerprint_stops_at_round_two(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo, max_rounds=5)
    first = defect(why="A.txt   IS WRONG")
    second = defect(why=" a.txt is wrong ")
    tools = FakeRoundTools(
        repo,
        tmp_path / "runs",
        verdicts=[verdict("FAIL", defect=first), verdict("FAIL", defect=second)],
    )
    try:
        outcome = start_task(
            task,
            runs_root=tmp_path / "runs",
            executor=tools.executor,
            verifier=tools.verifier,
            usage_provider=usage,
        )

        assert outcome.state["status"] == "failed"
        assert len(tools.execution_calls) == 2
        reason = outcome.state["terminal_reason"]
        assert "same defect persisted" in reason
        assert defect_fingerprint(first) in reason
        assert "R1 actual=wrong" in reason
        assert "R2 actual=wrong" in reason
    finally:
        for number in (1, 2):
            cleanup_worktree(
                repo,
                repo / "wt" / f"loop-fixture-r{number}",
                f"ag/loop-fixture-r{number}",
            )


def test_resume_after_interrupted_round_two_does_not_repeat_round_one(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo, max_rounds=3)
    tools = FakeRoundTools(
        repo,
        tmp_path / "runs",
        verdicts=[verdict("FAIL", defect=defect()), verdict("PASS")],
        fail_on_execution=2,
    )
    try:
        with pytest.raises(SimulatedKill):
            start_task(
                task,
                runs_root=tmp_path / "runs",
                executor=tools.executor,
                verifier=tools.verifier,
                usage_provider=usage,
            )
        assert [call["round_no"] for call in tools.execution_calls] == [1, 2]

        tools.fail_on_execution = None
        resumed = resume_task(
            "loop-fixture",
            runs_root=tmp_path / "runs",
            executor=tools.executor,
            verifier=tools.verifier,
            usage_provider=usage,
        )

        assert resumed.state["status"] == "done"
        assert [call["round_no"] for call in tools.execution_calls] == [1, 2, 2]
        assert tools.verification_calls == [1, 2]
    finally:
        for number in (1, 2):
            cleanup_worktree(
                repo,
                repo / "wt" / f"loop-fixture-r{number}",
                f"ag/loop-fixture-r{number}",
            )


def test_terminal_resume_requires_force(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)
    tools = FakeRoundTools(
        repo,
        tmp_path / "runs",
        verdicts=[verdict("PASS")],
    )
    try:
        start_task(
            task,
            runs_root=tmp_path / "runs",
            executor=tools.executor,
            verifier=tools.verifier,
            usage_provider=usage,
        )

        with pytest.raises(OrchestratorError, match="already terminal"):
            resume_task(
                "loop-fixture",
                runs_root=tmp_path / "runs",
                executor=tools.executor,
                verifier=tools.verifier,
                usage_provider=usage,
            )
    finally:
        cleanup_worktree(
            repo,
            repo / "wt" / "loop-fixture-r1",
            "ag/loop-fixture-r1",
        )


def test_resume_verifies_executed_round_without_reexecution(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo)
    tools = FakeRoundTools(
        repo,
        tmp_path / "runs",
        verdicts=[verdict("PASS")],
        fail_on_verification=1,
    )
    try:
        with pytest.raises(SimulatedKill):
            start_task(
                task,
                runs_root=tmp_path / "runs",
                executor=tools.executor,
                verifier=tools.verifier,
                usage_provider=usage,
            )
        state = json.loads(
            (tmp_path / "runs" / "loop-fixture" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["rounds"][-1]["status"] == "executed"
        assert len(tools.execution_calls) == 1

        tools.fail_on_verification = None
        outcome = resume_task(
            "loop-fixture",
            runs_root=tmp_path / "runs",
            executor=tools.executor,
            verifier=tools.verifier,
            usage_provider=usage,
        )

        assert outcome.state["status"] == "done"
        assert len(tools.execution_calls) == 1
        assert tools.verification_calls == [1, 1]
    finally:
        cleanup_worktree(
            repo,
            repo / "wt" / "loop-fixture-r1",
            "ag/loop-fixture-r1",
        )


def test_total_budget_exceeded_blocks(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path)
    task = make_task(tmp_path, repo, max_total_minutes=10)
    tools = FakeRoundTools(
        repo,
        tmp_path / "runs",
        verdicts=[verdict("PASS")],
    )
    ticks = iter((0.0, 601.0))
    try:
        outcome = start_task(
            task,
            runs_root=tmp_path / "runs",
            executor=tools.executor,
            verifier=tools.verifier,
            usage_provider=usage,
            clock=lambda: next(ticks),
        )

        assert outcome.state["status"] == "blocked"
        assert "max_total_minutes exceeded" in outcome.state["terminal_reason"]
    finally:
        cleanup_worktree(
            repo,
            repo / "wt" / "loop-fixture-r1",
            "ag/loop-fixture-r1",
        )
