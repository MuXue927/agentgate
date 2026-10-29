from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MARKERS = ("EXECUTED", "DONE", "FAILED", "BLOCKED")


def write_marker(run_dir: Path, marker: str, content: str = "") -> None:
    if marker not in MARKERS:
        raise ValueError(f"Unknown run marker: {marker}")
    for name in MARKERS:
        stale = run_dir / name
        if stale.exists():
            stale.unlink()
    (run_dir / marker).write_text(content, encoding="utf-8")


def clear_markers(run_dir: Path) -> None:
    for name in MARKERS:
        stale = run_dir / name
        if stale.exists():
            stale.unlink()


def read_state(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "state.json"
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"Run state root must be an object: {path}")
    return document


def write_state(run_dir: Path, state: dict[str, Any]) -> None:
    (run_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def set_status(
    run_dir: Path,
    status: str,
    *,
    marker: str | None = None,
    marker_content: str = "",
) -> dict[str, Any]:
    state = read_state(run_dir) or {}
    state["status"] = status
    write_state(run_dir, state)
    if marker is not None:
        write_marker(run_dir, marker, marker_content)
    return state
