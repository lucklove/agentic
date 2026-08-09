"""Conversation helpers for Web UI dialogue via Gitea comments.

Marker convention
-----------------
Agent comments on issue/pr threads start with a hidden HTML marker::

    <!-- agentic:@<agent-name> -->

This marker lets the poller and tool layer distinguish "conversation-type"
comments (Web UI dialogue) from regular context comments, and persist the
highest comment id the poller has already delivered to the agent.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

__all__ = [
    "marker_for",
    "ANY_AGENT_MARKER_PATTERN",
    "is_conversation_comment_for",
    "is_conversation_comment",
    "last_seen_comment_id_from_marker",
    "strip_all_conversation_markers",
    "visible_comments",
    "subject_message_key",
    "load_history",
    "save_history",
    "close_pending_tool_calls",
]

_MARKER_TEMPLATE = (
    "<!-- agentic:@{agent_name} last_seen_comment_id={last_seen_comment_id} -->"
)

# Matches *any* agent's conversation marker. Used by ``poller`` to detect
# comments authored by another agent (issue #225) and by
# ``strip_all_conversation_markers`` to scrub every marker from a body before
# delivering it as a chat message (issue #279).
ANY_AGENT_MARKER_PATTERN = re.compile(
    r"<!--\s*agentic:@[A-Za-z0-9._-]+\s+last_seen_comment_id=\d+\s*-->"
)

logger = logging.getLogger(__name__)

_HISTORY_LOAD_EXCEPTIONS = (
    OSError,
    pickle.PickleError,
    AttributeError,
    EOFError,
    ImportError,
    IndexError,
)


def marker_for(agent_name: str, last_seen_comment_id: int = 0) -> str:
    """Return the hidden HTML comment marker for *agent_name*."""
    return _MARKER_TEMPLATE.format(
        agent_name=agent_name,
        last_seen_comment_id=last_seen_comment_id,
    )


def _marker_pattern(agent_name: str) -> re.Pattern[str]:
    return re.compile(
        r"<!--\s*agentic:@"
        + re.escape(agent_name)
        + r"\s+last_seen_comment_id=(\d+)\s*-->",
    )


def is_conversation_comment_for(body: str, agent_name: str) -> bool:
    """Return ``True`` if *body* contains the conversation marker for *agent_name*.

    This answers the per-agent delivery question: *is ``agent_name`` one of
    the recipients of this comment?* Use this when deciding whether to
    dispatch a comment to a specific agent (e.g. :mod:`poller`'s
    ``_chat_messages_after`` and ``_build_input_message`` filter paths).

    For the recipient-agnostic counterpart — *is this comment a piece of
    conversation traffic at all?* — see :func:`is_conversation_comment`.
    """
    return _marker_pattern(agent_name).search(body or "") is not None


def is_conversation_comment(body: str) -> bool:
    """Return ``True`` if *body* carries any conversation marker.

    This is the recipient-agnostic counterpart of
    :func:`is_conversation_comment_for`: a comment is considered
    conversation-tagged when its body contains any
    ``<!-- agentic:@<agent> last_seen_comment_id=<n> -->`` marker,
    regardless of which agent the marker names. Use this for *filtering*
    decisions (e.g. dropping conversation traffic from a visible-comments
    stream — see :func:`visible_comments` and agentic/agentic#282). For
    per-agent delivery decisions, use the ``_for`` variant.
    """
    return ANY_AGENT_MARKER_PATTERN.search(body or "") is not None


def last_seen_comment_id_from_marker(body: str, agent_name: str) -> int | None:
    """Return the marker's delivered comment watermark, if present."""
    match = _marker_pattern(agent_name).search(body or "")
    if match is None:
        return None
    return int(match.group(1))


def strip_all_conversation_markers(body: str) -> str:
    """Remove every ``<!-- agentic:@<agent-name> last_seen_comment_id=<n> -->`` marker.

    A single comment can carry markers for several agents when a human or
    another agent wants to dispatch the same payload to multiple agents at
    once (agentic/agentic#279). The poller routes such comments to every
    agent whose marker is present; each delivered message must contain
    only the human-visible body, not the dispatching markers. Markers are
    internal coordination signals and never belong in the prompt, no
    matter whose marker they are.
    """
    return ANY_AGENT_MARKER_PATTERN.sub("", body or "").strip()


def visible_comments(
    comments: list[dict[str, Any]],
    agent_name: str,
) -> list[dict[str, Any]]:
    """Filter out every conversation-tagged comment, regardless of recipient agent.

    A comment is considered conversation-tagged when its ``body`` carries any
    ``<!-- agentic:@<agent> last_seen_comment_id=<n> -->`` marker, not just
    one addressed to *agent_name*. Both agent-authored and human-authored
    comments are excluded, irrespective of who the dispatcher routed the
    comment to (see agentic/agentic#282).

    The *agent_name* argument is retained for backwards compatibility with
    the pre-#282 call sites in :mod:`capabilities.harness`; it is
    intentionally ignored by the filtering predicate.
    """
    del agent_name
    return [c for c in comments if not is_conversation_comment(c.get("body", ""))]


def subject_message_key(
    owner: str,
    repo: str,
    number: str,
) -> str:
    """Return a stable hex key for an issue/PR suitable as a filename."""
    raw = f"{owner}/{repo}/{number}"
    return hashlib.md5(raw.encode()).hexdigest()


def load_history(messages_dir: Path, key: str) -> list[ModelMessage]:
    """Load persisted message history for *key*, or return ``[]``.

    Corrupt or incompatible history files are ignored after a warning so polling
    can continue without silently discarding the failure signal.
    """
    path = messages_dir / f"{key}.pkl"
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
        history = pickle.loads(data)  # noqa: S301
    except _HISTORY_LOAD_EXCEPTIONS:
        logger.warning("failed to load message history from %s", path, exc_info=True)
        return []

    if not isinstance(history, list) or any(
        not isinstance(message, (ModelRequest, ModelResponse)) for message in history
    ):
        raise TypeError(f"unexpected message history payload in {path}")

    return history


def save_history(
    messages_dir: Path,
    key: str,
    messages: list[ModelMessage],
) -> None:
    """Persist *messages* for *key* under *messages_dir*."""
    messages_dir.mkdir(parents=True, exist_ok=True)
    path = messages_dir / f"{key}.pkl"
    path.write_bytes(pickle.dumps(messages))


def close_pending_tool_calls(
    history: list[ModelMessage],
    reason: str,
    timestamp: datetime | None = None,
) -> list[ModelMessage]:
    """Append a synthetic ``ModelRequest`` that closes dangling tool calls.

    Walks *history* for ``ToolCallPart`` entries that have no matching
    ``ToolReturnPart`` or ``RetryPromptPart`` (i.e. the previous run aborted
    before the tool produced a result) and emits one
    ``ToolReturnPart(outcome='interrupted')`` per dangling call in a fresh
    ``ModelRequest`` appended to the end.

    ``RetryPromptPart`` must be treated as a closure: pydantic-ai emits it
    when a tool raised ``ModelRetry``, returned an unknown tool name, or
    triggered a pydantic validation error. Those responses carry a
    ``tool_call_id`` and *do* settle the call from pydantic-ai's perspective
    — emitting a second synthetic ``ToolReturnPart`` for the same id
    produces a malformed history that model APIs reject with HTTP 400 on the
    next resume.

    The returned list is the original *history* unchanged when nothing is
    dangling, so callers can use the return value directly for
    ``save_history`` and the next ``iter(message_history=...)`` without
    triggering pydantic-ai's "Cannot provide a new user prompt when the
    message history contains unprocessed tool calls" error.

    Args:
        history:   The accumulated message history captured before the
                   exception escaped ``agent.iter``.
        reason:    Human-readable description of why the previous run was
                   interrupted; embedded in each synthetic return's content
                   so the next model turn knows what happened.
        timestamp: Optional explicit timestamp for the synthetic parts;
                   defaults to ``datetime.now()``.
    """
    when = timestamp or datetime.now()
    seen_call_ids: set[str] = set()
    for message in history:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                # Both ToolReturnPart (tool succeeded) and RetryPromptPart
                # (tool failed / validation error) close the matching
                # ToolCallPart from pydantic-ai's perspective. Treating
                # only the success path as "seen" was the bug that
                # produced duplicate tool_call_id responses and 400 errors
                # on resume.
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    seen_call_ids.add(part.tool_call_id)

    dangling: list[ToolCallPart] = []
    for message in history:
        if isinstance(message, ModelResponse):
            for part in message.parts:  # type: ignore[assignment]
                if not isinstance(part, ToolCallPart):
                    continue
                if part.tool_call_id in seen_call_ids:
                    continue
                dangling.append(part)

    if not dangling:
        return history

    synth_parts = [
        ToolReturnPart(
            tool_name=part.tool_name,
            content=f"previous run was interrupted before this tool returned; reason: {reason}",
            tool_call_id=part.tool_call_id,
            outcome="interrupted",
            timestamp=when,
        )
        for part in dangling
    ]
    return [*history, ModelRequest(parts=synth_parts)]
