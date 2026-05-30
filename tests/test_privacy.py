from __future__ import annotations

import asyncio

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext

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
            "restore_tools": ["write_file"],
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


def test_before_tool_execute_restores_selected_tool_arguments() -> None:
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


def test_before_tool_execute_does_not_restore_unconfigured_tools() -> None:
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

    assert restored == {"content": placeholder}


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


def test_final_output_restoration() -> None:
    cap = make_capability()
    placeholder = cap.redact_text("my-api-key-123")

    restored = asyncio.run(
        cap.after_output_validate(
            None, output_context=object(), output=f"Done: {placeholder}"
        )
    )

    assert restored == "Done: my-api-key-123"


def test_excluded_values_are_not_redacted() -> None:
    cap = make_capability()

    assert cap.redact_text("visit example.com") == "visit example.com"


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
