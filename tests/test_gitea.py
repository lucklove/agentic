from __future__ import annotations

from capabilities.gitea import GiteaMCPCapability, make_gitea_capability


def _capability(opts: dict) -> GiteaMCPCapability:
    return make_gitea_capability(
        base_url="http://gitea.example",
        mcp_command=["gitea-mcp"],
        token="token",
        opts=opts,
    )


def test_gitea_instructions_include_pr_review_comments_when_unfiltered() -> None:
    instructions = _capability({}).get_instructions()

    assert "Use `gitea_*` functions for Gitea API actions" in instructions
    assert (
        "Use local filesystem/shell tools for local repository operations"
        in instructions
    )
    assert (
        "You may use either `gitea_*` functions or local `git` commands" in instructions
    )
    assert "gitea_pull_request_read" in instructions
    assert 'method="get_review_comments"' in instructions
    assert "review_id" in instructions


def test_gitea_instructions_include_pr_review_comments_when_allowed() -> None:
    instructions = _capability(
        {"allow": ["gitea_pull_request_read"]}
    ).get_instructions()

    assert "gitea_pull_request_read" in instructions
    assert 'method="get_review_comments"' in instructions


def test_gitea_instructions_exclude_pr_review_comments_when_not_allowed() -> None:
    instructions = _capability({"allow": ["gitea_issue_read"]}).get_instructions()

    assert "gitea_pull_request_read" not in instructions
    assert 'method="get_review_comments"' not in instructions


def test_gitea_instructions_exclude_pr_review_comments_when_denied() -> None:
    instructions = _capability({"deny": ["gitea_pull_request_read"]}).get_instructions()

    assert "gitea_pull_request_read" not in instructions
    assert 'method="get_review_comments"' not in instructions
