from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deps import AgentDeps
from poller import _handle_notification


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

    async def run(
        self,
        message: str,
        deps: AgentDeps,
        usage_limits: object | None = None,
    ) -> SimpleNamespace:
        self.usage_limits = usage_limits
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
        assert http.patches == ["/api/v1/notifications/threads/123"]

    asyncio.run(run())
