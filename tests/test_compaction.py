from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
from deps import WikiRead


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


# ----------------------------------------------------------------------------
# Already-Read Wikis section (issue #267)
# ----------------------------------------------------------------------------


def test_summary_template_contains_already_read_wikis_section() -> None:
    """The compaction template must carry the new ``## Already-Read
    Wikis`` section so the summarizer knows to fill it in.
    """
    from capabilities.compaction import _SUMMARY_TEMPLATE

    assert "## Already-Read Wikis" in _SUMMARY_TEMPLATE
    # The re-read hint is part of the contract -- without it, a later
    # turn would treat the preview as authoritative and miss the
    # instruction to refresh the page when relevant.
    assert "re-read" in _SUMMARY_TEMPLATE.lower()


def test_format_wiki_reads_lists_each_entry() -> None:
    """The ``<wiki-reads>`` block the summarizer sees lists every page
    the agent consulted, with the page identifier on the left and the
    short preview on the right. Empty input renders ``(none yet)`` so
    the summarizer knows the agent has not consulted any wiki yet.
    """
    entries = [
        WikiRead(
            owner="agentic",
            repo="agentic",
            page_name="Agent-Handbook",
            summary="Common handbook for run_code sandbox and Gitea MCP.",
        ),
        WikiRead(
            owner="agentic",
            repo="agentic",
            page_name="Code-Agent-Workflow",
            summary="Operating workflow for the code_agent profile.",
        ),
    ]

    rendered = AnchoredCompaction._format_wiki_reads(entries)
    assert "<wiki-reads>" in rendered
    assert "</wiki-reads>" in rendered
    assert "agentic/agentic/Agent-Handbook" in rendered
    assert "Common handbook for run_code sandbox and Gitea MCP." in rendered
    assert "agentic/agentic/Code-Agent-Workflow" in rendered
    assert "Operating workflow for the code_agent profile." in rendered

    empty = AnchoredCompaction._format_wiki_reads([])
    assert "(none yet)" in empty


def test_build_prompt_embeds_wiki_reads() -> None:
    """``_build_prompt`` plumbs the wiki reads into the summarizer
    prompt and reminds the model to copy each entry into the
    ``## Already-Read Wikis`` section of the output.
    """
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)
    entries = [
        WikiRead(
            owner="agentic",
            repo="agentic",
            page_name="Agent-Handbook",
            summary="Common handbook for run_code sandbox and Gitea MCP.",
        ),
    ]
    prompt = capability._build_prompt(
        previous_summary=None,
        history="[User]: old request",
        wiki_reads=entries,
    )

    assert "<wiki-reads>" in prompt
    assert "agentic/agentic/Agent-Handbook" in prompt
    assert "Common handbook for run_code sandbox and Gitea MCP." in prompt
    # The instructions to the summarizer ask it to copy the entries
    # verbatim; without that the new section would be skipped.
    assert "## Already-Read Wikis" in prompt
    assert "Copy each entry" in prompt

    # With no wiki reads the prompt still carries the empty block so
    # the summarizer is forced to emit the section rather than skip it.
    empty_prompt = capability._build_prompt(
        previous_summary=None, history="[User]: old", wiki_reads=[]
    )
    assert "(none yet)" in empty_prompt


def test_wiki_reads_from_ctx_falls_back_when_no_deps() -> None:
    """Defensive: the hook should not crash when ``ctx`` is a bare
    object without a ``deps`` attribute (e.g. a unit test fixture).
    ``wiki_reads`` falls back to ``[]`` so summarization continues.
    """
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)

    # Bare ``ctx`` -- no ``deps`` attribute at all.
    assert capability._wiki_reads_from_ctx(SimpleNamespace()) == []

    # ``deps`` present but no ``wiki_reads``.
    assert capability._wiki_reads_from_ctx(SimpleNamespace(deps=object())) == []

    # ``deps.wiki_reads`` is the wrong type -- fall back to empty
    # rather than crashing the summarizer.
    assert (
        capability._wiki_reads_from_ctx(
            SimpleNamespace(deps=SimpleNamespace(wiki_reads="not-a-list"))
        )
        == []
    )


def test_compaction_hook_does_not_crash_without_wiki_reads() -> None:
    """Sanity check: ``before_model_request`` runs to completion when
    ``ctx.deps.wiki_reads`` is missing or empty -- the hook must not
    regress the common path just because the new field is unset. The
    existing compaction tests above already cover the full
    summarization path; this test only pins the fallback behavior.
    """
    calls: list[list[object]] = []

    async def model_func(messages, info):
        calls.append(messages)
        return _response("## Objective\n- Keep the exact project state.")

    history: list[ModelMessage] = [
        _request("old user request"),
        _response("old assistant response"),
        _request("recent user request"),
        _response("recent assistant response"),
    ]
    capability = AnchoredCompaction(message_count_threshold=4, tail_turns=1)
    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[capability])

    result = asyncio.run(agent.run("current request", message_history=history))

    # The compaction still fires; only the outer model was captured.
    assert len(calls) >= 1
    assert "agentic_compaction" in str(result.all_messages())
