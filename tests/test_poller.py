from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deps import AgentDeps
from poller import (
    NotificationContext,
    _build_context_message,
    _handle_notification,
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
        self.comments = comments or []
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


def test_handle_notification_uses_existing_relevance_when_last_comment_has_no_mentions(
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
                    "body": "No direct mention here.",
                    "user": {"login": "human"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is not None

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


def test_get_last_subject_comment_skips_self_marker_comment() -> None:
    async def run() -> None:
        http = FakeHTTP(
            comments=[
                {
                    "body": "Please take a look @code_agent",
                    "user": {"login": "human"},
                },
                {
                    "body": "<!-- agentic:@review_agent -->\n\nRequested changes.",
                    "user": {"login": "review_agent"},
                },
            ]
        )
        ctx = NotificationContext(http=http, notif=notification())

        last_comment = await ctx.get_last_subject_comment()

        assert last_comment is not None
        assert last_comment["user"] == {"login": "human"}

    asyncio.run(run())


def test_handle_notification_uses_assignment_when_last_comment_is_other_agent_marker(
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
                    "body": "<!-- agentic:@review_agent -->\n\nRequested changes.",
                    "user": {"login": "review_agent"},
                }
            ],
        )
        agent = PassingAgent()

        await _handle_notification(agent, http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]
        assert agent.run_deps is not None

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
    marker_body = "<!-- agentic:@code_agent -->\n\nI fixed the bug."

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
        assert http.posts[0][1]["body"].startswith("<!-- agentic:@code_agent -->")

    asyncio.run(run())


def test_handle_notification_conversation_marker_does_not_strip_marker(
    deps: AgentDeps,
) -> None:
    body_with_marker = "<!-- agentic:@code_agent -->\n\nPlease continue."

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
        assert body["body"].startswith("<!-- agentic:@code_agent -->")
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
