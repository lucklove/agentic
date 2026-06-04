from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from tenacity import RetryAction, RetryCallState

from model_factory import _log_retrying_http_request, build_model


def test_build_model_supports_openai_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = build_model("openai-chat:gpt-5.4")

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "gpt-5.4"
    assert isinstance(model.provider, OpenAIProvider)


def test_build_model_supports_openai_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = build_model("openai-responses:gpt-5.4")

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model_name == "gpt-5.4"
    assert isinstance(model.provider, OpenAIProvider)


def test_build_model_supports_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
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


def test_log_retrying_http_request_logs_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = Mock()
    monkeypatch.setattr("model_factory.logfire.info", info)
    retry_state = RetryCallState(retry_object=Mock(), fn=None, args=(), kwargs={})
    retry_state.set_exception(
        (type(httpx.ConnectError("boom")), httpx.ConnectError("boom"), None)
    )
    retry_state.next_action = RetryAction(3.5)

    _log_retrying_http_request(retry_state)

    info.assert_called_once_with(
        "model provider request failed, retrying after 3.5 seconds",
        error_message="boom",
        attempt=1,
        retry_delay_seconds=3.5,
    )


def test_log_retrying_http_request_ignores_first_attempt_without_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = Mock()
    monkeypatch.setattr("model_factory.logfire.info", info)
    retry_state = RetryCallState(retry_object=Mock(), fn=None, args=(), kwargs={})

    _log_retrying_http_request(retry_state)

    info.assert_not_called()
