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
          init_timeout: 30            # seconds; default 30
          allow:
            - run_python
        weather-api:
          url: https://localhost:8080/sse
          auth: my-bearer-token        # string → Bearer header; or omit for no auth
          headers:
            X-Api-Version: "2025-01"
          include_instructions: false
          init_timeout: 10            # override per server

Each server entry is turned into an ``MCPToolset`` wrapped with a
name prefix to avoid tool-name collisions across servers.

``init_timeout`` defaults to ``30`` seconds, the same headroom the
Gitea MCP capability uses. The pydantic-ai default of 5 seconds is
too short when the underlying stdio command has to resolve modules
and compile from a cold/expired cache (see the rationale in
``capabilities/gitea.py`` and issue #201). Profiles may override
per server via ``init_timeout`` in each server config; YAML may
parse values as numbers or numeric strings, and a non-numeric
value fails loudly with ``ValueError``.

To override a globally-configured ``capabilities.mcp`` for one profile,
write ``mcp:`` (empty) in that profile — the merge replaces the global
servers with an empty set and ``make_mcp_capability`` returns a no-op
capability that exposes no MCP tools. See issue #242.
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
    """Factory used by the capability registry in ``agent_factory``.

    An empty ``opts`` (no server entries) returns a valid capability that
    exposes no MCP tools. This is how a profile disables a globally
    configured ``capabilities.mcp`` — writing ``mcp:`` (empty) in the
    profile replaces the merged global servers with nothing instead of
    raising. See issue #242.
    """
    toolsets: list[AbstractToolset[Any]] = []

    for name, server_opts in opts.items():
        if not isinstance(server_opts, dict):
            raise ValueError(
                f"MCP server {name!r} must be a configuration dict, got {type(server_opts).__name__}"
            )

        include_instructions = server_opts.get("include_instructions", True)

        # pydantic_ai's MCPToolset defaults to a 5-second init_timeout, which
        # is too short when an stdio MCP server has to resolve modules and
        # compile from a cold/expired cache (the Gitea MCP hit this in
        # issue #201 with `go run gitea-mcp@latest`). Default to 30s — the
        # same headroom the Gitea capability uses — and let profiles override
        # per server via ``init_timeout`` in the YAML.
        init_timeout: float = float(server_opts.get("init_timeout", 30))

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
                init_timeout=init_timeout,
            )
        elif "url" in server_opts:
            toolset = MCPToolset(
                server_opts["url"],
                include_instructions=include_instructions,
                auth=server_opts.get("auth"),
                headers=server_opts.get("headers"),
                init_timeout=init_timeout,
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

    # Empty opts is a valid no-op; see issue #242.
    return MCPServersCapability(_toolset=CombinedToolset(toolsets=toolsets))
