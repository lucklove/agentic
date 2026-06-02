#!/usr/bin/env -S uv run -q
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Force-push GitHub main to Gitea main for repo-sync."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml


CREDENTIAL_URL_RE = re.compile(r"(https?://)[^\s/@]+@")
REMOTE_LINE_RE = re.compile(r"^(?P<name>\S+)\s+(?P<url>\S+)\s+\((?P<kind>fetch|push)\)$")
SSH_GITHUB_REMOTE_RE = re.compile(r"^[^@\s]+@github\.com:")
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_GLOBAL_CONFIG = ROOT_DIR / "agentic.yaml"
DEFAULT_PROFILES_DIR = ROOT_DIR / "profiles"


def redact_argument(argument: str) -> str:
    parts = urlsplit(argument)
    if parts.scheme in {"http", "https"} and "@" in parts.netloc:
        host = parts.netloc.rsplit("@", 1)[1]
        return urlunsplit((parts.scheme, f"<redacted>@{host}", parts.path, parts.query, parts.fragment))
    return argument


def redact_text(text: str) -> str:
    return CREDENTIAL_URL_RE.sub(r"\1<redacted>@", text)


def format_command(command: list[str]) -> str:
    return " ".join(redact_argument(argument) for argument in command)


def run(command: list[str], *, dry_run: bool = False) -> subprocess.CompletedProcess[str] | None:
    redacted_command = format_command(command)
    print("$ " + redacted_command)
    if dry_run:
        return None
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sys.stdout.write(redact_text(result.stdout))
    sys.stderr.write(redact_text(result.stderr))
    if result.returncode != 0:
        raise SystemExit(f"command failed with exit code {result.returncode}: {redacted_command}")
    return result


def capture(command: list[str]) -> str:
    redacted_command = format_command(command)
    print("$ " + redacted_command)
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sys.stderr.write(redact_text(result.stderr))
    if result.returncode != 0:
        raise SystemExit(f"command failed with exit code {result.returncode}: {redacted_command}")
    return result.stdout


def git_repo(path: str) -> Path:
    repo_dir = Path(path).expanduser().resolve()
    if not repo_dir.is_dir():
        raise argparse.ArgumentTypeError(f"repo dir does not exist: {repo_dir}")
    if not (repo_dir / ".git").exists():
        raise argparse.ArgumentTypeError(f"not a git repo: {repo_dir}")
    return repo_dir


def load_yaml(path: Path) -> dict[str, object]:
    with path.open() as file_obj:
        data = yaml.safe_load(file_obj)
    if not isinstance(data, dict):
        raise SystemExit(f"expected mapping in config file: {path}")
    return data


def git_remote_lines(repo_dir: Path) -> list[tuple[str, str, str]]:
    output = capture(["git", "-C", str(repo_dir), "remote", "-v"])
    remotes: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        match = REMOTE_LINE_RE.match(line.strip())
        if match:
            remotes.append((match.group("name"), match.group("url"), match.group("kind")))
    return remotes


def is_github_remote(url: str) -> bool:
    parts = urlsplit(url)
    if parts.hostname == "github.com":
        return True
    return SSH_GITHUB_REMOTE_RE.match(url) is not None


def discover_github_url(repo_dir: Path) -> str:
    fetch_remotes = [
        (name, url)
        for name, url, kind in git_remote_lines(repo_dir)
        if kind == "fetch" and is_github_remote(url)
    ]
    if not fetch_remotes:
        raise SystemExit(
            "could not find a GitHub fetch remote in the local repository; "
            "pass --github-url explicitly"
        )
    if len(fetch_remotes) == 1:
        return fetch_remotes[0][1]
    for name, url in fetch_remotes:
        if name == "origin":
            return url
    names = ", ".join(sorted({name for name, _ in fetch_remotes}))
    raise SystemExit(
        "found multiple GitHub fetch remotes without origin "
        f"({names}); pass --github-url explicitly"
    )


def profile_token(profile_name: str, *, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> str:
    profile_path = profiles_dir / f"{profile_name}.yaml"
    data = load_yaml(profile_path)
    gitea = data.get("gitea")
    if not isinstance(gitea, dict) or not isinstance(gitea.get("token"), str):
        raise SystemExit(f"missing gitea.token in profile config: {profile_path}")
    return gitea["token"]


def build_gitea_url(
    base_url: str,
    token: str,
    owner: str,
    repo: str,
) -> str:
    parts = urlsplit(base_url.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise SystemExit(f"invalid gitea base url: {base_url}")
    host = parts.netloc.rsplit("@", 1)[-1]
    path = f"/{owner}/{repo}.git"
    return urlunsplit((parts.scheme, f"{token}@{host}", path, "", ""))


def discover_gitea_url(
    owner: str,
    repo: str,
    profile_name: str,
    *,
    global_config_path: Path = DEFAULT_GLOBAL_CONFIG,
    profiles_dir: Path = DEFAULT_PROFILES_DIR,
) -> str:
    global_config = load_yaml(global_config_path)
    gitea = global_config.get("gitea")
    if not isinstance(gitea, dict) or not isinstance(gitea.get("base_url"), str):
        raise SystemExit(f"missing gitea.base_url in global config: {global_config_path}")
    token = profile_token(profile_name, profiles_dir=profiles_dir)
    return build_gitea_url(gitea["base_url"], token, owner, repo)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Force-push GitHub main to Gitea main.",
    )
    parser.add_argument("--repo-dir", required=True, type=git_repo)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--profile", default="ops_agent")
    parser.add_argument("--github-url")
    parser.add_argument("--gitea-url")
    parser.add_argument("--main-branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.github_url:
        args.github_url = discover_github_url(args.repo_dir)
    if not args.gitea_url:
        args.gitea_url = discover_gitea_url(args.owner, args.repo, args.profile)
    return args


def main() -> None:
    args = parse_args()
    run(
        ["git", "-C", str(args.repo_dir), "fetch", args.github_url, args.main_branch],
        dry_run=args.dry_run,
    )
    run(
        [
            "git",
            "-C",
            str(args.repo_dir),
            "push",
            "--force",
            args.gitea_url,
            f"FETCH_HEAD:refs/heads/{args.main_branch}",
        ],
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("dry-run: GitHub to Gitea sync not executed")
    else:
        print(f"synced {args.main_branch} from GitHub to Gitea")


if __name__ == "__main__":
    main()
