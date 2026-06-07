# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "pydantic-ai-slim[openai,anthropic,mcp,logfire,retries]>=1.98.0",
#   "pydantic-ai-harness[code-mode]>=0.2.0",
#   "pydantic-ai-backend>=0.2.6",
#   "pydantic-ai-skills>=0.10.0",
#   "httpx>=0.28.0",
#   "pyyaml>=6.0",
# ]
# ///
"""
main.py — agentic entry point

Usage:
    uv run main.py
    uv run main.py <profile-name> [<profile-name> ...]
    uv run main.py <profile-name> --instruction "..."
    uv run main.py --config /path/to/agentic.yaml --profiles-root /path/to/profiles

Loads a global config file (default ``~/.agentic/agentic.yaml``) and profile
files from a profiles root (default ``~/.agentic/<profile-name>/profile.yaml``).
With no profile names, every discoverable profile is loaded.
By default, each profile starts its own notification polling loop. With
``--instruction``, a single profile runs once with that instruction and prints
the model output.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import NamedTuple

import httpx
import logfire
from pydantic_ai import Agent
from pydantic_ai_backends import LocalBackend

from agent_factory import make_agent
from config import ProfileConfig, load_global_config, load_profile
from deps import AgentDeps
from poller import poll_forever

_HERE = Path(__file__).parent
_DEFAULT_AGENTIC_DIR = Path.home() / ".agentic"
_DEFAULT_GLOBAL_CONFIG_PATH = _DEFAULT_AGENTIC_DIR / "agentic.yaml"


async def _resolve_username(base_url: str, token: str) -> str:
    """Fetch the Gitea login name for *token* via a single REST call."""
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"token {token}"},
    ) as client:
        resp = await client.get("/api/v1/user")
        resp.raise_for_status()
        return resp.json()["login"]


class AgentRuntime(NamedTuple):
    profile: ProfileConfig
    deps: AgentDeps
    agent: Agent[AgentDeps, str]
    request_limit: int


def _resolve_path(path: str) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = _HERE / resolved
    return resolved.resolve()


def _profile_path(profiles_root: Path, profile_name: str) -> Path:
    """Return the YAML path for *profile_name* under *profiles_root*."""
    return profiles_root / profile_name / "profile.yaml"


def _profile_dir(profiles_root: Path, profile_name: str) -> Path:
    """Return the data directory for *profile_name* under *profiles_root*."""
    return profiles_root / profile_name


def _discover_profiles(profiles_root: Path) -> list[str]:
    """Discover profile names from ``<profiles-root>/*/profile.yaml``."""
    if not profiles_root.is_dir():
        return []
    return sorted(
        d.name for d in profiles_root.iterdir() if (d / "profile.yaml").is_file()
    )


async def _build_runtime(
    profile_name: str, global_config_path: Path, profiles_root: Path
) -> AgentRuntime:
    global_cfg = load_global_config(global_config_path)
    profile = load_profile(_profile_path(profiles_root, profile_name))
    username = await _resolve_username(global_cfg.gitea.base_url, profile.gitea.token)
    working_dir = _resolve_path(profile.working_dir or global_cfg.working_dir)

    profile_dir = _profile_dir(profiles_root, profile_name)
    messages_dir = profile_dir / "messages"

    profile_skills_dirs: list[Path] = []
    skills_subdir = profile_dir / "skills"
    if skills_subdir.is_dir():
        profile_skills_dirs.append(skills_subdir)

    deps = AgentDeps(
        backend=LocalBackend(working_dir),
        gitea_username=username,
        gitea_base_url=global_cfg.gitea.base_url,
        gitea_token=profile.gitea.token,
        profile_name=profile_name,
        messages_dir=messages_dir,
    )
    agent = make_agent(
        profile,
        global_cfg,
        deps,
        profile_skills_dirs=profile_skills_dirs,
    )
    return AgentRuntime(
        profile=profile,
        deps=deps,
        agent=agent,
        request_limit=global_cfg.agent_request_limit,
    )


async def _poll_profile(
    profile_name: str, global_config_path: Path, profiles_root: Path
) -> None:
    runtime = await _build_runtime(profile_name, global_config_path, profiles_root)

    await poll_forever(
        runtime.agent,
        interval=runtime.profile.polling.interval,
        deps=runtime.deps,
        request_limit=runtime.request_limit,
    )


async def run_profiles(
    profile_names: list[str], global_config_path: Path, profiles_root: Path
) -> None:
    logfire.configure(
        send_to_logfire="if-token-present",
        scrubbing=False,
        inspect_arguments=False,
    )
    logfire.instrument_pydantic_ai()

    results = await asyncio.gather(
        *(
            _poll_profile(name, global_config_path, profiles_root)
            for name in profile_names
        ),
        return_exceptions=True,
    )
    for name, result in zip(profile_names, results, strict=True):
        if isinstance(result, BaseException):
            logfire.error(
                "profile {name} exited with error", name=name, exc_info=result
            )


async def run_instruction(
    profile_name: str,
    instruction: str,
    global_config_path: Path,
    profiles_root: Path,
) -> None:
    logfire.configure(
        send_to_logfire="if-token-present",
        scrubbing=False,
        inspect_arguments=False,
    )
    logfire.instrument_pydantic_ai()

    runtime = await _build_runtime(profile_name, global_config_path, profiles_root)
    async with runtime.agent:
        result = await runtime.agent.run(instruction, deps=runtime.deps)
        print(result.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="agentic — Gitea notification-driven agent runner"
    )
    parser.add_argument(
        "profiles",
        metavar="profile-name",
        nargs="*",
        help="Profile name(s) to load; omitted means every <profiles-root>/*/profile.yaml",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_GLOBAL_CONFIG_PATH,
        help=(
            "Path to the global agentic.yaml file "
            f"(default: {_DEFAULT_GLOBAL_CONFIG_PATH})"
        ),
    )
    parser.add_argument(
        "--profiles-root",
        type=Path,
        default=_DEFAULT_AGENTIC_DIR,
        help=(
            "Path to the profiles root directory containing named profile "
            f"subdirectories (default: {_DEFAULT_AGENTIC_DIR})"
        ),
    )
    parser.add_argument(
        "--instruction",
        "-i",
        help="Run one profile once with this instruction instead of polling",
    )
    args = parser.parse_args()

    global_config_path = args.config.expanduser()
    profiles_root = args.profiles_root.expanduser()

    if not global_config_path.is_file():
        parser.error(f"global config file not found: {global_config_path}")

    if not profiles_root.is_dir():
        parser.error(f"profiles root directory not found: {profiles_root}")

    profile_names = args.profiles or _discover_profiles(profiles_root)

    if not profile_names:
        parser.error(f"no profiles found in {profiles_root}/*/profile.yaml")

    if args.instruction and len(profile_names) != 1:
        parser.error("--instruction requires exactly one profile")

    try:
        if args.instruction:
            asyncio.run(
                run_instruction(
                    profile_names[0],
                    args.instruction,
                    global_config_path,
                    profiles_root,
                )
            )
        else:
            asyncio.run(run_profiles(profile_names, global_config_path, profiles_root))
    except KeyboardInterrupt:
        print("Interrupted.")
