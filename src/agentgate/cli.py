from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from agentgate import __version__
from agentgate.doctor import collect_report, render_report
from agentgate.dryrun import prepare_dry_run
from agentgate.spec import SpecError
from agentgate.validation import ModelPairError
from agentgate.workspace import WorkspaceError


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
) -> None:
    """Prepare an AgentGate task."""

    if not dry_run:
        typer.echo(
            "Real execution is not implemented in step 2; use --dry-run.",
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        result = prepare_dry_run(task)
    except (SpecError, ModelPairError, WorkspaceError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(result.render())


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
