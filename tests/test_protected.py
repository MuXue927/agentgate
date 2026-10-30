from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentgate.adapters.codex import ErrorClass, RunResult
from agentgate.cli import app
from agentgate.collector import collect_success, utc_now
from agentgate.dryrun import prepare_dry_run
from agentgate.protected import (
    FileChange,
    evaluate_protected,
    parse_name_status,
)
from agentgate.report import render_report
from agentgate.spec import load_task
from agentgate.validation import evaluate_model_pair
from agentgate.verifier import verify_run
from agentgate.workspace import cleanup_worktree, create_worktree

GUARDED = ("tests/**", "tasks/**")


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
    (repo / "tests" / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )
    (repo / "tasks").mkdir()
    (repo / "tasks" / "spec.yaml").write_text("goal: fixture\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    return repo


def make_task(
    tmp_path: Path,
    repo: Path,
    *,
    protected: tuple[str, ...] | None,
    task_id: str = "protected-fixture",
    name: str = "task.yaml",
) -> Path:
    blocked = ""
    if protected is not None:
        rendered = "".join(f'  - "{item}"\n' for item in protected)
        blocked = f"protected:\n{rendered}"
    task = tmp_path / name
    task.write_text(
        f"""\
id: {task_id}
repo: {repo.as_posix()}
goal: Create hello.txt with content ok.
executor: {{harness: codex, model: deepseek-v4-flash}}
verifier: {{harness: codex, model: nvidia/nemotron-3.5-lightning:free}}
constraints:
  - Do not push.
{blocked}checks:
  - name: hello-content
    run: type hello.txt
    expect: {{exit: 0, stdout_contains: ok}}
budget: {{max_rounds: 1, max_minutes_per_round: 5, max_total_minutes: 10}}
human_approval: {{before_execute: false, before_merge: true}}
""",
        encoding="utf-8",
    )
    return task


@dataclass
class Fixture:
    repo: Path
    task_path: Path
    runs_root: Path
    worktree: Path
    task_id: str
    branch: str
    round_dir: Path


def prepare(tmp_path: Path, *, protected: tuple[str, ...] | None = GUARDED) -> Fixture:
    repo = make_repo(tmp_path)
    task_path = make_task(tmp_path, repo, protected=protected)
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
    return Fixture(
        repo=repo,
        task_path=task_path,
        runs_root=runs_root,
        worktree=worktree.path,
        task_id=task.id,
        branch=f"ag/{task.id}-r1",
        round_dir=round_dir,
    )


def model_pass() -> str:
    return json.dumps(
        {
            "verdict": "PASS",
            "summary": "The file matches the goal.",
            "checks": [{"name": "hello-content", "status": "PASS", "evidence": "ok"}],
            "defects": [],
        }
    )


def run_verify(fixture: Fixture, *, calls: list[tuple[str, ...]] | None = None):
    recorded = calls if calls is not None else []

    def runner(command: tuple[str, ...], cwd: Path, timeout: float, stdin_text: str):
        recorded.append(command)
        return subprocess.CompletedProcess(command, 0, model_pass(), "")

    outcome = verify_run(
        fixture.task_id,
        runs_root=fixture.runs_root,
        command_runner=runner,
    )
    return outcome, recorded


def test_protected_modification_blocks(tmp_path: Path) -> None:
    fixture = prepare(tmp_path)
    (fixture.worktree / "tests" / "test_sample.py").write_text(
        "def test_sample():\n    assert True  # loosened\n", encoding="utf-8"
    )
    calls: list[tuple[str, ...]] = []

    outcome, recorded = run_verify(fixture, calls=calls)

    assert outcome.verdict is None
    assert outcome.blocked_reason.startswith("protected-path-modified")
    assert "M tests/test_sample.py" in outcome.blocked_reason
    assert "tests/**" in outcome.blocked_reason
    assert recorded == []
    assert (fixture.round_dir / "protected.json").is_file()
    assert (fixture.runs_root / fixture.task_id / "BLOCKED").is_file()


def test_protected_deletion_blocks(tmp_path: Path) -> None:
    fixture = prepare(tmp_path)
    (fixture.worktree / "tasks" / "spec.yaml").unlink()

    outcome, recorded = run_verify(fixture)

    assert outcome.verdict is None
    assert outcome.blocked_reason.startswith("protected-path-modified")
    assert "D tasks/spec.yaml" in outcome.blocked_reason
    assert recorded == []


def test_protected_rename_blocks(tmp_path: Path) -> None:
    fixture = prepare(tmp_path)
    git(fixture.worktree, "mv", "tests/test_sample.py", "tests/test_moved.py")

    outcome, recorded = run_verify(fixture)

    assert outcome.verdict is None
    assert outcome.blocked_reason.startswith("protected-path-modified")
    # Judged on the deleted side, which is the path that disappeared.
    assert "tests/test_sample.py" in outcome.blocked_reason
    assert "R" in outcome.blocked_reason
    assert recorded == []


def test_protected_new_file_allowed(tmp_path: Path) -> None:
    fixture = prepare(tmp_path)
    added = fixture.worktree / "tests" / "test_added.py"
    added.write_text("def test_added():\n    assert True\n", encoding="utf-8")
    git(fixture.worktree, "add", "tests/test_added.py")

    outcome, recorded = run_verify(fixture)

    assert outcome.verdict is not None
    assert outcome.verdict["verdict"] == "PASS"
    assert outcome.protected is not None
    assert outcome.protected.ok
    assert outcome.protected.checked == 2  # A hello.txt + A tests/test_added.py
    assert len(recorded) == 1


def test_protected_absent_prints_none_declared(tmp_path: Path) -> None:
    fixture = prepare(tmp_path, protected=None)

    outcome, recorded = run_verify(fixture)

    assert outcome.protected is not None
    assert outcome.protected.declared == ()
    assert outcome.protected.summary_line() == "PROTECTED: none declared"
    assert len(recorded) == 1

    task = load_task(fixture.task_path)
    state = {
        "status": "done",
        "terminal_reason": "verification passed",
        "elapsed_seconds": 10,
        "tier": "★★",
        "rounds": [
            {
                "round": 1,
                "status": "verified_pass",
                "verdict": json.loads(model_pass()),
                "protected": outcome.protected.as_document(),
            }
        ],
    }
    report = render_report(task, state, repo=fixture.repo)
    assert "PROTECTED: none declared" in report

    dry_task = make_task(
        fixture.repo.parent,
        fixture.repo,
        protected=None,
        task_id="protected-dryrun",
        name="task-dry.yaml",
    )
    dry = prepare_dry_run(dry_task)
    try:
        assert "PROTECTED: none declared" in dry.render()
    finally:
        cleanup_worktree(fixture.repo, dry.worktree.path, dry.worktree.branch)


def test_protected_check_runs_before_mechanical(tmp_path: Path) -> None:
    """Loosened tests still exit 0; the BLOCKED must come before the gate."""

    fixture = prepare(tmp_path)
    exit_file = fixture.round_dir / "artifacts" / "01-hello-content.exit.txt"
    assert exit_file.read_text(encoding="utf-8").strip() == "0"

    (fixture.worktree / "tests" / "test_sample.py").write_text(
        "def test_sample():\n    pass\n", encoding="utf-8"
    )

    outcome, recorded = run_verify(fixture)

    assert outcome.verdict is None
    assert outcome.blocked_reason.startswith("protected-path-modified")
    assert recorded == []
    # The mechanical gate never ran, so it recorded nothing: proof of order.
    assert not (fixture.round_dir / "mechanical.json").exists()
    assert (fixture.round_dir / "protected.json").is_file()


def test_parse_name_status_handles_rename_and_quotes() -> None:
    changes = parse_name_status(
        'M\ttests/a.py\nA\tREADME.md\nR100\t"docs/旧 名.md"\ttests/new.md\nD\tx/y.txt\n'
    )

    assert changes == (
        FileChange(status="M", path="tests/a.py"),
        FileChange(status="A", path="README.md"),
        FileChange(status="R100", path="tests/new.md", source_path="docs/旧 名.md"),
        FileChange(status="D", path="x/y.txt"),
    )


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    (
        ("tests/**", "tests/test_a.py", True),
        ("tests/**", "tests/nested/test_a.py", True),
        ("tests/*.py", "tests/test_a.py", True),
        ("tests/*.py", "tests/nested/test_a.py", False),
        ("tasks/**", "src/tasks/x.py", False),
        ("**/*.md", "docs/a.md", True),
        ("schemas/task.schema.json", "schemas/task.schema.json", True),
    ),
)
def test_glob_semantics(pattern: str, path: str, expected: bool) -> None:
    result = evaluate_protected((pattern,), (FileChange(status="M", path=path),))

    assert result.ok is not expected


def test_added_files_are_never_violations() -> None:
    result = evaluate_protected(
        ("tests/**",),
        (
            FileChange(status="A", path="tests/test_new.py"),
            FileChange(status="M", path="src/app.py"),
        ),
    )

    assert result.ok
    assert result.checked == 2


def test_run_output_prints_protected_line() -> None:
    """`ag run` must show the PROTECTED verdict, not just the report."""

    document = evaluate_protected(
        GUARDED,
        (FileChange(status="M", path="tests/test_sample.py"),),
    ).as_document()
    outcome = SimpleNamespace(
        state={
            "status": "blocked",
            "rounds": [{"round": 1, "status": "blocked", "protected": document}],
            "elapsed_seconds": 2.0,
        },
        events=("Round 1: PROTECTED: 命中 M tests/test_sample.py",),
    )

    with patch("agentgate.cli.start_task", return_value=outcome):
        result = CliRunner().invoke(app, ["run", "templates/add-feature.task.yaml"])

    assert "PROTECTED: 命中 M tests/test_sample.py" in result.output
