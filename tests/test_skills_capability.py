"""Tests for the wiki-backed skills capability."""

from __future__ import annotations

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
        "http://gitea.ai/autonomous/agentic/wiki/Issue-Triage"
    )
    assert (owner, repo, page) == ("autonomous", "agentic", "Issue-Triage")


def test_parse_wiki_url_sub_page_preserves_slashes() -> None:
    owner, repo, page = parse_wiki_url(
        "http://gitea.ai/autonomous/agentic/wiki/Skills/Issue-Triage/References/Foo"
    )
    assert (owner, repo, page) == (
        "autonomous",
        "agentic",
        "Skills/Issue-Triage/References/Foo",
    )


def test_parse_wiki_url_preserves_gitea_suffix() -> None:
    """Gitea appends a `.-` suffix to slashes in page names; we keep it."""
    owner, repo, page = parse_wiki_url(
        "http://gitea.ai/autonomous/agentic/wiki/Skills/Issue-Triage.-"
    )
    assert (owner, repo, page) == (
        "autonomous",
        "agentic",
        "Skills/Issue-Triage.-",
    )


def test_parse_wiki_url_strips_trailing_slashes() -> None:
    owner, repo, page = parse_wiki_url(
        "http://gitea.ai/autonomous/agentic/wiki/Issue-Triage/"
    )
    assert (owner, repo, page) == ("autonomous", "agentic", "Issue-Triage")


@pytest.mark.parametrize(
    "url",
    [
        "http://gitea.ai/autonomous/agentic",  # no /wiki/
        "http://gitea.ai/autonomous/agentic/wiki",  # no page
        "http://gitea.ai/autonomous/agentic/wiki/",  # no page (trailing slash)
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
        # The capability URL-encodes `/` in the page name as %2F when calling
        # the Gitea REST API. Keys include the `/page/` segment that the
        # live Gitea endpoint uses (GET /wiki/page/{page}, not /wiki/{page}),
        # so the fake matches against the real URL shape.
        "autonomous/agentic/wiki/page/Skills%2FFoo.-": "---\nname: foo\ndescription: Foo skill.\n---\nbody",
        "autonomous/agentic/wiki/page/Skills%2FBar.-": "---\nname: bar\ndescription: |\n  Bar skill.\n  Spans two lines.\n---\nbody",
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
                "http://gitea.example/autonomous/agentic/wiki/Skills/Foo.-",
                "http://gitea.example/autonomous/agentic/wiki/Skills/Bar.-",
            ],
            base_url="http://gitea.example",
            token="t",
        )

    names = [s.name for s in capability.skills]
    assert names == ["foo", "bar"]
    assert capability.skills[0].description == "Foo skill."
    assert "Spans two lines." in capability.skills[1].description
    # Lock in the Gitea wiki endpoint contract: GET /wiki/page/{page},
    # not /wiki/{page}. Sub-page slashes are URL-encoded as %2F.
    assert (
        "http://gitea.example/api/v1/repos/autonomous/agentic/wiki/page/Skills%2FFoo.-"
        in captured_urls
    )
    assert (
        "http://gitea.example/api/v1/repos/autonomous/agentic/wiki/page/Skills%2FBar.-"
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
                    "http://gitea.example/owner/repo/wiki/Missing.-",
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
