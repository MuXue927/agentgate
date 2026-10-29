from __future__ import annotations

from dataclasses import dataclass


KNOWN_FAMILIES = (
    ("deepseek", "deepseek"),
    ("nvidia/nemotron", "nemotron"),
    ("nemotron", "nemotron"),
    ("google/gemini", "gemini"),
    ("gemini", "gemini"),
    ("anthropic/claude", "claude"),
    ("claude", "claude"),
    ("openai/gpt", "gpt"),
    ("gpt", "gpt"),
)


class ModelPairError(ValueError):
    """Raised when execution and verification use the same model family."""


@dataclass(frozen=True)
class ModelPair:
    executor_model: str
    verifier_model: str
    executor_family: str
    verifier_family: str
    executor_scaffold: str
    verifier_scaffold: str
    tier: str


def model_family(model: str) -> str:
    """Map known model aliases to one explicit family."""

    normalized = model.strip().casefold()
    if not normalized:
        return ""
    for prefix, family in KNOWN_FAMILIES:
        if normalized.startswith(prefix):
            return family

    # Unknown names keep their full normalized identity. Collapsing them to a
    # generic bucket would make unrelated unknown models look like the same model.
    return f"unknown:{normalized}"


def evaluate_model_pair(
    executor_model: str,
    verifier_model: str,
    *,
    executor_scaffold: str = "codex",
    verifier_scaffold: str = "codex",
) -> ModelPair:
    executor_model = executor_model.strip()
    verifier_model = verifier_model.strip()
    if not executor_model or not verifier_model:
        raise ModelPairError("Executor and verifier models must both be configured.")
    executor_family = model_family(executor_model)
    verifier_family = model_family(verifier_model)
    if executor_family == verifier_family:
        raise ModelPairError(
            "Executor and verifier are in the same model family "
            f"({executor_family!r})；同一模型族禁止自证。"
            f"Execution={executor_model!r}, verification={verifier_model!r}."
        )

    tier = "★★★" if executor_scaffold != verifier_scaffold else "★★"
    return ModelPair(
        executor_model=executor_model,
        verifier_model=verifier_model,
        executor_family=executor_family,
        verifier_family=verifier_family,
        executor_scaffold=executor_scaffold,
        verifier_scaffold=verifier_scaffold,
        tier=tier,
    )


def require_different_models(
    executor_model: str,
    verifier_model: str,
    *,
    executor_scaffold: str = "codex",
    verifier_scaffold: str = "codex",
) -> ModelPair:
    """Validate the pair before a run starts and return its blind-tier rating."""

    return evaluate_model_pair(
        executor_model,
        verifier_model,
        executor_scaffold=executor_scaffold,
        verifier_scaffold=verifier_scaffold,
    )
