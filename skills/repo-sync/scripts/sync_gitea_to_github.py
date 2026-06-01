#!/usr/bin/env -S uv run -q
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Cherry-pick unsynced Gitea commits to GitHub and open pull requests."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

CREDENTIAL_URL_RE = re.compile(r"(https?://)[^\s/@]+@")
TRAILING_PR_RE = re.compile(r"\s*\(#[0-9]+\)\s*$")
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def github_repo_name(github_url: str) -> str:
    parts = urlsplit(github_url)
    path = parts.path.removeprefix("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or "/" not in path:
        raise argparse.ArgumentTypeError(f"invalid GitHub repo URL: {github_url}")
    return path


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
        return urlunsplit((parts.scheme, f"<redacted>@{host}", parts.path, parts.query, parts.fragment))
    return argument


def redact_text(text: str) -> str:
    return CREDENTIAL_URL_RE.sub(r"<redacted>@", text)


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
        raise SystemExit(f"command failed with exit code {result.returncode}: {format_command(command)}")
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


def git(repo_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess_run(["git", "-C", str(repo_dir), *args], check=check)


def git_capture(repo_dir: Path, *args: str, check: bool = True) -> str:
    return capture(["git", "-C", str(repo_dir), *args], check=check)


def git_repo(path: str) -> Path:
    repo_dir = Path(path).expanduser().resolve()
    if not repo_dir.is_dir():
        raise argparse.ArgumentTypeError(f"repo dir does not exist: {repo_dir}")
    if not (repo_dir / ".git").exists():
        raise argparse.ArgumentTypeError(f"not a git repo: {repo_dir}")
    return repo_dir


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
    return "
".join(lines)


def commit_message(repo_dir: Path, sha: str) -> tuple[str, str]:
    raw_message = git_capture(repo_dir, "log", "-1", "--format=%B", sha)
    title, _, body = raw_message.partition("
")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cherry-pick unsynced Gitea commits to GitHub and open PRs.",
    )
    parser.add_argument("--repo-dir", required=True, type=git_repo)
    parser.add_argument("--gitea-url", required=True)
    parser.add_argument("--github-url", required=True)
    parser.add_argument("--github-repo")
    parser.add_argument("--main-branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.github_repo:
        args.github_repo = github_repo_name(args.github_url)
    args.gitea_url_re = gitea_url_pattern(args.gitea_url)
    return args


def create_sync_pr(repo_dir: Path, sha: str, title: str, body: str, args: argparse.Namespace) -> str:
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
        git(repo_dir, "commit", "-m", title, "-m", body)
    else:
        print(f"dry-run: would commit {sha} as {title!r}")
        git(repo_dir, "reset", "--hard", "HEAD")

    push = run(["git", "-C", str(repo_dir), "push", "_github", branch], check=False, dry_run=args.dry_run)
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


def sync(args: argparse.Namespace) -> None:
    ensure_clean_worktree(args.repo_dir)
    add_remote(args.repo_dir, "_github", args.github_url)
    add_remote(args.repo_dir, "_gitea", args.gitea_url)
    original_head = git_capture(args.repo_dir, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if original_head == "HEAD":
        original_head = git_capture(args.repo_dir, "rev-parse", "HEAD").strip()
    synced = 0
    skipped = 0
    try:
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
        commits = gitea_commits(args.repo_dir, args.main_branch)
        print(f"found {len(commits)} Gitea commits not on GitHub")
        pr_titles = open_pr_titles(args.github_repo) if commits else set()
        for sha in commits:
            original_title, original_body = commit_message(args.repo_dir, sha)
            rewritten_title = core_title(original_title)
            if rewritten_title in github_titles:
                print(f"skipped {sha}: title already on GitHub main: {rewritten_title}")
                skipped += 1
                continue
            if rewritten_title in pr_titles:
                print(f"skipped {sha}: title already has open PR: {rewritten_title}")
                skipped += 1
                continue
            result = create_sync_pr(
                args.repo_dir,
                sha,
                rewritten_title,
                rewrite_body(original_body, args.gitea_url_re),
                args,
            )
            if result == "synced":
                synced += 1
                pr_titles.add(rewritten_title)
            else:
                skipped += 1
            git(args.repo_dir, "checkout", args.main_branch, check=False)
            git(args.repo_dir, "branch", "-D", f"sync/{slugify(rewritten_title)}", check=False)
    finally:
        git(args.repo_dir, "checkout", original_head, check=False)
        remove_remote(args.repo_dir, "_github")
        remove_remote(args.repo_dir, "_gitea")
    print(f"summary: synced={synced} skipped={skipped}")


def main() -> None:
    args = parse_args()
    sync(args)


if __name__ == "__main__":
    main()
