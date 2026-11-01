from __future__ import annotations

from enum import StrEnum


class BlockedCode(StrEnum):
    """Why a run stopped without a verdict.

    `BLOCKED` used to say one thing and mean six. A backend that died, a model
    that answered with prose, and a verifier that judged the evidence
    insufficient all ended the same way, so a reader could not tell "retry
    later" from "the change is unjudgeable" -- and neither could a script.

    One code per cause, no free-form values: a producer that has not decided
    which cause it hit does not get to emit a BLOCKED.
    """

    BACKEND_ERROR = "backend-error"
    INVALID_OUTPUT = "invalid-output"
    VERIFIER_BLOCKED = "verifier-blocked"
    NO_CHANGES = "no-changes"
    PROTECTED_PATH = "protected-path"
    STALE_SUBJECT = "stale-subject"
    UNREADABLE_STATE = "unreadable-state"
    VERIFIER_MUTATED = "verifier-mutated"
    BUDGET_EXCEEDED = "budget-exceeded"
    CHECKPOINT_FAILED = "checkpoint-failed"


def blocked_line(code: str | BlockedCode, reason: str) -> str:
    """`BLOCKED [code]: reason` -- the one shape every surface prints."""

    return f"BLOCKED [{code}]: {reason}"


def parse_code(value: object) -> BlockedCode | None:
    """A stored `terminal_code`, or None when the run predates the field."""

    if not isinstance(value, str):
        return None
    for code in BlockedCode:
        if code.value == value:
            return code
    return None


__all__ = ["BlockedCode", "blocked_line", "parse_code"]
