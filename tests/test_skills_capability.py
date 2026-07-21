"""Tests for the wiki-backed skills capability."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from capabilities.skills import (
    WikiSkill,
    WikiSkillCapability,
    make_skills_capability,
    parse_wiki_url,
)

# ── parse_wiki_url ──────────────────────────────────────────────────────────


def test_parse_wiki_url_simple_page() -> None:
    owner, repo, page = parse_wiki_url(
        "http://gitea.ai/agentic/agentic/wiki/Issue-Triage"
    )
    assert (owner, repo, page) == ("agentic", "agentic", "Issue-Triage")


def test_parse_wiki_url_accepts_hyphenated_page() -> None:
    owner, repo, page = parse_wiki_url(
        "http://gitea.ai/agentic/agentic/wiki/Agentic-Skill-Authoring-Checklist"
    )
    assert (owner, repo, page) == (
        "agentic",
        "agentic",
        "Agentic-Skill-Authoring-Checklist",
    )


def test_parse_wiki_url_preserves_page_name() -> None:
    owner, repo, page = parse_wiki_url(
        "http://gitea.ai/agentic/agentic/wiki/Writing-Plans"
    )
    assert (owner, repo, page) == ("agentic", "agentic", "Writing-Plans")


def test_parse_wiki_url_strips_trailing_slashes() -> None:
    owner, repo, page = parse_wiki_url(
        "http://gitea.ai/agentic/agentic/wiki/Issue-Triage/"
    )
    assert (owner, repo, page) == ("agentic", "agentic", "Issue-Triage")


@pytest.mark.parametrize(
    "url",
    [
        "http://gitea.ai/agentic/agentic",  # no /wiki/
        "http://gitea.ai/agentic/agentic/wiki",  # no page
        "http://gitea.ai/agentic/agentic/wiki/",  # no page (trailing slash)
        "not a url at all",
    ],
)
def test_parse_wiki_url_rejects_non_wiki_urls(url: str) -> None:
    with pytest.raises(ValueError, match="not a Gitea wiki URL|missing page name"):
        parse_wiki_url(url)


# ── get_instructions ───────────────────────────────────────────────────────


def test_get_instructions_empty() -> None:
    capability = WikiSkillCapability(skills=[])
    assert capability.get_instructions() == ""


def test_get_instructions_single_line_description() -> None:
    capability = WikiSkillCapability(
        skills=[
            WikiSkill(
                name="issue-triage",
                description="Triage Gitea issues.",
                url="http://gitea.ai/a/r/wiki/Issue-Triage",
                owner="a",
                repo="r",
                page_name="Issue-Triage",
            )
        ]
    )
    text = capability.get_instructions()
    assert "## Available Skills" in text
    assert "- name: issue-triage" in text
    assert "  url: http://gitea.ai/a/r/wiki/Issue-Triage" in text
    assert "  description: Triage Gitea issues." in text
    assert "description: |" not in text
    assert "gitea_wiki_read(owner, repo, pageName)" in text


def test_get_instructions_preserves_multiline_description() -> None:
    capability = WikiSkillCapability(
        skills=[
            WikiSkill(
                name="writing-plans",
                description="Line one.\nLine two.\nLine three.",
                url="http://gitea.ai/a/r/wiki/Writing-Plans",
                owner="a",
                repo="r",
                page_name="Writing-Plans",
            )
        ]
    )
    text = capability.get_instructions()
    # The multi-line description is rendered by yaml.safe_dump as a
    # single-quoted scalar with embedded newlines; all three lines must
    # still appear in the output verbatim.
    assert "Line one." in text
    assert "Line two." in text
    assert "Line three." in text
    # `name:` and `url:` keys must still be present (list item uses
    # "- name: ..." for the first key, then "  url: ..." for continuations).
    assert "- name: writing-plans" in text
    assert "  url: http://gitea.ai/a/r/wiki/Writing-Plans" in text


def test_get_instructions_orders_skills() -> None:
    capability = WikiSkillCapability(
        skills=[
            WikiSkill("a", "desc a", "u1", "o", "r", "A"),
            WikiSkill("b", "desc b", "u2", "o", "r", "B"),
        ]
    )
    text = capability.get_instructions()
    assert text.find("name: a") < text.find("name: b")


def test_get_instructions_does_not_wrap_long_descriptions() -> None:
    """Long `description` values must stay on a single YAML line.

    Regression test for #210: ``yaml.safe_dump(..., width=-1)`` was assumed
    to disable line wrapping, but PyYAML silently falls back to its 80-col
    default when ``width`` is not a positive number greater than 2 * indent.
    The fix is to pass ``width=float("inf")``.
    """
    long_description = "This is a deliberately long skill description. " * 8
    assert len(long_description) > 200  # sanity check on the fixture

    capability = WikiSkillCapability(
        skills=[
            WikiSkill(
                name="long-desc-skill",
                description=long_description,
                url="http://gitea.ai/a/r/wiki/Long-Desc-Skill",
                owner="a",
                repo="r",
                page_name="Long-Desc-Skill",
            )
        ]
    )
    text = capability.get_instructions()
    # The "Available Skills" section is the YAML between the heading and
    # the trailing blank line that precedes the gitea_wiki_read hint.
    yaml_section = text.split("## Available Skills\n\n", 1)[1].split(
        "\n\nIf a task looks like", 1
    )[0]
    # Direct check: the description literal must appear on a single line.
    # PyYAML may wrap the scalar in single quotes (e.g. ``description:
    # 'long text...'``), so accept either quoted or unquoted form — but
    # the literal must be present on a single line, not split by a
    # continuation indent that indicates an 80-col wrap.
    quoted = f"description: '{long_description}'"
    unquoted = f"description: {long_description}"
    assert quoted in yaml_section or unquoted in yaml_section, (
        "description literal not on a single YAML line; " "yaml.safe_dump is wrapping."
    )
    # Round-trip: the rendered YAML must parse back to the original
    # description. This is a value-level safety net that catches the
    # silent-wrap case even if a future refactor reintroduces wrapping.
    parsed = yaml.safe_load(yaml_section)
    assert parsed == [
        {
            "name": "long-desc-skill",
            "url": "http://gitea.ai/a/r/wiki/Long-Desc-Skill",
            "description": long_description,
        }
    ]


# ── make_skills_capability ─────────────────────────────────────────────────


def test_make_skills_capability_returns_empty_for_empty_list() -> None:
    capability = make_skills_capability(
        urls=[], base_url="http://gitea.example", token="t"
    )
    assert capability.get_instructions() == ""


def test_make_skills_capability_passes_through_urls_and_validation() -> None:
    """Happy path: each URL is fetched, frontmatter parsed, name/desc extracted."""
    page_bodies = {
        "agentic/agentic/wiki/page/Issue-Triage": "---\nname: issue-triage\ndescription: Issue triage skill.\n---\nbody",
        "agentic/agentic/wiki/page/Writing-Plans": "---\nname: writing-plans\ndescription: Writing plans skill.\n---\nbody",
    }

    captured_urls: list[str] = []

    def fake_get(url, headers=None, timeout=None):
        captured_urls.append(url)
        for path, body in page_bodies.items():
            if path in url:
                return SimpleNamespace(
                    status_code=200,
                    text=body,
                    json=lambda b=body: {
                        "content_base64": base64.b64encode(b.encode("utf-8")).decode(
                            "ascii"
                        )
                    },
                )
        return SimpleNamespace(status_code=404, text="", json=lambda: {})

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        capability = make_skills_capability(
            urls=[
                "http://gitea.example/agentic/agentic/wiki/Issue-Triage",
                "http://gitea.example/agentic/agentic/wiki/Writing-Plans",
            ],
            base_url="http://gitea.example",
            token="t",
        )

    names = [s.name for s in capability.skills]
    assert names == ["issue-triage", "writing-plans"]
    assert capability.skills[0].description == "Issue triage skill."
    assert "Writing plans skill." in capability.skills[1].description
    # Lock in the Gitea wiki endpoint contract: GET /wiki/page/{page},
    # not /wiki/{page}. Sub-page slashes are URL-encoded as %2F.
    assert (
        "http://gitea.example/api/v1/repos/agentic/agentic/wiki/page/Issue-Triage"
        in captured_urls
    )
    assert (
        "http://gitea.example/api/v1/repos/agentic/agentic/wiki/page/Writing-Plans"
        in captured_urls
    )
    assert "/wiki/page/" in captured_urls[0]  # explicit /page/ segment check


def test_make_skills_capability_raises_on_missing_page() -> None:
    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(status_code=404, text="", json=lambda: {})

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        with pytest.raises(RuntimeError, match="wiki page not found"):
            make_skills_capability(
                urls=[
                    "http://gitea.example/owner/repo/wiki/Missing-Page",
                ],
                base_url="http://gitea.example",
                token="t",
            )


def test_make_skills_capability_raises_on_missing_frontmatter() -> None:
    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "content_base64": "IyBObyBmcm9udG1hdHRlcgoKSnVzdCBtYXJrZG93bi4="
            },
        )

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        with pytest.raises(ValueError, match="missing YAML frontmatter"):
            make_skills_capability(
                urls=["http://gitea.example/owner/repo/wiki/Page"],
                base_url="http://gitea.example",
                token="t",
            )


def test_make_skills_capability_raises_on_unterminated_frontmatter() -> None:
    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "content_base64": "LS0tCm5hbWU6IGZvbwpkZXNjcmlwdGlvbjogb29wcwo="
            },
        )

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        with pytest.raises(ValueError, match="unterminated YAML frontmatter"):
            make_skills_capability(
                urls=["http://gitea.example/owner/repo/wiki/Page"],
                base_url="http://gitea.example",
                token="t",
            )


def test_make_skills_capability_raises_on_missing_name() -> None:
    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "content_base64": "LS0tCmRlc2NyaXB0aW9uOiBubyBuYW1lIGhlcmUKLS0tCg=="
            },
        )

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        with pytest.raises(ValueError, match="missing.*`name`"):
            make_skills_capability(
                urls=["http://gitea.example/owner/repo/wiki/Page"],
                base_url="http://gitea.example",
                token="t",
            )


def test_make_skills_capability_raises_on_missing_description() -> None:
    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"content_base64": "LS0tCm5hbWU6IGZvbwotLS0K"},
        )

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        with pytest.raises(ValueError, match="missing.*`description`"):
            make_skills_capability(
                urls=["http://gitea.example/owner/repo/wiki/Page"],
                base_url="http://gitea.example",
                token="t",
            )


def test_make_skills_capability_raises_on_empty_string_frontmatter_values() -> None:
    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "content_base64": "LS0tCm5hbWU6IApkZXNjcmlwdGlvbjogICAKLS0tCg=="
            },
        )

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        with pytest.raises(ValueError, match="missing.*`name`"):
            make_skills_capability(
                urls=["http://gitea.example/owner/repo/wiki/Page"],
                base_url="http://gitea.example",
                token="t",
            )


def test_make_skills_capability_sends_token() -> None:
    captured: dict = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "content_base64": "LS0tCm5hbWU6IGZvbwpkZXNjcmlwdGlvbjogZAotLS0K"
            },
        )

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        make_skills_capability(
            urls=["http://gitea.example/o/r/wiki/P"],
            base_url="http://gitea.example",
            token="secret-token",
        )

    assert captured["headers"]["Authorization"] == "token secret-token"


def test_make_skills_capability_first_failure_aborts() -> None:
    """If URL #2 is broken, the whole init fails — silent loss is not allowed."""

    def fake_get(url, headers=None, timeout=None):
        if "/Bad" in url:
            return SimpleNamespace(status_code=404, text="", json=lambda: {})
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "content_base64": "LS0tCm5hbWU6IG9rCmRlc2NyaXB0aW9uOiBkCi0tLQo="
            },
        )

    with patch("capabilities.skills.httpx.get", side_effect=fake_get):
        with pytest.raises(RuntimeError, match="wiki page not found"):
            make_skills_capability(
                urls=[
                    "http://gitea.example/o/r/wiki/Ok",
                    "http://gitea.example/o/r/wiki/Bad",
                ],
                base_url="http://gitea.example",
                token="t",
            )


# ── before_tool_execute (pageName normalisation) ────────────────────────────
# Regression tests for #219: WikiSkillCapability must rewrite ``-`` to `` `` in
# the ``pageName`` argument of ``gitea_wiki_read`` / ``gitea_wiki_write`` so
# agents can pass either the URL-slug form (``Issue-Triage``) or the natural
# title form (``Issue Triage``) interchangeably.


def test_before_tool_execute_rewrites_dashes_in_wiki_read_page_name() -> None:
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {
        "method": "get",
        "owner": "agentic",
        "repo": "agentic",
        "pageName": "Issue-Triage",
    }

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args["pageName"] == "Issue Triage"
    # Other keys must survive untouched.
    assert new_args["method"] == "get"
    assert new_args["owner"] == "agentic"
    assert new_args["repo"] == "agentic"
    # The original args dict must not be mutated in place; otherwise
    # downstream handlers / logging would observe an unexpected rewrite.
    assert args["pageName"] == "Issue-Triage"
    assert new_args is not args


def test_before_tool_execute_rewrites_dashes_in_wiki_write_page_name() -> None:
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_write")
    args = {
        "method": "create",
        "owner": "agentic",
        "repo": "agentic",
        "title": "Issue Triage",
        "pageName": "Issue-Triage",
        "content": "body",
    }

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args["pageName"] == "Issue Triage"
    # Other keys (including title, which is the user-facing display name and
    # should keep its space form) must be preserved verbatim.
    assert new_args["title"] == "Issue Triage"
    assert new_args["content"] == "body"


def test_before_tool_execute_rewrites_multiple_dashes() -> None:
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {"pageName": "Writing-Plans-And-Tracking"}

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args["pageName"] == "Writing Plans And Tracking"


def test_before_tool_execute_passes_through_already_spaced_page_name() -> None:
    """No rewrite needed when ``pageName`` already uses spaces."""
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {
        "method": "get",
        "owner": "agentic",
        "repo": "agentic",
        "pageName": "Issue Triage",
    }

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    # Identity return on the hot path so we don't allocate a new dict
    # when there is nothing to rewrite.
    assert new_args is args
    assert new_args["pageName"] == "Issue Triage"


def test_before_tool_execute_ignores_other_tools() -> None:
    """Only wiki MCP tools are subject to the rewrite."""
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_issue_write")
    args = {
        "method": "create",
        "owner": "agentic",
        "repo": "agentic",
        "title": "Has-Dashes-In-Title",
    }

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args is args
    assert new_args["title"] == "Has-Dashes-In-Title"


def test_before_tool_execute_ignores_wiki_list_method_without_page_name() -> None:
    """``gitea_wiki_read(method="list")`` has no ``pageName`` — pass through."""
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {"method": "list", "owner": "agentic", "repo": "agentic"}

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args is args


def test_before_tool_execute_handles_missing_page_name() -> None:
    """If the call has no ``pageName`` key at all, pass through unchanged."""
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {"method": "get", "owner": "agentic", "repo": "agentic"}

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args is args


def test_before_tool_execute_handles_non_string_page_name() -> None:
    """If ``pageName`` isn't a string (defensive), don't try to rewrite."""
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {"pageName": None, "method": "get"}

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args is args
    assert new_args["pageName"] is None


def test_before_tool_execute_preserves_subpage_dot_dash_suffix() -> None:
    """Sub-page skill slugs end in ``.-``; rewriting it would 404 the request.

    Regression test for review feedback on PR #220: a configured skill URL
    like ``http://gitea.ai/agentic/agentic/wiki/Issue-Triage``
    (the format the capability itself documents in its module docstring
    and that :func:`parse_wiki_url` returns verbatim) carries the Gitea
    sub-page slug with a ``.-`` suffix. The MCP ``pageName`` parameter
    must be passed through unchanged — the Gitea wiki API expects the
    slug form, not the natural ``Skills/Issue Triage. `` form. The
    rewrite must therefore skip any ``pageName`` ending in ``.-`` even
    though it contains ``-`` characters earlier in the string.
    """
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {
        "method": "get",
        "owner": "agentic",
        "repo": "agentic",
        "pageName": "Skills/Issue-Triage.-",
    }

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    # Identity return: skip path must not allocate a new dict.
    assert new_args is args
    # The sub-page slug must survive untouched — in particular, the ``.-``
    # suffix must not be turned into ``. `` (which is what an unconditional
    # ``str.replace('-', ' ')`` would do, and the exact failure that
    # the review on PR #220 flagged).
    assert new_args["pageName"] == "Skills/Issue-Triage.-"


def test_before_tool_execute_preserves_dot_dash_suffix_on_top_level_page() -> None:
    """Top-level pageName ending in ``.-`` is also left untouched (defensive).

    The ``.-`` suffix is the canonical marker for "this is a Gitea slug,
    not the natural title" — even if it somehow appears on a top-level
    page (e.g. a skill whose title happens to end with a literal ``.-``,
    or a hand-written URL in a profile), the hook must preserve it. The
    Gitea wiki API treats the slug as opaque; rewriting any ``-`` in it
    is unsafe.
    """
    capability = WikiSkillCapability(skills=[])
    tool_def = SimpleNamespace(name="gitea_wiki_read")
    args = {"pageName": "Foo.-"}

    new_args = asyncio.run(
        capability.before_tool_execute(
            ctx=SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=tool_def,
            args=args,
        )
    )

    assert new_args is args
    assert new_args["pageName"] == "Foo.-"
