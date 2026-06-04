from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


def test_instructions_include_shared_rules() -> None:
    instructions = HarnessCapability().get_instructions()

    assert "Harness Rules" in instructions
    assert "automatically posted as a Gitea issue/PR comment" in instructions
    assert "do not use `gitea_*` tools to post a normal reply/comment" in instructions
    assert "must @mention at least one person other than yourself" in instructions
    assert "your final response may @mention people" in instructions
    assert "Do not react to your own comments" in instructions
    assert (
        "Read the full relevant issue or pull request context before acting."
        in instructions
    )
    assert "If checks are pending, wait and poll again" in instructions
    assert "prefer the `sleep` function" in instructions
    assert "If a PR is blocked by failing checks or requested changes" in instructions
    assert "do not treat the inability to request review or merge" in instructions
    assert "gitea_pull_request_write" in instructions
    assert '`method: "merge"`' in instructions
    assert '`merge_style: "squash"`' in instructions
    assert "`delete_branch: true`" in instructions
    assert "not associated with an open PR" in instructions
    assert "choose exactly one of these actions" in instructions
    assert "apply that final state change now and do not post any reply" in instructions
    assert (
        "reply in the thread by calling `gitea_issue_write` and @mention the requester whether the task succeeded or failed"
        in instructions
    )
    assert "provide helpful relevant information in your final response" in instructions


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


def test_before_output_process_allows_missing_subject(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(
        deps=AgentDeps(
            backend=deps.backend,
            gitea_username=deps.gitea_username,
            gitea_base_url=deps.gitea_base_url,
            gitea_token=deps.gitea_token,
            notification_subject=None,
        ),
        partial_output=False,
    )

    output = asyncio.run(
        cap.before_output_process(ctx, output_context=object(), output="done")
    )

    assert output == "done"


def test_before_output_process_allows_partial_output(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps, partial_output=True)

    output = asyncio.run(
        cap.before_output_process(ctx, output_context=object(), output="done")
    )

    assert output == "done"


def test_before_output_process_allows_final_output_mentions_user(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps, partial_output=False)

    with patch.object(
        cap,
        "_is_subject_closed",
        AsyncMock(return_value=True),
    ):
        output = asyncio.run(
            cap.before_output_process(
                ctx,
                output_context=object(),
                output="Need input from @debug_agent.",
            )
        )
    assert output == "Need input from @debug_agent."


def test_before_output_process_allows_when_subject_already_closed(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps, partial_output=False)

    with patch.object(
        cap,
        "_is_subject_closed",
        AsyncMock(return_value=True),
    ), patch.object(
        cap,
        "_get_last_comment",
        AsyncMock(),
    ) as get_last_comment:
        output = asyncio.run(
            cap.before_output_process(ctx, output_context=object(), output="done")
        )

    assert output == "done"
    get_last_comment.assert_not_awaited()


def test_before_output_process_allows_when_last_comment_does_not_mention_agent(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps, partial_output=False)

    with patch.object(
        cap,
        "_is_subject_closed",
        AsyncMock(return_value=False),
    ), patch.object(
        cap,
        "_get_last_comment",
        AsyncMock(return_value={"body": "No mention here"}),
    ):
        output = asyncio.run(
            cap.before_output_process(ctx, output_context=object(), output="done")
        )

    assert output == "done"


def test_before_output_process_raises_when_last_comment_mentions_agent(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps, partial_output=False)

    with patch.object(
        cap,
        "_is_subject_closed",
        AsyncMock(return_value=False),
    ), patch.object(
        cap,
        "_get_last_comment",
        AsyncMock(return_value={"body": "Please check this @code_agent"}),
    ):
        with pytest.raises(
            ModelRetry,
            match="choose exactly one of these actions",
        ):
            asyncio.run(
                cap.before_output_process(ctx, output_context=object(), output="done")
            )


def test_before_output_process_allows_conversation_comment_with_marker(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps, partial_output=False)
    marker_body = (
        "<!-- agentic:@code_agent last_seen_comment_id=12 -->\n\nI fixed the bug."
    )

    with patch.object(
        cap,
        "_is_subject_closed",
        AsyncMock(return_value=False),
    ), patch.object(
        cap,
        "_get_last_comment",
        AsyncMock(return_value={"body": marker_body}),
    ):
        output = asyncio.run(
            cap.before_output_process(ctx, output_context=object(), output="done")
        )

    assert output == "done"


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
    assert ctx.deps.last_seen_comment_id == 3


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
