"""Gitea MCP capability.

Wraps ``MCPServerStdio`` in a pydantic-ai capability so that:

- The Gitea MCP server participates in the normal capability lifecycle
  (``async with agent`` starts/stops the subprocess automatically).
- Profile YAML can restrict which MCP tools the agent may call via
  ``allow`` / ``deny`` lists — the same semantics as the skills filter.

Profile YAML example::

    capabilities:
      gitea:
        allow:
          - get_issue
          - list_issue_comments
          - create_issue_comment
        # deny:
        #   - delete_issue_comment

Omit both ``allow`` and ``deny`` to expose every tool the MCP server
provides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.toolsets import AgentToolset, FilteredToolset

from capabilities.base import make_name_filter

__all__ = ["GiteaMCPCapability", "make_gitea_capability"]


@dataclass
class GiteaMCPCapability(AbstractCapability[Any]):
    """Gitea MCP capability with optional tool-name filtering.

    ``get_toolset()`` returns a ``FilteredToolset`` when a filter is
    configured, or the raw ``MCPServerStdio`` when no filtering is needed.
    Either way, ``async with agent`` correctly manages the subprocess
    lifecycle because both implement the toolset async context protocol.
    """

    _server: MCPServerStdio
    _filter: Callable[[Any], bool] | None  # None → expose all tools

    def get_toolset(self) -> AgentToolset[Any]:
        if self._filter is None:
            return self._server
        return FilteredToolset(self._server, filter_func=lambda _ctx, tool: self._filter(tool))

    def get_instructions(self) -> str:
        # The MCP server injects its own instructions via include_instructions=True.
        return ""


def make_gitea_capability(
    base_url: str,
    mcp_command: list[str],
    token: str,
    opts: dict[str, Any],
) -> GiteaMCPCapability:
    """Factory used by the capability registry in ``agent_factory``."""
    server = MCPServerStdio(
        mcp_command[0],
        args=mcp_command[1:],
        env={
            "GITEA_HOST": base_url,
            "GITEA_ACCESS_TOKEN": token,
        },
        include_instructions=True,
    )
    return GiteaMCPCapability(
        _server=server,
        _filter=make_name_filter(opts),
    )
