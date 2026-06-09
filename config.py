"""Configuration dataclasses and YAML loaders for agentic.

Two config layers:

  GlobalConfig   — loaded from ``~/.agentic/agentic.yaml`` (shared across all profiles)
  ProfileConfig  — loaded from ``~/.agentic/<name>/profile.yaml`` (per-agent settings)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "GlobalConfig",
    "GiteaGlobalConfig",
    "ProfileConfig",
    "GiteaProfileConfig",
    "PollingConfig",
    "load_global_config",
    "load_profile",
]


# ── Global config ─────────────────────────────────────────────────────────────


@dataclass
class GiteaGlobalConfig:
    """Gitea connection settings shared by all profiles."""

    base_url: str
    mcp_command: list[str]


@dataclass
class GlobalConfig:
    gitea: GiteaGlobalConfig
    instructions: str = ""
    working_dir: str = "."
    skills_dir: str = "./skills"
    agent_request_limit: int = 100
    # Keys are capability names; values are the option dicts from YAML.
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)


def _normalize_capabilities(
    raw_caps: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Normalize bare/null capability entries to empty option dicts."""
    return {k: (v or {}) for k, v in (raw_caps or {}).items()}


def load_global_config(path: str | Path) -> GlobalConfig:
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f)

    gitea = data["gitea"]
    mcp = gitea.get("mcp", {})

    return GlobalConfig(
        gitea=GiteaGlobalConfig(
            base_url=gitea["base_url"].rstrip("/"),
            mcp_command=mcp.get(
                "command",
                ["go", "run", "gitea.com/gitea/gitea-mcp@latest", "-t", "stdio"],
            ),
        ),
        instructions=data.get("instructions", ""),
        working_dir=data.get("working_dir", "."),
        skills_dir=data.get("skills_dir", "./skills"),
        agent_request_limit=int(data.get("agent_request_limit", 100)),
        capabilities=_normalize_capabilities(data.get("capabilities")),
    )


# ── Profile config ────────────────────────────────────────────────────────────


@dataclass
class GiteaProfileConfig:
    """Per-profile Gitea credentials."""

    token: str


@dataclass
class PollingConfig:
    interval: int = 30  # seconds between polls


@dataclass
class ProfileConfig:
    """Full configuration for one agent profile."""

    model: str
    gitea: GiteaProfileConfig
    instructions: str
    model_settings: dict[str, Any] = field(default_factory=dict)
    working_dir: str | None = None
    # Keys are capability names; values are the option dicts from YAML.
    # e.g. {"console": {"include_execute": false}, "code_exec": {}, "skills": {"names": [...]}}
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    polling: PollingConfig = field(default_factory=PollingConfig)


def load_profile(path: str | Path) -> ProfileConfig:
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f)

    capabilities = _normalize_capabilities(data.get("capabilities"))

    polling_data = data.get("polling", {})

    return ProfileConfig(
        model=data["model"],
        model_settings=data.get("model_settings", {}),
        gitea=GiteaProfileConfig(token=data["gitea"]["token"]),
        instructions=data["instructions"],
        working_dir=data.get("working_dir"),
        capabilities=capabilities,
        polling=PollingConfig(
            interval=int(polling_data.get("interval", 30)),
        ),
    )
