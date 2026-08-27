"""Helpers for constructing pydantic-ai models from profile config strings."""

from __future__ import annotations

from typing import Callable

import httpx2
import logfire
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import (
    AsyncHTTPX2TenacityTransport,
    RetryConfig,
    wait_retry_after,
)
from tenacity import RetryCallState, retry_if_exception_type, wait_exponential

__all__ = ["build_model"]

_ModelBuilder = Callable[[str, httpx2.AsyncClient], Model]


def _build_openai_chat_model(model_name: str, http_client: httpx2.AsyncClient) -> Model:
    return OpenAIChatModel(model_name, provider=OpenAIProvider(http_client=http_client))


def _build_openai_responses_model(
    model_name: str, http_client: httpx2.AsyncClient
) -> Model:
    return OpenAIResponsesModel(
        model_name, provider=OpenAIProvider(http_client=http_client)
    )


def _build_anthropic_model(model_name: str, http_client: httpx2.AsyncClient) -> Model:
    return AnthropicModel(
        model_name, provider=AnthropicProvider(http_client=http_client)
    )


_MODEL_BUILDERS: dict[str, _ModelBuilder] = {
    "openai-chat": _build_openai_chat_model,
    "openai-responses": _build_openai_responses_model,
    "anthropic": _build_anthropic_model,
}


def _validate_retryable_response(response: httpx2.Response) -> None:
    """Raise for transient HTTP responses that should be retried."""
    if response.status_code in {429, 500, 502, 503, 504}:
        response.raise_for_status()


def _log_retrying_http_request(retry_state: RetryCallState) -> None:
    """Log the last transient failure before tenacity sleeps and retries."""
    if retry_state.outcome is None or not retry_state.outcome.failed:
        return

    error = retry_state.outcome.exception()
    if error is None:
        return

    retry_delay_seconds = (
        retry_state.next_action.sleep if retry_state.next_action else None
    )

    logfire.info(
        f"model provider request failed, retrying after {retry_delay_seconds} seconds",
        error_message=str(error),
        attempt=retry_state.attempt_number,
        retry_delay_seconds=retry_delay_seconds,
    )


def _build_retrying_http_client() -> httpx2.AsyncClient:
    """Create a shared retry policy for transient model-provider failures."""
    transport = AsyncHTTPX2TenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type(
                (
                    httpx2.HTTPStatusError,
                    httpx2.TimeoutException,
                    httpx2.ConnectError,
                    httpx2.ReadError,
                )
            ),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=1, max=30),
                max_wait=120,
            ),
            before_sleep=_log_retrying_http_request,
            reraise=True,
        ),
        validate_response=_validate_retryable_response,
    )
    return httpx2.AsyncClient(transport=transport)


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
