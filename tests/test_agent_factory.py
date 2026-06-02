from __future__ import annotations

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
