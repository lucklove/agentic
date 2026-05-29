"""Gitea notification poller.

Polling loop
------------
1. ``GET /api/v1/notifications?all=false`` — fetch only unread notifications.
2. Filter to Issue/PR notifications triggered by a comment or mention.
3. For each matching notification, run the agent inside a logfire span.
4. In a ``finally`` block, mark the notification as read regardless of
   whether the agent succeeded or raised — this prevents infinite retry
   loops on persistent agent errors.

Errors are not caught here; they propagate to the caller and exit the process.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import logfire
from pydantic_ai import Agent
from pydantic_ai_backends import LocalBackend

from capabilities.filesystem import AgentDeps

# Gitea notification subject types we care about.
_SUBJECT_TYPES = frozenset({"Issue", "Pull"})


def _parse_number(url: str) -> str:
    """Extract the issue/PR number from a Gitea API subject URL.

    e.g. ``http://gitea.ai/api/v1/repos/owner/repo/issues/42`` → ``"42"``
    """
    return url.rstrip("/").rsplit("/", 1)[-1]


def _build_context_message(notif: dict[str, Any]) -> str:
    """Render the initial user message for an agent run from a notification."""
    subject = notif["subject"]
    repo: str = notif["repository"]["full_name"]
    subject_type: str = subject["type"]           # "Issue" or "Pull"
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


async def _handle_notification(
    agent: Agent[AgentDeps, str],
    http: httpx.AsyncClient,
    notif: dict[str, Any],
) -> None:
    """Run the agent for one notification, then mark it as read."""
    notif_id: int = notif["id"]
    repo: str = notif["repository"]["full_name"]
    number: str = _parse_number(notif["subject"]["url"])

    try:
        with logfire.span(
            "notification {repo}#{number}",
            repo=repo,
            number=number,
            notification_id=notif_id,
        ):
            result = await agent.run(
                _build_context_message(notif),
                deps=AgentDeps(backend=LocalBackend("/")),
            )
            logfire.info("agent output", output=result.output)
    finally:
        # Always mark as read so we don't re-process on the next poll.
        await http.patch(f"/api/v1/notifications/threads/{notif_id}")


async def poll_once(
    agent: Agent[AgentDeps, str],
    http: httpx.AsyncClient,
) -> None:
    """Fetch unread notifications and dispatch one agent run per match."""
    resp = await http.get("/api/v1/notifications", params={"all": "false"})
    resp.raise_for_status()
    notifications: list[dict[str, Any]] = resp.json()

    relevant = (
        n for n in notifications
        if n["subject"]["type"] in _SUBJECT_TYPES
    )

    for notif in relevant:
        await _handle_notification(agent, http, notif)


async def poll_forever(
    agent: Agent[AgentDeps, str],
    base_url: str,
    token: str,
    interval: int,
) -> None:
    """Main polling loop.  Runs until cancelled or an unhandled error occurs.

    Opens the MCP subprocess (via ``async with agent``) once and keeps it
    alive across all polls, then tears it down on exit.

    Args:
        agent:    The configured pydantic-ai Agent.
        base_url: Gitea instance base URL (e.g. ``http://gitea.ai``).
        token:    Gitea personal access token for the profile.
        interval: Seconds to sleep between polls.
    """
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=base_url, headers=headers) as http:
        async with agent:  # starts the Gitea MCP subprocess
            while True:
                await poll_once(agent, http)
                await asyncio.sleep(interval)
