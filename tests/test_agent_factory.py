from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic_ai import ModelSettings
from pydantic_ai.models.anthropic import AnthropicCompaction
from pydantic_ai.models.openai import OpenAICompaction

from agent_factory import _build_registry
from capabilities.harness import HarnessCapability
from config import GiteaGlobalConfig, GiteaProfileConfig, GlobalConfig, ProfileConfig
from deps import AgentDeps
from pydantic_ai_harness import CodeMode


def _registry() -> dict:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )
    return _build_registry(global_cfg, profile)


def _deps() -> AgentDeps:
    return AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
        profile_name="profile",
    )


def test_registry_builds_openai_compaction() -> None:
    capability = _registry()["openai_compaction"]({"token_threshold": 100_000})

    assert isinstance(capability, OpenAICompaction)
    assert capability.token_threshold == 100_000


def test_registry_builds_openai_stateless_compaction() -> None:
    capability = _registry()["openai_compaction"]({"message_count_threshold": 20})

    assert isinstance(capability, OpenAICompaction)
    assert capability.message_count_threshold == 20


def test_registry_builds_anthropic_compaction() -> None:
    capability = _registry()["anthropic_compaction"]({"token_threshold": 100_000})

    assert isinstance(capability, AnthropicCompaction)
    assert capability.token_threshold == 100_000


def test_registry_builds_harness() -> None:
    capability = _registry()["harness"]({})

    assert isinstance(capability, HarnessCapability)


def test_registry_builds_code_exec_with_mount_from_profile_working_dir(
    tmp_path: Path,
) -> None:
    profile_work = tmp_path / "profile_work"
    profile_work.mkdir()
    global_work = tmp_path / "global_work"
    global_work.mkdir()

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        working_dir=str(global_work),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
        working_dir=str(profile_work),
    )
    capability = _build_registry(global_cfg, profile)["code_exec"]({})

    assert isinstance(capability, CodeMode)
    assert capability.mount is not None
    assert capability.mount.virtual_path == str(profile_work)
    assert capability.mount.host_path == str(profile_work)


def test_registry_builds_code_exec_falls_back_to_global_working_dir(
    tmp_path: Path,
) -> None:
    global_work = tmp_path / "global_work"
    global_work.mkdir()

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        working_dir=str(global_work),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )
    capability = _build_registry(global_cfg, profile)["code_exec"]({})

    assert isinstance(capability, CodeMode)
    assert capability.mount is not None
    assert capability.mount.virtual_path == str(global_work)
    assert capability.mount.host_path == str(global_work)


def test_registry_builds_code_exec_passes_opts(tmp_path: Path) -> None:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        working_dir=str(tmp_path),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )
    capability = _build_registry(global_cfg, profile)["code_exec"](
        {"max_retries": 5, "dynamic_catalog": True}
    )

    assert isinstance(capability, CodeMode)
    assert capability.max_retries == 5
    assert capability.dynamic_catalog is True


def test_make_agent_passes_model_settings() -> None:
    from agent_factory import make_agent

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={"thinking": "high"},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    with patch("agent_factory.Agent") as agent_cls, patch(
        "agent_factory.build_model", return_value="model"
    ):
        make_agent(profile, global_cfg, _deps())

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["model_settings"] == ModelSettings(thinking="high")


def test_make_agent_appends_global_instructions_after_profile() -> None:
    from agent_factory import make_agent

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        instructions="shared for $gitea_username",
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="profile first",
    )

    with patch("agent_factory.Agent") as agent_cls, patch(
        "agent_factory.build_model", return_value="model"
    ):
        make_agent(profile, global_cfg, _deps())

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["instructions"] == "profile first\n\nshared for code_agent"


def test_make_agent_rejects_unknown_global_capability() -> None:
    from agent_factory import make_agent

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        capabilities={"typo_capability": {}},
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    with pytest.raises(ValueError, match="unknown capability keys: typo_capability"):
        make_agent(profile, global_cfg, _deps())


def test_make_agent_rejects_unknown_profile_capability() -> None:
    from agent_factory import make_agent

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
        capabilities={"typo_capability": {}},
    )

    with pytest.raises(ValueError, match="unknown capability keys: typo_capability"):
        make_agent(profile, global_cfg, _deps())


def test_registry_merges_skills_from_multiple_directories() -> None:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    def fake_discover_skills(skills_dir: str) -> list[SimpleNamespace]:
        if skills_dir == str(Path("/profile-skills").resolve()):
            return [
                SimpleNamespace(name="profile-only"),
                SimpleNamespace(name="shared"),
            ]
        if skills_dir == str(Path("/worktree/skills").resolve()):
            return [SimpleNamespace(name="global-only"), SimpleNamespace(name="shared")]
        raise AssertionError(skills_dir)

    captured: list[SimpleNamespace] = []

    def fake_skills_capability(*, skills: list[SimpleNamespace]) -> SimpleNamespace:
        captured.extend(skills)
        return SimpleNamespace(skills=skills)

    with patch(
        "agent_factory._SKILLS_DIR_BASE",
        Path("/worktree"),
    ), patch("agent_factory.discover_skills", side_effect=fake_discover_skills), patch(
        "agent_factory.SkillsCapability",
        side_effect=fake_skills_capability,
    ):
        capability = _build_registry(
            global_cfg,
            profile,
            profile_skills_dirs=(Path("/profile-skills"),),
        )["skills"]({})

    assert [skill.name for skill in captured] == [
        "profile-only",
        "shared",
        "global-only",
    ]
    assert [skill.name for skill in capability.skills] == [
        "profile-only",
        "shared",
        "global-only",
    ]


def test_registry_uses_global_skills_when_profile_skills_dirs_empty() -> None:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    captured: list[SimpleNamespace] = []

    def fake_skills_capability(*, skills: list[SimpleNamespace]) -> SimpleNamespace:
        captured.extend(skills)
        return SimpleNamespace(skills=skills)

    with patch(
        "agent_factory._SKILLS_DIR_BASE",
        Path("/worktree"),
    ), patch(
        "agent_factory.discover_skills",
        return_value=[SimpleNamespace(name="global-only")],
    ) as discover_skills, patch(
        "agent_factory.SkillsCapability",
        side_effect=fake_skills_capability,
    ):
        capability = _build_registry(global_cfg, profile)["skills"]({})

    assert [skill.name for skill in captured] == ["global-only"]
    assert [skill.name for skill in capability.skills] == ["global-only"]
    discover_skills.assert_called_once_with(str(Path("/worktree/skills").resolve()))


def test_registry_uses_repo_skills_directory_from_agent_factory_directory() -> None:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    expected_skills_dir = str(Path("/worktree/skills").resolve())

    with patch(
        "agent_factory._SKILLS_DIR_BASE",
        Path("/worktree"),
    ), patch(
        "agent_factory.discover_skills",
        return_value=[SimpleNamespace(name="global-only")],
    ) as discover_skills, patch(
        "agent_factory.SkillsCapability",
        side_effect=lambda *, skills: SimpleNamespace(skills=skills),
    ):
        _build_registry(global_cfg, profile)["skills"]({})

    discover_skills.assert_called_once_with(expected_skills_dir)
