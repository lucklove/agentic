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
from dataclasses import dataclass, replace
import re
from string import Template
from typing import Any

import httpx
import logfire
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from deps import AgentDeps, NotificationSubject

# Gitea notification subject types we care about.
_SUBJECT_TYPES = frozenset({"Issue", "Pull"})

# Default for longer autonomous notification-handling loops before pydantic-ai stops.
DEFAULT_AGENT_REQUEST_LIMIT = 100

_SUBJECT_PATH_TEMPLATE = Template("/api/v1/repos/$owner/$repo/$path/$number")
_DEPENDENCIES_PATH_TEMPLATE = Template(
    "/api/v1/repos/$owner/$repo/issues/$number/dependencies"
)
_MENTION_PATTERN = re.compile(r"(?:^|\W)@([A-Za-z0-9._-]+)(?=\W|$)")
_CONTEXT_MESSAGE_TEMPLATE = Template(
    """New notification
Repository: $repo
Type: $type_label #$number
Title: $title

Use your available tools to read the full context of $type_label #$number in $repo before deciding what action to take, then take the required action now when your available tools allow it."""
)


def _agent_run_usage_limits(request_limit: int) -> UsageLimits:
    return UsageLimits(request_limit=request_limit)


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


def _mentioned_users(body: str) -> set[str]:
    return {match.group(1) for match in _MENTION_PATTERN.finditer(body)}


def _notification_span_name(repo_full_name: str, number: str, gitea_username: str) -> str:
    return f"notification {repo_full_name}#{number} ({gitea_username})"


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
            _SUBJECT_PATH_TEMPLATE.substitute(
                owner=self.owner,
                repo=self.repo,
                path=path,
                number=self.number,
            )
        )
        resp.raise_for_status()
        return resp.json()

    async def get_dependencies(self) -> list[dict[str, Any]]:
        resp = await self.http.get(
            _DEPENDENCIES_PATH_TEMPLATE.substitute(
                owner=self.owner,
                repo=self.repo,
                number=self.number,
            )
        )
        resp.raise_for_status()
        return resp.json()

    async def collect_pr_reviewers(self) -> set[str]:
        resp = await self.http.get(
            _SUBJECT_PATH_TEMPLATE.substitute(
                owner=self.owner,
                repo=self.repo,
                path="pulls",
                number=self.number,
            )
            + "/reviews"
        )
        resp.raise_for_status()
        reviews: list[dict[str, Any]] = resp.json()
        return {
            user for user in (_login(review.get("user")) for review in reviews) if user
        }

    async def get_subject_comments(self) -> list[dict[str, Any]]:
        resp = await self.http.get(
            _SUBJECT_PATH_TEMPLATE.substitute(
                owner=self.owner,
                repo=self.repo,
                path="issues",
                number=self.number,
            )
            + "/comments"
        )
        resp.raise_for_status()
        comments: list[dict[str, Any]] = resp.json()
        return comments

    async def get_last_subject_comment(self) -> dict[str, Any] | None:
        comments = await self.get_subject_comments()
        if not comments:
            return None

        return comments[-1]

    async def is_subject_relevant_to_agent(
        self,
        subject: dict[str, Any],
        gitea_username: str,
    ) -> bool:
        last_comment = await self.get_last_subject_comment()
        if last_comment is not None:
            if _login(last_comment.get("user")) == gitea_username:
                return False

            mentioned_users = _mentioned_users(last_comment.get("body") or "")
            if mentioned_users:
                return gitea_username in mentioned_users

        users = _collect_users(subject)

        if self.subject_type == "Pull" and gitea_username not in users:
            users.update(await self.collect_pr_reviewers())

        return gitea_username in users

    async def open_dependencies(self) -> list[dict[str, Any]]:
        dependencies = await self.get_dependencies()
        return [dep for dep in dependencies if not _is_closed(dep)]


def _build_context_message(notif: dict[str, Any]) -> str:
    """Render the initial user message for an agent run from a notification."""
    subject = notif["subject"]
    repo: str = notif["repository"]["full_name"]
    subject_type: str = subject["type"]  # "Issue" or "Pull"
    number: str = _parse_number(subject["url"])
    title: str = subject["title"]

    type_label = "issue" if subject_type == "Issue" else "pull request"

    return _CONTEXT_MESSAGE_TEMPLATE.substitute(
        repo=repo,
        type_label=type_label,
        number=number,
        title=title,
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
    request_limit: int = DEFAULT_AGENT_REQUEST_LIMIT,
) -> None:
    """Run the agent for one notification, then mark it as read on success."""
    notif_ctx = NotificationContext(http=http, notif=notif)

    with logfire.span(
        _notification_span_name(
            notif_ctx.repo_full_name,
            notif_ctx.number,
            deps.gitea_username,
        ),
        repo=notif_ctx.repo_full_name,
        number=notif_ctx.number,
        gitea_username=deps.gitea_username,
        notification_id=notif_ctx.id,
    ):
        subject = await notif_ctx.get_subject()
        if _is_closed(subject):
            logfire.info(
                "skip notification for closed subject",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                gitea_username=deps.gitea_username,
            )
        elif open_dependencies := await notif_ctx.open_dependencies():
            logfire.info(
                "skip notification with open dependencies",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                gitea_username=deps.gitea_username,
                open_dependencies=len(open_dependencies),
            )
        elif not await notif_ctx.is_subject_relevant_to_agent(
            subject,
            deps.gitea_username,
        ):
            logfire.info(
                "skip notification unrelated to agent",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                gitea_username=deps.gitea_username,
            )
        else:
            run_deps = replace(
                deps,
                notification_subject=NotificationSubject(
                    owner=notif_ctx.owner,
                    repo=notif_ctx.repo,
                    number=notif_ctx.number,
                    subject_type=notif_ctx.subject_type,
                ),
            )
            result = await agent.run(
                _build_context_message(notif),
                deps=run_deps,
                usage_limits=_agent_run_usage_limits(request_limit),
            )
            logfire.info("agent output", output=result.output)

        await _mark_notification_read(http, notif_ctx)


async def poll_once(
    agent: Agent[AgentDeps, str],
    http: httpx.AsyncClient,
    deps: AgentDeps,
    request_limit: int = DEFAULT_AGENT_REQUEST_LIMIT,
) -> None:
    """Fetch unread notifications and dispatch one agent run per match."""
    resp = await http.get("/api/v1/notifications", params={"all": "false"})
    resp.raise_for_status()
    notifications: list[dict[str, Any]] = resp.json()

    relevant = (n for n in notifications if n["subject"]["type"] in _SUBJECT_TYPES)

    for notif in relevant:
        await _handle_notification(agent, http, notif, deps, request_limit=request_limit)


async def poll_forever(
    agent: Agent[AgentDeps, str],
    interval: int,
    deps: AgentDeps,
    request_limit: int = DEFAULT_AGENT_REQUEST_LIMIT,
) -> None:
    """Main polling loop.  Runs until cancelled or an unhandled error occurs.

    Opens the MCP subprocess (via ``async with agent``) once and keeps it
    alive across all polls, then tears it down on exit.

    Args:
        agent:    The configured pydantic-ai Agent.
        interval:      Seconds to sleep between polls.
        deps:          Shared runtime deps for filtering and agent runs.
        request_limit: Maximum pydantic-ai model requests per agent run.
    """
    headers = {
        "Authorization": f"token {deps.gitea_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=deps.gitea_base_url, headers=headers) as http:
        async with agent:  # starts the Gitea MCP subprocess
            while True:
                await poll_once(agent, http, deps, request_limit=request_limit)
                await asyncio.sleep(interval)
