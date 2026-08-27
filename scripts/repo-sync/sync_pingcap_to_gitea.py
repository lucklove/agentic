#!/usr/bin/env -S uv run -qs
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Clone from Gitea.ai, add PingCAP as upstream, and force-push to Gitea.ai for repo-sync."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import yaml

CREDENTIAL_URL_RE = re.compile(r"(https?://)[^\s/@]+@")
PINGCAP_REMOTE_NAME = "upstream"
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_GLOBAL_CONFIG = Path.home() / ".agentic" / "agentic.yaml"
DEFAULT_AGENTIC_DIR = Path.home() / ".agentic"


def redact_argument(argument: str) -> str:
    parts = urlsplit(argument)
    if parts.scheme in {"http", "https"} and "@" in parts.netloc:
        host = parts.netloc.rsplit("@", 1)[1]
        return urlunsplit(
            (
                parts.scheme,
                f"<redacted>@{host}",
                parts.path,
                parts.query,
                parts.fragment,
            )
        )
    return argument


def redact_text(text: str) -> str:
    return CREDENTIAL_URL_RE.sub(r"\1<redacted>@", text)


def format_command(command: list[str]) -> str:
    return " ".join(redact_argument(argument) for argument in command)


def run(
    command: list[str], *, dry_run: bool = False
) -> subprocess.CompletedProcess[str] | None:
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
        raise SystemExit(
            f"command failed with exit code {result.returncode}: {redacted_command}"
        )
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
        raise SystemExit(
            f"command failed with exit code {result.returncode}: {redacted_command}"
        )
    return result.stdout


def build_pingcap_ssh_url(pingcap_owner: str, repo: str) -> str:
    return f"git@git.pingcap.net:{pingcap_owner}/{repo}.git"


def load_yaml(path: Path) -> dict[str, object]:
    with path.open() as file_obj:
        data = yaml.safe_load(file_obj)
    if not isinstance(data, dict):
        raise SystemExit(f"expected mapping in config file: {path}")
    return data


def profile_token(profile_name: str, *, agentic_dir: Path = DEFAULT_AGENTIC_DIR) -> str:
    profile_path = agentic_dir / profile_name / "profile.yaml"
    data = load_yaml(profile_path)
    gitea = data.get("gitea")
    if not isinstance(gitea, dict) or not isinstance(gitea.get("token"), str):
        raise SystemExit(f"missing gitea.token in profile config: {profile_path}")
    return gitea["token"]


def build_gitea_url(
    base_url: str,
    token: str,
    gitea_owner: str,
    repo: str,
) -> str:
    parts = urlsplit(base_url.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise SystemExit(f"invalid gitea base url: {base_url}")
    host = parts.netloc.rsplit("@", 1)[-1]
    path = f"/{gitea_owner}/{repo}.git"
    return urlunsplit((parts.scheme, f"{token}@{host}", path, "", ""))


def discover_gitea_url(
    gitea_owner: str,
    repo: str,
    profile_name: str,
    *,
    global_config_path: Path = DEFAULT_GLOBAL_CONFIG,
    agentic_dir: Path = DEFAULT_AGENTIC_DIR,
) -> str:
    global_config = load_yaml(global_config_path)
    gitea = global_config.get("gitea")
    if not isinstance(gitea, dict) or not isinstance(gitea.get("base_url"), str):
        raise SystemExit(
            f"missing gitea.base_url in global config: {global_config_path}"
        )
    token = profile_token(profile_name, agentic_dir=agentic_dir)
    return build_gitea_url(gitea["base_url"], token, gitea_owner, repo)


def gitea_api_repo_url(gitea_url: str, gitea_owner: str, repo: str) -> str:
    parts = urlsplit(gitea_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise SystemExit(f"invalid Gitea.ai repository URL: {gitea_url}")
    host = parts.hostname or parts.netloc.rsplit("@", 1)[-1]
    if parts.port is not None and ":" not in host:
        host = f"{host}:{parts.port}"
    expected_repo_path = f"/{gitea_owner}/{repo}.git"
    repo_path = parts.path.rstrip("/")
    if not repo_path.endswith(expected_repo_path):
        raise SystemExit(f"invalid Gitea.ai repository URL: {gitea_url}")
    install_prefix = repo_path[: -len(expected_repo_path)]
    path = (
        f"{install_prefix}/api/v1/repos/{quote(gitea_owner)}/{quote(repo)}/pulls"
        "?state=open&limit=1"
    )
    return urlunsplit((parts.scheme, host, path, "", ""))


def gitea_api_token(
    gitea_url: str, profile_name: str, *, agentic_dir: Path = DEFAULT_AGENTIC_DIR
) -> str:
    parts = urlsplit(gitea_url)
    if parts.username:
        return parts.username
    return profile_token(profile_name, agentic_dir=agentic_dir)


def assert_no_open_gitea_prs(
    gitea_owner: str,
    repo: str,
    gitea_url: str,
    profile_name: str,
    *,
    agentic_dir: Path = DEFAULT_AGENTIC_DIR,
) -> None:
    api_url = gitea_api_repo_url(gitea_url, gitea_owner, repo)
    token = gitea_api_token(gitea_url, profile_name, agentic_dir=agentic_dir)
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/json",
            "Authorization": f"token {token}",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"failed to query open Gitea.ai pull requests ({exc.code}): {api_url}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"failed to query open Gitea.ai pull requests: {exc.reason}"
        ) from exc
    if not isinstance(payload, list):
        raise SystemExit(
            f"unexpected Gitea.ai API response when listing pull requests: {api_url}"
        )
    if payload:
        raise SystemExit(
            "refusing PingCAP to Gitea.ai sync because the Gitea.ai repository has open pull requests"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone from Gitea.ai, add PingCAP as upstream, and force-push to Gitea.ai.",
    )
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--gitea-owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--profile", default="ops_agent")
    parser.add_argument("--pingcap-owner", default="tidbcloud")
    parser.add_argument("--gitea-url")
    parser.add_argument("--main-branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.pingcap_url = build_pingcap_ssh_url(args.pingcap_owner, args.repo)
    if not args.gitea_url:
        args.gitea_url = discover_gitea_url(args.gitea_owner, args.repo, args.profile)
    return args


def main() -> None:
    args = parse_args()
    assert_no_open_gitea_prs(args.gitea_owner, args.repo, args.gitea_url, args.profile)
    # Clone from the local Gitea.ai mirror (much faster than SSH-cloning PingCAP).
    run(
        ["git", "clone", args.gitea_url, str(args.repo_dir)],
        dry_run=args.dry_run,
    )
    # Add PingCAP as a temporary upstream so we can fetch its main branch.
    run(
        [
            "git",
            "-C",
            str(args.repo_dir),
            "remote",
            "add",
            PINGCAP_REMOTE_NAME,
            args.pingcap_url,
        ],
        dry_run=args.dry_run,
    )
    # Fetch PingCAP main into a named remote-tracking ref so the working tree
    # is not disturbed by the fetch; this is what we will force-push.
    run(
        [
            "git",
            "-C",
            str(args.repo_dir),
            "fetch",
            PINGCAP_REMOTE_NAME,
            f"{args.main_branch}:refs/remotes/{PINGCAP_REMOTE_NAME}/{args.main_branch}",
        ],
        dry_run=args.dry_run,
    )
    # Force-push the PingCAP-fetched tip to Gitea.ai main, independent of local HEAD.
    run(
        [
            "git",
            "-C",
            str(args.repo_dir),
            "push",
            "--force",
            args.gitea_url,
            f"{PINGCAP_REMOTE_NAME}/{args.main_branch}:refs/heads/{args.main_branch}",
        ],
        dry_run=args.dry_run,
    )
    # Drop the temporary upstream so a re-run does not accumulate remotes.
    run(
        [
            "git",
            "-C",
            str(args.repo_dir),
            "remote",
            "remove",
            PINGCAP_REMOTE_NAME,
        ],
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("dry-run: PingCAP to Gitea.ai sync not executed")
    else:
        print(f"synced {args.main_branch} from PingCAP to Gitea.ai")


if __name__ == "__main__":
    main()
