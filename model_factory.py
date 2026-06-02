"""Helpers for constructing pydantic-ai models from profile config strings."""

from __future__ import annotations

from typing import Callable

import httpx
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

__all__ = ["build_model"]

_ModelBuilder = Callable[[str, httpx.AsyncClient], Model]


def _build_openai_chat_model(model_name: str, http_client: httpx.AsyncClient) -> Model:
    return OpenAIChatModel(model_name, provider=OpenAIProvider(http_client=http_client))


def _build_openai_responses_model(
    model_name: str, http_client: httpx.AsyncClient
) -> Model:
    return OpenAIResponsesModel(
        model_name, provider=OpenAIProvider(http_client=http_client)
    )


def _build_anthropic_model(model_name: str, http_client: httpx.AsyncClient) -> Model:
    return AnthropicModel(
        model_name, provider=AnthropicProvider(http_client=http_client)
    )


_MODEL_BUILDERS: dict[str, _ModelBuilder] = {
    "openai-chat": _build_openai_chat_model,
    "openai-responses": _build_openai_responses_model,
    "anthropic": _build_anthropic_model,
}


def _validate_retryable_response(response: httpx.Response) -> None:
    """Raise for transient HTTP responses that should be retried."""
    if response.status_code in {429, 502, 503, 504}:
        response.raise_for_status()


def _build_retrying_http_client() -> httpx.AsyncClient:
    """Create a shared retry policy for transient model-provider failures."""
    transport = AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type(
                (
                    httpx.HTTPStatusError,
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    httpx.ReadError,
                )
            ),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=1, max=30),
                max_wait=120,
            ),
            stop=stop_after_attempt(100),
            reraise=True,
        ),
        validate_response=_validate_retryable_response,
    )
    return httpx.AsyncClient(transport=transport)


def build_model(model_spec: str) -> Model:
    """Build a model instance from a ``<kind>:<name>`` profile string."""
    kind, separator, model_name = model_spec.partition(":")
    if not separator or not kind or not model_name:
        raise ValueError(f"invalid model spec {model_spec!r}; expected '<kind>:<name>'")

    builder = _MODEL_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(
            f"unsupported model kind {kind!r} in model spec {model_spec!r}"
        )

    http_client = _build_retrying_http_client()
    return builder(model_name, http_client)
