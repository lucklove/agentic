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
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse

__all__ = [
    "marker_for",
    "is_conversation_comment",
    "last_seen_comment_id_from_marker",
    "strip_conversation_marker",
    "visible_comments",
    "subject_message_key",
    "load_history",
    "save_history",
]

_MARKER_TEMPLATE = (
    "<!-- agentic:@{agent_name} last_seen_comment_id={last_seen_comment_id} -->"
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


def is_conversation_comment(body: str, agent_name: str) -> bool:
    """Return ``True`` if *body* contains the conversation marker for *agent_name*."""
    return _marker_pattern(agent_name).search(body or "") is not None


def last_seen_comment_id_from_marker(body: str, agent_name: str) -> int | None:
    """Return the marker's delivered comment watermark, if present."""
    match = _marker_pattern(agent_name).search(body or "")
    if match is None:
        return None
    return int(match.group(1))


def strip_conversation_marker(body: str, agent_name: str) -> str:
    """Remove the current agent's leading conversation marker from *body*."""
    return _marker_pattern(agent_name).sub("", body or "", count=1).lstrip()


def visible_comments(
    comments: list[dict[str, Any]],
    agent_name: str,
) -> list[dict[str, Any]]:
    """Filter out conversation-type comments for *agent_name*.

    A comment is considered conversation-type if its ``body`` contains the
    current agent's marker. Both agent-authored and human-authored comments
    that reference the marker are excluded.
    """
    return [
        c
        for c in comments
        if not is_conversation_comment(c.get("body", ""), agent_name)
    ]


def subject_message_key(
    owner: str,
    repo: str,
    subject_type: str,
    number: str,
) -> str:
    """Return a stable hex key for an issue/PR suitable as a filename."""
    raw = f"{owner}/{repo}/{subject_type}/{number}"
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
