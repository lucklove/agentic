---
name: private-github-repo-access
description: Access private GitHub repository files and pages using the gh CLI. Use when reading files from private GitHub repos, fetching internal GitHub README/docs, listing private repository contents, browsing private repo structure, or accessing tidbcloud/pingcap internal repositories.
---

# Private GitHub Repository Access

Use this skill to inspect files, directories, and search results from private GitHub repositories through the authenticated `gh` CLI.

## When to Use

- The user asks to read a file from a private GitHub repository.
- The user shares a `github.com` URL pointing to a private repository.
- You need to inspect configs, docs, manifests, or repository structure in an internal GitHub repository.
- A normal webpage fetch returns 404 or 403 for a GitHub repository URL that may be private.
- You need to access `tidbcloud` or `pingcap` internal repositories.

## Prerequisites

Verify that the GitHub CLI is authenticated before making repository API calls:

```shell
gh auth status
```

If it is not authenticated, ask the user to authenticate or run:

```shell
gh auth login
```

## Procedure

### 1. Read a Single File

GitHub's contents API returns file content as base64. Always decode text files before reading them:

```shell
# Fetch and decode file content
gh api repos/{owner}/{repo}/contents/{path} --jq '.content' | base64 --decode

# Example: read README
gh api repos/tidbcloud/app-delivery/contents/README.md --jq '.content' | base64 --decode

# Example: read a nested file
gh api repos/tidbcloud/infra-provider/contents/config/default.yaml --jq '.content' | base64 --decode
```

### 2. List Directory Contents

```shell
# List files and folders at a path
gh api repos/{owner}/{repo}/contents/{dir} --jq '[.[] | {name: .name, type: .type, path: .path}]'

# Example: list root directory
gh api repos/tidbcloud/app-delivery/contents/ --jq '[.[] | {name: .name, type: .type}]'

# Example: list a subdirectory
gh api repos/tidbcloud/app-delivery/contents/app-registry --jq '[.[] | {name: .name, type: .type}]'
```

### 3. Convert a GitHub URL to an API Call

When given a URL like `https://github.com/owner/repo/blob/branch/path/to/file`:

| URL segment | Maps to |
|-------------|---------|
| `owner/repo` | `repos/{owner}/{repo}` |
| `/blob/{branch}/` | `?ref={branch}` (default: main) |
| `/path/to/file` | `/contents/path/to/file` |

```shell
# URL: https://github.com/tidbcloud/app-delivery/blob/main/some-service/base/deployment.yaml
gh api repos/tidbcloud/app-delivery/contents/some-service/base/deployment.yaml --jq '.content' | base64 --decode

# URL pointing to a non-main branch
gh api 'repos/tidbcloud/app-delivery/contents/some-service/base/deployment.yaml?ref=feature-branch' --jq '.content' | base64 --decode
```

### 4. Search for Files

```shell
# Search files in a repo by name
gh api 'search/code?q=filename:kustomization.yaml+repo:tidbcloud/app-delivery' --jq '.items[] | {path: .path, url: .html_url}'

# Search by content keyword
gh api 'search/code?q=ExternalSecret+repo:tidbcloud/app-delivery' --jq '.items[] | .path'
```

### 5. Read Large Files with Truncated Contents

If the contents API returns `"encoding": "none"`, the file is too large for inline content. Use the Git blob API:

```shell
# Step 1: get the blob SHA
gh api repos/{owner}/{repo}/contents/{path} --jq '.sha'

# Step 2: fetch raw content via blob
gh api repos/{owner}/{repo}/git/blobs/{sha} --jq '.content' | base64 --decode
```

## Tips

- Always pipe text file contents through `base64 --decode`; the GitHub contents API returns base64.
- For binary files, do not decode into the conversation. Save to disk only when necessary.
- Authenticated GitHub API rate limit is typically 5000 requests per hour.
- If output is very large, redirect decoded content to a file and inspect the relevant section:

```shell
gh api repos/{owner}/{repo}/contents/{path} --jq '.content' | base64 --decode > output.yaml
```
