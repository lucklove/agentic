"""Tests for the generic MCP server capability."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from capabilities.mcp_servers import MCPServersCapability, make_mcp_capability


class FakeToolset:
    """Minimal fake that quacks like an AbstractToolset."""

    def __init__(self, name: str):
        self.name = name
        self.prefixed_name = name
        self._include_instructions = False

    def prefixed(self, prefix: str) -> FakeToolset:
        self.prefixed_name = prefix
        return self

    async def get_tools(self, _ctx):
        return {}

    async def call_tool(self, name, tool_args, ctx, tool):
        return None

    async def for_run(self, _ctx):
        return self

    async def for_run_step(self, _ctx):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    @property
    def id(self):
        return None

    @property
    def label(self):
        return f"FakeToolset({self.name})"

    @property
    def tool_name_conflict_hint(self):
        return "hint"

    def apply(self, visitor):
        visitor(self)

    def visit_and_replace(self, visitor):
        return visitor(self)

    def filtered(self, filter_func):
        return self


class _FakeFilteredToolset(FakeToolset):
    """Fake that mimics FilteredToolset wrapping."""

    def __init__(self, wrapped: FakeToolset):
        super().__init__(wrapped.name)
        self.wrapped = wrapped


def _fake_prefixed(self, prefix: str):
    self.prefixed_name = prefix
    return self


def _fake_filtered(self, filter_func):
    ft = _FakeFilteredToolset(self)
    ft.prefixed_name = self.prefixed_name
    return ft


def test_make_mcp_capability_stdio() -> None:
    with patch("capabilities.mcp_servers.MCPToolset") as MockToolset:
        instance = FakeToolset("stdio-server")
        instance.prefixed = _fake_prefixed.__get__(instance, FakeToolset)
        instance.filtered = _fake_filtered.__get__(instance, FakeToolset)
        MockToolset.return_value = instance

        cap = make_mcp_capability(
            {
                "python-runner": {
                    "command": "uv",
                    "args": ["run", "server.py"],
                    "env": {"KEY": "val"},
                }
            }
        )

    assert isinstance(cap, MCPServersCapability)
    MockToolset.assert_called_once()
    call_kwargs = MockToolset.call_args.kwargs
    assert call_kwargs["include_instructions"] is True
    assert instance.prefixed_name == "python-runner"


def test_make_mcp_capability_url() -> None:
    with patch("capabilities.mcp_servers.MCPToolset") as MockToolset:
        instance = FakeToolset("url-server")
        instance.prefixed = _fake_prefixed.__get__(instance, FakeToolset)
        instance.filtered = _fake_filtered.__get__(instance, FakeToolset)
        MockToolset.return_value = instance

        cap = make_mcp_capability(
            {
                "weather-api": {
                    "url": "https://localhost:8080/sse",
                    "include_instructions": False,
                }
            }
        )

    assert isinstance(cap, MCPServersCapability)
    MockToolset.assert_called_once_with(
        "https://localhost:8080/sse",
        include_instructions=False,
        auth=None,
        headers=None,
    )
    assert instance.prefixed_name == "weather-api"


def test_make_mcp_capability_multiple_servers() -> None:
    with patch("capabilities.mcp_servers.MCPToolset") as MockToolset:
        calls = []

        def side_effect(client, *, include_instructions=True, **kwargs):
            inst = FakeToolset(str(client))
            inst._include_instructions = include_instructions
            inst.prefixed = _fake_prefixed.__get__(inst, FakeToolset)
            inst.filtered = _fake_filtered.__get__(inst, FakeToolset)
            calls.append(inst)
            return inst

        MockToolset.side_effect = side_effect

        cap = make_mcp_capability(
            {
                "server-a": {"command": "cmd-a"},
                "server-b": {"url": "http://b"},
            }
        )

    assert isinstance(cap, MCPServersCapability)
    assert len(calls) == 2
    assert calls[0].prefixed_name == "server-a"
    assert calls[1].prefixed_name == "server-b"


def test_make_mcp_capability_allow_filter() -> None:
    with patch("capabilities.mcp_servers.MCPToolset") as MockToolset:
        instance = FakeToolset("stdio-server")
        instance.prefixed = _fake_prefixed.__get__(instance, FakeToolset)
        instance.filtered = _fake_filtered.__get__(instance, FakeToolset)
        MockToolset.return_value = instance

        cap = make_mcp_capability(
            {
                "python-runner": {
                    "command": "uv",
                    "allow": ["run_python"],
                }
            }
        )

    assert isinstance(cap, MCPServersCapability)
    # Filtering wraps the prefixed toolset; our fake records that.
    # We mainly assert no crash and the toolset chain was built.


def test_make_mcp_capability_missing_server_config() -> None:
    with pytest.raises(ValueError, match="MCP server .* must have either"):
        make_mcp_capability({"bad-server": {}})


def test_make_mcp_capability_no_servers() -> None:
    with pytest.raises(ValueError, match="no server entries"):
        make_mcp_capability({})


def test_make_mcp_capability_url_auth_and_headers() -> None:
    with patch("capabilities.mcp_servers.MCPToolset") as MockToolset:
        instance = FakeToolset("url-server")
        instance.prefixed = _fake_prefixed.__get__(instance, FakeToolset)
        instance.filtered = _fake_filtered.__get__(instance, FakeToolset)
        MockToolset.return_value = instance

        make_mcp_capability(
            {
                "secure-api": {
                    "url": "https://api.example.com/mcp",
                    "auth": "my-secret-token",
                    "headers": {"X-Api-Version": "2025-01"},
                    "include_instructions": False,
                }
            }
        )

    MockToolset.assert_called_once_with(
        "https://api.example.com/mcp",
        include_instructions=False,
        auth="my-secret-token",
        headers={"X-Api-Version": "2025-01"},
    )


def test_make_mcp_capability_multiple_servers_with_different_filters() -> None:
    """Verify each server gets its own filter (regression test for closure-in-loop bug)."""
    from capabilities.base import make_name_filter

    # Create two configs with *different* allow lists
    opts = {
        "server-a": {"command": "cmd-a", "allow": ["tool_a"]},
        "server-b": {"command": "cmd-b", "allow": ["tool_b"]},
    }

    # We can't easily mock MCPToolset here because we need real FilteredToolset
    # behavior. Instead, we directly test make_name_filter isolation.
    filter_a = make_name_filter(opts["server-a"])
    filter_b = make_name_filter(opts["server-b"])

    # These should be independent
    assert filter_a is not None
    assert filter_b is not None

    class FakeDef:
        def __init__(self, name):
            self.name = name

    assert filter_a(FakeDef("tool_a")) is True
    assert filter_a(FakeDef("tool_b")) is False
    assert filter_b(FakeDef("tool_a")) is False
    assert filter_b(FakeDef("tool_b")) is True
