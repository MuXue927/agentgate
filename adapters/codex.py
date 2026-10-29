"""Compatibility entry point for the packaged Codex adapter."""

from agentgate.adapters.codex import (
    Adapter,
    CodexAdapter,
    ErrorClass,
    RunResult,
    TokenUsage,
    classify_error,
)

__all__ = [
    "Adapter",
    "CodexAdapter",
    "ErrorClass",
    "RunResult",
    "TokenUsage",
    "classify_error",
]
