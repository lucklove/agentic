from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deps import AgentDeps
from poller import _build_context_message, _handle_notification


class FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


class FakeHTTP:
    def __init__(self, subject: dict[str, object] | None = None) -> None:
        self.subject = subject or {
            "state": "open",
            "closed_at": None,
            "user": {"login": "human"},
            "assignees": [{"login": "code_agent"}],
        }
        self.patches: list[str] = []

    async def get(self, path: str, params: dict[str, str] | None = None) -> FakeResp:
        if path.endswith("/issues/31"):
            return FakeResp(self.subject)
        if path.endswith("/issues/31/dependencies"):
            return FakeResp([])
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


def notification() -> dict[str, object]:
    return {
        "id": 123,
        "repository": {"full_name": "autonomous/agentic"},
        "subject": {
            "type": "Issue",
            "url": "http://gitea.example/api/v1/repos/autonomous/agentic/issues/31",
            "title": "bug",
        },
    }


def test_build_context_message_excludes_shared_rules() -> None:
    message = _build_context_message(notification())

    assert "Shared notification-handling rules" not in message
    assert "if someone @mentions you" not in message
    assert "Do not react to your own comments" not in message
    assert "Read the project's AGENTS.md" not in message


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


def test_handle_notification_logs_gitea_username_for_skips(
    deps: AgentDeps,
) -> None:
    async def run() -> None:
        closed_http = FakeHTTP(subject={
            "state": "closed",
            "closed_at": "2026-01-01T00:00:00Z",
            "user": {"login": "human"},
            "assignees": [{"login": "code_agent"}],
        })
        unrelated_http = FakeHTTP(subject={
            "state": "open",
            "closed_at": None,
            "user": {"login": "human"},
            "assignees": [],
        })

        async def open_dependencies(self) -> list[dict[str, object]]:
            return [{"state": "open", "number": 99}]

        with patch("poller.logfire.info") as info:
            await _handle_notification(PassingAgent(), closed_http, notification(), deps)
            await _handle_notification(PassingAgent(), unrelated_http, notification(), deps)
            with patch("poller.NotificationContext.open_dependencies", new=open_dependencies):
                await _handle_notification(PassingAgent(), FakeHTTP(), notification(), deps)

        skip_calls = [call for call in info.call_args_list if call.args[0].startswith("skip notification")]
        assert len(skip_calls) == 3
        for call in skip_calls:
            assert call.kwargs["gitea_username"] == "code_agent"

    asyncio.run(run())
