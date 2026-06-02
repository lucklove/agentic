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
    assert "reply with an @mention back to that person by calling `gitea_issue_write`" in instructions
    assert "even when the notification subject is a pull request" in instructions
    assert "Do not react to your own comments" in instructions
    assert "Read the full relevant issue or pull request context before acting." in instructions


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


def test_before_output_process_allows_when_last_comment_does_not_mention_agent(
    deps: AgentDeps,
) -> None:
    cap = HarnessCapability()
    ctx = SimpleNamespace(deps=deps, partial_output=False)

    with patch.object(
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
        "_get_last_comment",
        AsyncMock(return_value={"body": "Please check this @code_agent"}),
    ):
        with pytest.raises(ModelRetry, match="mentions @code_agent"):
            asyncio.run(
                cap.before_output_process(ctx, output_context=object(), output="done")
            )
