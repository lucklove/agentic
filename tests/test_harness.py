from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry, UsageLimitExceeded, UserError

from capabilities.harness import HarnessCapability
from deps import AgentDeps, NotificationSubject


@pytest.fixture
def deps() -> AgentDeps:
    return AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
        notification_subject=NotificationSubject(
            owner="autonomous",
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
    args = {"method": "add_reviewers", "owner": "autonomous", "repo": "agentic"}

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
    args = {"method": "merge", "owner": "autonomous", "repo": "agentic"}

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
                owner="autonomous",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "autonomous",
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
                owner="autonomous",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "autonomous",
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
        "owner": "autonomous",
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
                owner="autonomous",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "autonomous",
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
                owner="autonomous",
                repo="agentic",
                number="31",
                subject_type="Issue",
            ),
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {
        "method": "get_comments",
        "owner": "autonomous",
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

    with pytest.raises(ModelRetry) as exc_info:
        asyncio.run(
            cap.wrap_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={},
                handler=handler,
            )
        )

    # The original exception is preserved via __cause__ so logs stay inspectable.
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "sandbox error"
    assert deps.run_code_errored is True


def test_wrap_tool_execute_passes_through_on_success(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")

    async def handler(args: dict) -> str:
        return "ok"

    result = asyncio.run(
        cap.wrap_tool_execute(
            ctx,
            call=SimpleNamespace(),
            tool_def=tool_def,
            args={},
            handler=handler,
        )
    )

    assert result == "ok"
    assert deps.run_code_errored is False


def test_wrap_tool_execute_ignores_other_tools(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")

    async def handler(args: dict) -> None:
        raise RuntimeError("some error")

    # Non-run_code tools are intentionally NOT converted to ModelRetry.
    # A real failure (auth error, 4xx, network) cannot be recovered by
    # retrying, so it propagates as-is and run_code_errored stays False.
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

    assert str(exc_info.value) == "some error"
    assert exc_info.value.__cause__ is None
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


# wrap_tool_execute — exception-conversion semantics
# ---------------------------------------------------------------------------


def test_wrap_tool_execute_propagates_keyboard_interrupt_unchanged(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")

    async def handler(args: dict) -> None:
        raise KeyboardInterrupt

    # KeyboardInterrupt inherits from BaseException, not Exception, so the
    # ``except Exception`` filter does not catch it. Intentional aborts
    # (including run_code's ``restart=True`` pathway) must propagate so the
    # framework can do the right thing.
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


def test_wrap_tool_execute_passes_user_error_through_run_code(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")

    async def handler(args: dict) -> None:
        raise UserError("bad run_code args")

    with pytest.raises(UserError):
        asyncio.run(
            cap.wrap_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={},
                handler=handler,
            )
        )

    # UserError propagates without being re-wrapped and without flipping the
    # ``run_code_errored`` flag — the pydantic-ai loop handles UserError
    # separately from ModelRetry.
    assert deps.run_code_errored is False


def test_wrap_tool_execute_passes_user_error_through_other_tools(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")

    async def handler(args: dict) -> None:
        raise UserError("bad args")

    with pytest.raises(UserError):
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


def test_wrap_tool_execute_passes_usage_limit_exceeded_through_run_code(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="run_code")

    async def handler(args: dict) -> None:
        raise UsageLimitExceeded("limit hit")

    with pytest.raises(UsageLimitExceeded):
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


def test_wrap_tool_execute_does_not_wrap_other_tools_passes_through(
    deps: AgentDeps,
) -> None:
    """Non-run_code tools (gitea_*, mcp_*, execute, sleep, ...) propagate
    their raw exceptions instead of being converted to ModelRetry. The agent
    can only meaningfully retry sandboxed code, so wrapping other tools
    would just burn the retry budget on failures that editing code cannot fix.
    """
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="execute")

    async def handler(args: dict) -> None:
        raise KeyError("missing-key")

    with pytest.raises(KeyError) as exc_info:
        asyncio.run(
            cap.wrap_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args={},
                handler=handler,
            )
        )

    assert exc_info.value.__cause__ is None
    assert deps.run_code_errored is False
