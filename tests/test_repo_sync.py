from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "repo-sync"
    / "scripts"
    / "sync_github_to_gitea.py"
)
SPEC = importlib.util.spec_from_file_location("sync_github_to_gitea", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_gitea_url_uses_token_owner_and_repo() -> None:
    url = MODULE.build_gitea_url(
        "http://gitea.ai/",
        "secret-token",
        "autonomous",
        "docker-image-controller",
    )

    assert url == "http://secret-token@gitea.ai/autonomous/docker-image-controller.git"


def test_discover_github_url_prefers_origin_when_multiple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "git_remote_lines",
        lambda repo_dir: [
            (
                "upstream",
                "git@github.com:tidbcloud/docker-image-controller.git",
                "fetch",
            ),
            (
                "origin",
                "https://github.com/tidbcloud/docker-image-controller.git",
                "fetch",
            ),
        ],
    )

    url = MODULE.discover_github_url(Path("/tmp/repo"))

    assert url == "https://github.com/tidbcloud/docker-image-controller.git"


def test_discover_github_url_requires_explicit_url_when_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "git_remote_lines",
        lambda repo_dir: [
            (
                "upstream",
                "git@github.com:tidbcloud/docker-image-controller.git",
                "fetch",
            ),
            ("mirror", "https://github.com/acme/docker-image-controller.git", "fetch"),
        ],
    )

    with pytest.raises(SystemExit, match="multiple GitHub fetch remotes"):
        MODULE.discover_github_url(Path("/tmp/repo"))


def test_discover_github_url_requires_github_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "git_remote_lines",
        lambda repo_dir: [
            (
                "origin",
                "https://gitea.ai/autonomous/docker-image-controller.git",
                "fetch",
            )
        ],
    )

    with pytest.raises(SystemExit, match="could not find a GitHub fetch remote"):
        MODULE.discover_github_url(Path("/tmp/repo"))


def test_discover_gitea_url_reads_configs(tmp_path: Path) -> None:
    global_config = tmp_path / "agentic.yaml"
    global_config.write_text("""
gitea:
  base_url: http://gitea.ai
""")
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "ops_agent.yaml").write_text("""
gitea:
  token: top-secret
""")

    url = MODULE.discover_gitea_url(
        "autonomous",
        "docker-image-controller",
        "ops_agent",
        global_config_path=global_config,
        profiles_dir=profiles_dir,
    )

    assert url == "http://top-secret@gitea.ai/autonomous/docker-image-controller.git"
