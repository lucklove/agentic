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
from types import SimpleNamespace
from typing import Any, Callable, cast

from fastmcp.client.transports import StdioTransport
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, AgentToolset, FilteredToolset

from capabilities.base import make_name_filter

__all__ = ["GiteaMCPCapability", "make_gitea_capability"]


_GITEA_INSTRUCTIONS = """\
## Gitea functions

Use `gitea_*` functions for Gitea API actions and server-side repository state, \
such as issues, pull requests, reviews, labels, assignees, releases, and \
workflow actions.

Use local filesystem/shell tools for local repository operations when those \
capabilities are available. Local `git fetch`, `git checkout`, `git worktree`, \
`git commit`, and `git push` are normal local repository operations.

You may use either `gitea_*` functions or local `git` commands when both fit \
the task, based on the available tools and the profile's role.
"""

_GITEA_PULL_REQUEST_READ_INSTRUCTIONS = """\
When using `gitea_pull_request_read` with method="get_review_comments", pass \
`review_id`. To read all PR review comments, first call method="get_reviews", \
then call method="get_review_comments" once per review id.
"""


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

    def get_instructions(self) -> str:
        instructions = _GITEA_INSTRUCTIONS
        tool = SimpleNamespace(name="gitea_pull_request_read")
        if self._filter is None or self._filter(tool):
            instructions += "\n\n" + _GITEA_PULL_REQUEST_READ_INSTRUCTIONS
        return instructions

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
    ).prefixed("gitea")
    return GiteaMCPCapability(
        _server=server,
        _filter=make_name_filter(opts),
    )
