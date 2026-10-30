"""Protected paths: fail closed when the executor edits what it must not.

Why this is not just another `constraint`
-----------------------------------------
`constraints` is prose handed to the executor; nothing enforces it. The
mechanical gate (P0-1) compares `expect` against the *current* output, so it
cannot tell "the test still passes" from "the test was loosened until it
passes". Both look green.

So the gate is a whitelist of git statuses, not a diff heuristic:

======  ==========================================  ====================
status  meaning                                     verdict
======  ==========================================  ====================
``A``   new file                                    allowed
``M``   modified existing file                      BLOCKED if matched
``D``   deleted file                                BLOCKED if matched
``R``   renamed                                     BLOCKED if the deleted
                                                    side matches
``T``   type change (file <-> symlink)              BLOCKED if matched
``U``   unmerged                                    BLOCKED if matched
======  ==========================================  ====================

`A` must stay allowed: a self-improvement task has to be able to *add* tests.
Renaming is judged on the side that disappears, so moving a guarded file away
is caught, while a rename that merely lands inside a guarded directory is
treated like the new file it creates.

Matching is a whole-path glob: `*` does not cross `/`, `**` does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

BLOCKED_REASON_PREFIX = "protected-path-modified"

NONE_DECLARED_LINE = "PROTECTED: none declared"

#: Statuses that may still be reported when a path disappears or changes
#: shape. `A` is deliberately absent (see module docstring).
BLOCKING_STATUSES = frozenset({"M", "D", "R", "T", "U"})


@dataclass(frozen=True)
class FileChange:
    """One entry of ``git diff --name-status``."""

    status: str
    path: str
    source_path: str = ""

    @property
    def code(self) -> str:
        return self.status[:1].upper()


@dataclass(frozen=True)
class Violation:
    status: str
    path: str
    pattern: str

    def as_document(self) -> dict[str, str]:
        return {"status": self.status, "path": self.path, "pattern": self.pattern}


@dataclass(frozen=True)
class ProtectedResult:
    declared: tuple[str, ...]
    checked: int
    violations: tuple[Violation, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_document(self) -> dict[str, Any]:
        return {
            "declared": list(self.declared),
            "checked": self.checked,
            "ok": self.ok,
            "violations": [item.as_document() for item in self.violations],
        }

    @classmethod
    def from_document(cls, document: Any) -> ProtectedResult | None:
        if not isinstance(document, dict):
            return None
        declared = document.get("declared")
        if not isinstance(declared, list):
            return None
        violations = document.get("violations")
        items: list[Violation] = []
        if isinstance(violations, list):
            for entry in violations:
                if isinstance(entry, dict):
                    items.append(
                        Violation(
                            status=str(entry.get("status", "?")),
                            path=str(entry.get("path", "")),
                            pattern=str(entry.get("pattern", "")),
                        )
                    )
        checked = document.get("checked")
        return cls(
            declared=tuple(str(item) for item in declared),
            checked=int(checked) if isinstance(checked, int) else 0,
            violations=tuple(items),
        )

    def reason(self) -> str:
        """BLOCKED reason: every hit with its status and the rule that matched."""

        if self.ok:
            return ""
        shown = self.violations[:10]
        parts = [
            f"{item.status} {item.path} (matches {item.pattern})" for item in shown
        ]
        extra = len(self.violations) - len(shown)
        if extra > 0:
            parts.append(f"+{extra} more")
        return f"{BLOCKED_REASON_PREFIX}: " + "; ".join(parts)

    def summary_line(self) -> str:
        """One line for `report.md` / `ag verify`; never silent."""

        if not self.declared:
            return NONE_DECLARED_LINE
        if self.violations:
            names = "、".join(
                f"{item.status} {item.path}" for item in self.violations[:5]
            )
            extra = len(self.violations) - 5
            suffix = f" 等 {len(self.violations)} 处" if extra > 0 else ""
            return f"PROTECTED: 命中 {names}{suffix}"
        return (
            f"PROTECTED: {len(self.declared)} 条规则，{self.checked} 个改动路径，无命中"
        )


def describe_declared(declared: tuple[str, ...]) -> str:
    if not declared:
        return NONE_DECLARED_LINE
    return "PROTECTED: " + ", ".join(declared)


def parse_name_status(text: str) -> tuple[FileChange, ...]:
    """Parse ``git diff --name-status`` (optionally with rename detection)."""

    changes: list[FileChange] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()
        if status[:1] in {"R", "C"} and len(parts) >= 3:
            changes.append(
                FileChange(
                    status=status,
                    path=_unquote(parts[2]),
                    source_path=_unquote(parts[1]),
                )
            )
            continue
        changes.append(FileChange(status=status, path=_unquote(parts[1])))
    return tuple(changes)


def evaluate_protected(
    declared: tuple[str, ...] | list[str],
    changes: tuple[FileChange, ...] | list[FileChange],
) -> ProtectedResult:
    """Return every change that touches a guarded path."""

    patterns = tuple(str(item) for item in declared)
    changes_tuple = tuple(changes)
    if not patterns:
        return ProtectedResult(declared=patterns, checked=len(changes_tuple))

    compiled = tuple((pattern, _compile(pattern)) for pattern in patterns)
    violations: list[Violation] = []
    for change in changes_tuple:
        if change.code == "A":
            continue
        candidates = (change.path,)
        if change.code == "R" and change.source_path:
            # The deleted side is what must be judged: moving a guarded file
            # out of its directory is a deletion, not an addition.
            candidates = (change.source_path,)
        for candidate in candidates:
            for pattern, regex in compiled:
                if regex.fullmatch(_normalize(candidate)):
                    violations.append(
                        Violation(status=change.status, path=candidate, pattern=pattern)
                    )
                    break
    return ProtectedResult(
        declared=patterns,
        checked=len(changes_tuple),
        violations=tuple(violations),
    )


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _compile(pattern: str) -> re.Pattern[str]:
    """Whole-path glob: `*` stops at `/`, `**` does not."""

    normalized = _normalize(pattern)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts: list[str] = ["^"]
    index = 0
    while index < len(normalized):
        char = normalized[index]
        if char == "*":
            if normalized.startswith("**", index):
                parts.append(".*")
                index += 2
            else:
                parts.append("[^/]*")
                index += 1
            continue
        if char == "?":
            parts.append("[^/]")
            index += 1
            continue
        parts.append(re.escape(char))
        index += 1
    parts.append("$")
    return re.compile("".join(parts))
