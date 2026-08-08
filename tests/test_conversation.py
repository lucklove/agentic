from __future__ import annotations

import logging
import pickle
from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from conversation import (
    close_pending_tool_calls,
    is_conversation_comment,
    last_seen_comment_id_from_marker,
    load_history,
    marker_for,
    save_history,
    strip_all_conversation_markers,
    subject_message_key,
    visible_comments,
)


def test_marker_for_basic() -> None:
    assert marker_for("code_agent") == (
        "<!-- agentic:@code_agent last_seen_comment_id=0 -->"
    )


def test_marker_for_special_characters() -> None:
    assert marker_for("my.bot-1", 42) == (
        "<!-- agentic:@my.bot-1 last_seen_comment_id=42 -->"
    )


def test_is_conversation_comment_true() -> None:
    body = "<!-- agentic:@code_agent last_seen_comment_id=12 -->\n\nI fixed the bug."
    assert is_conversation_comment(body, "code_agent") is True


def test_old_marker_is_not_conversation_comment() -> None:
    body = "<!-- agentic:@code_agent -->\n\nI fixed the bug."
    assert is_conversation_comment(body, "code_agent") is False


def test_is_conversation_comment_false_no_marker() -> None:
    body = "This is a regular comment."
    assert is_conversation_comment(body, "code_agent") is False


def test_is_conversation_comment_false_different_agent() -> None:
    body = "<!-- agentic:@review_agent last_seen_comment_id=12 -->\n\nLGTM"
    assert is_conversation_comment(body, "code_agent") is False


def test_last_seen_comment_id_from_marker() -> None:
    body = "<!-- agentic:@code_agent last_seen_comment_id=123 -->\n\nDone."
    assert last_seen_comment_id_from_marker(body, "code_agent") == 123


def test_last_seen_comment_id_from_marker_missing() -> None:
    assert (
        last_seen_comment_id_from_marker("<!-- agentic:@code_agent -->", "code_agent")
        is None
    )


def test_is_conversation_comment_empty_body() -> None:
    assert is_conversation_comment("", "code_agent") is False


def test_strip_all_conversation_markers_removes_every_marker() -> None:
    """Regression for agentic/agentic#279.

    A comment may carry one marker per dispatch target (one per agent the
    message is addressed to). Each agent must receive only the visible
    body — none of the dispatching markers, including its own.
    """
    body = (
        "<!-- agentic:@code_agent last_seen_comment_id=0 -->\n\n"
        "<!-- agentic:@review_agent last_seen_comment_id=0 -->\n\n"
        "please coordinate"
    )
    assert strip_all_conversation_markers(body) == "please coordinate"


def test_strip_all_conversation_markers_handles_markers_mid_body() -> None:
    """Markers can appear anywhere in a body; all occurrences are scrubbed.

    The helper focuses on removing the markers themselves, not on
    de-duplicating the newlines that surround each marker; the assertion
    is that every marker is gone and the human-visible prose is preserved
    in order.
    """
    body = (
        "prefix\n\n"
        "<!-- agentic:@code_agent last_seen_comment_id=4 -->\n\n"
        "middle\n\n"
        "<!-- agentic:@review_agent last_seen_comment_id=5 -->\n\n"
        "suffix"
    )
    result = strip_all_conversation_markers(body)
    assert "<!-- agentic:@" not in result
    assert "prefix" in result
    assert "middle" in result
    assert "suffix" in result
    assert result.index("prefix") < result.index("middle") < result.index("suffix")


def test_strip_all_conversation_markers_no_marker_returns_body_stripped() -> None:
    body = "  no markers here  "
    assert strip_all_conversation_markers(body) == "no markers here"


def test_strip_all_conversation_markers_empty_body() -> None:
    assert strip_all_conversation_markers("") == ""


def test_visible_comments_filters_conversation() -> None:
    comments = [
        {"body": "Regular context comment", "user": {"login": "human"}},
        {
            "body": "<!-- agentic:@code_agent last_seen_comment_id=12 -->\n\nDone.",
            "user": {"login": "code_agent"},
        },
        {
            "body": "<!-- agentic:@code_agent last_seen_comment_id=12 -->\n\nThanks @code_agent",
            "user": {"login": "human"},
        },
        {"body": "Another regular comment", "user": {"login": "other"}},
    ]
    result = visible_comments(comments, "code_agent")
    assert len(result) == 2
    assert result[0]["body"] == "Regular context comment"
    assert result[1]["body"] == "Another regular comment"


def test_visible_comments_keeps_different_agent_marker() -> None:
    comments = [
        {"body": "<!-- agentic:@review_agent last_seen_comment_id=12 -->\n\nLGTM"},
        {"body": "Regular comment"},
    ]
    result = visible_comments(comments, "code_agent")
    assert len(result) == 2


def test_visible_comments_empty_list() -> None:
    assert visible_comments([], "code_agent") == []


def test_subject_message_key_deterministic() -> None:
    key1 = subject_message_key("owner", "repo", "42")
    key2 = subject_message_key("owner", "repo", "42")
    assert key1 == key2
    assert len(key1) == 32


def test_subject_message_key_same_for_issue_and_pr_number() -> None:
    issue_key = subject_message_key("owner", "repo", "42")
    pr_key = subject_message_key("owner", "repo", "42")
    assert issue_key == pr_key


def test_subject_message_key_differs_by_number() -> None:
    key_1 = subject_message_key("owner", "repo", "1")
    key_2 = subject_message_key("owner", "repo", "2")
    assert key_1 != key_2


def test_save_and_load_history_roundtrip(tmp_path: Path) -> None:
    messages = [
        ModelRequest(parts=(UserPromptPart(content="hello"),)),
    ]
    key = "abc123"

    save_history(tmp_path, key, messages)
    loaded = load_history(tmp_path, key)

    assert len(loaded) == 1
    assert isinstance(loaded[0], ModelRequest)
    part = loaded[0].parts[0]
    assert isinstance(part, UserPromptPart)
    assert part.content == "hello"


def test_load_history_returns_empty_for_missing_key(tmp_path: Path) -> None:
    assert load_history(tmp_path, "nonexistent") == []


def test_load_history_warns_and_returns_empty_for_invalid_pickle(
    tmp_path: Path, caplog
) -> None:
    key = "broken"
    path = tmp_path / f"{key}.pkl"
    path.write_bytes(b"not-a-pickle")

    with caplog.at_level(logging.WARNING):
        loaded = load_history(tmp_path, key)

    assert loaded == []
    assert f"failed to load message history from {path}" in caplog.text


def test_load_history_propagates_unexpected_exceptions(tmp_path: Path) -> None:
    key = "unexpected"
    path = tmp_path / f"{key}.pkl"
    path.write_bytes(pickle.dumps({"unexpected": "payload"}))

    try:
        load_history(tmp_path, key)
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError for unexpected history payload")


def test_save_history_creates_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    messages = [ModelRequest(parts=(UserPromptPart(content="test"),))]

    save_history(nested, "key", messages)

    assert (nested / "key.pkl").exists()
    loaded = load_history(nested, "key")
    assert len(loaded) == 1


def test_close_pending_tool_calls_returns_unchanged_when_no_dangling() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="ok", args={}, tool_call_id="c1")],
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="ok", content="ok-result", tool_call_id="c1")
            ],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="boom")

    assert fixed is history
    assert len(fixed) == 3


def test_close_pending_tool_calls_appends_interrupted_return() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="boom", args={}, tool_call_id="c1")],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="RuntimeError: kaboom")

    assert len(fixed) == 3
    assert fixed[:2] == history  # original entries preserved as-is
    closing = fixed[2]
    assert isinstance(closing, ModelRequest)
    assert len(closing.parts) == 1
    part = closing.parts[0]
    assert isinstance(part, ToolReturnPart)
    assert part.tool_call_id == "c1"
    assert part.tool_name == "boom"
    assert part.outcome == "interrupted"
    assert "RuntimeError: kaboom" in part.content


def test_close_pending_tool_calls_closes_multiple_dangling_calls() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="a", args={}, tool_call_id="c1"),
                ToolCallPart(tool_name="b", args={}, tool_call_id="c2"),
            ],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="crash")

    closing = fixed[-1]
    assert isinstance(closing, ModelRequest)
    by_id = {p.tool_call_id: p for p in closing.parts}
    assert set(by_id) == {"c1", "c2"}
    for part in by_id.values():
        assert part.outcome == "interrupted"
        assert "crash" in part.content


def test_close_pending_tool_calls_only_closes_unmatched_calls() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="ok", args={}, tool_call_id="c1"),
                ToolCallPart(tool_name="boom", args={}, tool_call_id="c2"),
            ],
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="ok", content="ok", tool_call_id="c1")],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="oops")

    closing = fixed[-1]
    assert isinstance(closing, ModelRequest)
    assert len(closing.parts) == 1
    assert closing.parts[0].tool_call_id == "c2"
    assert closing.parts[0].outcome == "interrupted"


def test_close_pending_tool_calls_survives_pickle_round_trip(tmp_path: Path) -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="boom", args={}, tool_call_id="c1")],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="boom")
    key = "round-trip"
    save_history(tmp_path, key, fixed)
    loaded = load_history(tmp_path, key)

    assert len(loaded) == 3
    closing = loaded[-1]
    assert isinstance(closing, ModelRequest)
    part = closing.parts[0]
    assert isinstance(part, ToolReturnPart)
    assert part.tool_call_id == "c1"
    assert part.outcome == "interrupted"


def test_close_pending_tool_calls_treats_retry_prompt_as_closure() -> None:
    """A tool call that came back as a ``RetryPromptPart`` is *closed* —
    pydantic-ai does not re-issue it on resume, so we must NOT append a
    synthetic ``ToolReturnPart(outcome='interrupted')`` for the same id.
    Otherwise the next ``iter(message_history=...)`` sees two responses for
    one ``tool_call_id`` and the model provider returns HTTP 400.

    Mirrors the agentic/agentic#237 scenario: 5 ``run_code`` calls that
    errored out (ModuleNotFoundError, GetContentsOrList err, unknown
    method) all came back as ``RetryPromptPart``; the older implementation
    treated them as pending and wrote 5 duplicate synthetic closures.
    """
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="run_code", args={}, tool_call_id="c1"),
                ToolCallPart(tool_name="run_code", args={}, tool_call_id="c2"),
            ],
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    tool_name="run_code",
                    content="RuntimeError: No module named 'subprocess'",
                    tool_call_id="c1",
                ),
                RetryPromptPart(
                    tool_name="run_code",
                    content="Exception: get file err: GetContentsOrList",
                    tool_call_id="c2",
                ),
            ],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="CancelledError: ")

    assert fixed is history, (
        "all tool calls already have RetryPromptPart responses; nothing "
        "should be appended"
    )
    assert len(fixed) == 3


def test_close_pending_tool_calls_does_not_duplicate_response_for_retry_prompt() -> (
    None
):
    """Regression: previously the helper only tracked ``ToolReturnPart``,
    so any tool call that came back as ``RetryPromptPart`` got a *second*
    synthetic ``ToolReturnPart(outcome='interrupted')`` appended on top of
    its real response. Loading that history back into a pydantic-ai agent
    causes the model API to reject the request with HTTP 400.
    """
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="run_code", args={}, tool_call_id="c1")],
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    tool_name="run_code",
                    content="some error",
                    tool_call_id="c1",
                ),
            ],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="CancelledError: ")

    # Only one response for c1, no synthetic interruption appended.
    assert len(fixed) == 3
    responses = [
        p
        for m in fixed
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, (ToolReturnPart, RetryPromptPart))
    ]
    assert len(responses) == 1
    assert isinstance(responses[0], RetryPromptPart)
    assert responses[0].tool_call_id == "c1"


def test_close_pending_tool_calls_mixes_return_and_retry_prompt() -> None:
    """A history with mixed ToolReturnPart + RetryPromptPart closures
    should not get any synthetic append for those closed ids — both
    kinds of response settle their matching ToolCallPart.
    """
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="a", args={}, tool_call_id="c1"),
                ToolCallPart(tool_name="b", args={}, tool_call_id="c2"),
            ],
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="a", content="ok", tool_call_id="c1"),
                RetryPromptPart(tool_name="b", content="bad", tool_call_id="c2"),
            ],
        ),
    ]

    fixed = close_pending_tool_calls(history, reason="CancelledError: ")

    assert fixed is history, "both tool calls already settled; no append"
    assert len(fixed) == 3
