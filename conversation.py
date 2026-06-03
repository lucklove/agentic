"""Conversation helpers for Web UI dialogue via Gitea comments.

Marker convention
-----------------
Agent comments on issue/pr threads start with a hidden HTML marker::

    <!-- agentic:@<agent-name> -->

This marker lets the poller and tool layer distinguish "conversation-type"
comments (Web UI dialogue) from regular context comments.
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage

__all__ = [
    "marker_for",
    "is_conversation_comment",
    "visible_comments",
    "subject_message_key",
    "load_history",
    "save_history",
]


def marker_for(agent_name: str) -> str:
    """Return the hidden HTML comment marker for *agent_name*."""
    return f"<!-- agentic:@{agent_name} -->"


def is_conversation_comment(body: str, agent_name: str) -> bool:
    """Return ``True`` if *body* contains the conversation marker for *agent_name*."""
    return marker_for(agent_name) in (body or "")


def visible_comments(
    comments: list[dict[str, Any]],
    agent_name: str,
) -> list[dict[str, Any]]:
    """Filter out conversation-type comments for *agent_name*.

    A comment is considered conversation-type if its ``body`` contains the
    current agent's marker.  Both agent-authored and human-authored comments
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
    """Load persisted message history for *key*, or return ``[]``."""
    path = messages_dir / f"{key}.pkl"
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
        return pickle.loads(data)  # noqa: S301
    except Exception:
        return []


def save_history(
    messages_dir: Path,
    key: str,
    messages: list[ModelMessage],
) -> None:
    """Persist *messages* for *key* under *messages_dir*."""
    messages_dir.mkdir(parents=True, exist_ok=True)
    path = messages_dir / f"{key}.pkl"
    path.write_bytes(pickle.dumps(messages))
