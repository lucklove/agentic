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

Loads ``agentic.yaml`` (global config) and profile files from
``profiles/<profile-name>.yaml``. With no profile names, every
``profiles/*.yaml`` file is loaded. By default, each profile starts its own
notification polling loop. With ``--instruction``, a single profile runs once
with that instruction and prints the model output.
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


def _discover_profiles() -> list[str]:
    return sorted(path.stem for path in (_HERE / "profiles").glob("*.yaml"))


async def _build_runtime(profile_name: str) -> AgentRuntime:
    global_cfg = load_global_config(_HERE / "agentic.yaml")
    profile = load_profile(_HERE / "profiles" / f"{profile_name}.yaml")
    username = await _resolve_username(global_cfg.gitea.base_url, profile.gitea.token)
    working_dir = _resolve_path(profile.working_dir or global_cfg.working_dir)
    deps = AgentDeps(
        backend=LocalBackend(working_dir),
        gitea_username=username,
        gitea_base_url=global_cfg.gitea.base_url,
        gitea_token=profile.gitea.token,
    )
    agent = make_agent(profile, global_cfg, deps)
    return AgentRuntime(
        profile=profile,
        deps=deps,
        agent=agent,
        request_limit=global_cfg.agent_request_limit,
    )


async def _poll_profile(profile_name: str) -> None:
    runtime = await _build_runtime(profile_name)

    await poll_forever(
        runtime.agent,
        interval=runtime.profile.polling.interval,
        deps=runtime.deps,
        request_limit=runtime.request_limit,
    )


async def run_profiles(profile_names: list[str]) -> None:
    logfire.configure(
        send_to_logfire="if-token-present",
        scrubbing=False,
        inspect_arguments=False,
    )
    logfire.instrument_pydantic_ai()

    results = await asyncio.gather(
        *(_poll_profile(name) for name in profile_names),
        return_exceptions=True,
    )
    for name, result in zip(profile_names, results, strict=True):
        if isinstance(result, BaseException):
            logfire.error("profile {name} exited with error", name=name, exc_info=result)


async def run_instruction(profile_name: str, instruction: str) -> None:
    logfire.configure(
        send_to_logfire="if-token-present",
        scrubbing=False,
        inspect_arguments=False,
    )
    logfire.instrument_pydantic_ai()

    runtime = await _build_runtime(profile_name)
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
        help="Profile name(s) to load; omitted means every profiles/*.yaml file",
    )
    parser.add_argument(
        "--instruction",
        "-i",
        help="Run one profile once with this instruction instead of polling",
    )
    args = parser.parse_args()

    profile_names = args.profiles or _discover_profiles()

    if not profile_names:
        parser.error("no profiles found in profiles/*.yaml")

    if args.instruction and len(profile_names) != 1:
        parser.error("--instruction requires exactly one profile")

    try:
        if args.instruction:
            asyncio.run(run_instruction(profile_names[0], args.instruction))
        else:
            asyncio.run(run_profiles(profile_names))
    except KeyboardInterrupt:
        print("Interrupted.")
