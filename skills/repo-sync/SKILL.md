---
name: repo-sync
description: Bidirectional sync between GitHub and Gitea repositories using force push
  (GitHub→Gitea) and cherry-pick+PR (Gitea→GitHub). Sync detection via commit title
  matching (both sides use squash merge).
---

# Repo Sync

Use this skill to synchronize changes between any GitHub repository and its
corresponding Gitea mirror. Both repositories should use squash merge, because
sync detection is based on matching the core commit title on each side.

Only use this skill from an operations-oriented agent profile that is explicitly
authorized to perform cross-forge sync work. General coding agents should fix or
improve the skill itself in this repository, but should not execute repository
synchronization tasks with it unless their task and profile explicitly allow
that operational action.

## GitHub to Gitea

Run `scripts/sync_github_to_gitea.py` to force-push GitHub `main` to Gitea
`main`.

Arguments:

- `--repo-dir`: local clone path; it must already exist and be a Git repository.
- `--owner`: Gitea repository owner, for example `autonomous`.
- `--repo`: repository name, for example `docker-image-controller`.
- `--profile`: profile name used to read the Gitea token from
  `~/.agentic/<name>/profile.yaml`; defaults to `ops_agent`.
- `--github-url`: optional override for the GitHub repository URL. If omitted,
  the script reads `git remote -v` and uses the GitHub fetch remote, preferring
  `origin` when multiple GitHub remotes exist.
- `--gitea-url`: optional override for the Gitea repository URL. If omitted,
  the script reads `agentic.yaml` for `gitea.base_url`, reads the Gitea token
  from the selected profile, and builds the target repository URL from
  `--owner/--repo`.
- `--main-branch`: main branch name; defaults to `main`.
- `--dry-run`: print the commands that would be run without changing anything.

Example:

```shell
skills/repo-sync/scripts/sync_github_to_gitea.py \
  --repo-dir /path/to/repo \
  --owner autonomous \
  --repo docker-image-controller
```

Override URLs explicitly when auto-discovery is ambiguous or when using a
different profile or remote layout:

```shell
skills/repo-sync/scripts/sync_github_to_gitea.py \
  --repo-dir /path/to/repo \
  --owner autonomous \
  --repo docker-image-controller \
  --github-url https://<token>@github.com/owner/repo.git \
  --gitea-url https://<token>@gitea.example.com/org/repo.git
```

## Gitea to GitHub

Run `scripts/sync_gitea_to_github.py` to find commits on Gitea `main` that are
not yet represented on GitHub `main` or in open GitHub pull requests. For each
unsynced commit, the script cherry-picks it onto GitHub `main`, pushes a
`sync/<slug>` branch, and opens a GitHub pull request.

Arguments:

- `--repo-dir`: local clone path; it must already exist and be a Git repository.
- `--gitea-url`: Gitea repository URL with token auth.
- `--github-url`: GitHub repository URL with token auth.
- `--github-repo`: optional GitHub `owner/repo` name for `gh pr` commands; if
  omitted, it is derived from `--github-url`.
- `--main-branch`: main branch name; defaults to `main`.
- `--dry-run`: skip pushes and `gh pr create`, and print the actions that would
  be performed.

Example:

```shell
skills/repo-sync/scripts/sync_gitea_to_github.py   --repo-dir /path/to/repo   --gitea-url https://<token>@gitea.example.com/org/repo.git   --github-url https://<token>@github.com/owner/repo.git
```

## Sync Detection

The Gitea-to-GitHub script treats a Gitea commit as already synced when either:

1. GitHub `main` contains a commit with the same core title.
2. An open GitHub pull request has the same core title.

The core title is the commit or PR title after removing a trailing ` (#N)` suffix.
Commit bodies are copied after removing URLs that point at the configured
Gitea host.

## Conflict and Error Handling

Cherry-pick conflicts are aborted and reported as warnings before the script
continues with the next commit. Push and pull-request creation failures are also
reported as warnings so later commits can still be processed.
