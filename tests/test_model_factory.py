from __future__ import annotations

import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from model_factory import build_model


def test_build_model_supports_openai_chat() -> None:
    model = build_model("openai-chat:gpt-5.4")

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-5.4"
    assert isinstance(model.provider, OpenAIProvider)


def test_build_model_supports_openai_responses() -> None:
    model = build_model("openai-responses:gpt-5.4")

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model_name == "gpt-5.4"
    assert isinstance(model.provider, OpenAIProvider)


def test_build_model_supports_anthropic() -> None:
    model = build_model("anthropic:claude-sonnet-4-5")

    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-4-5"
    assert isinstance(model.provider, AnthropicProvider)


def test_build_model_rejects_missing_prefix_separator() -> None:
    with pytest.raises(ValueError, match="expected '<kind>:<name>'"):
        build_model("gpt-5.4")


def test_build_model_rejects_unknown_prefix() -> None:
    with pytest.raises(ValueError, match="unsupported model kind"):
        build_model("openai:gpt-5.4")


def test_build_model_does_not_create_http_client_for_unknown_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_build_retrying_http_client() -> object:
        nonlocal calls
        calls += 1
        return object()

    monkeypatch.setattr(
        "model_factory._build_retrying_http_client",
        fake_build_retrying_http_client,
    )

    with pytest.raises(ValueError, match="unsupported model kind"):
        build_model("openai:gpt-5.4")

    assert calls == 0
