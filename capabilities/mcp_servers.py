"""Generic MCP server capability.

Loads any number of MCP servers from the profile YAML and exposes their
tools through pydantic-ai's ``MCPToolset``.

Profile YAML example::

    capabilities:
      mcp:
        python-runner:
          command: uv
          args: ["run", "https://example.com/server.py"]
          env:
            API_KEY: xxx
          include_instructions: true
          allow:
            - run_python
        weather-api:
          url: https://localhost:8080/sse
          auth: my-bearer-token        # string → Bearer header; or omit for no auth
          headers:
            X-Api-Version: "2025-01"
          include_instructions: false

Each server entry is turned into an ``MCPToolset`` wrapped with a
name prefix to avoid tool-name collisions across servers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, AgentToolset, FilteredToolset
from pydantic_ai.toolsets.combined import CombinedToolset

from capabilities.base import make_name_filter

__all__ = ["MCPServersCapability", "make_mcp_capability"]


@dataclass
class MCPServersCapability(AbstractCapability[Any]):
    """Capability that exposes one or more MCP servers as agent tools."""

    _toolset: CombinedToolset[Any]

    def get_toolset(self) -> AgentToolset[Any]:
        return self._toolset


def make_mcp_capability(opts: dict[str, Any]) -> MCPServersCapability:
    """Factory used by the capability registry in ``agent_factory``."""
    toolsets: list[AbstractToolset[Any]] = []

    for name, server_opts in opts.items():
        if not isinstance(server_opts, dict):
            raise ValueError(
                f"MCP server {name!r} must be a configuration dict, got {type(server_opts).__name__}"
            )

        include_instructions = server_opts.get("include_instructions", True)

        if "command" in server_opts:
            from fastmcp.client.transports import StdioTransport

            transport = StdioTransport(
                command=server_opts["command"],
                args=list(server_opts.get("args") or []),
                env=server_opts.get("env"),
                cwd=server_opts.get("cwd"),
            )
            toolset: AbstractToolset[Any] = MCPToolset(
                transport,
                include_instructions=include_instructions,
            )
        elif "url" in server_opts:
            toolset = MCPToolset(
                server_opts["url"],
                include_instructions=include_instructions,
                auth=server_opts.get("auth"),
                headers=server_opts.get("headers"),
            )
        else:
            raise ValueError(f"MCP server {name!r} must have either `command` or `url`")

        # Prefix tool names with the server name to avoid collisions.
        toolset = toolset.prefixed(name)

        # Apply optional allow/deny filter.
        filter_func = make_name_filter(server_opts)
        if filter_func is not None:

            def _build_filter(f):  # type: ignore[no-redef]
                def _filter(_ctx: Any, tool_def: Any) -> bool:
                    return f(tool_def)

                return _filter

            toolset = FilteredToolset(
                cast(AbstractToolset[Any], toolset),
                filter_func=_build_filter(filter_func),
            )

        toolsets.append(toolset)

    if not toolsets:
        raise ValueError("`capabilities.mcp` contains no server entries")

    return MCPServersCapability(_toolset=CombinedToolset(toolsets=toolsets))
