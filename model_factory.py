"""Helpers for constructing pydantic-ai models from profile config strings."""

from __future__ import annotations

import httpx
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after

__all__ = ["build_model"]


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
            stop=stop_after_attempt(5),
            reraise=True,
        ),
        validate_response=_validate_retryable_response,
    )
    return httpx.AsyncClient(transport=transport)


def build_model(model_spec: str) -> Model:
    """Build a model instance from a ``<kind>:<name>`` profile string."""
    kind, separator, model_name = model_spec.partition(":")
    if not separator or not kind or not model_name:
        raise ValueError(
            f"invalid model spec {model_spec!r}; expected '<kind>:<name>'"
        )

    http_client = _build_retrying_http_client()

    if kind == "openai-chat":
        return OpenAIChatModel(model_name, provider=OpenAIProvider(http_client=http_client))
    if kind == "openai-responses":
        return OpenAIResponsesModel(
            model_name, provider=OpenAIProvider(http_client=http_client)
        )
    if kind == "anthropic":
        return AnthropicModel(model_name, provider=AnthropicProvider(http_client=http_client))

    raise ValueError(f"unsupported model kind {kind!r} in model spec {model_spec!r}")
