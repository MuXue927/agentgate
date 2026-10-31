from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The identity of the evidence a verdict is about.
#
# A verdict used to be a bare opinion: nothing tied it to the tree it judged, so
# `ag verify --force` could re-ask the model about a frozen diff while the
# worktree had moved on, and the answer still looked authoritative. The subject
# is computed from artifacts and git state only -- never from the executor's
# claims -- and it is written before the model is called, echoed back by the
# model, and recomputed afterwards.

SUBJECT_FIELDS = ("base_commit", "head_sha", "diff_sha256", "changed_files")


@dataclass(frozen=True)
class Subject:
    base_commit: str
    head_sha: str
    diff_sha256: str
    changed_files: tuple[str, ...]

    def as_document(self) -> dict[str, Any]:
        return {
            "base_commit": self.base_commit,
            "head_sha": self.head_sha,
            "diff_sha256": self.diff_sha256,
            "changed_files": list(self.changed_files),
        }

    def digest(self) -> str:
        canonical = json.dumps(self.as_document(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unquote(raw: str) -> str:
    """Undo git's porcelain quoting for the paths that need it."""

    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        body = raw[1:-1]
        return body.replace('\\"', '"').replace("\\\\", "\\")
    return raw


def parse_changed_paths(status_text: str) -> tuple[str, ...]:
    """Sorted paths from a `git status --porcelain` block.

    Rename entries read `R  old -> new`; the new path is what a reader opens,
    so that is the one recorded. Directories of untracked files appear as a
    single `dir/` entry -- collection runs `git add -A` first, so in practice
    the round artifacts list files rather than directories.
    """

    paths: list[str] = []
    for line in status_text.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = _unquote(path)
        if path:
            paths.append(path)
    return tuple(sorted(paths))


def compute_subject(
    *,
    base_commit: str | None,
    head_sha: str,
    patch_path: Path,
    status_text: str,
) -> Subject:
    return Subject(
        base_commit=base_commit or "",
        head_sha=head_sha,
        diff_sha256=hashlib.sha256(patch_path.read_bytes()).hexdigest(),
        changed_files=parse_changed_paths(status_text),
    )


def _same_revision(expected: str, actual: str) -> bool:
    """Prefix-tolerant comparison.

    Models routinely truncate a 40-character hash; a truncated echo is a
    transcription slip, not a different subject. A genuinely different commit
    never shares a 7-character prefix in practice.
    """

    left = expected.strip().lower()
    right = actual.strip().lower()
    if not left and not right:
        return True
    if not left or not right:
        return False
    return left.startswith(right) or right.startswith(left)


def subject_mismatch(expected: Subject, candidate: Any) -> str:
    """Empty string when the verdict speaks about the evidence we collected.

    Otherwise a one-line description naming the first field that disagrees,
    because "subject mismatch" alone tells a reader nothing.
    """

    if not isinstance(candidate, dict):
        return "verdict.subject is missing"
    problems: list[str] = []
    for field in ("base_commit", "head_sha", "diff_sha256"):
        missing = candidate.get(field)
        if not isinstance(missing, str):
            problems.append(f"verdict.subject.{field}: missing")
            continue
        if not _same_revision(str(getattr(expected, field)), missing):
            problems.append(
                f"verdict.subject.{field}: expected {getattr(expected, field)}, "
                f"verdict says {missing}"
            )
    echoed_files = candidate.get("changed_files")
    if not isinstance(echoed_files, list):
        problems.append("verdict.subject.changed_files: missing")
    else:
        expected_files = sorted(expected.changed_files)
        actual_files = sorted(
            str(item).strip() for item in echoed_files if str(item).strip()
        )
        if expected_files != actual_files:
            problems.append(
                "verdict.subject.changed_files: expected "
                f"{expected_files}, verdict says {actual_files}"
            )
    return "; ".join(problems)
