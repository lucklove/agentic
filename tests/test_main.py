from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from main import ProfileLockError, profile_lock


def test_profile_lock_writes_holder_metadata(tmp_path: Path) -> None:
    profiles_root = tmp_path
    profile_dir = profiles_root / "demo"
    profile_dir.mkdir()

    lock_path = profile_dir / "profile.yaml.lock"

    with profile_lock(profiles_root, "demo"):
        assert lock_path.is_file()
        holder = json.loads(lock_path.read_text())
        assert holder["profile"] == "demo"
        assert holder["pid"] == os.getpid()
        assert holder["hostname"]

    assert lock_path.read_text() == ""


def test_profile_lock_reports_existing_holder(tmp_path: Path) -> None:
    profiles_root = tmp_path
    profile_dir = profiles_root / "demo"
    profile_dir.mkdir()

    with profile_lock(profiles_root, "demo"):
        with pytest.raises(ProfileLockError) as exc_info:
            with profile_lock(profiles_root, "demo"):
                pass

    assert "profile 'demo' is already running" in str(exc_info.value)
    assert f"pid {os.getpid()}" in str(exc_info.value)


def test_main_reports_profile_lock_for_polling(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "agentic.yaml"
    config_path.write_text("""
gitea:
  base_url: http://gitea.example
""")

    profile_dir = tmp_path / "demo"
    profile_dir.mkdir()
    (profile_dir / "profile.yaml").write_text("""
model: openai-responses:gpt-5.5
gitea:
  token: token
instructions: test
""")

    with profile_lock(tmp_path, "demo"):
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "demo",
                "--config",
                str(config_path),
                "--profiles-root",
                str(tmp_path),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 1
    assert "profile 'demo' is already running" in result.stderr
    assert "Traceback" not in result.stderr


def test_main_reports_profile_lock_for_instruction(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = tmp_path / "agentic.yaml"
    config_path.write_text("""
gitea:
  base_url: http://gitea.example
""")

    profile_dir = tmp_path / "demo"
    profile_dir.mkdir()
    (profile_dir / "profile.yaml").write_text("""
model: openai-responses:gpt-5.5
gitea:
  token: token
instructions: test
""")

    with profile_lock(tmp_path, "demo"):
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "demo",
                "--instruction",
                "hello",
                "--config",
                str(config_path),
                "--profiles-root",
                str(tmp_path),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 1
    assert "profile 'demo' is already running" in result.stderr
    assert "Traceback" not in result.stderr
