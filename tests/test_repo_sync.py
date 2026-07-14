from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GITHUB_TO_GITEA_PATH = (
    ROOT / "skills" / "repo-sync" / "scripts" / "sync_github_to_gitea.py"
)
GITEA_TO_GITHUB_PATH = (
    ROOT / "skills" / "repo-sync" / "scripts" / "sync_gitea_to_github.py"
)


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GITHUB_TO_GITEA = load_module("sync_github_to_gitea", GITHUB_TO_GITEA_PATH)
GITEA_TO_GITHUB = load_module("sync_gitea_to_github", GITEA_TO_GITHUB_PATH)


class UrlOpenResponse:
    def __init__(self, payload: str) -> None:
        self._buffer = io.StringIO(payload)

    def __enter__(self):
        return self._buffer

    def __exit__(self, exc_type, exc, tb) -> None:
        self._buffer.close()


def test_build_gitea_url_uses_token_owner_and_repo() -> None:
    url = GITHUB_TO_GITEA.build_gitea_url(
        "http://gitea.ai/",
        "secret-token",
        "autonomous",
        "docker-image-controller",
    )

    assert url == "http://secret-token@gitea.ai/autonomous/docker-image-controller.git"


def test_build_github_ssh_url() -> None:
    url = GITHUB_TO_GITEA.build_github_ssh_url("tidbcloud", "auto-deploy")
    assert url == "git@github.com:tidbcloud/auto-deploy.git"


def test_build_github_ssh_url_custom_owner() -> None:
    url = GITHUB_TO_GITEA.build_github_ssh_url("myorg", "myrepo")
    assert url == "git@github.com:myorg/myrepo.git"


def test_gitea_to_github_build_github_ssh_url() -> None:
    url = GITEA_TO_GITHUB.build_github_ssh_url("tidbcloud", "auto-deploy")
    assert url == "git@github.com:tidbcloud/auto-deploy.git"


def test_github_repo_name_from_ssh_url() -> None:
    name = GITEA_TO_GITHUB.github_repo_name("git@github.com:tidbcloud/auto-deploy.git")
    assert name == "tidbcloud/auto-deploy"


def test_github_repo_name_from_ssh_url_without_git_suffix() -> None:
    name = GITEA_TO_GITHUB.github_repo_name("git@github.com:tidbcloud/auto-deploy")
    assert name == "tidbcloud/auto-deploy"


def test_github_repo_name_from_https_url() -> None:
    name = GITEA_TO_GITHUB.github_repo_name(
        "https://github.com/tidbcloud/auto-deploy.git"
    )
    assert name == "tidbcloud/auto-deploy"


def test_github_repo_name_rejects_invalid() -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError, match="invalid GitHub repo URL"):
        GITEA_TO_GITHUB.github_repo_name("not-a-url")


def test_gitea_repo_name_extracts_owner_and_repo() -> None:
    name = GITEA_TO_GITHUB.gitea_repo_name(
        "https://token@gitea.ai/autonomous/agentic.git"
    )
    assert name == "autonomous/agentic"
    name2 = GITEA_TO_GITHUB.gitea_repo_name("http://gitea.ai/autonomous/agentic.git")
    assert name2 == "autonomous/agentic"


def test_gitea_repo_name_with_install_prefix() -> None:
    name = GITEA_TO_GITHUB.gitea_repo_name(
        "https://token@gitea.example/git/autonomous/agentic.git"
    )
    assert name == "autonomous/agentic"


def test_gitea_repo_name_rejects_invalid() -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError, match="invalid Gitea repo URL"):
        GITEA_TO_GITHUB.gitea_repo_name("https://gitea.ai/single")


def test_discover_gitea_url_reads_configs(tmp_path: Path) -> None:
    agentic_dir = tmp_path / ".agentic"
    agentic_dir.mkdir()
    global_config = agentic_dir / "agentic.yaml"
    global_config.write_text("""
gitea:
  base_url: http://gitea.ai
""")
    profile_dir = agentic_dir / "ops_agent"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.yaml").write_text("""
gitea:
  token: top-secret
""")

    url = GITHUB_TO_GITEA.discover_gitea_url(
        "autonomous",
        "docker-image-controller",
        "ops_agent",
        global_config_path=global_config,
        agentic_dir=agentic_dir,
    )

    assert url == "http://top-secret@gitea.ai/autonomous/docker-image-controller.git"


def test_gitea_api_repo_url_preserves_install_prefix() -> None:
    url = GITHUB_TO_GITEA.gitea_api_repo_url(
        "https://token@gitea.example/git/autonomous/agentic.git",
        "autonomous",
        "agentic",
    )

    assert (
        url
        == "https://gitea.example/git/api/v1/repos/autonomous/agentic/pulls?state=open&limit=1"
    )


def test_assert_no_open_gitea_prs_allows_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GITHUB_TO_GITEA.urllib.request,
        "urlopen",
        lambda request: UrlOpenResponse("[]"),
    )

    GITHUB_TO_GITEA.assert_no_open_gitea_prs(
        "autonomous",
        "agentic",
        "http://token@gitea.ai/autonomous/agentic.git",
        "ops_agent",
    )


def test_assert_no_open_gitea_prs_rejects_open_pulls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GITHUB_TO_GITEA.urllib.request,
        "urlopen",
        lambda request: UrlOpenResponse('[{"number": 1, "title": "sync me"}]'),
    )

    with pytest.raises(SystemExit, match="has open pull requests"):
        GITHUB_TO_GITEA.assert_no_open_gitea_prs(
            "autonomous",
            "agentic",
            "http://token@gitea.ai/autonomous/agentic.git",
            "ops_agent",
        )


def test_rewrite_body_strips_gitea_links() -> None:
    rewritten = GITEA_TO_GITHUB.rewrite_body(
        "\nSee http://gitea.ai/autonomous/agentic/issues/155\n\nMore context\n",
        GITEA_TO_GITHUB.gitea_url_pattern(
            "http://token@gitea.ai/autonomous/agentic.git"
        ),
    )

    assert rewritten == "See\n\nMore context"


def test_validate_pr_text_accepts_clean_input() -> None:
    title, body = GITEA_TO_GITHUB.validate_pr_text(
        "Improve repo sync skill",
        "Port the validated commit without forge-specific references.",
        GITEA_TO_GITHUB.gitea_url_pattern(
            "http://token@gitea.ai/autonomous/agentic.git"
        ),
    )

    assert title == "Improve repo sync skill"
    assert body == "Port the validated commit without forge-specific references."


@pytest.mark.parametrize(
    ("title", "body", "match"),
    [
        ("Improve repo sync skill (#155)", "Clean body", "title must not include"),
        ("Improve repo sync skill", "References #155", "body must not include issue"),
        (
            "Improve repo sync skill",
            "See http://gitea.ai/autonomous/agentic/issues/155",
            "body must not include Gitea links",
        ),
    ],
)
def test_validate_pr_text_rejects_forbidden_references(
    title: str,
    body: str,
    match: str,
) -> None:
    with pytest.raises(SystemExit, match=match):
        GITEA_TO_GITHUB.validate_pr_text(
            title,
            body,
            GITEA_TO_GITHUB.gitea_url_pattern(
                "http://token@gitea.ai/autonomous/agentic.git"
            ),
        )
