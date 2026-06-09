from __future__ import annotations

import logging
import pickle
from pathlib import Path

from pydantic_ai.messages import ModelRequest, UserPromptPart

from conversation import (
    is_conversation_comment,
    last_seen_comment_id_from_marker,
    load_history,
    marker_for,
    save_history,
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
    key1 = subject_message_key("owner", "repo", "Issue", "42")
    key2 = subject_message_key("owner", "repo", "Issue", "42")
    assert key1 == key2
    assert len(key1) == 32


def test_subject_message_key_differs_by_type() -> None:
    key_issue = subject_message_key("owner", "repo", "Issue", "42")
    key_pr = subject_message_key("owner", "repo", "Pull", "42")
    assert key_issue != key_pr


def test_subject_message_key_differs_by_number() -> None:
    key_1 = subject_message_key("owner", "repo", "Issue", "1")
    key_2 = subject_message_key("owner", "repo", "Issue", "2")
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
