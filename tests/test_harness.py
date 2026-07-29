from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolReturn

from capabilities.harness import HarnessCapability
from deps import AgentDeps, NotificationSubject, WikiRead


@pytest.fixture
def deps() -> AgentDeps:
    return AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
        notification_subject=NotificationSubject(
            owner="agentic",
            repo="agentic",
            number="31",
            subject_type="Issue",
        ),
    )


def test_get_toolset_exposes_sleep_tool() -> None:
    cap = HarnessCapability()

    toolset = cap.get_toolset()

    assert "sleep" in toolset.tools


def test_before_tool_execute_passes_through_non_comment_tool(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_pull_request_write")
    args = {"method": "add_reviewers", "owner": "agentic", "repo": "agentic"}

    output = asyncio.run(
        cap.before_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert output == args


def test_before_tool_execute_blocks_comment_without_mentioned_context(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")
    args = {"method": "add_comment", "body": "Done @review_agent"}

    with pytest.raises(
        ModelRetry, match="current delivered message did not include any direct mention"
    ):
        asyncio.run(
            cap.before_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args=args,
            )
        )


def test_before_tool_execute_blocks_comment_without_other_mention(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")
    args = {"method": "add_comment", "body": "Done @code_agent"}

    deps.has_mentioned_comments = True

    with pytest.raises(ModelRetry, match="must @mention"):
        asyncio.run(
            cap.before_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args=args,
            )
        )


def test_before_tool_execute_blocks_comment_with_only_backtick_wrapped_mention(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")
    args = {"method": "add_comment", "body": "Done `@review_agent`"}

    deps.has_mentioned_comments = True

    with pytest.raises(ModelRetry, match="must @mention"):
        asyncio.run(
            cap.before_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args=args,
            )
        )


def test_before_tool_execute_blocks_email_like_text(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")
    args = {"method": "add_comment", "body": "Contact foo@bar for details."}

    deps.has_mentioned_comments = True

    with pytest.raises(ModelRetry, match="must @mention"):
        asyncio.run(
            cap.before_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args=args,
            )
        )


def test_before_tool_execute_allows_comment_with_other_mention(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    deps.has_mentioned_comments = True
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")
    args = {"method": "add_comment", "body": "Done @review_agent"}

    output = asyncio.run(
        cap.before_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert output == args


def test_before_tool_execute_allows_other_pull_request_methods(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_pull_request_write")
    args = {"method": "merge", "owner": "agentic", "repo": "agentic"}

    output = asyncio.run(
        cap.before_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert output == args


def test_after_tool_execute_filters_conversation_comments() -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
            notification_subject=NotificationSubject(
                owner="agentic",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "agentic",
        "repo": "agentic",
        "issue_number": 31,
    }
    result = [
        {"body": "Regular comment", "user": {"login": "human"}},
        {
            "id": 2,
            "body": "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\nDone.",
            "user": {"login": "code_agent"},
        },
        {"id": 3, "body": "Another comment", "user": {"login": "other"}},
    ]

    filtered = asyncio.run(
        cap.after_tool_execute(
            ctx, call=SimpleNamespace(), tool_def=tool_def, args=args, result=result
        )
    )

    assert len(filtered) == 2
    assert filtered[0]["body"] == "Regular comment"
    assert filtered[1]["body"] == "Another comment"


def test_after_tool_execute_passes_through_non_list_result() -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {"method": "get"}
    result = {"body": "issue body", "title": "test"}

    output = asyncio.run(
        cap.after_tool_execute(
            ctx, call=SimpleNamespace(), tool_def=tool_def, args=args, result=result
        )
    )

    assert output == result


def test_after_tool_execute_passes_through_non_comment_method() -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {"method": "get_labels"}
    result = [{"name": "bug"}]

    output = asyncio.run(
        cap.after_tool_execute(
            ctx, call=SimpleNamespace(), tool_def=tool_def, args=args, result=result
        )
    )

    assert output == result


def test_after_tool_execute_preserves_different_agent_marker() -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
            notification_subject=NotificationSubject(
                owner="agentic",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "agentic",
        "repo": "agentic",
        "issue_number": 31,
    }
    result = [
        {
            "id": 1,
            "body": "<!-- agentic:@review_agent last_seen_comment_id=0 -->\n\nLGTM",
            "user": {"login": "review_agent"},
        },
    ]

    filtered = asyncio.run(
        cap.after_tool_execute(
            ctx, call=SimpleNamespace(), tool_def=tool_def, args=args, result=result
        )
    )

    assert len(filtered) == 1


def test_after_tool_execute_skips_filter_when_no_notification_subject() -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "agentic",
        "repo": "agentic",
        "issue_number": 31,
    }
    result = [
        {
            "id": 2,
            "body": "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\nDone.",
            "user": {"login": "code_agent"},
        },
    ]

    output = asyncio.run(
        cap.after_tool_execute(
            ctx, call=SimpleNamespace(), tool_def=tool_def, args=args, result=result
        )
    )

    assert output is result
    assert len(output) == 1


def test_after_tool_execute_skips_filter_for_different_issue() -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
            notification_subject=NotificationSubject(
                owner="agentic",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "agentic",
        "repo": "agentic",
        "issue_number": 200,
    }
    result = [
        {"body": "Regular comment", "user": {"login": "human"}},
        {
            "id": 2,
            "body": "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\nDone.",
            "user": {"login": "code_agent"},
        },
    ]

    output = asyncio.run(
        cap.after_tool_execute(
            ctx, call=SimpleNamespace(), tool_def=tool_def, args=args, result=result
        )
    )

    assert output is result
    assert len(output) == 2


def test_after_tool_execute_skips_filter_for_different_repo() -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=object(),
            gitea_username="code_agent",
            gitea_base_url="http://gitea.example",
            gitea_token="token",
            notification_subject=NotificationSubject(
                owner="agentic",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "agentic",
        "repo": "other-repo",
        "issue_number": 31,
    }
    result = [
        {
            "id": 2,
            "body": "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\nDone.",
            "user": {"login": "code_agent"},
        },
    ]

    output = asyncio.run(
        cap.after_tool_execute(
            ctx, call=SimpleNamespace(), tool_def=tool_def, args=args, result=result
        )
    )

    assert output is result
    assert len(output) == 1


def test_wrap_tool_execute_marks_run_code_errored_on_model_retry(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")

    async def handler(args: dict) -> None:
        raise ModelRetry("Runtime error: name 'x' is not defined")

    with pytest.raises(ModelRetry):
        asyncio.run(
            cap.wrap_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={},
                handler=handler,
            )
        )

    assert deps.run_code_errored is True


def test_wrap_tool_execute_marks_run_code_errored_on_exception(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")

    async def handler(args: dict) -> None:
        raise RuntimeError("sandbox error")

    with pytest.raises(RuntimeError, match="sandbox error"):
        asyncio.run(
            cap.wrap_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={},
                handler=handler,
            )
        )

    assert deps.run_code_errored is True


def test_wrap_tool_execute_propagates_keyboard_interrupt_unchanged(
    deps: AgentDeps,
) -> None:
    """``KeyboardInterrupt`` is a ``BaseException`` subclass, not an
    ``Exception``. ``except Exception`` must NOT swallow it; it must
    propagate unchanged so the agent run actually stops when the user
    aborts. ``run_code_errored`` must also stay False — the user did
    not ask the agent to recover from anything.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")

    async def handler(args: dict) -> None:
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            cap.wrap_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={},
                handler=handler,
            )
        )

    assert deps.run_code_errored is False


def test_wrap_tool_execute_passes_through_on_success(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")
    original = ToolReturn(return_value={"output": "ok"})

    async def handler(args: dict) -> ToolReturn:
        return original

    result = asyncio.run(
        cap.wrap_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={},
            handler=handler,
        )
    )

    assert result is original
    assert result.return_value == {"output": "ok"}
    assert deps.run_code_errored is False


def test_wrap_tool_execute_ignores_other_tools(deps: AgentDeps) -> None:
    """Non-``run_code`` tools (gitea_*, mcp_*, execute, sleep, ...) propagate
    raw exceptions unchanged — they aren't recoverable by editing code, so
    retrying through ``code_exec.max_retries`` would just burn the budget.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")

    async def handler(args: dict) -> None:
        raise RuntimeError("some error")

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            cap.wrap_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={},
                handler=handler,
            )
        )

    # ``__cause__`` must be None: the original RuntimeError was never wrapped
    # in anything, so the traceback chain has no synthetic link.
    assert exc_info.value.__cause__ is None
    assert deps.run_code_errored is False


def test_wrap_tool_execute_sanitizes_non_jsonable_run_code_result(
    deps: AgentDeps,
) -> None:
    """Successful ``run_code`` returns whose ``ToolReturn.return_value``
    contains non-JSON-serializable leaves (e.g. ``type(1)`` → ``<class 'int'>``)
    must be sanitized so pydantic-ai's OTel instrumentation does not raise
    ``PydanticSerializationError`` while dumping the span.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")
    sentinel_metadata = {"code_mode": True}
    original = ToolReturn(
        return_value={"output": "", "result": type(1)},
        metadata=sentinel_metadata,
    )

    async def handler(args: dict) -> ToolReturn:
        return original

    result = asyncio.run(
        cap.wrap_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={},
            handler=handler,
        )
    )

    # Mutated in place: same object, ``return_value`` rewritten, ``metadata``
    # untouched.
    assert result is original
    assert result.return_value == {"output": "", "result": "<class 'int'>"}
    assert result.metadata is sentinel_metadata
    assert deps.run_code_errored is False


def test_wrap_tool_execute_passes_through_jsonable_run_code_result(
    deps: AgentDeps,
) -> None:
    """The common path (already-JSON-serializable results) returns an
    equivalent ``ToolReturn`` and does not flip ``run_code_errored``.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")
    sentinel_metadata = {"code_mode": True}
    original = ToolReturn(
        return_value={"output": "", "result": 42},
        metadata=sentinel_metadata,
    )

    async def handler(args: dict) -> ToolReturn:
        return original

    result = asyncio.run(
        cap.wrap_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={},
            handler=handler,
        )
    )

    assert isinstance(result, ToolReturn)
    assert result.return_value == {"output": "", "result": 42}
    assert result.metadata is sentinel_metadata
    assert deps.run_code_errored is False


def test_after_tool_execute_marks_memory_modified_on_save(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="save_memory")

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={},
            result="Memory saved: test",
        )
    )

    assert deps.memory_modified is True


def test_after_tool_execute_marks_memory_modified_on_delete(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="delete_memory")

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={},
            result="Memory deleted: test",
        )
    )

    assert deps.memory_modified is True


def test_before_output_process_raises_when_error_without_memory_update(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    deps.run_code_errored = True
    deps.memory_modified = False
    ctx = SimpleNamespace(deps=deps)

    with pytest.raises(ModelRetry, match="run_code encountered an error"):
        asyncio.run(
            cap.before_output_process(
                ctx,
                output_context=None,
                output="Here is my answer.",
            )
        )


def test_before_output_process_passes_when_error_with_memory_update(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    deps.run_code_errored = True
    deps.memory_modified = True
    ctx = SimpleNamespace(deps=deps)

    output = asyncio.run(
        cap.before_output_process(
            ctx,
            output_context=None,
            output="Here is my answer.",
        )
    )

    assert output == "Here is my answer."


def test_before_output_process_passes_when_no_error(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    deps.run_code_errored = False
    deps.memory_modified = False
    ctx = SimpleNamespace(deps=deps)

    output = asyncio.run(
        cap.before_output_process(
            ctx,
            output_context=None,
            output="Here is my answer.",
        )
    )

    assert output == "Here is my answer."


# ----------------------------------------------------------------------------
# Wiki read tracking (issue #267)
# ----------------------------------------------------------------------------


def test_after_tool_execute_records_wiki_read_with_frontmatter_description(
    deps: AgentDeps,
) -> None:
    """Successful ``gitea_wiki_read`` records a ``WikiRead`` whose
    summary prefers the frontmatter ``description`` field over the body
    text. The deps list is what the compaction summarizer consumes.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    page_body = (
        "---\n"
        "name: agent-handbook\n"
        "description: Common handbook for run_code sandbox and Gitea MCP.\n"
        "---\n"
        "\n"
        "# Agent Handbook\n"
        "\n"
        "Detailed instructions follow, much longer than the description.\n"
    )

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={
                "method": "get",
                "owner": "agentic",
                "repo": "agentic",
                "pageName": "Agent-Handbook",
            },
            result={"content": page_body},
        )
    )

    assert len(deps.wiki_reads) == 1
    entry = deps.wiki_reads[0]
    assert isinstance(entry, WikiRead)
    assert entry.owner == "agentic"
    assert entry.repo == "agentic"
    assert entry.page_name == "Agent-Handbook"
    assert entry.summary == "Common handbook for run_code sandbox and Gitea MCP."


def test_after_tool_execute_records_wiki_read_with_body_fallback(
    deps: AgentDeps,
) -> None:
    """Pages without frontmatter fall back to the first 300 chars of the
    body, whitespace-collapsed so the preview fits on one line in the
    compaction summary.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    body = "First line.\n\nSecond line with    whitespace.\nThird."

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={
                "method": "get",
                "owner": "agentic",
                "repo": "agentic",
                "pageName": "Body-Only",
            },
            result={"content": body},
        )
    )

    assert len(deps.wiki_reads) == 1
    entry = deps.wiki_reads[0]
    assert entry.page_name == "Body-Only"
    assert entry.summary == "First line. Second line with whitespace. Third."


def test_after_tool_execute_truncates_long_wiki_summary(deps: AgentDeps) -> None:
    """Bodies longer than the cap are truncated with a single-character
    ellipsis suffix so the summary stays under the cap and remains
    machine-greppable.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    long_body = "a" * 1000

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={
                "method": "get",
                "owner": "agentic",
                "repo": "agentic",
                "pageName": "Long-Page",
            },
            result={"content": long_body},
        )
    )

    [entry] = deps.wiki_reads
    # 300-char cap (the harness constant) minus the ellipsis itself.
    assert len(entry.summary) == 300
    assert entry.summary.endswith("\u2026")


def test_after_tool_execute_replaces_existing_wiki_read(deps: AgentDeps) -> None:
    """Re-reading the same page replaces the prior entry rather than
    appending -- the compaction summary must reflect the agent's most
    recent view of the page, not every version seen.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {
        "method": "get",
        "owner": "agentic",
        "repo": "agentic",
        "pageName": "Agent-Handbook",
    }

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
            result={
                "content": (
                    "---\n"
                    "name: agent-handbook\n"
                    "description: First version.\n"
                    "---\n"
                )
            },
        )
    )
    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
            result={
                "content": (
                    "---\n"
                    "name: agent-handbook\n"
                    "description: Updated version.\n"
                    "---\n"
                )
            },
        )
    )

    assert len(deps.wiki_reads) == 1
    assert deps.wiki_reads[0].summary == "Updated version."


def test_after_tool_execute_preserves_wiki_read_order_across_pages(
    deps: AgentDeps,
) -> None:
    """Distinct pages stay in call order so the summary reads like the
    agent's actual reading history.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")

    for page_name in ("First", "Second", "Third"):
        asyncio.run(
            cap.after_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={
                    "method": "get",
                    "owner": "agentic",
                    "repo": "agentic",
                    "pageName": page_name,
                },
                result={
                    "content": (
                        "---\n"
                        f"name: {page_name.lower()}\n"
                        f"description: description for {page_name}\n"
                        "---\n"
                    )
                },
            )
        )

    assert [entry.page_name for entry in deps.wiki_reads] == [
        "First",
        "Second",
        "Third",
    ]


def test_after_tool_execute_skips_wiki_read_when_args_missing(
    deps: AgentDeps,
) -> None:
    """``gitea_wiki_read`` calls without owner/repo/pageName (e.g.
    ``method='list'`` enumerations) are not tracked -- we have no page
    to attribute the read to.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={"method": "list", "owner": "agentic", "repo": "agentic"},
            result=[{"sub_url": "Foo"}, {"sub_url": "Bar"}],
        )
    )

    assert deps.wiki_reads == []


def test_after_tool_execute_skips_wiki_read_when_result_has_no_content(
    deps: AgentDeps,
) -> None:
    """Error responses or empty payloads do not pollute the read log."""
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {
        "method": "get",
        "owner": "agentic",
        "repo": "agentic",
        "pageName": "Missing",
    }

    # Non-dict / non-string result.
    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
            result=None,
        )
    )
    assert deps.wiki_reads == []

    # Dict without ``content``.
    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
            result={"error": "not found"},
        )
    )
    assert deps.wiki_reads == []

    # Dict with empty ``content``.
    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
            result={"content": ""},
        )
    )
    assert deps.wiki_reads == []


def test_after_tool_execute_accepts_string_wiki_result(deps: AgentDeps) -> None:
    """Some MCP responses return the body as a plain string. The
    tracking code accepts both dict and string shapes.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_wiki_read")

    asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={
                "method": "get",
                "owner": "agentic",
                "repo": "agentic",
                "pageName": "String-Body",
            },
            result="plain string body without frontmatter",
        )
    )

    [entry] = deps.wiki_reads
    assert entry.page_name == "String-Body"
    assert entry.summary == "plain string body without frontmatter"


def test_after_tool_execute_passes_through_non_wiki_tool(deps: AgentDeps) -> None:
    """Unrelated tools leave ``wiki_reads`` untouched and return the
    result unchanged.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_read")
    result = {"title": "foo"}

    output = asyncio.run(
        cap.after_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={"method": "get"},
            result=result,
        )
    )

    assert output is result
    assert deps.wiki_reads == []
