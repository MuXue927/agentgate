"""Full-text evidence for the verifier: the files a round actually changed.

Why a diff is not enough
------------------------
A diff shows what moved, not what the code now *means*. "This now calls
`parse_round_dir()`, which does not exist" or "this helper is called with three
arguments and returns two values" are invisible in a hunk: both sides of the
call can look locally plausible. The verifier had no way to see the file, so
those defects could only be guessed at.

Where the bytes come from
-------------------------
Not the worktree. The worktree can be dirty by the time verification runs
(`--force` re-checks, a human edit, a re-run the next day), and a verdict
assembled from *this* tree's diff plus *that* tree's file body is a verdict
about two different trees. Tracked files are therefore read from the round's
checkpoint commit — the same commit `diff.patch` was taken from. Only files
that exist nowhere in that commit fall back to the worktree, and those blocks
say so in their header.

Limits are byte budgets, not lines: a single minified bundle can be 400KB of
one line. Whatever is cut is reported in the input header, in `evidence.json`
and in `report.md` — a PASS given on partial evidence must be visible as such.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentgate.protected import FileChange

#: Per-file ceiling. Big enough for any hand-written module, small enough that
#: a stray lockfile cannot crowd out every other file in the bundle.
PER_FILE_LIMIT = 32 * 1024

#: Ceiling for the whole bundle. The verifier prompt is already a few KB of
#: instructions; 128KB of content is ~32k tokens, which stays well inside the
#: context of the models used here.
TOTAL_LIMIT = 128 * 1024

#: `git show` on a local object is fast; this only guards against a corrupted
#: repository blocking verification forever.
GIT_TIMEOUT_S = 30.0

SOURCE_COMMIT = "commit {sha}"
SOURCE_WORKTREE = "worktree (untracked)"


@dataclass(frozen=True)
class ContentBlock:
    """One changed file's text, plus where it came from and what was cut."""

    path: str
    source: str
    size: int
    kept: int
    body: str = ""
    omitted: str = ""

    @property
    def truncated(self) -> bool:
        return not self.omitted and self.kept < self.size

    def header(self) -> str:
        return f"### {self.path}  (source: {self.source})  ({self.size} bytes)"

    def as_document(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source": self.source,
            "size": self.size,
            "kept": self.kept,
            "truncated": self.truncated,
            "omitted": self.omitted,
        }


@dataclass(frozen=True)
class EvidencePack:
    blocks: tuple[ContentBlock, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    head_commit: str = ""

    @property
    def truncated(self) -> tuple[ContentBlock, ...]:
        return tuple(block for block in self.blocks if block.truncated)

    def truncation_lines(self) -> list[str]:
        """One line per cut file, worded identically everywhere it surfaces."""

        return [
            f"TRUNCATED: {block.path} (kept {block.kept}/{block.size} bytes)"
            for block in self.truncated
        ]

    def summary(self) -> str:
        total = sum(block.kept for block in self.blocks)
        source = f"commit {self.head_commit[:7]}" if self.head_commit else "worktree"
        text = f"{len(self.blocks)} 个文件全文，共 {total // 1024} KiB（来源 {source}）"
        if self.truncated:
            text += f"；{len(self.truncated)} 个文件被截断"
        if self.skipped:
            text += f"；{len(self.skipped)} 个文件未提供内容"
        return text

    def as_document(self) -> dict[str, Any]:
        return {
            "head_commit": self.head_commit,
            "per_file_limit": PER_FILE_LIMIT,
            "total_limit": TOTAL_LIMIT,
            "blocks": [block.as_document() for block in self.blocks],
            "skipped": [{"path": path, "reason": why} for path, why in self.skipped],
            "truncated": [
                f"{block.path} (kept {block.kept}/{block.size} bytes)"
                for block in self.truncated
            ],
        }


def collect_evidence(
    worktree: Path,
    head_commit: str | None,
    changes: Sequence[FileChange],
    *,
    per_file_limit: int = PER_FILE_LIMIT,
    total_limit: int = TOTAL_LIMIT,
) -> EvidencePack:
    """Read the changed files, newest-path-first, under the byte budgets.

    Deterministic: files are visited in path order and the budget is spent in
    that order, so two collections from the same commit produce identical
    output (the verifier input is regenerated on every `ag verify`).
    """

    blocks: list[ContentBlock] = []
    skipped: list[tuple[str, str]] = []
    remaining = total_limit

    for change in sorted(changes, key=lambda item: item.path):
        if change.code == "D":
            skipped.append((change.path, "deleted in this round"))
            continue

        data, source = _read_source(worktree, head_commit, change.path)
        if data is None:
            skipped.append((change.path, source))
            continue

        size = len(data)
        allow = min(per_file_limit, remaining)
        if allow <= 0:
            blocks.append(
                ContentBlock(
                    path=change.path,
                    source=source,
                    size=size,
                    kept=0,
                    omitted=f"<not included: total budget of {total_limit} bytes reached>",
                )
            )
            continue
        if b"\x00" in data[:8192]:
            blocks.append(
                ContentBlock(
                    path=change.path,
                    source=source,
                    size=size,
                    kept=0,
                    omitted=f"<binary: {size} bytes omitted>",
                )
            )
            continue

        kept = min(size, allow)
        body = data[:kept].decode("utf-8", errors="replace")
        remaining -= kept
        blocks.append(
            ContentBlock(
                path=change.path,
                source=source,
                size=size,
                kept=kept,
                body=body,
            )
        )

    return EvidencePack(
        blocks=tuple(blocks),
        skipped=tuple(skipped),
        head_commit=head_commit or "",
    )


def _read_source(
    worktree: Path,
    head_commit: str | None,
    path: str,
) -> tuple[bytes | None, str]:
    """Return (bytes, source label). ``(None, reason)`` when nothing was read."""

    if head_commit:
        try:
            completed = _git_bytes(worktree, ["show", f"{head_commit}:{path}"])
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is not None and completed.returncode == 0:
            return completed.stdout, SOURCE_COMMIT.format(sha=head_commit[:7])

    target = (worktree / path).resolve()
    try:
        target.relative_to(worktree.resolve())
    except ValueError:
        return None, "path escapes the worktree"
    if not target.is_file():
        return None, "not found in commit or worktree"
    try:
        return target.read_bytes(), SOURCE_WORKTREE
    except OSError as exc:
        return None, f"unreadable: {exc}"


def _git_bytes(
    cwd: Path,
    args: Sequence[str],
) -> subprocess.CompletedProcess[bytes]:
    """`git` without the text layer: binary detection needs the real bytes.

    Deliberately not `proc.run_bounded`: that wrapper exists for `.cmd` shims
    whose grandchildren hold our pipes. `git.exe` is a native binary with no
    such layer, and this call cannot stream output, so a plain bounded run is
    enough (the same choice `collector._run_git` makes).
    """

    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
