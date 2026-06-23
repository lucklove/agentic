from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

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


def test_before_tool_execute_blocks_comment_without_other_mention(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")
    args = {"method": "add_comment", "body": "Done @code_agent"}

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
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {"method": "get_comments"}
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
        )
    )
    tool_def = SimpleNamespace(name="gitea_issue_read")
    args = {"method": "get_comments"}
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

    with pytest.raises(RuntimeError):
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

    with pytest.raises(RuntimeError):
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
