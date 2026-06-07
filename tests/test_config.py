from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from config import load_global_config, load_profile


def test_load_global_config_defaults_working_dir(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text("""
gitea:
  base_url: http://gitea.example
""")

    config = load_global_config(path)

    assert config.working_dir == "."


def test_load_global_config_reads_working_dir(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text("""
gitea:
  base_url: http://gitea.example
working_dir: /workspace/default
""")

    config = load_global_config(path)

    assert config.working_dir == "/workspace/default"


def test_load_global_config_reads_agent_request_limit(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text("""
gitea:
  base_url: http://gitea.example
agent_request_limit: 42
""")

    config = load_global_config(path)

    assert config.agent_request_limit == 42


def test_load_global_config_defaults_agent_request_limit(tmp_path: Path) -> None:
    path = tmp_path / "agentic.yaml"
    path.write_text("""
gitea:
  base_url: http://gitea.example
""")

    config = load_global_config(path)

    assert config.agent_request_limit == 100


def test_load_profile_reads_optional_working_dir(tmp_path: Path) -> None:
    path = tmp_path / "code_agent.yaml"
    path.write_text("""
model: openai-responses:gpt-5.5
gitea:
  token: token
working_dir: ./profile-workspace
instructions: test
""")

    profile = load_profile(path)

    assert profile.working_dir == "./profile-workspace"


def test_main_help_includes_config_and_profiles_root_flags() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--config CONFIG" in result.stdout
    assert "--profiles-root PROFILES_ROOT" in result.stdout


def test_main_reports_missing_profiles_root(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "agentic.yaml"
    config_path.write_text("""
gitea:
  base_url: http://gitea.example
""")
    missing_profiles_root = tmp_path / "profiles"

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "--config",
            str(config_path),
            "--profiles-root",
            str(missing_profiles_root),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (
        f"profiles root directory not found: {missing_profiles_root}" in result.stderr
    )
