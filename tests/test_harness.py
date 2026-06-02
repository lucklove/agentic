from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
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
    assert "reply in the thread by calling `gitea_issue_write`" in instructions
    assert "even when the notification subject is a pull request" in instructions
    assert "Do not react to your own comments" in instructions
    assert (
        "Read the full relevant issue or pull request context before acting."
        in instructions
    )
    assert "If checks are pending, wait and poll again" in instructions
    assert 'prefer `execute("sleep N")`' in instructions
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
    assert (
        "post a helpful comment with relevant information by calling `gitea_issue_write` and do not @mention anyone"
        in instructions
    )


def test_before_tool_execute_allows_non_review_request_tool(deps: AgentDeps) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_issue_write")
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


def test_before_tool_execute_allows_review_request_without_checks(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_pull_request_write")
    args = {
        "method": "add_reviewers",
        "owner": "autonomous",
        "repo": "agentic",
        "pull_number": 82,
    }

    with patch.object(
        cap,
        "_get_non_successful_checks",
        AsyncMock(return_value=[]),
    ):
        output = asyncio.run(
            cap.before_tool_execute(
                ctx,
                call=SimpleNamespace(),
                tool_def=tool_def,
                args=args,
            )
        )

    assert output == args


def test_before_tool_execute_blocks_review_request_with_failing_checks(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps)
    tool_def = SimpleNamespace(name="gitea_pull_request_write")
    args = {
        "method": "add_reviewers",
        "owner": "autonomous",
        "repo": "agentic",
        "pull_number": 82,
    }

    with patch.object(
        cap,
        "_get_non_successful_checks",
        AsyncMock(return_value=["ci/test (failure)", "lint (pending)"]),
    ):
        with pytest.raises(ModelRetry, match=r"ci/test \(failure\), lint \(pending\)"):
            asyncio.run(
                cap.before_tool_execute(
                    ctx,
                    call=SimpleNamespace(),
                    tool_def=tool_def,
                    args=args,
                )
            )


def test_get_non_successful_checks_returns_only_non_success_statuses(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/repos/autonomous/agentic/pulls/82":
            return httpx.Response(200, json={"head": {"sha": "abc123"}})
        if request.url.path == "/api/v1/repos/autonomous/agentic/commits/abc123/status":
            return httpx.Response(
                200,
                json={
                    "statuses": [
                        {"context": "ci/test", "status": "failure"},
                        {"context": "lint", "status": "pending"},
                        {"context": "typecheck", "status": "success"},
                        {"target_url": "http://example/check", "status": "error"},
                        {"status": None},
                    ]
                },
            )
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)

    with patch.object(
        cap,
        "_gitea_client",
        return_value=httpx.AsyncClient(
            base_url=deps.gitea_base_url,
            headers={
                "Authorization": f"token {deps.gitea_token}",
                "Content-Type": "application/json",
            },
            transport=transport,
        ),
    ):
        checks = asyncio.run(
            cap._get_non_successful_checks(
                owner="autonomous",
                repo="agentic",
                pull_number=82,
                deps=deps,
            )
        )

    assert checks == [
        "ci/test (failure)",
        "lint (pending)",
        "http://example/check (error)",
        "unknown (unknown)",
    ]


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
