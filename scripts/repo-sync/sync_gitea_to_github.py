#!/usr/bin/env -S uv run -qs
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Clone from Gitea and cherry-pick unsynced commits to GitHub via PRs."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

CREDENTIAL_URL_RE = re.compile(r"(https?://)[^\s/@]+@")
TRAILING_PR_RE = re.compile(r"\s*\(#[0-9]+\)\s*$")
ISSUE_REF_RE = re.compile(r"(?<!\w)#[0-9]+\b")
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
SSH_GITHUB_URL_RE = re.compile(r"^[^@\s]+@github\.com:(.+?)(?:\.git)?$")


def github_repo_name(github_url: str) -> str:
    ssh_match = SSH_GITHUB_URL_RE.match(github_url)
    if ssh_match:
        path = ssh_match.group(1)
        if path and "/" in path:
            return path
    parts = urlsplit(github_url)
    path = parts.path.removeprefix("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or "/" not in path:
        raise argparse.ArgumentTypeError(f"invalid GitHub repo URL: {github_url}")
    return path


def build_github_ssh_url(github_owner: str, repo: str) -> str:
    return f"git@github.com:{github_owner}/{repo}.git"


def gitea_repo_name(gitea_url: str) -> str:
    """Extract owner/repo from a Gitea URL like https://token@host/owner/repo.git."""
    parts = urlsplit(gitea_url)
    path = parts.path.removeprefix("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or "/" not in path:
        raise argparse.ArgumentTypeError(f"invalid Gitea repo URL: {gitea_url}")
    # Take the last two path segments as owner/repo
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        raise argparse.ArgumentTypeError(f"invalid Gitea repo URL: {gitea_url}")
    return "/".join(segments[-2:])


def gitea_url_pattern(gitea_url: str) -> re.Pattern[str]:
    parts = urlsplit(gitea_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise argparse.ArgumentTypeError(f"invalid Gitea URL: {gitea_url}")
    host = parts.hostname or parts.netloc.rsplit("@", 1)[-1]
    if parts.port is not None and ":" not in host:
        host = f"{host}:{parts.port}"
    return re.compile(rf"{re.escape(parts.scheme)}://{re.escape(host)}/\S+")


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


def subprocess_run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if capture_output:
        sys.stderr.write(redact_text(result.stderr))
    else:
        sys.stdout.write(redact_text(result.stdout))
        sys.stderr.write(redact_text(result.stderr))
    if check and result.returncode != 0:
        raise SystemExit(
            f"command failed with exit code {result.returncode}: {format_command(command)}"
        )
    return result


def run(
    command: list[str],
    *,
    check: bool = True,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    print("$ " + format_command(command))
    if dry_run:
        return None
    return subprocess_run(command, check=check)


def capture(command: list[str], *, check: bool = True) -> str:
    print("$ " + format_command(command))
    result = subprocess_run(command, check=check, capture_output=True)
    return result.stdout


def git(
    repo_dir: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess_run(["git", "-C", str(repo_dir), *args], check=check)


def git_capture(repo_dir: Path, *args: str, check: bool = True) -> str:
    return capture(["git", "-C", str(repo_dir), *args], check=check)


def core_title(title: str) -> str:
    return TRAILING_PR_RE.sub("", title).strip()


def slugify(title: str) -> str:
    slug = NON_SLUG_RE.sub("-", title.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return (slug or "commit")[:50].strip("-") or "commit"


def rewrite_body(body: str, gitea_url_re: re.Pattern[str]) -> str:
    body = gitea_url_re.sub("", body)
    lines = [line.rstrip() for line in body.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def validate_pr_text(
    title: str, body: str, gitea_url_re: re.Pattern[str]
) -> tuple[str, str]:
    cleaned_title = core_title(title)
    if cleaned_title != title.strip():
        raise SystemExit(
            "pull request title must not include trailing issue or pull-request numbers"
        )
    if ISSUE_REF_RE.search(cleaned_title):
        raise SystemExit(
            "pull request title must not include issue or pull-request numbers"
        )
    cleaned_body = rewrite_body(body, gitea_url_re)
    if cleaned_body != body.strip():
        raise SystemExit("pull request body must not include Gitea links")
    if ISSUE_REF_RE.search(cleaned_body):
        raise SystemExit(
            "pull request body must not include issue or pull-request numbers"
        )
    return cleaned_title, cleaned_body


def commit_message(repo_dir: Path, sha: str) -> tuple[str, str]:
    raw_message = git_capture(repo_dir, "log", "-1", "--format=%B", sha)
    title, _, body = raw_message.partition("\n")
    return title.strip(), body.strip()


def log_titles(repo_dir: Path, ref: str) -> set[str]:
    output = git_capture(repo_dir, "log", "--format=%s", ref)
    return {core_title(line) for line in output.splitlines() if line.strip()}


def open_pr_titles(github_repo: str) -> set[str]:
    output = capture(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            github_repo,
            "--state",
            "open",
            "--json",
            "title",
            "--jq",
            ".[] | .title",
        ]
    )
    return {core_title(line) for line in output.splitlines() if line.strip()}


def gitea_commits(repo_dir: Path, main_branch: str) -> list[str]:
    output = git_capture(
        repo_dir,
        "log",
        "--reverse",
        "--format=%H",
        f"_github/{main_branch}.._gitea/{main_branch}",
    )
    return [line for line in output.splitlines() if line.strip()]


def add_remote(repo_dir: Path, name: str, url: str) -> None:
    git(repo_dir, "remote", "remove", name, check=False)
    git(repo_dir, "remote", "add", name, url)


def remove_remote(repo_dir: Path, name: str) -> None:
    git(repo_dir, "remote", "remove", name, check=False)


def ensure_clean_worktree(repo_dir: Path) -> None:
    status = git_capture(repo_dir, "status", "--porcelain")
    if status.strip():
        raise RuntimeError(
            "repository has uncommitted changes; commit, stash, or discard them before syncing"
        )


def fetch_sync_state(
    args: argparse.Namespace,
) -> tuple[set[str], set[str], dict[str, tuple[str, str]]]:
    git(
        args.repo_dir,
        "fetch",
        "_github",
        f"{args.main_branch}:refs/remotes/_github/{args.main_branch}",
    )
    git(
        args.repo_dir,
        "fetch",
        "_gitea",
        f"{args.main_branch}:refs/remotes/_gitea/{args.main_branch}",
    )
    github_titles = log_titles(args.repo_dir, f"_github/{args.main_branch}")
    pr_titles = open_pr_titles(args.github_repo)
    candidates: dict[str, tuple[str, str]] = {}
    for sha in gitea_commits(args.repo_dir, args.main_branch):
        original_title, original_body = commit_message(args.repo_dir, sha)
        rewritten_title = core_title(original_title)
        if rewritten_title in github_titles or rewritten_title in pr_titles:
            continue
        candidates[sha] = (
            rewritten_title,
            rewrite_body(original_body, args.gitea_url_re),
        )
    return github_titles, pr_titles, candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List or sync unsynced Gitea commits to GitHub.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_shared_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--repo-dir", required=True, type=Path)
        command_parser.add_argument("--gitea-url", required=True)
        command_parser.add_argument("--github-owner", default="tidbcloud")
        command_parser.add_argument("--github-repo")
        command_parser.add_argument("--main-branch", default="main")

    list_parser = subparsers.add_parser(
        "list", description="List unsynced Gitea commits that still need GitHub PRs."
    )
    add_shared_arguments(list_parser)

    sync_parser = subparsers.add_parser(
        "sync", description="Sync one unsynced Gitea commit to GitHub as a PR."
    )
    add_shared_arguments(sync_parser)
    sync_parser.add_argument("--commit", required=True)
    sync_parser.add_argument("--pr-title", required=True)
    sync_parser.add_argument("--pr-body", required=True)
    sync_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    repo_name = gitea_repo_name(args.gitea_url).split("/")[-1]
    args.github_url = build_github_ssh_url(args.github_owner, repo_name)
    if not args.github_repo:
        args.github_repo = f"{args.github_owner}/{repo_name}"
    args.gitea_url_re = gitea_url_pattern(args.gitea_url)
    return args


def create_sync_pr(
    repo_dir: Path, sha: str, title: str, body: str, args: argparse.Namespace
) -> str:
    branch = f"sync/{slugify(title)}"
    current_branch = git_capture(repo_dir, "branch", "--show-current").strip()
    if current_branch == args.main_branch:
        start_point = f"_github/{args.main_branch}"
        git(repo_dir, "checkout", "--detach", start_point)
        git(repo_dir, "checkout", "-b", branch)
    else:
        git(repo_dir, "checkout", "-b", branch, f"_github/{args.main_branch}")
    cherry_pick = git(repo_dir, "cherry-pick", "--no-commit", sha, check=False)
    if cherry_pick.returncode != 0:
        git(repo_dir, "cherry-pick", "--abort", check=False)
        print(f"warning: skipped {sha} ({title}) because cherry-pick failed")
        return "skipped"

    if not args.dry_run:
        commit_command = ["git", "-C", str(repo_dir), "commit", "-m", title]
        if body:
            commit_command.extend(["-m", body])
        subprocess_run(commit_command)
    else:
        print(f"dry-run: would commit {sha} as {title!r}")
        git(repo_dir, "reset", "--hard", "HEAD")

    push = run(
        ["git", "-C", str(repo_dir), "push", "_github", branch],
        check=False,
        dry_run=args.dry_run,
    )
    if push is not None and push.returncode != 0:
        print(f"warning: skipped PR for {sha} ({title}) because push failed")
        return "skipped"

    pr_command = [
        "gh",
        "pr",
        "create",
        "--repo",
        args.github_repo,
        "--base",
        args.main_branch,
        "--head",
        branch,
        "--title",
        title,
        "--body",
        body,
    ]
    pr_create = run(pr_command, check=False, dry_run=args.dry_run)
    if pr_create is not None and pr_create.returncode != 0:
        print("warning: gh pr create failed: " + format_command(pr_command))
        return "skipped"

    print(f"synced {sha} as {branch}: {title}")
    return "synced"


def clone_from_gitea(repo_dir: Path, gitea_url: str) -> None:
    subprocess_run(["git", "clone", gitea_url, str(repo_dir)])


def list_commits(args: argparse.Namespace) -> None:
    clone_from_gitea(args.repo_dir, args.gitea_url)
    add_remote(args.repo_dir, "_github", args.github_url)
    add_remote(args.repo_dir, "_gitea", args.gitea_url)
    try:
        _, _, candidates = fetch_sync_state(args)
    finally:
        remove_remote(args.repo_dir, "_github")
        remove_remote(args.repo_dir, "_gitea")
    print(
        json.dumps(
            [
                {
                    "commit": sha,
                    "commit_title": title,
                    "commit_body": body,
                }
                for sha, (title, body) in candidates.items()
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


def sync_commit(args: argparse.Namespace) -> None:
    clone_from_gitea(args.repo_dir, args.gitea_url)
    ensure_clean_worktree(args.repo_dir)
    pr_title, pr_body = validate_pr_text(args.pr_title, args.pr_body, args.gitea_url_re)
    add_remote(args.repo_dir, "_github", args.github_url)
    add_remote(args.repo_dir, "_gitea", args.gitea_url)
    original_head = git_capture(
        args.repo_dir, "rev-parse", "--abbrev-ref", "HEAD"
    ).strip()
    if original_head == "HEAD":
        original_head = git_capture(args.repo_dir, "rev-parse", "HEAD").strip()
    try:
        github_titles, pr_titles, candidates = fetch_sync_state(args)
        if args.commit not in candidates:
            raise SystemExit(
                "commit is not eligible for sync; re-run the list command to refresh unsynced commits"
            )
        if pr_title in github_titles:
            raise SystemExit("pull request title already exists on GitHub main")
        if pr_title in pr_titles:
            raise SystemExit(
                "pull request title already exists in an open GitHub pull request"
            )
        result = create_sync_pr(args.repo_dir, args.commit, pr_title, pr_body, args)
        if result != "synced":
            raise SystemExit("sync failed")
    finally:
        git(args.repo_dir, "checkout", original_head, check=False)
        git(args.repo_dir, "branch", "-D", f"sync/{slugify(pr_title)}", check=False)
        remove_remote(args.repo_dir, "_github")
        remove_remote(args.repo_dir, "_gitea")


def main() -> None:
    args = parse_args()
    if args.command == "list":
        list_commits(args)
        return
    if args.command == "sync":
        sync_commit(args)
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
