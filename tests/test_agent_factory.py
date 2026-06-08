from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic_ai import ModelSettings
from pydantic_ai.models.anthropic import AnthropicCompaction
from pydantic_ai.models.openai import OpenAICompaction

from agent_factory import _build_registry
from capabilities.harness import HarnessCapability
from config import GiteaGlobalConfig, GiteaProfileConfig, GlobalConfig, ProfileConfig


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


def test_make_agent_passes_model_settings() -> None:
    from agent_factory import make_agent
    from deps import AgentDeps

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
        make_agent(
            profile,
            global_cfg,
            AgentDeps(
                backend=object(),
                gitea_username="code_agent",
                gitea_base_url="http://gitea.example",
                gitea_token="token",
                profile_name="profile",
            ),
        )

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["model_settings"] == ModelSettings(thinking="high")


def test_registry_merges_skills_from_multiple_directories() -> None:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        skills_dir="/global-skills",
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
        if skills_dir == str(Path("/global-skills").resolve()):
            return [SimpleNamespace(name="global-only"), SimpleNamespace(name="shared")]
        raise AssertionError(skills_dir)

    captured: list[SimpleNamespace] = []

    def fake_skills_capability(*, skills: list[SimpleNamespace]) -> SimpleNamespace:
        captured.extend(skills)
        return SimpleNamespace(skills=skills)

    with patch(
        "agent_factory.discover_skills", side_effect=fake_discover_skills
    ), patch(
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
        skills_dir="/global-skills",
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
        "agent_factory.discover_skills",
        return_value=[SimpleNamespace(name="global-only")],
    ) as discover_skills, patch(
        "agent_factory.SkillsCapability",
        side_effect=fake_skills_capability,
    ):
        capability = _build_registry(global_cfg, profile)["skills"]({})

    assert [skill.name for skill in captured] == ["global-only"]
    assert [skill.name for skill in capability.skills] == ["global-only"]
    discover_skills.assert_called_once_with(str(Path("/global-skills").resolve()))


def test_registry_resolves_relative_global_skills_dir_from_agent_factory_directory() -> (
    None
):
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        skills_dir="./skills",
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    base_dir = Path("/repo/subdir")
    expected_skills_dir = str((base_dir / "skills").resolve())

    with patch(
        "agent_factory.discover_skills",
        return_value=[SimpleNamespace(name="global-only")],
    ) as discover_skills, patch(
        "agent_factory.SkillsCapability",
        side_effect=lambda *, skills: SimpleNamespace(skills=skills),
    ):
        _build_registry(
            global_cfg,
            profile,
            global_skills_dir_base=base_dir,
        )[
            "skills"
        ]({})

    discover_skills.assert_called_once_with(expected_skills_dir)
