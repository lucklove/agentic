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
import contextlib
import fcntl
import json
import os
import socket
import sys
from pathlib import Path
from typing import NamedTuple, TextIO

import httpx
import logfire
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai_backends import LocalBackend

from agent_factory import make_agent
from config import ProfileConfig, load_global_config, load_profile
from conversation import load_history, save_history, subject_message_key
from deps import AgentDeps, NotificationSubject
from poller import poll_forever

_HERE = Path(__file__).parent
_DEFAULT_AGENTIC_DIR = Path.home() / ".agentic"
_DEFAULT_GLOBAL_CONFIG_PATH = _DEFAULT_AGENTIC_DIR / "agentic.yaml"


class ProfileLockError(RuntimeError):
    """Raised when a profile is already running in another process."""


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


def _profile_lock_path(profiles_root: Path, profile_name: str) -> Path:
    """Return the lock file path for *profile_name* under *profiles_root*."""
    return _profile_dir(profiles_root, profile_name) / "profile.yaml.lock"


def _discover_profiles(profiles_root: Path) -> list[str]:
    """Discover profile names from ``<profiles-root>/*/profile.yaml``."""
    if not profiles_root.is_dir():
        return []
    return sorted(
        d.name for d in profiles_root.iterdir() if (d / "profile.yaml").is_file()
    )


async def _resolve_username(base_url: str, token: str) -> str:
    """Fetch the Gitea login name for *token* via a single REST call."""
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"token {token}"},
    ) as client:
        resp = await client.get("/api/v1/user")
        resp.raise_for_status()
        return resp.json()["login"]


def _make_gitea_client(base_url: str, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"token {token}"},
    )


def _parse_attach_target(attach: str) -> tuple[str, str, str, str]:
    candidate = attach.strip()
    if not candidate:
        raise ValueError("attach target cannot be empty")

    if "://" in candidate:
        parsed = httpx.URL(candidate)
    else:
        parsed = httpx.URL("https://attach.local/" + candidate.lstrip("/"))
    segments = [segment for segment in parsed.path.split("/") if segment]

    if len(segments) != 4 or segments[2] not in {"issues", "pulls"}:
        raise ValueError(
            "attach target must look like <owner>/<repo>/issues/<number> or <owner>/<repo>/pulls/<number>"
        )

    owner, repo, path_kind, number = segments
    if not number.isdigit():
        raise ValueError("attach target number must be numeric")

    subject_type = "Issue" if path_kind == "issues" else "Pull"
    return owner, repo, subject_type, number


async def _load_attach_history(
    deps: AgentDeps,
    attach: str,
) -> tuple[NotificationSubject, list[ModelMessage]]:
    owner, repo, subject_type, number = _parse_attach_target(attach)
    path_kind = "issues" if subject_type == "Issue" else "pulls"
    client_factory = deps.http_client_factory or _make_gitea_client

    async with client_factory(deps.gitea_base_url, deps.gitea_token) as client:
        response = await client.get(
            f"/api/v1/repos/{owner}/{repo}/{path_kind}/{number}"
        )
        response.raise_for_status()

    history: list[ModelMessage] = []
    if deps.messages_dir is not None:
        key = subject_message_key(owner, repo, number)
        history = load_history(deps.messages_dir, key)

    return (
        NotificationSubject(
            owner=owner,
            repo=repo,
            number=number,
            subject_type=subject_type,
        ),
        history,
    )


def _profile_lock_metadata(profile_name: str) -> dict[str, str | int]:
    return {
        "profile": profile_name,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }


def _write_profile_lock(lock_file: TextIO, profile_name: str) -> None:
    lock_file.seek(0)
    lock_file.truncate()
    json.dump(_profile_lock_metadata(profile_name), lock_file)
    lock_file.write("\n")
    lock_file.flush()


def _read_profile_lock(lock_file: TextIO) -> dict[str, object]:
    lock_file.seek(0)
    raw = lock_file.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {"raw": raw}


def _profile_lock_error(
    profile_name: str, holder: dict[str, object]
) -> ProfileLockError:
    pid = holder.get("pid")
    hostname = holder.get("hostname")
    if pid is not None and hostname is not None:
        details = f"pid {pid} on host {hostname}"
    elif pid is not None:
        details = f"pid {pid}"
    elif holder.get("raw"):
        details = f"lock details: {holder['raw']}"
    else:
        details = "another process"
    return ProfileLockError(f"profile {profile_name!r} is already running in {details}")


@contextlib.contextmanager
def profile_lock(profiles_root: Path, profile_name: str):
    lock_path = _profile_lock_path(profiles_root, profile_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _profile_lock_error(
                profile_name, _read_profile_lock(lock_file)
            ) from None

        _write_profile_lock(lock_file, profile_name)
        try:
            yield
        finally:
            lock_file.seek(0)
            lock_file.truncate()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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
    with profile_lock(profiles_root, profile_name):
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

    await asyncio.gather(
        *(
            _poll_profile(name, global_config_path, profiles_root)
            for name in profile_names
        )
    )


async def run_instruction(
    profile_name: str,
    instruction: str,
    global_config_path: Path,
    profiles_root: Path,
    attach: str | None = None,
) -> None:
    logfire.configure(
        send_to_logfire="if-token-present",
        scrubbing=False,
        inspect_arguments=False,
    )
    logfire.instrument_pydantic_ai()

    with profile_lock(profiles_root, profile_name):
        runtime = await _build_runtime(profile_name, global_config_path, profiles_root)
        run_deps = runtime.deps
        history = None
        attached_key = None

        if attach is not None:
            notification_subject, loaded_history = await _load_attach_history(
                runtime.deps,
                attach,
            )
            run_deps = AgentDeps(
                backend=runtime.deps.backend,
                gitea_username=runtime.deps.gitea_username,
                gitea_base_url=runtime.deps.gitea_base_url,
                gitea_token=runtime.deps.gitea_token,
                http_client_factory=runtime.deps.http_client_factory,
                notification_subject=notification_subject,
                profile_name=runtime.deps.profile_name,
                messages_dir=runtime.deps.messages_dir,
            )
            history = loaded_history or None
            attached_key = subject_message_key(
                notification_subject.owner,
                notification_subject.repo,
                notification_subject.number,
            )

        async with runtime.agent:
            result = await runtime.agent.run(
                instruction,
                deps=run_deps,
                message_history=history,
            )
            if run_deps.messages_dir is not None and attached_key is not None:
                save_history(run_deps.messages_dir, attached_key, result.all_messages())
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
    parser.add_argument(
        "--attach",
        "-a",
        help=(
            "Attach --instruction to an existing issue or pull request and load its "
            "saved message history; accepts <owner>/<repo>/issues/<number>, "
            "<owner>/<repo>/pulls/<number>, or the full URL"
        ),
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

    if args.attach and not args.instruction:
        parser.error("--attach requires --instruction")

    try:
        if args.instruction:
            asyncio.run(
                run_instruction(
                    profile_names[0],
                    args.instruction,
                    global_config_path,
                    profiles_root,
                    attach=args.attach,
                )
            )
        else:
            asyncio.run(run_profiles(profile_names, global_config_path, profiles_root))
    except ProfileLockError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        print(f"attach target lookup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Interrupted.")
