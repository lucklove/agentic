from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from capabilities.compaction import AnchoredCompaction
from capabilities.privacy import PrivacyCapability


def _request(text: str) -> ModelRequest:
    return ModelRequest([UserPromptPart(text)])


def _response(text: str) -> ModelResponse:
    return ModelResponse([TextPart(text)])


def _summary(messages: list[object]) -> str:
    return str(messages[-1])


def test_compacts_old_history_and_preserves_recent_turns() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return _response("## Objective\n- Keep the exact project state.")
        return _response("continued")

    history: list[ModelMessage] = [
        _request("old user request"),
        _response("old assistant response"),
        _request("recent user request"),
        _response("recent assistant response"),
    ]
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    result = asyncio.run(agent.run("current request", message_history=history))

    assert result.output == "continued"
    assert len(calls) == 2
    summary_prompt = _summary(calls[0])
    assert "Create a new anchored summary" in summary_prompt
    assert "[User]: old user request" in summary_prompt
    assert "[Assistant]: old assistant response" in summary_prompt
    assert "recent user request" not in summary_prompt

    final_messages = calls[1]
    assert isinstance(final_messages[0], ModelRequest)
    assert isinstance(final_messages[1], ModelResponse)
    assert "What did we do so far?" in str(final_messages[0])
    assert "Keep the exact project state" in str(final_messages[1])
    assert "recent user request" in str(final_messages)
    assert "old user request" not in str(final_messages)
    persisted = str(result.all_messages())
    assert "agentic_compaction" in persisted
    assert "recent user request" in persisted
    assert "old user request" not in persisted


def test_repeated_compaction_uses_previous_summary() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return _response("## Objective\n- Updated objective.")
        return _response("continued")

    previous_marker = ModelRequest(
        [UserPromptPart("What did we do so far?")],
        metadata={"agentic_compaction": "request"},
    )
    previous_summary = ModelResponse(
        [TextPart("## Objective\n- Previous objective.")],
        metadata={"agentic_compaction": "summary"},
    )
    history: list[ModelMessage] = [
        previous_marker,
        previous_summary,
        _request("new old request"),
        _response("new old response"),
        _request("recent request"),
        _response("recent response"),
    ]
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    result = asyncio.run(agent.run("current request", message_history=history))

    summary_prompt = _summary(calls[0])
    assert "<previous-summary>" in summary_prompt
    assert "Previous objective" in summary_prompt
    assert "new old request" in summary_prompt
    assert "recent request" not in summary_prompt
    persisted_messages = result.all_messages()
    compacted = [
        message
        for message in persisted_messages
        if message.metadata and message.metadata.get("agentic_compaction")
    ]
    assert len(compacted) == 2
    persisted = str(persisted_messages)
    assert "Updated objective" in persisted
    assert "Previous objective" not in persisted


def test_preserved_tail_does_not_immediately_trigger_another_compaction() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return _response("## Objective\n- Keep working.")
        return _response("continued")

    history: list[ModelMessage] = [
        _request("old request"),
        _response("old response"),
        _request("tool-heavy recent request"),
        ModelResponse([ToolCallPart("read", {"path": "/tmp/a"}, "call-1")]),
        ModelRequest([ToolReturnPart("read", "a", "call-1")]),
        _response("recent response"),
    ]
    capability = AnchoredCompaction(message_count_threshold=5, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    first = asyncio.run(agent.run("first current request", message_history=history))
    assert len(calls) == 2

    second = asyncio.run(
        agent.run("second current request", message_history=first.all_messages())
    )

    assert second.output == "continued"
    assert len(calls) == 3
    assert "Keep working" in str(calls[-1])
    assert "tool-heavy recent request" in str(calls[-1])


def test_preserves_complete_tool_turn_at_tail_boundary() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return _response("## Objective\n- Preserve tool state.")
        return _response("continued")

    history: list[ModelMessage] = [
        _request("old request"),
        _response("old response"),
        _request("inspect repository"),
        ModelResponse([ToolCallPart("read", {"path": "/tmp/file"}, "call-1")]),
        ModelRequest([ToolReturnPart("read", "contents", "call-1")]),
        _response("inspection complete"),
    ]
    capability = AnchoredCompaction(message_count_threshold=6, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    asyncio.run(agent.run("continue", message_history=history))

    final = str(calls[1])
    assert "inspect repository" in final
    assert "call-1" in final
    assert "contents" in final
    assert "old request" not in final


def test_summary_is_redacted_before_nested_model_call() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return _response("## Objective\n- Preserve the placeholder.")
        return _response("continued")

    secret = "secret-value-123"
    privacy = PrivacyCapability.from_spec(
        {"patterns": {"keywords": [{"value": secret, "category": "SECRET"}]}}
    )
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)
    history: list[ModelMessage] = [
        _request(f"use {secret}"),
        _response(f"used {secret}"),
        _request("recent request"),
        _response("recent response"),
    ]
    agent = Agent(
        FunctionModel(model_func),
        output_type=str,
        capabilities=[capability, privacy],
    )

    asyncio.run(agent.run("continue", message_history=history))

    prompt = _summary(calls[0])
    assert secret not in prompt
    assert "__VG_SECRET_" in prompt


def test_compaction_failure_keeps_original_history() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            raise RuntimeError("summary unavailable")
        return _response("continued")

    history: list[ModelMessage] = [
        _request("old request"),
        _response("old response"),
        _request("recent request"),
        _response("recent response"),
    ]
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    result = asyncio.run(agent.run("continue", message_history=history))

    assert result.output == "continued"
    assert "old request" in str(calls[1])
    assert "agentic_compaction" not in str(result.all_messages())


def test_does_not_compact_at_or_below_threshold() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        return _response("continued")

    history: list[ModelMessage] = [
        _request("one"),
        _response("two"),
        _request("three"),
    ]
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    asyncio.run(agent.run("current request", message_history=history))

    assert len(calls) == 1


def test_disabled_anchored_compaction_is_a_noop() -> None:
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        return _response("continued")

    history: list[ModelMessage] = [
        _request("old request"),
        _response("old response"),
        _request("recent request"),
        _response("recent response"),
    ]
    capability = AnchoredCompaction(
        enabled=False,
        message_count_threshold=4,
        tail_turns=1,
    )
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    result = asyncio.run(agent.run("current request", message_history=history))

    assert len(calls) == 1
    assert "old request" in str(calls[0])
    assert "agentic_compaction" not in str(result.all_messages())


def test_bundled_tool_turn_keeps_tool_call_pair_on_same_side() -> None:
    """Regression for issue #271.

    pydantic-ai's ``_merge_consecutive_messages`` (in
    ``pydantic_ai/_agent_graph.py``) bundles a pending ``ToolReturnPart``
    with the next ``UserPromptPart`` into a single ``ModelRequest``. With
    the original ``_is_user_turn`` check, that bundled request counted as
    a user turn, so the compaction split landed in front of it and the
    matching ``ModelResponse(tool_call)`` was summarised away — leaving
    an orphan ``ToolReturnPart`` that the model API rejected with
    ``tool result's tool id ... not found``. The fix excludes bundled
    requests from being treated as a user turn, so the split lands on the
    previous pure user turn and the ``tool_call`` / ``tool_return`` pair
    stays together on the recent side.
    """
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return _response("## Objective\n- Keep tool pair intact.")
        return _response("continued")

    bundled_user_follow_up = UserPromptPart("user follow-up after tool")
    tool_call_response = ModelResponse(
        [ToolCallPart("read", {"path": "/tmp/x"}, "call-x")]
    )
    bundled_request = ModelRequest(
        [ToolReturnPart("read", "contents", "call-x"), bundled_user_follow_up]
    )
    history: list[ModelMessage] = [
        _request("old user request"),
        _response("old assistant response"),
        _request("recent pure user request"),
        tool_call_response,
        bundled_request,
        _response("assistant response after bundle"),
    ]
    capability = AnchoredCompaction(message_count_threshold=6, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    asyncio.run(agent.run("current request", message_history=history))

    assert len(calls) == 2
    final = str(calls[1])
    # Both halves of the tool_call/tool_return pair must survive on the
    # recent side of the split — otherwise the model API sees an orphan
    # ToolReturnPart and rejects the request.
    assert "call-x" in final, (
        "tool_call(call-x) was summarised away; the split must land on the "
        "previous pure user turn so it stays paired with its ToolReturnPart"
    )
    assert "contents" in final, "bundled ToolReturnPart content must survive"
    assert (
        "user follow-up after tool" in final
    ), "bundled UserPromptPart must remain on the recent side"
    assert (
        "recent pure user request" in final
    ), "the pure user turn that anchors the recent side must survive"
    assert "old user request" not in final, "old turn must be summarised away"
