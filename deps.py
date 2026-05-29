"""Runtime dependencies passed to every agent.run() call."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai_backends import LocalBackend


@dataclass
class AgentDeps:
    """Shared runtime context for agent runs and polling decisions."""

    backend: LocalBackend
    gitea_username: str
