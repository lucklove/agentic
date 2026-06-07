from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import FunctionModel

from capabilities.privacy import PrivacyCapability


class _ToolDef:
    name = "write_file"


def make_capability() -> PrivacyCapability:
    return PrivacyCapability.from_spec(
        {
            "patterns": {
                "keywords": [{"value": "my-api-key-123", "category": "API_KEY"}],
                "regex": [{"pattern": r"sk-[A-Za-z0-9]{48}", "category": "OPENAI_KEY"}],
                "builtin": ["email"],
                "exclude": ["example.com"],
            },
        }
    )


def test_request_redaction_redacts_messages_and_instructions() -> None:
    cap = make_capability()
    request = ModelRequest(
        [UserPromptPart("contact me@example.com using my-api-key-123")],
        instructions="email admin@example.com",
    )
    ctx = ModelRequestContext(
        model=object(),
        messages=[request],
        model_settings=None,
        model_request_parameters=object(),
    )

    redacted_ctx = asyncio.run(cap.before_model_request(None, ctx))

    redacted_request = redacted_ctx.messages[0]
    assert isinstance(redacted_request, ModelRequest)
    assert "my-api-key-123" not in redacted_request.parts[0].content
    assert "admin@example.com" not in redacted_request.instructions
    assert "__VG_API_KEY_" in redacted_request.parts[0].content
    assert "__VG_EMAIL_" in redacted_request.instructions


def test_before_tool_execute_restores_tool_arguments() -> None:
    cap = make_capability()
    placeholder = cap.redact_text("write my-api-key-123")

    restored = asyncio.run(
        cap.before_tool_execute(
            None,
            call=ToolCallPart("write_file"),
            tool_def=_ToolDef(),
            args={"content": placeholder},
        )
    )

    assert restored == {"content": "write my-api-key-123"}


def test_before_tool_execute_restores_unconfigured_tool_arguments() -> None:
    cap = make_capability()
    placeholder = cap.redact_text("write my-api-key-123")

    restored = asyncio.run(
        cap.before_tool_execute(
            None,
            call=ToolCallPart("publish"),
            tool_def=type("ToolDef", (), {"name": "publish"})(),
            args={"content": placeholder},
        )
    )

    assert restored == {"content": "write my-api-key-123"}


def test_after_tool_execute_redacts_results() -> None:
    cap = make_capability()

    redacted = asyncio.run(
        cap.after_tool_execute(
            None,
            call=ToolCallPart("write_file"),
            tool_def=_ToolDef(),
            args={},
            result={"output": "saved my-api-key-123"},
        )
    )

    assert redacted["output"].startswith("saved __VG_API_KEY_")
    assert "my-api-key-123" not in redacted["output"]


def test_final_output_restoration_after_output_process() -> None:
    cap = make_capability()
    placeholder = cap.redact_text("my-api-key-123")

    restored = asyncio.run(
        cap.after_output_process(
            None, output_context=object(), output=f"Done: {placeholder}"
        )
    )

    assert restored == "Done: my-api-key-123"


def test_plain_text_agent_output_restores_placeholders() -> None:
    cap = PrivacyCapability.from_spec({"patterns": {"builtin": ["ipv4"]}})

    async def model_func(messages, info):
        placeholder = cap.redact_text("1.2.3.4")
        return ModelResponse([TextPart(f"ip is {placeholder}")])

    agent = Agent(FunctionModel(model_func), output_type=str, capabilities=[cap])

    result = asyncio.run(agent.run("what ip"))

    assert result.output == "ip is 1.2.3.4"


def test_excluded_values_are_not_redacted() -> None:
    cap = PrivacyCapability.from_spec(
        {
            "patterns": {
                "keywords": [{"value": "my-api-key-123", "category": "API_KEY"}],
                "exclude": ["my-api-key-123"],
            },
        }
    )

    assert cap.redact_text("use my-api-key-123") == "use my-api-key-123"


def test_string_keyword_entries_raise_error() -> None:
    with pytest.raises(
        ValueError, match=r"privacy\.patterns\.keywords\[0\] must be a mapping"
    ):
        PrivacyCapability.from_spec({"patterns": {"keywords": ["my-api-key-123"]}})


def test_non_mapping_regex_entries_raise_error() -> None:
    with pytest.raises(
        ValueError, match=r"privacy\.patterns\.regex\[0\] must be a mapping"
    ):
        PrivacyCapability.from_spec({"patterns": {"regex": ["token-[a-z]+"]}})


def test_non_string_builtin_entries_raise_error() -> None:
    with pytest.raises(
        ValueError, match=r"privacy\.patterns\.builtin\[0\] must be a string"
    ):
        PrivacyCapability.from_spec({"patterns": {"builtin": [{"name": "email"}]}})


def test_unknown_builtin_entries_raise_error() -> None:
    with pytest.raises(
        ValueError, match=r"privacy\.patterns\.builtin\[0\] references unknown builtin"
    ):
        PrivacyCapability.from_spec({"patterns": {"builtin": ["not-a-builtin"]}})


def test_non_string_exclude_entries_raise_error() -> None:
    with pytest.raises(
        ValueError, match=r"privacy\.patterns\.exclude\[0\] must be a string"
    ):
        PrivacyCapability.from_spec(
            {"patterns": {"exclude": [{"value": "example.com"}]}}
        )


def test_non_list_pattern_sections_raise_error() -> None:
    with pytest.raises(ValueError, match=r"privacy\.patterns\.keywords must be a list"):
        PrivacyCapability.from_spec({"patterns": {"keywords": {"value": "secret"}}})


def test_disabled_capability_does_not_redact() -> None:
    cap = PrivacyCapability.from_spec(
        {
            "enabled": False,
            "patterns": {
                "keywords": [{"value": "my-api-key-123", "category": "API_KEY"}],
            },
        }
    )

    assert cap.redact_text("keep my-api-key-123") == "keep my-api-key-123"


def test_enabled_capability_injects_privacy_placeholder_instructions() -> None:
    cap = PrivacyCapability.from_spec({"placeholder_prefix": "__SAFE_"})

    instructions = cap.get_instructions()

    assert instructions is not None
    assert "Privacy placeholders" in instructions
    assert "`__SAFE_`" in instructions
    assert "pass the placeholder string exactly as-is" in instructions
    assert "original, unredacted value" in instructions


def test_disabled_capability_does_not_inject_instructions() -> None:
    cap = PrivacyCapability.from_spec({"enabled": False})

    assert cap.get_instructions() is None


def test_historical_tool_messages_are_redacted_before_request() -> None:
    cap = make_capability()
    response = ModelResponse(
        [ToolCallPart("write_file", {"content": "my-api-key-123"})]
    )
    request = ModelRequest([ToolReturnPart("write_file", "wrote my-api-key-123")])
    ctx = ModelRequestContext(
        model=object(),
        messages=[response, request],
        model_settings=None,
        model_request_parameters=object(),
    )

    redacted_ctx = asyncio.run(cap.before_model_request(None, ctx))

    call = redacted_ctx.messages[0].parts[0]
    result = redacted_ctx.messages[1].parts[0]
    assert "my-api-key-123" not in call.args["content"]
    assert "my-api-key-123" not in result.content


def test_zero_ttl_disables_expiry() -> None:
    cap = PrivacyCapability.from_spec(
        {
            "session": {"ttl": 0},
            "patterns": {
                "keywords": [{"value": "my-api-key-123", "category": "API_KEY"}],
            },
        }
    )
    placeholder = cap.redact_text("my-api-key-123")

    cap.session.cleanup()

    assert cap.restore_text(placeholder) == "my-api-key-123"


def test_ttl_cleanup_expires_old_mappings() -> None:
    cap = PrivacyCapability.from_spec(
        {
            "session": {"ttl": "1s"},
            "patterns": {
                "keywords": [{"value": "my-api-key-123", "category": "API_KEY"}],
            },
        }
    )
    placeholder = cap.redact_text("my-api-key-123")
    cap.session.created[placeholder] = datetime.now(timezone.utc) - timedelta(seconds=2)

    cap.session.cleanup()

    assert cap.restore_text(placeholder) == placeholder


def test_max_mappings_evicts_oldest_mapping() -> None:
    cap = PrivacyCapability.from_spec(
        {
            "session": {"max_mappings": 1},
            "patterns": {
                "keywords": [
                    {"value": "first-secret", "category": "SECRET"},
                    {"value": "second-secret", "category": "SECRET"},
                ],
            },
        }
    )

    first_placeholder = cap.redact_text("first-secret")
    second_placeholder = cap.redact_text("second-secret")

    assert cap.restore_text(first_placeholder) == first_placeholder
    assert cap.restore_text(second_placeholder) == "second-secret"


def test_overlapping_matches_use_earliest_longest_match() -> None:
    cap = PrivacyCapability.from_spec(
        {
            "patterns": {
                "keywords": [
                    {"value": "secret-token", "category": "LONG"},
                    {"value": "secret", "category": "SHORT"},
                ],
            },
        }
    )

    redacted = cap.redact_text("use secret-token")

    assert redacted.startswith("use __VG_LONG_")
    assert "secret" not in redacted
    assert cap.restore_text(redacted) == "use secret-token"


def test_inline_regex_flags_are_compiled_by_re() -> None:
    cap = PrivacyCapability.from_spec(
        {
            "patterns": {
                "regex": [{"pattern": r"(?i)token-[a-z]+", "category": "TOKEN"}],
            },
        }
    )

    redacted = cap.redact_text("TOKEN-ABC")

    assert redacted.startswith("__VG_TOKEN_")
    assert cap.restore_text(redacted) == "TOKEN-ABC"
