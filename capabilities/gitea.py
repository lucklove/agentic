"""Gitea MCP capability.

Wraps ``MCPServerStdio`` in a pydantic-ai capability so that:

- The Gitea MCP server participates in the normal capability lifecycle
  (``async with agent`` starts/stops the subprocess automatically).
- Profile YAML can restrict which MCP tools the agent may call via
  ``allow`` / ``deny`` lists — the same semantics as the skills filter.
- Profile YAML can override ``init_timeout`` (default: ``30`` seconds)
  to wait for the MCP initialization handshake. The pydantic-ai default
  of 5 seconds is too short when the underlying ``go run @latest`` has
  to resolve modules and compile from a cold/expired Go module cache
  (see issue #201).

Profile YAML example::

    capabilities:
      gitea:
        init_timeout: 30        # seconds; default 30
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
from typing import Any, Callable, cast

from fastmcp.client.transports import StdioTransport
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, AgentToolset, FilteredToolset

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

    _server: AgentToolset[Any]
    _filter: Callable[[Any], bool] | None  # None → expose all tools

    def get_toolset(self) -> AgentToolset[Any]:
        if self._filter is None:
            return self._server
        f = self._filter  # capture non-None for the lambda
        return FilteredToolset(
            cast(AbstractToolset[Any], self._server),
            filter_func=lambda _ctx, tool: f(tool),
        )


def make_gitea_capability(
    base_url: str,
    mcp_command: list[str],
    token: str,
    opts: dict[str, Any],
) -> GiteaMCPCapability:
    """Factory used by the capability registry in ``agent_factory``."""
    # pydantic_ai's MCPToolset defaults to a 5-second init_timeout, which
    # is too short for `go run gitea-mcp@latest` when the Go module cache
    # is cold or expired (the compile + version-resolution step can take
    # 6-8 seconds). Default to 30s to give the handshake enough headroom
    # while still failing fast on genuinely broken servers. Profiles may
    # override via ``init_timeout`` in the capability config.
    init_timeout: float = float(opts.get("init_timeout", 30))
    server = MCPToolset(
        StdioTransport(
            mcp_command[0],
            args=mcp_command[1:],
            env={
                "GITEA_HOST": base_url,
                "GITEA_ACCESS_TOKEN": token,
                # Force direct resolution for the internal gitea.ai module path.
                # Some environments default to public proxies that cannot serve it.
                "GOPROXY": "direct",
                # Bypass Go module proxy and checksum DB for the local gitea.ai domain
                # (which may only be resolvable via /etc/hosts and not reachable by the proxy).
                "GOPRIVATE": "gitea.ai",
                "GONOSUMDB": "gitea.ai",
                "GOINSECURE": "gitea.ai",
            },
        ),
        include_instructions=False,
        init_timeout=init_timeout,
    ).prefixed("gitea")
    return GiteaMCPCapability(
        _server=server,
        _filter=make_name_filter(opts),
    )
