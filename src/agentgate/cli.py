from __future__ import annotations

import sys
from pathlib import Path

import typer

from agentgate import __version__
from agentgate.doctor import collect_report, render_report


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
def doctor() -> None:
    """Probe execution and verification backends."""

    report = collect_report(Path.cwd())
    typer.echo(render_report(report))
    if not report.required_ok:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the AgentGate version."""

    typer.echo(f"AgentGate {__version__}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
