---
name: repo-sync
description: Bidirectional sync between GitHub and Gitea repositories using force push
  (GitHub→Gitea) and cherry-pick+PR (Gitea→GitHub). Sync detection via commit title
  matching (both sides use squash merge).
---

# Repo Sync

Use this skill to synchronize changes between the GitHub repository
`tidbcloud/auto-deploy` and the Gitea repository `gitea.ai/autonomous/auto-deploy`.
Both repositories use squash merge, so sync detection is based on matching the
core commit title on each side.

## GitHub to Gitea

Run `scripts/sync_github_to_gitea.py` to force-push GitHub `main` to Gitea
`main`.

Arguments:

- `--repo-dir`: local clone path; it must already exist and be a Git repository.
- `--github-url`: GitHub repository URL with token auth, for example
  `https://<token>@github.com/owner/repo.git`.
- `--gitea-url`: Gitea repository URL with token auth.
- `--main-branch`: main branch name; defaults to `main`.
- `--dry-run`: print the commands that would be run without changing anything.

Example:

```shell
skills/repo-sync/scripts/sync_github_to_gitea.py   --repo-dir /path/to/auto-deploy   --github-url https://<token>@github.com/tidbcloud/auto-deploy.git   --gitea-url https://<token>@gitea.ai/autonomous/auto-deploy.git
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
- `--main-branch`: main branch name; defaults to `main`.
- `--dry-run`: skip pushes and `gh pr create`, and print the actions that would
  be performed.

Example:

```shell
skills/repo-sync/scripts/sync_gitea_to_github.py   --repo-dir /path/to/auto-deploy   --gitea-url https://<token>@gitea.ai/autonomous/auto-deploy.git   --github-url https://<token>@github.com/tidbcloud/auto-deploy.git
```

## Sync Detection

The Gitea-to-GitHub script treats a Gitea commit as already synced when either:

1. GitHub `main` contains a commit with the same core title.
2. An open GitHub pull request has the same core title.

The core title is the commit or PR title after removing a trailing ` (#N)` suffix.
Commit bodies are copied after removing `https://gitea.ai/...` URLs.

## Conflict and Error Handling

Cherry-pick conflicts are aborted and reported as warnings before the script
continues with the next commit. Push and pull-request creation failures are also
reported as warnings so later commits can still be processed.
