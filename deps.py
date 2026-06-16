"""Runtime dependencies passed to every agent.run() call."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai_backends import LocalBackend


@dataclass
class NotificationSubject:
    """Current issue/PR subject being handled during a poller-driven run."""

    owner: str
    repo: str
    number: str
    subject_type: str


@dataclass
class AgentDeps:
    """Shared runtime context for agent runs and polling decisions."""

    backend: LocalBackend
    gitea_username: str
    gitea_base_url: str
    gitea_token: str
    http_client_factory: Callable[[str, str], Any] | None = None
    notification_subject: NotificationSubject | None = None
    profile_name: str = ""
    messages_dir: Path | None = None
