"""Compatibility entry point for the packaged Codex adapter."""

from agentgate.adapters.codex import (
    CodexAdapter,
    ErrorClass,
    RunResult,
    TokenUsage,
    classify_error,
)

__all__ = [
    "CodexAdapter",
    "ErrorClass",
    "RunResult",
    "TokenUsage",
    "classify_error",
]
