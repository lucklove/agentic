"""Gitea notification poller.

Polling loop
------------
1. ``GET /api/v1/notifications?all=false`` — fetch only unread notifications.
2. Filter to Issue/PR notifications triggered by a comment or mention.
3. For each matching notification, mark it as read **before** reading its
   comments, then run the agent inside a logfire span. Marking before
   ``get_comments`` (and before any post that follows the run) means a
   comment that lands mid-handling becomes a separate, still-unread
   notification that the next poll will pick up; if we marked read after
   the run, that fresh comment could be silently absorbed into the
   notification we just marked read and disappear.
4. Skip paths (closed subject, open dependencies, comment unrelated to this
   agent) also mark the notification read before returning, since no
   progress can be made on them.

Failure isolation (issue #237)
-----------------------------
Per-notification ``Exception`` (including tool / agent / model errors)
raised inside ``_run_agent_iter`` are caught at the
``_handle_notification`` boundary: the partial message history has
already been persisted by ``_run_agent_iter`` via
``close_pending_tool_calls``, so we just log the failure, post a
comment with the conversation marker and the error message so the
human sees what happened, and return. ``BaseException`` subclasses
(``KeyboardInterrupt``, ``asyncio.CancelledError``) still propagate.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass, replace
from string import Template
from typing import Any

import httpx
import logfire
from pydantic_ai import Agent
from pydantic_ai._agent_graph import End
from pydantic_ai.messages import ModelMessage
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import UsageLimits
from tenacity import RetryCallState, retry_if_exception_type, wait_exponential

from conversation import (
    close_pending_tool_calls,
    is_conversation_comment,
    last_seen_comment_id_from_marker,
    load_history,
    marker_for,
    save_history,
    strip_conversation_marker,
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
# Conversation markers identify the *author* of a comment (set in
# ``_post_agent_response``), not the target.  When another agent @-mentions
# us inside its own conversation response, we must not treat that as a fresh
# mention notification, or agents start @-mentioning each other back and
# forth in a ping-pong loop (issue #225).
_ANY_AGENT_MARKER_PATTERN = re.compile(
    r"<!--\s*agentic:@[A-Za-z0-9._-]+\s+last_seen_comment_id=\d+\s*-->"
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
        f"{type_label} #{notif_ctx.number}",
        "You have the right (but not the obligation) to reply via gitea_issue_write.",
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
        strip_conversation_marker(comment.get("body", ""), agent_name)
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
        if not _ANY_AGENT_MARKER_PATTERN.search(comment.get("body", "") or "")
        and _mentions_agent(comment, agent_name)
    ]


def _build_input_message(
    notif_ctx: NotificationContext,
    comments: list[dict[str, Any]],
    last_seen_comment_id: int,
    agent_name: str,
) -> tuple[str | None, int, bool]:
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
        return None, last_seen_comment_id, False

    return (
        "\n\n".join(message_parts),
        max(delivered_comment_ids),
        bool(mentioned_comments),
    )


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


async def _mark_notification_read(
    http: httpx.AsyncClient,
    notif_ctx: NotificationContext,
) -> None:
    await http.patch(f"/api/v1/notifications/threads/{notif_ctx.id}")


async def _run_agent_iter(
    agent: Agent[AgentDeps, str],
    input_message: str,
    run_deps: AgentDeps,
    request_limit: int,
    history: list[ModelMessage],
) -> AgentRunResult[Any]:
    """Drive ``agent.iter`` to completion, persisting history on both paths.

    On success returns the run's ``AgentRunResult``. On failure snapshots
    the partial message history that the ``AgentRun`` accumulated up to
    the exception, closes any dangling tool calls with a synthetic
    ``ToolReturnPart(outcome='interrupted')`` so the next ``iter`` can
    resume the conversation without pydantic-ai's "unprocessed tool calls"
    error, persists that history when ``run_deps.messages_dir`` and
    ``run_deps.notification_subject`` are both set, then re-raises the
    original exception.

    The snapshot is taken **inside** the ``async with agent.iter(...)``
    block so the ``AgentRun``'s internal state is still readable; the
    surrounding ``finally`` always calls ``__aexit__`` to tear the run
    down cleanly.
    """
    subject = run_deps.notification_subject
    assert (
        subject is not None
    ), "_run_agent_iter requires run_deps.notification_subject to persist history"
    messages_dir = run_deps.messages_dir
    history_key = subject_message_key(subject.owner, subject.repo, subject.number)

    iter_ctx = agent.iter(
        input_message,
        deps=run_deps,
        usage_limits=_agent_run_usage_limits(request_limit),
        message_history=history or None,
    )
    run = await iter_ctx.__aenter__()
    saved_messages: list[ModelMessage] = list(history or [])
    result: AgentRunResult[Any]
    try:
        node = run.next_node
        while not isinstance(node, End):
            node = await run.next(node)
        final = run.result
        assert final is not None, "AgentRun reached End without populating result"
        result = final
        saved_messages = list(result.all_messages())
        logfire.info("agent output", output=result.output)
    except BaseException as exc:
        partial = saved_messages
        try:
            partial = list(run.all_messages())
        except Exception:
            pass
        saved_messages = close_pending_tool_calls(
            partial,
            reason=f"{type(exc).__name__}: {exc}",
        )
        if messages_dir is not None:
            save_history(messages_dir, history_key, saved_messages)
        raise
    finally:
        await iter_ctx.__aexit__(*sys.exc_info())

    if messages_dir is not None:
        save_history(messages_dir, history_key, saved_messages)

    return result


async def _handle_notification(
    agent: Agent[AgentDeps, str],
    http: httpx.AsyncClient,
    notif: dict[str, Any],
    deps: AgentDeps,
    request_limit: int = DEFAULT_AGENT_REQUEST_LIMIT,
) -> None:
    """Run the agent for one notification.

    The notification is marked as read as soon as we have committed to
    handling it — before any ``get_comments`` / ``get_subject`` reads, so a
    new comment that lands mid-handling cannot end up being silently marked
    read by a PATCH that races past it. Every skip path marks the
    notification read for the same reason.

    Per-notification agent failures are caught here (Layer 1 isolation —
    issue #237): ``_run_agent_iter`` has already persisted the partial
    message history via ``close_pending_tool_calls``, so this function
    logs the failure, posts a comment with the conversation marker and
    the error message so the human sees what happened, and returns so
    ``poll_once`` can move on to the next notification. ``Exception``
    is caught (not ``BaseException``) so ``KeyboardInterrupt`` and
    ``asyncio.CancelledError`` still propagate to the caller.
    """
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
            await _mark_notification_read(http, notif_ctx)
            return
        if open_dependencies := await notif_ctx.open_dependencies():
            logfire.info(
                "skip notification with open dependencies",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                gitea_username=deps.gitea_username,
                open_dependencies=len(open_dependencies),
            )
            await _mark_notification_read(http, notif_ctx)
            return

        # Mark the thread read *before* reading its comments. A new comment
        # that arrives after this PATCH creates a separate, still-unread
        # notification that the next poll will pick up; if we waited until
        # after ``get_comments`` instead, that new comment could be absorbed
        # into the notification we are about to mark read and silently lost.
        await _mark_notification_read(http, notif_ctx)

        comments = await notif_ctx.get_subject_comments()
        last_seen_comment_id = _latest_delivered_comment_id(
            comments,
            deps.gitea_username,
        )
        input_message, max_delivered_comment_id, has_mentioned_comments = (
            _build_input_message(
                notif_ctx,
                comments,
                last_seen_comment_id,
                deps.gitea_username,
            )
        )

        if input_message is None:
            logfire.info(
                "skip notification unrelated to agent",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                gitea_username=deps.gitea_username,
            )
            return

        run_deps = replace(
            deps,
            notification_subject=NotificationSubject(
                owner=notif_ctx.owner,
                repo=notif_ctx.repo,
                number=notif_ctx.number,
                subject_type=notif_ctx.subject_type,
            ),
            has_mentioned_comments=has_mentioned_comments,
            # ``wiki_reads`` accumulates ``gitea_wiki_read`` calls via
            # ``HarnessCapability.after_tool_execute`` during this run.
            # Reset it so reads from a previous notification don't
            # bleed into the next summary; the compacted history is
            # loaded from ``messages_dir`` for this thread only.
            wiki_reads=[],
        )
        notif_subject = run_deps.notification_subject
        assert notif_subject is not None  # we just set it above

        # Load message history.
        history: list[Any] = []
        if run_deps.messages_dir is not None:
            key = subject_message_key(
                notif_subject.owner,
                notif_subject.repo,
                notif_subject.number,
            )
            history = load_history(run_deps.messages_dir, key)

        try:
            result = await _run_agent_iter(
                agent,
                input_message,
                run_deps,
                request_limit,
                history,
            )
        except Exception as exc:
            # ``_run_agent_iter`` already saved the partial message history
            # via ``close_pending_tool_calls``; we just log, post an error
            # comment so the human sees the failure, then return so
            # ``poll_once`` continues with the next notification instead of
            # letting the exception kill the polling process. ``Exception``
            # (not ``BaseException``) so ``KeyboardInterrupt`` and
            # ``asyncio.CancelledError`` still propagate.
            logfire.exception(
                "agent run failed; partial history persisted, posting error comment",
                repo=notif_ctx.repo_full_name,
                number=notif_ctx.number,
                gitea_username=deps.gitea_username,
                exception_type=type(exc).__name__,
            )
            comment_body = (
                f"{marker_for(deps.gitea_username, max_delivered_comment_id)}"
                f"\n\n{type(exc).__name__}: {exc}"
            )
        else:
            # Post agent output as a comment with conversation marker.
            comment_body = (
                f"{marker_for(deps.gitea_username, max_delivered_comment_id)}"
                f"\n\n{result.output}"
            )
        await http.post(
            _SUBJECT_PATH_TEMPLATE.substitute(
                owner=notif_subject.owner,
                repo=notif_subject.repo,
                path="issues",
                number=notif_subject.number,
            )
            + "/comments",
            json={"body": comment_body},
        )


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


def _validate_retryable_gitea_response(response: httpx.Response) -> None:
    """Raise for transient Gitea HTTP responses that should be retried."""
    if response.status_code in {429, 502, 503, 504}:
        response.raise_for_status()


def _log_retrying_gitea_request(retry_state: RetryCallState) -> None:
    """Log the last transient failure before tenacity sleeps and retries."""
    if retry_state.outcome is None or not retry_state.outcome.failed:
        return

    error = retry_state.outcome.exception()
    if error is None:
        return

    retry_delay_seconds = (
        retry_state.next_action.sleep if retry_state.next_action else None
    )

    logfire.info(
        f"gitea request failed, retrying after {retry_delay_seconds} seconds",
        error_message=str(error),
        attempt=retry_state.attempt_number,
        retry_delay_seconds=retry_delay_seconds,
    )


def _build_gitea_http_client(
    base_url: str, headers: dict[str, str]
) -> httpx.AsyncClient:
    """Create an httpx client with tenacity retry for transient Gitea failures."""
    transport = AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type(
                (
                    httpx.HTTPStatusError,
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    httpx.ReadError,
                )
            ),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=1, max=30),
                max_wait=120,
            ),
            before_sleep=_log_retrying_gitea_request,
            reraise=True,
        ),
        validate_response=_validate_retryable_gitea_response,
    )
    return httpx.AsyncClient(base_url=base_url, headers=headers, transport=transport)


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

    async with _build_gitea_http_client(deps.gitea_base_url, headers) as http:
        async with agent:  # starts the Gitea MCP subprocess
            while True:
                await poll_once(agent, http, deps, request_limit=request_limit)
                await asyncio.sleep(interval)
