"""Gitea notification poller.

Polling loop
------------
1. ``GET /api/v1/notifications?all=false`` — fetch only unread notifications.
2. Filter to Issue/PR notifications triggered by a comment or mention.
3. For each matching notification, run the agent inside a logfire span.
4. Mark the notification as read only after it is successfully handled or
   intentionally skipped. If the agent or an underlying tool raises, leave the
   notification unread so it remains visible for retry or human intervention.

Errors are not caught here; they propagate to the caller and exit the process.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import logfire
from pydantic_ai import Agent

from deps import AgentDeps

# Gitea notification subject types we care about.
_SUBJECT_TYPES = frozenset({"Issue", "Pull"})


def _parse_number(url: str) -> str:
    """Extract the issue/PR number from a Gitea API subject URL.

    e.g. ``http://gitea.ai/api/v1/repos/owner/repo/issues/42`` → ``"42"``
    """
    return url.rstrip("/").rsplit("/", 1)[-1]


def _parse_repo(full_name: str) -> tuple[str, str]:
    """Split a Gitea ``owner/repo`` full name."""
    owner, repo = full_name.split("/", 1)
    return owner, repo


def _login(user: dict[str, Any] | None) -> str | None:
    if not user:
        return None
    return user.get("login") or user.get("username")


def _collect_users(item: dict[str, Any]) -> set[str]:
    """Return creator, assignees, and reviewers from an issue/PR payload."""
    users = {_login(item.get("user")), _login(item.get("assignee"))}

    for field in ("assignees", "requested_reviewers", "reviewers"):
        users.update(_login(user) for user in item.get(field, []) or [])

    return {user for user in users if user}


def _is_closed(item: dict[str, Any]) -> bool:
    return item.get("state") == "closed" or item.get("closed_at") is not None


@dataclass
class NotificationContext:
    """Gitea notification plus API helpers for its issue/PR subject."""

    http: httpx.AsyncClient
    notif: dict[str, Any]

    @property
    def id(self) -> int:
        return self.notif["id"]

    @property
    def repo_full_name(self) -> str:
        return self.notif["repository"]["full_name"]

    @property
    def owner(self) -> str:
        owner, _ = _parse_repo(self.repo_full_name)
        return owner

    @property
    def repo(self) -> str:
        _, repo = _parse_repo(self.repo_full_name)
        return repo

    @property
    def subject_type(self) -> str:
        return self.notif["subject"]["type"]

    @property
    def number(self) -> str:
        return _parse_number(self.notif["subject"]["url"])

    async def get_subject(self) -> dict[str, Any]:
        path = "pulls" if self.subject_type == "Pull" else "issues"
        resp = await self.http.get(
            f"/api/v1/repos/{self.owner}/{self.repo}/{path}/{self.number}"
        )
        resp.raise_for_status()
        return resp.json()

    async def get_dependencies(self) -> list[dict[str, Any]]:
        resp = await self.http.get(
            f"/api/v1/repos/{self.owner}/{self.repo}/issues/{self.number}/dependencies"
        )
        resp.raise_for_status()
        return resp.json()

    async def get_blocks(self) -> list[dict[str, Any]]:
        resp = await self.http.get(
            f"/api/v1/repos/{self.owner}/{self.repo}/issues/{self.number}/blocks"
        )
        resp.raise_for_status()
        return resp.json()

    async def collect_pr_reviewers(self) -> set[str]:
        resp = await self.http.get(
            f"/api/v1/repos/{self.owner}/{self.repo}/pulls/{self.number}/reviews"
        )
        resp.raise_for_status()
        reviews: list[dict[str, Any]] = resp.json()
        return {
            user for user in (_login(review.get("user")) for review in reviews) if user
        }

    async def is_subject_relevant_to_agent(
        self,
        subject: dict[str, Any],
        gitea_username: str,
    ) -> bool:
        users = _collect_users(subject)

        if self.subject_type == "Pull" and gitea_username not in users:
            users.update(await self.collect_pr_reviewers())

        return gitea_username in users

    async def open_dependencies(self) -> list[dict[str, Any]]:
        dependencies = await self.get_dependencies()
        return [dep for dep in dependencies if not _is_closed(dep)]

    async def comment_on_open_blocks(self, closed_subject: dict[str, Any]) -> None:
        closed_url = closed_subject.get("html_url")
        body = (
            f"A dependency has been closed: {closed_url}"
            if closed_url
            else "A dependency has been closed."
        )

        for block in await self.get_blocks():
            if _is_closed(block):
                continue

            repo_full_name = (block.get("repository") or {}).get("full_name")
            if not repo_full_name:
                continue

            owner, repo = _parse_repo(repo_full_name)
            number = block["number"]
            resp = await self.http.post(
                f"/api/v1/repos/{owner}/{repo}/issues/{number}/comments",
                json={"body": body},
            )
            resp.raise_for_status()


def _build_context_message(notif: dict[str, Any]) -> str:
    """Render the initial user message for an agent run from a notification."""
    subject = notif["subject"]
    repo: str = notif["repository"]["full_name"]
    subject_type: str = subject["type"]  # "Issue" or "Pull"
    number: str = _parse_number(subject["url"])
    title: str = subject["title"]

    type_label = "issue" if subject_type == "Issue" else "pull request"

    return (
        f"New notification\n"
        f"Repository: {repo}\n"
        f"Type: {type_label} #{number}\n"
        f"Title: {title}\n\n"
        f"Use your Gitea tools to read the full context of {type_label} #{number} "
        f"in {repo} (body and all comments), then decide what action to take."
    )


async def _mark_notification_read(
    http: httpx.AsyncClient,
    notif_ctx: NotificationContext,
) -> None:
    await http.patch(f"/api/v1/notifications/threads/{notif_ctx.id}")


async def _handle_notification(
    agent: Agent[AgentDeps, str],
    http: httpx.AsyncClient,
    notif: dict[str, Any],
    deps: AgentDeps,
) -> None:
    """Run the agent for one notification, then mark it as read on success."""
    notif_ctx = NotificationContext(http=http, notif=notif)

    with logfire.span(
        "notification {repo}#{number}",
        repo=notif_ctx.repo_full_name,
        number=notif_ctx.number,
        notification_id=notif_ctx.id,
    ):
        subject = await notif_ctx.get_subject()
        if _is_closed(subject):
            logfire.info(
                "skip notification for closed subject",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
            )
            await _mark_notification_read(http, notif_ctx)
            return

        if not await notif_ctx.is_subject_relevant_to_agent(
            subject,
            deps.gitea_username,
        ):
            logfire.info(
                "skip notification unrelated to agent",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                gitea_username=deps.gitea_username,
            )
            await _mark_notification_read(http, notif_ctx)
            return

        open_dependencies = await notif_ctx.open_dependencies()
        if open_dependencies:
            logfire.info(
                "skip notification with open dependencies",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                open_dependencies=len(open_dependencies),
            )
            await _mark_notification_read(http, notif_ctx)
            return

        result = await agent.run(
            _build_context_message(notif),
            deps=deps,
        )
        logfire.info("agent output", output=result.output)

        subject = await notif_ctx.get_subject()
        if _is_closed(subject):
            await notif_ctx.comment_on_open_blocks(subject)

        await _mark_notification_read(http, notif_ctx)


async def poll_once(
    agent: Agent[AgentDeps, str],
    http: httpx.AsyncClient,
    deps: AgentDeps,
) -> None:
    """Fetch unread notifications and dispatch one agent run per match."""
    resp = await http.get("/api/v1/notifications", params={"all": "false"})
    resp.raise_for_status()
    notifications: list[dict[str, Any]] = resp.json()

    relevant = (n for n in notifications if n["subject"]["type"] in _SUBJECT_TYPES)

    for notif in relevant:
        await _handle_notification(agent, http, notif, deps)


async def poll_forever(
    agent: Agent[AgentDeps, str],
    interval: int,
    deps: AgentDeps,
) -> None:
    """Main polling loop.  Runs until cancelled or an unhandled error occurs.

    Opens the MCP subprocess (via ``async with agent``) once and keeps it
    alive across all polls, then tears it down on exit.

    Args:
        agent:    The configured pydantic-ai Agent.
        interval: Seconds to sleep between polls.
        deps:     Shared runtime deps for filtering and agent runs.
    """
    headers = {
        "Authorization": f"token {deps.gitea_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=deps.gitea_base_url, headers=headers) as http:
        async with agent:  # starts the Gitea MCP subprocess
            while True:
                await poll_once(agent, http, deps)
                await asyncio.sleep(interval)
