from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from agentgate import __version__
from agentgate.collector import CollectorError
from agentgate.doctor import collect_report, render_report
from agentgate.dryrun import prepare_dry_run
from agentgate.orchestrator import (
    OrchestratorError,
    RunOutcome,
    resume_task,
    start_task,
)
from agentgate.report import write_report
from agentgate.spec import PROJECT_ROOT, SpecError, load_task
from agentgate.status import read_state
from agentgate.validation import ModelPairError
from agentgate.verifier import VerificationError, verify_run
from agentgate.workspace import WorkspaceError, clean_agent_worktrees


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_configure_utf8_stdio()

app = typer.Typer(
    name="ag",
    no_args_is_help=True,
    add_completion=False,
    help="Single-purpose Codex execution and read-only verification loop.",
)


@app.command()
def doctor(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Print raw optional-backend output.",
    ),
) -> None:
    """Probe execution and verification backends."""

    report = collect_report(Path.cwd())
    typer.echo(render_report(report, verbose=verbose))
    if not report.required_ok:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the AgentGate version."""

    typer.echo(f"AgentGate {__version__}")


@app.command()
def run(
    task: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to task.yaml.",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Validate, create the round-1 worktree, and print commands only.",
        ),
    ] = False,
    allow_self: Annotated[
        bool,
        typer.Option(
            "--allow-self",
            help="Allow the AgentGate repository as the target.",
        ),
    ] = False,
    rounds: Annotated[
        int | None,
        typer.Option(
            "--rounds",
            min=1,
            help="Override the task's max_rounds for this run.",
        ),
    ] = None,
) -> None:
    """Execute, verify, and iterate until a terminal state or the round limit."""

    try:
        if dry_run:
            result = prepare_dry_run(task, allow_self=allow_self)
        else:
            outcome = start_task(
                task,
                max_rounds=rounds,
                allow_self=allow_self,
            )
    except (
        SpecError,
        ModelPairError,
        WorkspaceError,
        CollectorError,
        OrchestratorError,
        ValueError,
    ) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    if dry_run:
        typer.echo(result.render())
        return
    _print_outcome(outcome)
    code = {"done": 0, "failed": 1, "blocked": 2}.get(
        str(outcome.state.get("status")),
        2,
    )
    raise typer.Exit(code=code)


@app.command()
def clean(
    task_id: Annotated[
        str | None,
        typer.Argument(help="Task id; omit when using --all."),
    ] = None,
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Clean every wt/ worktree and ag/* branch."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Delete dirty AgentGate worktrees."),
    ] = False,
) -> None:
    """Remove AgentGate worktrees and ag/* branches safely."""

    try:
        result = clean_agent_worktrees(
            Path.cwd(),
            task_id=task_id,
            all_tasks=all_tasks,
            force=force,
        )
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    if not result.worktrees and not result.branches:
        typer.echo("无残留")
        return
    for path in result.worktrees:
        typer.echo(f"removed worktree: {path}")
    for branch in result.branches:
        typer.echo(f"deleted branch: {branch}")


@app.command()
def verify(
    task_id: Annotated[
        str,
        typer.Argument(help="Run / task id under runs/."),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-run verification even when the run already passed.",
        ),
    ] = False,
) -> None:
    """Verify an executed round once, without defect feedback or retry rounds."""

    try:
        outcome = verify_run(task_id, force=force)
    except (VerificationError, SpecError, ModelPairError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    if outcome.verdict is None:
        typer.echo(f"BLOCKED: {outcome.blocked_reason}", err=True)
        raise typer.Exit(code=2)

    verdict = outcome.verdict
    mechanical = getattr(outcome, "mechanical", None)
    if mechanical is not None:
        if mechanical.ok:
            typer.echo(f"机检: {mechanical.total}/{mechanical.total} 通过")
        else:
            details = "；".join(
                f"{item.name}（{item.reason}）" for item in mechanical.failed
            )
            typer.echo(f"机检未通过：{details}")
    if mechanical is not None and not mechanical.ok:
        typer.echo(f"Verdict: {verdict['verdict']}（机检，未调用验收官）")
    else:
        typer.echo(
            f"Verdict: {verdict['verdict']}（验收官）— {verdict.get('summary', '')}"
        )
    for defect in verdict.get("defects", []):
        typer.echo(
            f"{defect['id']} [{defect['severity']}] {defect['where']}: {defect['why']}"
        )
        typer.echo(f"  reproduce: {defect['reproduce']}")
        typer.echo(f"  expect: {defect['expect']}")
        typer.echo(f"  actual: {defect['actual']}")


def _print_outcome(outcome: RunOutcome) -> None:
    for event in outcome.events:
        typer.echo(event)
    state = outcome.state
    rounds = state.get("rounds") or []
    last = rounds[-1] if rounds else {}
    mechanical = last.get("mechanical") if isinstance(last, dict) else None
    verdict = last.get("verdict") if isinstance(last, dict) else None
    mechanical_failed = isinstance(mechanical, dict) and bool(mechanical.get("failed"))
    if isinstance(mechanical, dict):
        if mechanical_failed:
            details = "；".join(
                f"{item.get('name')}（{item.get('reason')}）"
                for item in mechanical.get("failed") or []
            )
            typer.echo(f"机检未通过：{details}")
        else:
            total = len(mechanical.get("passed") or [])
            typer.echo(f"机检: {total}/{total} 通过")
    if isinstance(verdict, dict):
        if mechanical_failed:
            typer.echo(f"Verdict: {verdict.get('verdict')}（机检，未调用验收官）")
        else:
            typer.echo(
                f"Verdict: {verdict.get('verdict')}（验收官）"
                f"— {verdict.get('summary', '')}"
            )
    typer.echo(
        f"{str(state.get('status', 'unknown')).upper()} + "
        f"{len(state.get('rounds', []))} 轮 + "
        f"{float(state.get('elapsed_seconds', 0)):.1f}s"
    )
    if state.get("status") == "done" and state.get("final_branch"):
        typer.echo(f"Branch: {state['final_branch']}")
        typer.echo(f"Merge: git merge --no-ff {state['final_branch']}")


@app.command()
def resume(
    task_id: Annotated[
        str,
        typer.Argument(help="Task id under runs/."),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Discard a terminal run and start it again.",
        ),
    ] = False,
    allow_self: Annotated[
        bool,
        typer.Option(
            "--allow-self",
            help="Permit a task whose repo is AgentGate itself.",
        ),
    ] = False,
) -> None:
    """Resume from the next unfinished round."""

    try:
        outcome = resume_task(task_id, force=force, allow_self=allow_self)
    except (
        OrchestratorError,
        SpecError,
        ModelPairError,
        WorkspaceError,
        CollectorError,
        ValueError,
    ) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    _print_outcome(outcome)
    code = {"done": 0, "failed": 1, "blocked": 2}.get(
        str(outcome.state.get("status")),
        2,
    )
    raise typer.Exit(code=code)


@app.command()
def report(
    task_id: Annotated[
        str,
        typer.Argument(help="Task id under runs/."),
    ],
) -> None:
    """Regenerate report.md from an existing run without model calls."""

    run_dir = PROJECT_ROOT / "runs" / task_id
    task_path = run_dir / "task.yaml"
    if not task_path.is_file():
        typer.echo(f"Task snapshot not found: {task_path}", err=True)
        raise typer.Exit(code=1)
    state = read_state(run_dir)
    if state is None:
        typer.echo(f"Run state not found: {run_dir / 'state.json'}", err=True)
        raise typer.Exit(code=1)
    task = load_task(task_path)
    path = write_report(task, state, run_dir=run_dir, repo=task.repo)
    typer.echo(str(path))


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
