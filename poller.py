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
import re
from dataclasses import dataclass, replace
from string import Template
from typing import Any

import httpx
import logfire
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from conversation import (
    is_conversation_comment,
    last_seen_comment_id_from_marker,
    load_history,
    marker_for,
    save_history,
    subject_message_key,
)
from deps import AgentDeps, NotificationSubject

# Gitea notification subject types we care about.
_SUBJECT_TYPES = frozenset({"Issue", "Pull"})

# Default for longer autonomous notification-handling loops before pydantic-ai stops.
DEFAULT_AGENT_REQUEST_LIMIT = 100

_SUBJECT_PATH_TEMPLATE = Template("/api/v1/repos/$owner/$repo/$path/$number")
_DEPENDENCIES_PATH_TEMPLATE = Template(
    "/api/v1/repos/$owner/$repo/issues/$number/dependencies"
)
_MENTION_PATTERN = re.compile(r"(?:^|[^\w`])@([A-Za-z0-9._-]+)(?=\W|$)")
_CONTEXT_MESSAGE_TEMPLATE = Template(
    """New notification
Repository: $repo
Type: $type_label #$number
Title: $title
$visible_comments_hint

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


def _comment_author(comment: dict[str, Any]) -> str | None:
    """Extract the comment author's login name.

    Gitea's real HTTP API returns ``user`` as an object with ``login`` (and
    ``username``) keys, but the ``gitea_*`` MCP tools normalize it to a plain
    string.  We handle both shapes so marker-based self-authorship detection
    works on the real payload.
    """
    user = comment.get("user")
    if isinstance(user, str):
        return user
    if isinstance(user, dict):
        return user.get("login") or user.get("username")
    return None


def _is_closed(item: dict[str, Any]) -> bool:
    return item.get("state") == "closed" or item.get("closed_at") is not None


def _mentioned_users(body: str) -> set[str]:
    return {match.group(1) for match in _MENTION_PATTERN.finditer(body)}


def _is_self_marker_comment(comment: dict[str, Any]) -> bool:
    author = _comment_author(comment)
    if not author:
        return False

    return is_conversation_comment(comment.get("body", ""), author)


def _comment_id(comment: dict[str, Any]) -> int:
    return int(comment.get("id") or 0)


def _latest_delivered_comment_id(
    comments: list[dict[str, Any]], agent_name: str
) -> int:
    for comment in reversed(comments):
        if _comment_author(comment) != agent_name:
            continue

        seen_id = last_seen_comment_id_from_marker(
            comment.get("body", ""),
            agent_name,
        )
        if seen_id is not None:
            return seen_id
    return 0


def _comments_after(
    comments: list[dict[str, Any]],
    last_seen_comment_id: int,
) -> list[dict[str, Any]]:
    return [
        comment for comment in comments if _comment_id(comment) > last_seen_comment_id
    ]


def _mentions_agent(comment: dict[str, Any], agent_name: str) -> bool:
    if _comment_author(comment) == agent_name:
        return False
    return agent_name in _mentioned_users(comment.get("body") or "")


def _format_mentioned_comments_message(
    notif_ctx: NotificationContext,
    comments: list[dict[str, Any]],
) -> str:
    type_label = "issue" if notif_ctx.subject_type == "Issue" else "PR"
    parts = [
        f"Someone mentioned you in {notif_ctx.repo_full_name} "
        f"{type_label} #{notif_ctx.number}"
    ]

    for comment in comments:
        author = _comment_author(comment) or "unknown"
        parts.append(
            f"======== comment id: {_comment_id(comment)}, from @{author} ========"
        )
        parts.append(comment.get("body", ""))

    return "\n\n".join(parts)


def _chat_messages_after(
    comments: list[dict[str, Any]],
    last_seen_comment_id: int,
    agent_name: str,
) -> list[str]:
    return [
        comment.get("body", "")
        for comment in _comments_after(comments, last_seen_comment_id)
        if _comment_author(comment) != agent_name
        and is_conversation_comment(comment.get("body", ""), agent_name)
    ]


def _mentioned_comments_after(
    comments: list[dict[str, Any]],
    last_seen_comment_id: int,
    agent_name: str,
) -> list[dict[str, Any]]:
    return [
        comment
        for comment in _comments_after(comments, last_seen_comment_id)
        if not is_conversation_comment(comment.get("body", ""), agent_name)
        and _mentions_agent(comment, agent_name)
    ]


def _build_input_message(
    notif_ctx: NotificationContext,
    comments: list[dict[str, Any]],
    last_seen_comment_id: int,
    agent_name: str,
) -> tuple[str | None, int]:
    chat_messages = _chat_messages_after(comments, last_seen_comment_id, agent_name)
    mentioned_comments = _mentioned_comments_after(
        comments,
        last_seen_comment_id,
        agent_name,
    )

    message_parts: list[str] = []
    delivered_comment_ids: list[int] = []

    if chat_messages:
        message_parts.append("\n\n".join(chat_messages))
        delivered_comment_ids.extend(
            _comment_id(comment)
            for comment in _comments_after(comments, last_seen_comment_id)
            if _comment_author(comment) != agent_name
            and is_conversation_comment(comment.get("body", ""), agent_name)
        )

    if mentioned_comments:
        message_parts.append(
            _format_mentioned_comments_message(notif_ctx, mentioned_comments)
        )
        delivered_comment_ids.extend(
            _comment_id(comment) for comment in mentioned_comments
        )

    if not delivered_comment_ids:
        return None, last_seen_comment_id

    return "\n\n".join(message_parts), max(delivered_comment_ids)


def _notification_span_name(
    repo_full_name: str, number: str, gitea_username: str
) -> str:
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

    async def open_dependencies(self) -> list[dict[str, Any]]:
        dependencies = await self.get_dependencies()
        return [dep for dep in dependencies if not _is_closed(dep)]


def _build_context_message(
    notif: dict[str, Any],
    visible_count: int = 0,
) -> str:
    """Render the initial user message for an agent run from a notification."""
    subject = notif["subject"]
    repo: str = notif["repository"]["full_name"]
    subject_type: str = subject["type"]  # "Issue" or "Pull"
    number: str = _parse_number(subject["url"])
    title: str = subject["title"]

    type_label = "issue" if subject_type == "Issue" else "pull request"

    if visible_count > 0:
        visible_comments_hint = (
            f"There are currently {visible_count} visible comment(s) on this thread "
            "(conversation-type comments are excluded). "
            "Read the latest context with tools before taking action."
        )
    else:
        visible_comments_hint = "No visible comments yet on this thread."

    return _CONTEXT_MESSAGE_TEMPLATE.substitute(
        repo=repo,
        type_label=type_label,
        number=number,
        title=title,
        visible_comments_hint=visible_comments_hint,
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
        else:
            comments = await notif_ctx.get_subject_comments()
            last_seen_comment_id = _latest_delivered_comment_id(
                comments,
                deps.gitea_username,
            )
            input_message, max_delivered_comment_id = _build_input_message(
                notif_ctx,
                comments,
                last_seen_comment_id,
                deps.gitea_username,
            )

            if input_message is None:
                logfire.info(
                    "skip notification unrelated to agent",
                    repo=notif_ctx.repo_full_name,
                    number=notif_ctx.number,
                    gitea_username=deps.gitea_username,
                )
                await _mark_notification_read(http, notif_ctx)
                return

            run_deps = replace(
                deps,
                notification_subject=NotificationSubject(
                    owner=notif_ctx.owner,
                    repo=notif_ctx.repo,
                    number=notif_ctx.number,
                    subject_type=notif_ctx.subject_type,
                ),
            )

            # Load message history.
            history: list[Any] = []
            if run_deps.messages_dir is not None:
                key = subject_message_key(
                    notif_ctx.owner,
                    notif_ctx.repo,
                    notif_ctx.subject_type,
                    notif_ctx.number,
                )
                history = load_history(run_deps.messages_dir, key)

            result = await agent.run(
                input_message,
                deps=run_deps,
                usage_limits=_agent_run_usage_limits(request_limit),
                message_history=history or None,
            )
            logfire.info("agent output", output=result.output)

            # Save message history.
            if run_deps.messages_dir is not None:
                save_history(run_deps.messages_dir, key, result.all_messages())

            # Post agent output as a comment with conversation marker.
            comment_body = (
                f"{marker_for(deps.gitea_username, max_delivered_comment_id)}"
                f"\n\n{result.output}"
            )
            await http.post(
                _SUBJECT_PATH_TEMPLATE.substitute(
                    owner=notif_ctx.owner,
                    repo=notif_ctx.repo,
                    path="issues",
                    number=notif_ctx.number,
                )
                + "/comments",
                json={"body": comment_body},
            )

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
        await _handle_notification(
            agent, http, notif, deps, request_limit=request_limit
        )


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
