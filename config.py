"""Configuration dataclasses and YAML loaders for agentic.

Two config layers:

  GlobalConfig   — loaded from ``agentic.yaml`` (shared across all profiles)
  ProfileConfig  — loaded from ``profiles/<name>.yaml`` (per-agent settings)
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
    skills_dir: str = "./skills"


def load_global_config(path: str | Path = "agentic.yaml") -> GlobalConfig:
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
        skills_dir=data.get("skills_dir", "./skills"),
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
    # Keys are capability names; values are the option dicts from YAML.
    # e.g. {"filesystem": {"include_execute": false}, "code_exec": {}, "skills": {"names": [...]}}
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    polling: PollingConfig = field(default_factory=PollingConfig)


def load_profile(path: str | Path) -> ProfileConfig:
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f)

    # Normalise capability values: None (bare key with no sub-keys) → empty dict
    raw_caps: dict[str, Any] = data.get("capabilities", {})
    capabilities = {k: (v or {}) for k, v in raw_caps.items()}

    polling_data = data.get("polling", {})

    return ProfileConfig(
        model=data["model"],
        gitea=GiteaProfileConfig(token=data["gitea"]["token"]),
        instructions=data["instructions"],
        capabilities=capabilities,
        polling=PollingConfig(
            interval=int(polling_data.get("interval", 30)),
        ),
    )
