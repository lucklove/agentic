from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic_ai import ModelSettings
from pydantic_ai_harness import CodeMode

from agent_factory import _build_registry
from capabilities.compaction import (
    AnchoredCompaction,
    AnthropicCompaction,
    OpenAICompaction,
)
from capabilities.harness import HarnessCapability
from config import GiteaGlobalConfig, GiteaProfileConfig, GlobalConfig, ProfileConfig
from deps import AgentDeps


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
    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    return _build_registry(global_cfg, profile, working_dir)


def _deps() -> AgentDeps:
    return AgentDeps(
        backend=object(),
        gitea_username="code_agent",
        gitea_base_url="http://gitea.example",
        gitea_token="token",
        profile_name="profile",
    )


def test_registry_builds_anchored_compaction() -> None:
    capability = _registry()["anchored_compaction"](
        {"message_count_threshold": 20, "tail_turns": 3}
    )

    assert isinstance(capability, AnchoredCompaction)
    assert capability.message_count_threshold == 20
    assert capability.tail_turns == 3


def test_registry_builds_default_anchored_compaction() -> None:
    capability = _registry()["anchored_compaction"]({})

    assert isinstance(capability, AnchoredCompaction)
    assert capability.message_count_threshold == 40
    assert capability.tail_turns == 2


def test_registry_builds_openai_compaction() -> None:
    capability = _registry()["openai_compaction"](
        {"enabled": True, "token_threshold": 100_000}
    )

    assert isinstance(capability, OpenAICompaction)
    assert capability.enabled is True
    assert capability.token_threshold == 100_000


def test_registry_builds_openai_stateless_compaction() -> None:
    capability = _registry()["openai_compaction"](
        {"enabled": True, "message_count_threshold": 20}
    )

    assert isinstance(capability, OpenAICompaction)
    assert capability.enabled is True
    assert capability.stateless is True
    assert capability.message_count_threshold == 20


def test_registry_builds_anthropic_compaction() -> None:
    capability = _registry()["anthropic_compaction"](
        {"enabled": True, "token_threshold": 100_000}
    )

    assert isinstance(capability, AnthropicCompaction)
    assert capability.enabled is True
    assert capability.token_threshold == 100_000


def test_disabled_provider_compactions_do_not_add_model_settings() -> None:
    openai = _registry()["openai_compaction"]({"enabled": False})
    anthropic = _registry()["anthropic_compaction"]({"enabled": False})

    assert openai.get_model_settings() is None
    assert anthropic.get_model_settings()(None) == {}  # type: ignore[arg-type]


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
    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    capability = _build_registry(global_cfg, profile, working_dir)["code_exec"]({})

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
    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    capability = _build_registry(global_cfg, profile, working_dir)["code_exec"]({})

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
    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    capability = _build_registry(global_cfg, profile, working_dir)["code_exec"](
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

    working_dir = Path(".")

    with patch("agent_factory.Agent") as agent_cls, patch(
        "agent_factory.build_model", return_value="model"
    ):
        make_agent(profile, global_cfg, working_dir, _deps())

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

    working_dir = Path(".")

    with patch("agent_factory.Agent") as agent_cls, patch(
        "agent_factory.build_model", return_value="model"
    ):
        make_agent(profile, global_cfg, working_dir, _deps())

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["instructions"] == "profile first\n\nshared for code_agent"


def test_make_agent_substitutes_working_dir_in_instructions(
    tmp_path: Path,
) -> None:
    from agent_factory import make_agent

    work = tmp_path / "work"
    work.mkdir()

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        working_dir=str(work),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="workdir is $working_dir",
    )

    with patch("agent_factory.Agent") as agent_cls, patch(
        "agent_factory.build_model", return_value="model"
    ):
        make_agent(profile, global_cfg, work, _deps())

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["instructions"] == f"workdir is {work}"


def test_make_agent_substitutes_working_dir_from_profile(
    tmp_path: Path,
) -> None:
    from agent_factory import make_agent

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
        instructions="workdir is $working_dir",
        working_dir=str(profile_work),
    )

    with patch("agent_factory.Agent") as agent_cls, patch(
        "agent_factory.build_model", return_value="model"
    ):
        make_agent(profile, global_cfg, profile_work, _deps())

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["instructions"] == f"workdir is {profile_work}"


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

    working_dir = Path(".")

    with pytest.raises(ValueError, match="unknown capability keys: typo_capability"):
        make_agent(profile, global_cfg, working_dir, _deps())


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

    working_dir = Path(".")

    with pytest.raises(ValueError, match="unknown capability keys: typo_capability"):
        make_agent(profile, global_cfg, working_dir, _deps())


def test_registry_builds_skills_capability_from_url_list() -> None:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    captured: dict = {}

    def fake_make(*, urls: list[str], base_url: str, token: str) -> SimpleNamespace:
        captured["urls"] = urls
        captured["base_url"] = base_url
        captured["token"] = token
        return SimpleNamespace(skills=[])

    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    with patch("agent_factory.make_skills_capability", side_effect=fake_make):
        _build_registry(global_cfg, profile, working_dir)["skills"](
            ["http://gitea.example/autonomous/agentic/wiki/Skills/Foo.-"]
        )

    assert captured["urls"] == [
        "http://gitea.example/autonomous/agentic/wiki/Skills/Foo.-"
    ]
    assert captured["base_url"] == "http://gitea.example"
    assert captured["token"] == "token"


def test_registry_skills_with_empty_url_list() -> None:
    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    captured: dict = {}

    def fake_make(*, urls: list[str], base_url: str, token: str) -> SimpleNamespace:
        captured["urls"] = urls
        return SimpleNamespace(skills=[])

    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    with patch("agent_factory.make_skills_capability", side_effect=fake_make):
        _build_registry(global_cfg, profile, working_dir)["skills"]([])

    assert captured["urls"] == []


def test_registry_skills_profile_overrides_global() -> None:
    """Profile skills list entirely replaces global (same as other capabilities)."""

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        capabilities={
            "skills": [
                "http://gitea.example/owner/repo/wiki/Global.-",
            ],
        },
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
        capabilities={
            "skills": [
                "http://gitea.example/owner/repo/wiki/Profile-A.-",
                "http://gitea.example/owner/repo/wiki/Profile-B.-",
            ],
        },
    )

    captured: dict = {}

    def fake_make(*, urls: list[str], base_url: str, token: str) -> SimpleNamespace:
        captured["urls"] = urls
        return SimpleNamespace(skills=[])

    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    with patch("agent_factory.make_skills_capability", side_effect=fake_make):
        # effective_capabilities is built in make_agent, so simulate that merge here.
        merged = global_cfg.capabilities | profile.capabilities
        _build_registry(global_cfg, profile, working_dir)["skills"](merged["skills"])

    assert captured["urls"] == [
        "http://gitea.example/owner/repo/wiki/Profile-A.-",
        "http://gitea.example/owner/repo/wiki/Profile-B.-",
    ]


def test_registry_builds_mcp_capability() -> None:
    from unittest.mock import patch

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
    )

    working_dir = (
        Path(profile.working_dir or global_cfg.working_dir).expanduser().resolve()
    )
    registry = _build_registry(global_cfg, profile, working_dir)
    assert "mcp" in registry

    with patch("agent_factory.make_mcp_capability") as mock_make:
        mock_make.return_value = SimpleNamespace(get_toolset=lambda: None)
        registry["mcp"]({"server": {"command": "uv"}})

    mock_make.assert_called_once_with({"server": {"command": "uv"}})


def test_make_agent_profile_empty_mcp_overrides_global() -> None:
    """Profile can disable a globally-configured ``capabilities.mcp``.

    Regression test for issue #242: writing ``mcp:`` (empty) in a profile
    YAML must replace the merged global MCP servers with nothing instead of
    raising ``ValueError("`capabilities.mcp` contains no server entries")``.
    The constructed capability must be a valid ``MCPServersCapability`` that
    simply exposes no MCP tools.
    """
    from agent_factory import make_agent
    from capabilities.mcp_servers import MCPServersCapability

    global_cfg = GlobalConfig(
        gitea=GiteaGlobalConfig(base_url="http://gitea.example", mcp_command=[]),
        capabilities={
            "mcp": {
                "global-server": {"command": "global-cmd"},
            },
        },
    )
    profile = ProfileConfig(
        model="openai-responses:gpt-5.5",
        model_settings={},
        gitea=GiteaProfileConfig(token="token"),
        instructions="test",
        capabilities={"mcp": {}},
    )

    working_dir = Path(".")

    built_caps: list = []

    class _SpyAgent:
        def __init__(self, *args, **kwargs):
            built_caps.extend(kwargs.get("capabilities", []))

    with patch("agent_factory.Agent", _SpyAgent), patch(
        "agent_factory.build_model", return_value="model"
    ):
        make_agent(profile, global_cfg, working_dir, _deps())

    # Exactly one MCP capability slot, built from the (now empty) merged
    # opts. It must be a real MCPServersCapability — not None and not the
    # raw ``make_mcp_capability({})`` exception bubbling out.
    mcp_caps = [c for c in built_caps if isinstance(c, MCPServersCapability)]
    assert len(mcp_caps) == 1
    assert mcp_caps[0].get_toolset() is not None
