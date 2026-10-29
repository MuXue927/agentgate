from __future__ import annotations

from dataclasses import dataclass


class ModelPairError(ValueError):
    """Raised when execution and verification use the same model."""


@dataclass(frozen=True)
class ModelPair:
    executor_model: str
    verifier_model: str
    executor_scaffold: str
    verifier_scaffold: str
    tier: str


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
    if executor_model.casefold() == verifier_model.casefold():
        raise ModelPairError(
            "Executor and verifier must use different models. "
            f"Both are {executor_model!r}; use `-m <non-deepseek>` for verification."
        )

    tier = "★★★" if executor_scaffold != verifier_scaffold else "★★"
    return ModelPair(
        executor_model=executor_model,
        verifier_model=verifier_model,
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
