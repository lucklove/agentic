"""Agent factory — builds a pydantic-ai Agent from a profile config.

Capability registry
-------------------
``_build_registry`` maps each capability name to a factory:

    (opts: dict[str, Any]) -> AbstractCapability

The factory receives the option dict from the profile YAML verbatim, so
every capability can define its own option schema.

Adding a new capability means adding one entry to the registry —
no ``if`` chains needed anywhere.

Always-on
---------
``CodeMode`` is unconditionally prepended to the capability list and is
not user-configurable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic_ai import Agent, AgentRetries
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai_harness import CodeMode
from pydantic_ai_skills import SkillsCapability, discover_skills

from capabilities.base import make_name_filter
from capabilities.filesystem import AgentDeps, make_fs_capability
from capabilities.gitea import make_gitea_capability
from capabilities.memory import Memory
from config import GlobalConfig, ProfileConfig

__all__ = ["make_agent"]

# Type alias for a capability factory function.
_CapabilityFactory = Callable[[dict[str, Any]], AbstractCapability]


def _make_skills_capability(skills_dir: str, opts: dict[str, Any]) -> SkillsCapability:
    """Discover skills from *skills_dir*, apply allow/deny filter, return capability."""
    all_skills = discover_skills(skills_dir)
    name_filter = make_name_filter(opts)
    filtered = [s for s in all_skills if name_filter(s)] if name_filter else all_skills
    return SkillsCapability(skills=filtered)


def _build_registry(
    global_cfg: GlobalConfig,
    profile: ProfileConfig,
) -> dict[str, _CapabilityFactory]:
    """Return the capability-name → factory map for this profile.

    Defined as a function (not a module-level constant) so each call gets a
    fresh registry that safely closes over ``global_cfg`` and ``profile``
    without sharing state between profiles.

    ``code_exec`` is intentionally absent — ``CodeMode`` is added
    unconditionally in ``make_agent`` and is not user-configurable.
    """
    skills_dir = str(Path(global_cfg.skills_dir).resolve())

    return {
        "gitea": lambda opts: make_gitea_capability(
            base_url=global_cfg.gitea.base_url,
            mcp_command=global_cfg.gitea.mcp_command,
            token=profile.gitea.token,
            opts=opts,
        ),
        "filesystem": lambda opts: make_fs_capability(
            include_execute=opts.get("include_execute", False),
        ),
        "skills": lambda opts: _make_skills_capability(skills_dir, opts),
        "memory": lambda opts: Memory.from_spec(
            backend=opts.get("backend", "memory"),
            path=opts.get("path", ".memories.json"),
            inject_memories_in_instructions=opts.get("inject_memories_in_instructions", True),
            max_instructions_memories=opts.get("max_instructions_memories", 20),
        ),
    }


def make_agent(
    profile: ProfileConfig,
    global_cfg: GlobalConfig,
    gitea_username: str,
) -> Agent[AgentDeps, str]:
    """Build and return an Agent configured from *profile*.

    Args:
        profile:        Loaded profile config (model, capabilities, instructions…).
        global_cfg:     Global config (Gitea base URL, MCP command, skills dir).
        gitea_username: The Gitea login name for the token in this profile,
                        resolved at startup. Substituted into instructions as
                        ``{gitea_username}``.
    """
    registry = _build_registry(global_cfg, profile)

    # CodeMode is always first — mandatory, not configurable per profile.
    capabilities: list[AbstractCapability[Any]] = [CodeMode()]

    # Append profile-declared capabilities in declaration order.
    capabilities += [
        registry[name](opts)
        for name, opts in profile.capabilities.items()
        if name in registry
    ]

    instructions = profile.instructions.replace("{gitea_username}", gitea_username)

    return Agent(
        profile.model,
        output_type=str,
        deps_type=AgentDeps,
        capabilities=capabilities,
        instructions=instructions,
        retries=AgentRetries(output=3),
    )
