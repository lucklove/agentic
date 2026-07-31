"""Provider-independent anchored-summary context compaction."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from pydantic_ai import Agent, ModelSettings
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.anthropic import AnthropicCompaction as _AnthropicCompaction
from pydantic_ai.models.openai import OpenAICompaction as _OpenAICompaction
from pydantic_ai.tools import RunContext
from pydantic_core import to_jsonable_python

from capabilities.privacy import PrivacyCapability

__all__ = ["AnchoredCompaction", "OpenAICompaction", "AnthropicCompaction"]

logger = logging.getLogger(__name__)

_COMPACTION_METADATA_KEY = "agentic_compaction"
_COMPACTION_TAIL_COUNT_KEY = "agentic_compaction_tail_messages"
_COMPACTION_MARKER = "What did we do so far?"

_SYSTEM_PROMPT = """You are an anchored context summarization assistant for coding sessions.

Summarize only the conversation history you are given. The newest turns may be kept verbatim outside your summary, so focus on the older context that still matters for continuing the work.

If the prompt includes a <previous-summary> block, treat it as the current anchored summary. Update it with the new history by preserving still-true details, removing stale details, and merging in new facts.

Always follow the exact output structure requested by the user prompt. Keep every section, preserve exact file paths and identifiers when known, and prefer terse bullets over paragraphs.

Do not answer the conversation itself. Do not mention that you are summarizing, compacting, or merging context. Respond in the same language as the conversation."""

_SUMMARY_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted."""


class OpenAICompaction(_OpenAICompaction):
    """OpenAI compaction with a configuration-level enable switch."""

    def __init__(self, *, enabled: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.enabled = bool(enabled)

    def get_model_settings(self) -> Callable[..., ModelSettings] | None:
        if not self.enabled:
            return None
        return super().get_model_settings()

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not self.enabled:
            return request_context
        return await super().before_model_request(ctx, request_context)


class AnthropicCompaction(_AnthropicCompaction):
    """Anthropic compaction with a configuration-level enable switch."""

    def __init__(self, *, enabled: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.enabled = bool(enabled)

    def get_model_settings(self) -> Callable[..., ModelSettings]:
        if not self.enabled:
            return lambda ctx: ModelSettings()
        return super().get_model_settings()


class AnchoredCompaction(AbstractCapability[Any]):
    """Compact old turns into an OpenCode-style Markdown summary."""

    def __init__(
        self,
        *,
        message_count_threshold: int = 40,
        tail_turns: int = 2,
        summary_output_tokens: int = 4096,
        tool_output_max_chars: int = 2000,
        enabled: bool = True,
    ) -> None:
        if message_count_threshold < 2:
            raise ValueError("message_count_threshold must be at least 2")
        if tail_turns < 0:
            raise ValueError("tail_turns must not be negative")
        if summary_output_tokens < 1:
            raise ValueError("summary_output_tokens must be positive")
        if tool_output_max_chars < 1:
            raise ValueError("tool_output_max_chars must be positive")

        self.message_count_threshold = message_count_threshold
        self.tail_turns = tail_turns
        self.summary_output_tokens = summary_output_tokens
        self.tool_output_max_chars = tool_output_max_chars
        self.enabled = bool(enabled)

    def get_ordering(self) -> CapabilityOrdering:
        # The outer privacy hook redacts messages before this hook serializes them.
        return CapabilityOrdering(wrapped_by=(PrivacyCapability,))

    @staticmethod
    def _is_compaction(message: ModelMessage) -> bool:
        return bool(message.metadata and message.metadata.get(_COMPACTION_METADATA_KEY))

    @staticmethod
    def _summary_from(message: ModelMessage) -> str | None:
        if not isinstance(message, ModelResponse):
            return None
        if (
            not message.metadata
            or message.metadata.get(_COMPACTION_METADATA_KEY) != "summary"
        ):
            return None
        text = "\n\n".join(
            part.content.strip()
            for part in message.parts
            if isinstance(part, TextPart) and part.content.strip()
        )
        return text or None

    @staticmethod
    def _messages_since_compaction(messages: list[ModelMessage]) -> int:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if AnchoredCompaction._summary_from(message) is None:
                continue
            metadata = message.metadata or {}
            tail_count = metadata.get(_COMPACTION_TAIL_COUNT_KEY, 0)
            if not isinstance(tail_count, int) or tail_count < 0:
                tail_count = 0
            return max(0, len(messages) - index - 1 - tail_count)
        return len(messages)

    @staticmethod
    def _is_user_turn(message: ModelMessage) -> bool:
        # A user turn is a ModelRequest that carries a UserPromptPart but no
        # tool-closure parts (ToolReturnPart / RetryPromptPart). pydantic-ai's
        # _merge_consecutive_messages (see pydantic_ai/_agent_graph.py) bundles
        # a pending ToolReturnPart with the next UserPromptPart into a single
        # ModelRequest; if we count that bundled message as a user turn the
        # compaction split point falls in front of it and the matching
        # ModelResponse(tool_call) is summarised away, leaving an orphan
        # ToolReturnPart that the model API rejects with
        # "tool result's tool id ... not found". Excluding bundled requests
        # keeps the tool_call/tool_return pair together on the same side of
        # the split.
        if not isinstance(message, ModelRequest):
            return False
        has_tool_closure = any(
            isinstance(part, (ToolReturnPart, RetryPromptPart))
            for part in message.parts
        )
        if has_tool_closure:
            return False
        return any(isinstance(part, UserPromptPart) for part in message.parts)

    def _select(
        self, history: list[ModelMessage]
    ) -> tuple[list[ModelMessage], list[ModelMessage]] | None:
        if self.tail_turns == 0:
            return history, []
        starts = [
            index
            for index, message in enumerate(history)
            if self._is_user_turn(message)
        ]
        if len(starts) <= self.tail_turns:
            return None
        split = starts[-self.tail_turns]
        if split <= 0:
            return None
        return history[:split], history[split:]

    def _truncate_tool_output(self, value: str) -> str:
        if len(value) <= self.tool_output_max_chars:
            return value
        return f"{value[: self.tool_output_max_chars]}\n[truncated]"

    @staticmethod
    def _json(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(to_jsonable_python(value), ensure_ascii=False)

    def _user_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        result: list[str] = []
        for item in content:
            if isinstance(item, str):
                result.append(item)
                continue
            media_type = getattr(item, "media_type", None) or type(item).__name__
            name = getattr(item, "identifier", None) or getattr(item, "url", None)
            result.append(f"[Attached {media_type}{f': {name}' if name else ''}]")
        return "\n".join(result)

    def _serialize(self, messages: list[ModelMessage]) -> str:
        lines: list[str] = []
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart):
                        lines.append(f"[User]: {self._user_content(part.content)}")
                    elif isinstance(part, ToolReturnPart):
                        output = self._truncate_tool_output(self._json(part.content))
                        lines.append(f"[Tool result: {part.tool_name}]: {output}")
                    elif isinstance(part, RetryPromptPart):
                        lines.append(f"[Tool error]: {self._json(part.content)}")
                    elif isinstance(part, SystemPromptPart):
                        lines.append(f"[System update]: {part.content}")
                continue

            for response_part in message.parts:
                if isinstance(response_part, TextPart):
                    lines.append(f"[Assistant]: {response_part.content}")
                elif isinstance(response_part, ThinkingPart) and response_part.content:
                    lines.append(f"[Assistant reasoning]: {response_part.content}")
                elif isinstance(response_part, ToolCallPart):
                    lines.append(
                        f"[Assistant tool call]: {response_part.tool_name}({self._json(response_part.args)})"
                    )
        return "\n\n".join(lines)

    def _build_prompt(self, previous_summary: str | None, history: str) -> str:
        anchor = (
            "Update the anchored summary below using the conversation history above.\n"
            "Preserve still-true details, remove stale details, and merge in the new facts.\n"
            f"<previous-summary>\n{previous_summary}\n</previous-summary>"
            if previous_summary
            else "Create a new anchored summary from the conversation history."
        )
        return "\n\n".join((history, anchor, _SUMMARY_TEMPLATE))

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not self.enabled:
            return request_context
        if (
            self._messages_since_compaction(request_context.messages)
            <= self.message_count_threshold
        ):
            return request_context

        current = request_context.messages[-1]
        history = request_context.messages[:-1]
        previous_summary = next(
            (
                summary
                for message in reversed(history)
                if (summary := self._summary_from(message))
            ),
            None,
        )
        history = [message for message in history if not self._is_compaction(message)]
        selected = self._select(history)
        if selected is None:
            return request_context
        old_messages, recent_messages = selected
        transcript = self._serialize(old_messages)
        if not transcript:
            return request_context

        summarizer = Agent(
            request_context.model,
            instructions=_SYSTEM_PROMPT,
            output_type=str,
            model_settings=ModelSettings(max_tokens=self.summary_output_tokens),
        )
        try:
            result = await summarizer.run(
                self._build_prompt(previous_summary, transcript)
            )
        except Exception:
            logger.warning("context compaction failed", exc_info=True)
            return request_context
        summary = result.output.strip()
        if not summary:
            return request_context

        marker = ModelRequest(
            [UserPromptPart(_COMPACTION_MARKER)],
            metadata={_COMPACTION_METADATA_KEY: "request"},
        )
        summary_message = ModelResponse(
            [TextPart(summary)],
            metadata={
                _COMPACTION_METADATA_KEY: "summary",
                _COMPACTION_TAIL_COUNT_KEY: len(recent_messages),
            },
            finish_reason="stop",
        )
        request_context.messages = [marker, summary_message, *recent_messages, current]
        return request_context
