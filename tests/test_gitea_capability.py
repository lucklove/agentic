"""Tests for the Gitea MCP capability."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from capabilities.gitea import GiteaMCPCapability, make_gitea_capability


class FakeMCPToolset:
    """Minimal fake that captures how MCPToolset was invoked."""

    instances: list["FakeMCPToolset"] = []

    def __init__(
        self, transport, *, include_instructions=False, init_timeout=None, **_kwargs
    ):
        self.transport = transport
        self.include_instructions = include_instructions
        self.init_timeout = init_timeout
        self.prefixed_name = "gitea"
        FakeMCPToolset.instances.append(self)

    def prefixed(self, prefix: str) -> "FakeMCPToolset":
        self.prefixed_name = prefix
        return self


@pytest.fixture(autouse=True)
def _reset_fake_instances() -> None:
    FakeMCPToolset.instances = []


def test_make_gitea_capability_default_init_timeout_30() -> None:
    """Default to 30s — fixes issue #201 cold-cache MCP handshake timeout."""
    with patch("capabilities.gitea.MCPToolset", FakeMCPToolset):
        cap = make_gitea_capability(
            base_url="http://gitea.example",
            mcp_command=[
                "go",
                "run",
                "gitea.com/gitea/gitea-mcp@latest",
                "-t",
                "stdio",
            ],
            token="token",
            opts={},
        )

    assert isinstance(cap, GiteaMCPCapability)
    assert len(FakeMCPToolset.instances) == 1
    assert FakeMCPToolset.instances[0].init_timeout == 30
    assert FakeMCPToolset.instances[0].include_instructions is False
    assert FakeMCPToolset.instances[0].prefixed_name == "gitea"


def test_make_gitea_capability_init_timeout_override() -> None:
    """Profiles may override the default via the ``init_timeout`` option."""
    with patch("capabilities.gitea.MCPToolset", FakeMCPToolset):
        make_gitea_capability(
            base_url="http://gitea.example",
            mcp_command=["go", "run", "gitea-mcp@latest", "-t", "stdio"],
            token="token",
            opts={"init_timeout": 60},
        )

    assert FakeMCPToolset.instances[0].init_timeout == 60


def test_make_gitea_capability_init_timeout_accepts_numeric_string() -> None:
    """YAML may parse values as strings/numbers; we coerce to float."""
    with patch("capabilities.gitea.MCPToolset", FakeMCPToolset):
        make_gitea_capability(
            base_url="http://gitea.example",
            mcp_command=["go", "run", "gitea-mcp@latest", "-t", "stdio"],
            token="token",
            opts={"init_timeout": "45"},
        )

    assert FakeMCPToolset.instances[0].init_timeout == 45.0


def test_make_gitea_capability_rejects_non_numeric_init_timeout() -> None:
    """A non-numeric init_timeout must fail loudly, not silently fall back."""
    with patch("capabilities.gitea.MCPToolset", FakeMCPToolset):
        with pytest.raises(ValueError):
            make_gitea_capability(
                base_url="http://gitea.example",
                mcp_command=["go", "run", "gitea-mcp@latest", "-t", "stdio"],
                token="token",
                opts={"init_timeout": "not-a-number"},
            )
