from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deps import AgentDeps
from poller import (
    _build_context_message,
    _handle_notification,
    _latest_seen_comment_id,
    _notification_span_name,
)


class FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


class FakeHTTP:
    def __init__(
        self,
        subject: dict[str, object] | None = None,
        comments: list[dict[str, object]] | None = None,
        reviews: list[dict[str, object]] | None = None,
    ) -> None:
        self.subject = subject or {
            "state": "open",
            "closed_at": None,
            "user": {"login": "human"},
            "assignees": [{"login": "code_agent"}],
        }
        self.comments = comments or [
            {
                "id": 1,
                "body": "Please handle this @code_agent",
                "user": {"login": "human"},
            }
        ]
        self.reviews = reviews or []
        self.patches: list[str] = []
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def get(self, path: str, params: dict[str, str] | None = None) -> FakeResp:
        if path.endswith("/issues/31"):
            return FakeResp(self.subject)
        if path.endswith("/issues/31/comments"):
            return FakeResp(self.comments)
        if path.endswith("/issues/31/dependencies"):
            return FakeResp([])
        if path.endswith("/pulls/31"):
            return FakeResp(self.subject)
        if path.endswith("/pulls/31/comments"):
            raise AssertionError(path)
        if path.endswith("/pulls/31/reviews"):
            return FakeResp(self.reviews)
        raise AssertionError(path)

    async def patch(self, path: str) -> FakeResp:
        self.patches.append(path)
        return FakeResp({})

    async def post(self, path: str, json: dict[str, object] | None = None) -> FakeResp:
        self.posts.append((path, json or {}))
        return FakeResp({})


class PassingAgent:
    def __init__(self) -> None:
        self.usage_limits: object | None = None
        self.run_deps: AgentDeps | None = None
        self.run_message: str | None = None
        self.run_history: object | None = None

    async def run(
        self,
        message: str,
        deps: AgentDeps,
        usage_limits: object | None = None,
        message_history: object | None = None,
    ) -> SimpleNamespace:
        self.usage_limits = usage_limits
        self.run_deps = deps
        self.run_message = message
        self.run_history = message_history
        return SimpleNamespace(
            output="done",
            all_messages=lambda: [],
        )


class FailingAgent:
    async def run(
        self,
        message: str,
        deps: AgentDeps,
        usage_limits: object | None = None,
        message_history: object | None = None,
    ) -> SimpleNamespace:
        raise RuntimeError("simulated underlying tool failure")


@pytest.fixture
def deps() -> AgentDeps:
    return AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
    )


def notification(subject_type: str = "Issue") -> dict[str, object]:
    path = "pulls" if subject_type == "Pull" else "issues"
    return {
        "id": 123,
        "repository": {"full_name": "autonomous/agentic"},
        "subject": {
            "type": subject_type,
            "url": f"http://gitea.example/api/v1/repos/autonomous/agentic/{path}/31",
            "title": "bug",
        },
    }


def test_build_context_message_excludes_shared_rules() -> None:
    message = _build_context_message(notification(), visible_count=0)

    assert "Shared notification-handling rules" not in message
    assert "if someone @mentions you" not in message
    assert "Do not react to your own comments" not in message
    assert "Read the project's AGENTS.md" not in message


def test_build_context_message_includes_visible_count() -> None:
    message = _build_context_message(notification(), visible_count=3)

    assert "3 visible comment(s)" in message
    assert "conversation-type comments are excluded" in message


def test_build_context_message_zero_visible_comments() -> None:
    message = _build_context_message(notification(), visible_count=0)

    assert "No visible comments yet" in message


def test_notification_span_name_includes_gitea_username() -> None:
    assert (
        _notification_span_name("autonomous/agentic", "31", "code_agent")
        == "notification autonomous/agentic#31 (code_agent)"
    )


def test_handle_notification_leaves_thread_unread_when_agent_fails(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP()

        with pytest.raises(RuntimeError, match="simulated underlying tool failure"):
            await _handle_notification(FailingAgent(), http, notification(), deps)

        assert http.patches == []

    asyncio.run(run())


def test_handle_notification_marks_thread_read_after_success(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP()
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps, request_limit=42)

        assert getattr(agent.usage_limits, "request_limit") == 42
        assert agent.run_deps is not None
        assert agent.run_deps.notification_subject is not None
        assert agent.run_deps.notification_subject.owner == "autonomous"
        assert agent.run_deps.notification_subject.repo == "agentic"
        assert agent.run_deps.notification_subject.number == "31"
        assert agent.run_deps.notification_subject.subject_type == "Issue"
        assert deps.notification_subject is None
        assert http.patches == ["/api/v1/notifications/threads/123"]

    asyncio.run(run())


def test_handle_notification_ignores_backtick_wrapped_mentions(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Please check `@code_agent` as an example.",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is None

    asyncio.run(run())


def test_handle_notification_ignores_email_like_text(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Contact foo@bar for details.",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is None

    asyncio.run(run())


def test_handle_notification_marks_thread_read_when_last_comment_mentions_agent(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Please take a look @code_agent",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is not None

    asyncio.run(run())


def test_handle_notification_uses_unseen_mention_not_last_comment(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\ndone",
                    "user": {"login": "code_agent"},
                },
                {
                    "id": 2,
                    "body": "Please take a look @code_agent",
                    "user": {"login": "human"},
                },
                {
                    "id": 3,
                    "body": "Can you review this? @review_agent",
                    "user": {"login": "human"},
                },
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert agent.run_deps is not None
        assert agent.run_deps.last_seen_comment_id == 0
        assert agent.run_message is not None
        assert (
            "Someone mentioned you in autonomous/agentic issue #31" in agent.run_message
        )
        assert "======== comment id: 2, from @human ========" in agent.run_message
        assert "Please take a look @code_agent" in agent.run_message

    asyncio.run(run())


def test_handle_notification_skips_when_no_unseen_comment_mentions_agent(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [{"login": "code_agent"}],
            },
            comments=[
                {
                    "id": 1,
                    "body": "No direct mention here.",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is None

    asyncio.run(run())


def test_handle_notification_skips_when_last_comment_mentions_someone_else(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [{"login": "code_agent"}],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Please check this @review_agent",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is None

    asyncio.run(run())


def test_handle_notification_skips_when_last_comment_is_by_agent(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [{"login": "code_agent"}],
            },
            comments=[
                {
                    "id": 1,
                    "body": "I already handled this @code_agent",
                    "user": {"login": "code_agent"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is None

    asyncio.run(run())


def test_handle_notification_skips_pull_when_last_comment_mentions_someone_else(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
                "requested_reviewers": [{"login": "code_agent"}],
                "reviewers": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Can you review this? @review_agent",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification("Pull"), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is None

    asyncio.run(run())


def test_handle_notification_skips_when_only_other_agent_marker_after_seen(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [{"login": "code_agent"}],
            },
            comments=[
                {
                    "id": 1,
                    "body": "<!-- agentic:@review_agent last_seen_comment_id=0 -->\n\nRequested changes.",
                    "user": {"login": "review_agent"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is None

    asyncio.run(run())


def test_handle_notification_marks_pull_thread_read_when_last_comment_mentions_agent(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
                "requested_reviewers": [],
                "reviewers": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Can you review this? @code_agent",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification("Pull"), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is not None
        assert agent.run_deps.notification_subject is not None
        assert agent.run_deps.notification_subject.subject_type == "Pull"

    asyncio.run(run())


def test_handle_notification_skips_when_last_comment_is_by_agent_even_with_mentions(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Done! @human",
                    "user": {"login": "code_agent"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert agent.run_deps is None
        assert http.patches == ["/api/v1/notifications/threads/123"]

    asyncio.run(run())


def test_handle_notification_conversation_marker_uses_body_as_input(
    deps: AgentDeps,
) -> None:
    marker_body = (
        "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\nI fixed the bug."
    )

    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": marker_body,
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert agent.run_message == marker_body
        assert agent.run_deps is not None
        # Auto-post still happens unconditionally
        assert len(http.posts) == 1
        assert http.posts[0][1]["body"].startswith(
            "<!-- agentic:@code_agent last_seen_comment_id=1 -->"
        )

    asyncio.run(run())


def test_handle_notification_conversation_marker_does_not_strip_marker(
    deps: AgentDeps,
) -> None:
    body_with_marker = (
        "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\nPlease continue."
    )

    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": body_with_marker,
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert agent.run_message == body_with_marker

    asyncio.run(run())


def test_handle_notification_auto_posts_comment_with_marker(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP()
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert len(http.posts) == 1
        path, body = http.posts[0]
        assert path.endswith("/issues/31/comments")
        assert body["body"].startswith(
            "<!-- agentic:@code_agent last_seen_comment_id=1 -->"
        )
        assert "done" in body["body"]

    asyncio.run(run())


def test_handle_notification_does_not_auto_post_when_skipped(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [{"login": "code_agent"}],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Done by agent",
                    "user": {"login": "code_agent"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.posts == []

    asyncio.run(run())


def test_handle_notification_logs_gitea_username_for_skips(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        closed_http = FakeHTTP(
            subject={
                "state": "closed",
                "closed_at": "2026-01-01T00:00:00Z",
                "user": {"login": "human"},
                "assignees": [{"login": "code_agent"}],
            }
        )

        async def open_dependencies(self) -> list[dict[str, object]]:
            return [{"state": "open", "number": 99}]

        with patch("poller.logfire.info") as info:
            await _handle_notification(
                PassingAgent(), closed_http, notification(), deps
            )
            with patch(
                "poller.NotificationContext.open_dependencies", new=open_dependencies
            ):
                await _handle_notification(
                    PassingAgent(), FakeHTTP(), notification(), deps
                )

        skip_calls = [
            call
            for call in info.call_args_list
            if call.args[0].startswith("skip notification")
        ]
        assert len(skip_calls) == 2
        for call in skip_calls:
            assert call.kwargs["gitea_username"] == "code_agent"

    asyncio.run(run())


def test_handle_notification_checks_open_dependencies_before_relevance(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            }
        )

        async def open_dependencies(self) -> list[dict[str, object]]:
            return [{"state": "open", "number": 99}]

        with patch(
            "poller.NotificationContext.open_dependencies", new=open_dependencies
        ):
            await _handle_notification(PassingAgent(), http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]

    asyncio.run(run())


def test_handle_notification_excludes_agent_own_marker_from_input_message(
    deps: AgentDeps,
) -> None:
    """Regression: the agent's own marker comment must not appear in input.

    When the raw Gitea HTTP payload uses a dict for ``user``, the old
    ``_comment_author`` returned ``None`` for the agent's own comment,
    causing it to leak into ``self_marker_messages`` alongside the human's
    reply.  This test ensures only the human's conversation comment is passed
    as the input message.
    """

    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "@code_agent please fix",
                    "user": {"login": "human"},
                },
                {
                    "id": 2,
                    "body": "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\ndone",
                    "user": {"login": "code_agent"},
                },
                {
                    "id": 3,
                    "body": "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\nsecond reply from human",
                    "user": {"login": "human"},
                },
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert agent.run_message is not None
        assert agent.run_message == (
            "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\nsecond reply from human"
        )
        assert http.posts[0][1]["body"].startswith(
            "<!-- agentic:@code_agent last_seen_comment_id=3 -->"
        )

    asyncio.run(run())


def test_handle_notification_merges_chat_then_mentions_into_one_message(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\nchat 1",
                    "user": {"login": "human"},
                },
                {
                    "id": 2,
                    "body": "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\nchat 2 @code_agent",
                    "user": {"login": "human"},
                },
                {
                    "id": 3,
                    "body": "Please handle this @code_agent",
                    "user": {"login": "agent_a"},
                },
                {
                    "id": 4,
                    "body": "Following up for @code_agent",
                    "user": {"login": "agent_b"},
                },
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert agent.run_message == (
            "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\nchat 1\n\n"
            "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\nchat 2 @code_agent\n\n"
            "Someone mentioned you in autonomous/agentic issue #31\n\n"
            "======== comment id: 3, from @agent_a ========\n\n"
            "Please handle this @code_agent\n\n"
            "======== comment id: 4, from @agent_b ========\n\n"
            "Following up for @code_agent"
        )
        assert http.posts[0][1]["body"].startswith(
            "<!-- agentic:@code_agent last_seen_comment_id=4 -->"
        )

    asyncio.run(run())


def test_handle_notification_chat_comment_with_mention_is_not_duplicated(
    deps: AgentDeps,
) -> None:
    chat_body = "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\nplease continue @code_agent"

    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": chat_body,
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert agent.run_message == chat_body
        assert agent.run_message.count("please continue @code_agent") == 1
        assert (
            "Someone mentioned you in autonomous/agentic issue #31"
            not in agent.run_message
        )
        assert http.posts[0][1]["body"].startswith(
            "<!-- agentic:@code_agent last_seen_comment_id=1 -->"
        )

    asyncio.run(run())


def test_handle_notification_formats_pull_mentions_as_pr(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
                "requested_reviewers": [],
                "reviewers": [],
            },
            comments=[
                {
                    "id": 1,
                    "body": "Can you review this? @code_agent",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification("Pull"), deps)

        assert agent.run_message is not None
        assert "Someone mentioned you in autonomous/agentic PR #31" in agent.run_message

    asyncio.run(run())


def test_latest_seen_comment_id_ignores_marker_from_other_authors() -> None:
    comments = [
        {
            "id": 2,
            "body": "<!-- agentic:@code_agent last_seen_comment_id=1 -->\n\ndone",
            "user": {"login": "code_agent"},
        },
        {
            "id": 3,
            "body": "<!-- agentic:@code_agent last_seen_comment_id=999 -->\n\nspoofed",
            "user": {"login": "human"},
        },
    ]

    assert _latest_seen_comment_id(comments, "code_agent") == 1
