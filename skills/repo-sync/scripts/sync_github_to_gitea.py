#!/usr/bin/env -S uv run -q
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Force-push GitHub main to Gitea main for repo-sync."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CREDENTIAL_URL_RE = re.compile(r"(https?://)[^\s/@]+@")


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


def git_repo(path: str) -> Path:
    repo_dir = Path(path).expanduser().resolve()
    if not repo_dir.is_dir():
        raise argparse.ArgumentTypeError(f"repo dir does not exist: {repo_dir}")
    if not (repo_dir / ".git").exists():
        raise argparse.ArgumentTypeError(f"not a git repo: {repo_dir}")
    return repo_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Force-push GitHub main to Gitea main.",
    )
    parser.add_argument("--repo-dir", required=True, type=git_repo)
    parser.add_argument("--github-url", required=True)
    parser.add_argument("--gitea-url", required=True)
    parser.add_argument("--main-branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


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
