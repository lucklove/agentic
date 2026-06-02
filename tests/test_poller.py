from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deps import AgentDeps
from poller import _build_context_message, _handle_notification, _notification_span_name


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


class PassingAgent:
    def __init__(self) -> None:
        self.usage_limits: object | None = None
        self.run_deps: AgentDeps | None = None

    async def run(
        self,
        message: str,
        deps: AgentDeps,
        usage_limits: object | None = None,
    ) -> SimpleNamespace:
        self.usage_limits = usage_limits
        self.run_deps = deps
        return SimpleNamespace(output="done")


class FailingAgent:
    async def run(
        self,
        message: str,
        deps: AgentDeps,
        usage_limits: object | None = None,
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
    message = _build_context_message(notification())

    assert "Shared notification-handling rules" not in message
    assert "if someone @mentions you" not in message
    assert "Do not react to your own comments" not in message
    assert "Read the project's AGENTS.md" not in message


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
        unrelated_http = FakeHTTP(
            subject={
                "state": "open",
                "closed_at": None,
                "user": {"login": "human"},
                "assignees": [],
            }
        )

        async def open_dependencies(self) -> list[dict[str, object]]:
            return [{"state": "open", "number": 99}]

        with patch("poller.logfire.info") as info:
            await _handle_notification(
                PassingAgent(), closed_http, notification(), deps
            )
            await _handle_notification(
                PassingAgent(), unrelated_http, notification(), deps
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
        assert len(skip_calls) == 3
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

        async def is_subject_relevant_to_agent(
            self,
            subject: dict[str, object],
            gitea_username: str,
        ) -> bool:
            raise AssertionError(
                "relevance should not be checked after dependency skip"
            )

        with patch(
            "poller.NotificationContext.open_dependencies", new=open_dependencies
        ):
            with patch(
                "poller.NotificationContext.is_subject_relevant_to_agent",
                new=is_subject_relevant_to_agent,
            ):
                await _handle_notification(PassingAgent(), http, notification(), deps)

        assert http.patches == ["/api/v1/notifications/threads/123"]

    asyncio.run(run())
