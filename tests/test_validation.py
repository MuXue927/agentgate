from __future__ import annotations

import pytest

from agentgate.validation import (
    ModelPairError,
    evaluate_model_pair,
    model_family,
)


def test_same_family_aliases_are_rejected() -> None:
    pairs = (
        ("deepseek-flash", "deepseek-v4-flash"),
        ("deepseek-v4-pro", "deepseek-flash"),
    )
    for executor, verifier in pairs:
        with pytest.raises(ModelPairError, match="同一模型族"):
            evaluate_model_pair(executor, verifier)


def test_cross_family_is_allowed() -> None:
    pair = evaluate_model_pair(
        "deepseek-v4-flash",
        "nvidia/nemotron-3.5-lightning:free",
    )

    assert pair.executor_family == "deepseek"
    assert pair.verifier_family == "nemotron"
    assert pair.tier == "★★"


@pytest.mark.parametrize(
    ("model", "expected"),
    (
        ("deepseek-flash", "deepseek"),
        ("nvidia/nemotron-3.5-lightning:free", "nemotron"),
        ("google/gemini-3.8-flash", "gemini"),
        ("anthropic/claude-sonnet-5", "claude"),
        ("openai/gpt-6-luna", "gpt"),
    ),
)
def test_model_family_known_prefixes(model: str, expected: str) -> None:
    assert model_family(model) == expected


def test_unknown_model_uses_own_family() -> None:
    first = model_family("vendor-one/mystery-alpha")
    second = model_family("vendor-two/other-beta")

    assert first != second
    assert first != "unknown"
