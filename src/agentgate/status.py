from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MARKERS = ("EXECUTED", "DONE", "FAILED", "BLOCKED")

# Keys a finished round cannot run without: `rounds` decides which round comes
# next, `effective_max_rounds` decides when to stop, `repo` decides where the
# worktree lives. A document missing any of them is not a run to resume, it is
# a file somebody else wrote.
REQUIRED_KEYS = ("repo", "effective_max_rounds", "rounds")


class StateError(RuntimeError):
    """`runs/<id>/state.json` is missing, unreadable, or incomplete.

    Never repaired silently. A state that cannot be read is a state whose
    rounds are unknown, and guessing would either re-run a round that already
    spent budget or re-verify a tree nobody merged.
    """


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def _stamp(state: dict[str, Any]) -> None:
    # Same format `collector.utc_now` writes into the round artifacts.
    state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")


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
    """The run state, or None when the run never wrote one.

    Corrupt JSON raises instead of returning None: "there is no state" and
    "there is a state nobody can read" lead to completely different actions.
    """

    path = _state_path(run_dir)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Unreadable run state: {path} ({exc})") from exc
    if not isinstance(document, dict):
        raise StateError(f"Run state root must be an object: {path}")
    return document


def load_state(
    run_dir: Path,
    *,
    require: tuple[str, ...] = REQUIRED_KEYS,
) -> dict[str, Any]:
    """Read the run state and refuse to continue on a partial document.

    `require` is a parameter because the two readers need different keys:
    resuming drives rounds (`repo` included), while `ag verify` only reads the
    round entries.
    """

    path = _state_path(run_dir)
    state = read_state(run_dir)
    if state is None:
        raise StateError(f"Run state not found: {path}")
    missing = [key for key in require if key not in state]
    if missing:
        raise StateError(
            f"Incomplete run state: {path} is missing {', '.join(missing)}"
        )
    return state


def create_state(run_dir: Path, state: dict[str, Any]) -> None:
    """Write the first version of the run state; refuse to replace one.

    The only writer that may start from nothing. Starting a run over an
    existing state is what `ag resume --force` archives explicitly, never a
    thing a stray call may do.
    """

    path = _state_path(run_dir)
    if path.exists():
        raise StateError(f"Run state already exists: {path}")
    write_state(run_dir, state)


def write_state(run_dir: Path, state: dict[str, Any]) -> None:
    """Atomically replace runs/<id>/state.json.

    Writes a sibling temp file and os.replace()s it into place, so a crash
    mid-write leaves the previous state intact instead of a truncated file.
    The temp file lives in the same directory on purpose: os.replace is only
    atomic within a filesystem, and /tmp is often a different one.

    Not meant to be called from feature code -- use `update_state` or
    `create_state`, which keep a single writer per document version.
    """

    path = _state_path(run_dir)
    document = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(
        dir=run_dir,
        prefix=".state-",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def update_state(run_dir: Path, mutate: Callable[[dict[str, Any]], Any]) -> Any:
    """Read the newest state from disk, apply mutate(), write it back atomically.

    Every writer must go through here. Holding a dict in memory and calling
    write_state() later loses whatever another writer committed in between --
    that is exactly how the P1-3 `no_progress` detail got overwritten.

    The document is written back even when mutate() changed nothing: skipping
    the write would need a "did you change anything" protocol, and an
    unchanged atomic replace costs nothing.
    """

    state = read_state(run_dir)
    if state is None:
        raise StateError(f"Run state not found: {_state_path(run_dir)}")
    result = mutate(state)
    _stamp(state)
    write_state(run_dir, state)
    return result


def set_status(
    run_dir: Path,
    status: str,
    *,
    marker: str | None = None,
    marker_content: str = "",
) -> dict[str, Any]:
    path = _state_path(run_dir)
    if path.is_file():
        state = update_state(run_dir, _status_setter(status))
    else:
        # No document to update. The marker is what an operator reads at 2am,
        # so it is still written; inventing a state document here would make a
        # run look resumable when nothing about its rounds is known.
        state = {"status": status}
    if marker is not None:
        write_marker(run_dir, marker, marker_content)
    return state


def _status_setter(status: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        state["status"] = status
        return state

    return mutate
