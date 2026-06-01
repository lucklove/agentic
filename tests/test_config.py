from __future__ import annotations

from pathlib import Path

from config import load_global_config, load_profile


def test_load_global_config_defaults_working_dir(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text(
        """
gitea:
  base_url: http://gitea.example
"""
    )

    config = load_global_config(path)

    assert config.working_dir == "."


def test_load_global_config_reads_working_dir(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text(
        """
gitea:
  base_url: http://gitea.example
working_dir: /workspace/default
"""
    )

    config = load_global_config(path)

    assert config.working_dir == "/workspace/default"


def test_load_global_config_reads_agent_request_limit(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text(
        """
gitea:
  base_url: http://gitea.example
agent_request_limit: 42
"""
    )

    config = load_global_config(path)

    assert config.agent_request_limit == 42


def test_load_global_config_defaults_agent_request_limit(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text(
        """
gitea:
  base_url: http://gitea.example
"""
    )

    config = load_global_config(path)

    assert config.agent_request_limit == 100


def test_load_profile_reads_optional_working_dir(tmp_path: Path) -> None:
    path = tmp_path / "code_agent.yaml"
    path.write_text(
        """
model: openai-responses:gpt-5.5
gitea:
  token: token
working_dir: ./profile-workspace
instructions: test
"""
    )

    profile = load_profile(path)

    assert profile.working_dir == "./profile-workspace"
