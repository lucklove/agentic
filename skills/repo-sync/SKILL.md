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

Invoke helper automation through `run_skill_script(...)`; pass the exact
relative script path exposed by the skill, such as
`script_name="scripts/sync_github_to_gitea.py"`. Do not describe these scripts
as if the agent should shell into `skills/repo-sync/scripts/...` directly.

## GitHub to Gitea

Use `run_skill_script(skill_name="repo-sync", script_name="scripts/sync_github_to_gitea.py", args={...})`
to force-push GitHub `main` to Gitea `main`.

The script clones a fresh copy from GitHub (via SSH) into the specified
`repo_dir`, then force-pushes to Gitea. Before the force-push runs, the script
queries the Gitea repository and refuses to continue if it already has open pull
requests. Resolve or close those pull requests first so the mirror update does
not invalidate active review work.

Arguments:

- `repo_dir`: target directory for the fresh clone; it must not already exist as
  a git repository. The AI agent should choose a unique path to avoid conflicts
  with other agents.
- `gitea_owner`: Gitea repository owner, for example `autonomous`.
- `repo`: repository name, for example `docker-image-controller`.
- `profile`: profile name used to read the Gitea token from
  `~/.agentic/<name>/profile.yaml`; defaults to `ops_agent`.
- `github_owner`: GitHub organization or user that owns the repository;
  defaults to `tidbcloud`. Used to build the SSH clone URL
  `git@github.com:{github_owner}/{repo}.git`.
- `gitea_url`: optional override for the Gitea repository URL. If omitted, the
  script reads `~/.agentic/agentic.yaml` for `gitea.base_url`, reads the Gitea
  token from the selected profile, and builds the target repository URL from
  `gitea_owner`/`repo`.
- `main_branch`: main branch name; defaults to `main`.
- `dry_run`: print the commands that would be run without changing anything.

Example:

```python
await run_skill_script(
    skill_name="repo-sync",
    script_name="scripts/sync_github_to_gitea.py",
    args={
        "repo_dir": "{working_dir}/sync-gh2gitea-docker-image-controller",
        "gitea_owner": "autonomous",
        "repo": "docker-image-controller",
    },
)
```

Override URLs explicitly when using a different GitHub owner or profile.

## Gitea to GitHub

This direction runs in two explicit steps so the agent controls the GitHub
PR title and body.

The script clones a fresh copy from Gitea into the specified `repo_dir`,
then adds GitHub as a remote using SSH protocol.

### Step 1: list unsynced commits

Use `run_skill_script(skill_name="repo-sync", script_name="scripts/sync_gitea_to_github.py", args={...})`
with `command="list"` to discover which Gitea commits are not yet represented
on GitHub `main` or in open GitHub pull requests.

Arguments:

- `command`: set to `list`.
- `repo_dir`: target directory for the fresh clone; it must not already exist
  as a git repository. The AI agent should choose a unique path.
- `gitea_url`: Gitea repository URL with token auth.
- `github_owner`: GitHub organization or user; defaults to `tidbcloud`.
  Used to build the GitHub SSH URL `git@github.com:{github_owner}/{repo}.git`.
- `github_repo`: optional GitHub `owner/repo` name for `gh pr` commands; if
  omitted, it is derived from `github_owner` and the repo name extracted from
  `gitea_url`.
- `main_branch`: main branch name; defaults to `main`.

The script prints a JSON array of objects with `commit`, `commit_title`, and
`commit_body` so the agent can decide what to sync next.

Example:

```python
await run_skill_script(
    skill_name="repo-sync",
    script_name="scripts/sync_gitea_to_github.py",
    args={
        "command": "list",
        "repo_dir": "{working_dir}/sync-gitea2gh-agentic",
        "gitea_url": "https://<token>@gitea.example.com/org/repo.git",
    },
)
```

### Step 2: sync one listed commit

After reviewing the listed commits, call the same script with `command="sync"`
and pass exactly one listed commit id plus the PR text to use on GitHub.

Additional arguments for `command="sync"`:

- `commit`: one commit id returned by the `list` command.
- `pr_title`: GitHub PR title to use for the cherry-picked commit.
- `pr_body`: GitHub PR body to use for the cherry-picked commit.
- `dry_run`: skip push and PR creation, and print the actions that would be
  performed.

Constraints:

- Do not include issue numbers or PR numbers such as `#155` in `pr_title` or
  `pr_body`.
- Do not include Gitea links in `pr_body`.
- Re-run `list` if the target commit may already have been synced elsewhere;
  `sync` rejects commits that are no longer eligible.

Example:

```python
await run_skill_script(
    skill_name="repo-sync",
    script_name="scripts/sync_gitea_to_github.py",
    args={
        "command": "sync",
        "repo_dir": "{working_dir}/sync-gitea2gh-agentic",
        "gitea_url": "https://<token>@gitea.example.com/org/repo.git",
        "commit": "abc123def456",
        "pr_title": "Improve repo sync skill invocation",
        "pr_body": "Carry the mirrored change without forge-specific links.",
    },
)
```

## Sync Detection

The Gitea-to-GitHub script treats a Gitea commit as already synced when either:

1. GitHub `main` contains a commit with the same core title.
2. An open GitHub pull request has the same core title.

The core title is the commit or PR title after removing a trailing ` (#N)` suffix.
Commit bodies are copied into the list output after removing URLs that point at
the configured Gitea host.

## Conflict and Error Handling

Cherry-pick conflicts are aborted and reported as warnings. The per-commit
`sync` command fails for that requested commit without modifying later work.
Push and pull-request creation failures are also reported so the operator can
retry after fixing the underlying problem.
