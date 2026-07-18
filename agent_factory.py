"""Agent factory — builds a pydantic-ai Agent from a profile config.

Capability registry
-------------------
``_build_registry`` maps each capability name to a factory:

    (opts: dict[str, Any]) -> AbstractCapability

The factory receives the option dict from the profile YAML verbatim, so
every capability can define its own option schema.

Adding a new capability means adding one entry to the registry —
no ``if`` chains needed anywhere. ``code_exec`` is the registry name for
``CodeMode`` and is enabled by default through ``~/.agentic/agentic.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any, Callable, cast

from pydantic_ai import Agent, AgentRetries, ModelSettings
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai_backends import ConsoleCapability
from pydantic_ai_backends.permissions.presets import PERMISSIVE_RULESET
from pydantic_ai_harness import CodeMode
from pydantic_monty import MountDir

from capabilities.compaction import (
    AnchoredCompaction,
    AnthropicCompaction,
    OpenAICompaction,
)
from capabilities.gitea import make_gitea_capability
from capabilities.harness import HarnessCapability
from capabilities.mcp_servers import make_mcp_capability
from capabilities.memory import Memory
from capabilities.privacy import PrivacyCapability
from capabilities.skills import make_skills_capability
from config import GlobalConfig, ProfileConfig
from deps import AgentDeps
from model_factory import build_model

__all__ = ["make_agent"]

# Type alias for a capability factory function.
_CapabilityFactory = Callable[[dict[str, Any]], AbstractCapability]

_AGENTIC_DIR = Path.home() / ".agentic"
_SKILLS_DIR_BASE = Path(__file__).resolve().parent


def _resolve_working_dir(working_dir: str) -> Path:
    """Resolve *working_dir* to an absolute path (relative to agent_factory.py dir)."""
    resolved = Path(working_dir).expanduser()
    if not resolved.is_absolute():
        resolved = _SKILLS_DIR_BASE / resolved
    return resolved.resolve()


def _make_skills_capability(
    urls: list[str],
    base_url: str,
    token: str,
) -> Any:
    """Validate and load skills from a list of Gitea wiki URLs.

    The list is the user-supplied value of ``capabilities.skills`` in
    profile / global YAML. The capability fetches each page at init time
    and raises on any validation failure (bad URL, missing page, missing
    frontmatter ``name``/``description``).
    """
    return make_skills_capability(
        urls=list(urls),
        base_url=base_url,
        token=token,
    )


def _build_registry(
    global_cfg: GlobalConfig,
    profile: ProfileConfig,
    working_dir: Path,
    *,
    profile_name: str = "",
) -> dict[str, _CapabilityFactory]:
    """Return the capability-name → factory map for this profile.

    Defined as a function (not a module-level constant) so each call gets a
    fresh registry that safely closes over ``global_cfg`` and ``profile``
    without sharing state between profiles.
    """
    memory_path = str(
        (_AGENTIC_DIR / profile_name / "memory.json")
        if profile_name
        else ".memories.json"
    )

    return {
        "code_exec": lambda opts: CodeMode(
            **opts,
            mount=MountDir(str(working_dir), str(working_dir), mode="read-write"),
        ),
        "gitea": lambda opts: make_gitea_capability(
            base_url=global_cfg.gitea.base_url,
            mcp_command=global_cfg.gitea.mcp_command,
            token=profile.gitea.token,
            opts=opts,
        ),
        "console": lambda opts: ConsoleCapability(
            include_execute=opts.get("include_execute", False),
            permissions=PERMISSIVE_RULESET,
        ),
        "skills": lambda opts: _make_skills_capability(
            urls=list(opts) if opts else [],
            base_url=global_cfg.gitea.base_url,
            token=profile.gitea.token,
        ),
        "memory": lambda opts: Memory.from_spec(
            backend=opts.get("backend", "file"),
            path=opts.get("path", memory_path),
            inject_memories_in_instructions=opts.get(
                "inject_memories_in_instructions", True
            ),
            max_instructions_memories=opts.get("max_instructions_memories", 20),
        ),
        "harness": lambda opts: HarnessCapability(),
        "privacy": lambda opts: PrivacyCapability.from_spec(opts),
        "openai_compaction": lambda opts: OpenAICompaction(**opts),
        "anthropic_compaction": lambda opts: AnthropicCompaction(**opts),
        "anchored_compaction": lambda opts: AnchoredCompaction(**opts),
        "mcp": lambda opts: make_mcp_capability(opts),
    }


def make_agent(
    profile: ProfileConfig,
    global_cfg: GlobalConfig,
    deps: AgentDeps,
) -> Agent[AgentDeps, str]:
    """Build and return an Agent configured from *profile*.

    Args:
        profile:    Loaded profile config (model, capabilities, instructions…).
        global_cfg: Global config (Gitea base URL, MCP command).
        deps:       Shared runtime deps. ``gitea_username`` and ``working_dir``
                    are substituted into instructions as ``$gitea_username``
                    and ``$working_dir``.
    """
    working_dir = _resolve_working_dir(profile.working_dir or global_cfg.working_dir)

    registry = _build_registry(
        global_cfg,
        profile,
        working_dir,
        profile_name=deps.profile_name,
    )

    # Merge global defaults with profile overrides; profile entries win on name collision.
    effective_capabilities = global_cfg.capabilities | profile.capabilities
    unknown_capabilities = sorted(set(effective_capabilities).difference(registry))
    if unknown_capabilities:
        unknown_list = ", ".join(unknown_capabilities)
        raise ValueError(f"unknown capability keys: {unknown_list}")

    capabilities: list[AbstractCapability[Any]] = [
        registry[name](opts) for name, opts in effective_capabilities.items()
    ]

    raw_instructions = "\n\n".join(
        instruction
        for instruction in [profile.instructions, global_cfg.instructions]
        if instruction
    )
    instructions = Template(raw_instructions).safe_substitute(
        gitea_username=deps.gitea_username,
        working_dir=str(working_dir),
    )

    return Agent(
        build_model(profile.model),
        model_settings=cast(ModelSettings, profile.model_settings),
        output_type=str,
        deps_type=AgentDeps,
        capabilities=capabilities,
        instructions=instructions,
        retries=AgentRetries(output=3),
    )
