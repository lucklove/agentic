# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "pydantic-ai-slim[openai,anthropic,mcp,logfire]>=1.98.0",
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
    uv run main.py <profile-name>

Loads ``agentic.yaml`` (global config) and ``profiles/<profile-name>.yaml``,
resolves the Gitea username for the profile token, builds the agent, and
starts the notification polling loop.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx
import logfire

from agent_factory import make_agent
from config import load_global_config, load_profile
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


async def run(profile_name: str) -> None:
    logfire.configure(send_to_logfire="if-token-present")
    logfire.instrument_pydantic_ai()

    global_cfg = load_global_config(_HERE / "agentic.yaml")
    profile = load_profile(_HERE / "profiles" / f"{profile_name}.yaml")
    username = await _resolve_username(global_cfg.gitea.base_url, profile.gitea.token)
    agent = make_agent(profile, global_cfg, username)

    await poll_forever(
        agent,
        base_url=global_cfg.gitea.base_url,
        token=profile.gitea.token,
        interval=profile.polling.interval,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="agentic — Gitea notification-driven agent runner"
    )
    parser.add_argument(
        "profile",
        metavar="profile-name",
        help="Name of the profile to load from profiles/<name>.yaml",
    )
    args = parser.parse_args()

    asyncio.run(run(args.profile))
