"""Wiki-backed skills capability.

Replaces the directory-based ``pydantic-ai-skills`` discovery with a list of
Gitea wiki page URLs declared in the profile (or global) YAML.

Profile YAML example::

    capabilities:
      skills:
        - http://gitea.ai/autonomous/agentic/wiki/Skills/Issue-Triage.-
        - http://gitea.ai/autonomous/agentic/wiki/Skills/Writing-Plans.-

Validation policy
-----------------
At capability init time, every URL is fetched and validated. Any failure
raises — a broken skill list prevents the agent from starting. This is
intentional: silent skill loss is worse than a visible startup error, and
the cost of one failed wiki fetch is much lower than the cost of an agent
that quietly loses a skill mid-task.

The following are validated:

- The URL parses as a Gitea wiki URL of the form
  ``<base>/<owner>/<repo>/wiki/<page>`` (page may contain ``/`` for
  sub-pages).
- The page exists; a 404 from the wiki API raises immediately.
- The page starts with a YAML frontmatter block that contains a non-empty
  ``name`` and a non-empty ``description`` (the ``description`` is also
  what the prompt injection shows the agent, so a missing value fails
  closed).

Prompt injection
----------------
The capability's ``get_instructions()`` returns a YAML list of every
configured skill, each with ``name`` / ``url`` / ``description`` (the YAML
is produced with :func:`yaml.safe_dump`), followed by a hint to use
``gitea_wiki_read`` to fetch the full body on demand. Wiki content is
fetched via the Gitea REST API at
``/api/v1/repos/{owner}/{repo}/wiki/page/{page_name}`` and decoded from
the response's ``content_base64`` field.

The agent reads the full body via the Gitea MCP ``gitea_wiki_read`` tool,
which is already exposed to the agent through the standard Gitea MCP
capability — no new tool is added.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from pydantic_ai.capabilities import AbstractCapability

__all__ = [
    "WikiSkill",
    "WikiSkillCapability",
    "make_skills_capability",
    "parse_wiki_url",
]


@dataclass(frozen=True)
class WikiSkill:
    """A skill loaded from a Gitea wiki page."""

    name: str
    description: str
    url: str
    owner: str
    repo: str
    page_name: str


class WikiSkillCapability(AbstractCapability[Any]):
    """Capability that injects a YAML list of wiki-backed skills.

    This is a pure prompt-injection capability — it does not expose any
    tools of its own. Full page contents are fetched on demand by the
    agent via the Gitea MCP ``gitea_wiki_read`` tool.
    """

    def __init__(self, skills: list[WikiSkill]) -> None:
        self.skills = list(skills)

    def get_instructions(self) -> str:
        if not self.skills:
            return ""
        items = [
            {"name": s.name, "url": s.url, "description": s.description}
            for s in self.skills
        ]
        # PyYAML has no "no wrap" sentinel: `width=-1` and `width=0`
        # both fall through to the default 80-column wrap. Only `float("inf")`
        # (or a very large int) disables wrapping, which is what we want so
        # long skill descriptions stay on a single line in the prompt.
        yaml_str = yaml.safe_dump(
            items,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=float("inf"),
        ).rstrip()
        hint = (
            "If a task looks like it matches one of the listed skills, call "
            "`gitea_wiki_read(owner, repo, pageName)` to fetch the full body "
            "(owner / repo / pageName can be derived from the url). Each wiki "
            "page starts with a YAML frontmatter that defines `name` and "
            "`description` (already shown above); the skill content is what "
            "follows."
        )
        return f"## Available Skills\n\n{yaml_str}\n\n{hint}"


def parse_wiki_url(url: str) -> tuple[str, str, str]:
    """Parse a Gitea wiki URL into (owner, repo, page_name).

    The page name is the verbatim path segment after ``/wiki/`` and may
    itself contain slashes (for sub-pages) and a ``.-`` suffix that
    Gitea appends automatically when the page name contains a slash. The
    suffix is preserved because the Gitea wiki API expects the slug form
    when re-fetching the page by name.

    Args:
        url: Full URL of a Gitea wiki page
            (e.g. ``http://gitea.ai/autonomous/agentic/wiki/Skills/Issue-Triage.-``).

    Returns:
        ``(owner, repo, page_name)`` tuple.

    Raises:
        ValueError: If the URL does not look like a Gitea wiki URL.
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[2] != "wiki":
        raise ValueError(
            f"not a Gitea wiki URL: {url!r} " f"(expected /<owner>/<repo>/wiki/<page>)"
        )
    owner, repo, _, *page_parts = parts
    page_name = "/".join(page_parts)
    if not page_name:
        raise ValueError(f"wiki URL is missing page name: {url!r}")
    return owner, repo, page_name


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown document body.

    Returns the parsed frontmatter dict. Raises ``ValueError`` if the
    frontmatter block is missing, unterminated, or not a mapping.
    """
    if not content.startswith("---"):
        raise ValueError("missing YAML frontmatter (expected '---' at start)")
    end = content.find("\n---", 3)
    if end == -1:
        raise ValueError("unterminated YAML frontmatter (expected closing '---')")
    fm_text = content[3:end].lstrip("\n")
    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return parsed


def _fetch_wiki_page(
    base_url: str,
    token: str,
    owner: str,
    repo: str,
    page_name: str,
) -> str:
    """Fetch the raw markdown body of a wiki page via the Gitea REST API.

    Raises:
        RuntimeError: If the page does not exist or the API errors.
    """
    api_base = base_url.rstrip("/")
    page_enc = page_name.replace("/", "%2F")
    url = f"{api_base}/api/v1/repos/{owner}/{repo}/wiki/page/{page_enc}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"
    response = httpx.get(url, headers=headers, timeout=30.0)
    if response.status_code == 404:
        raise RuntimeError(f"wiki page not found: {owner}/{repo}/{page_name}")
    if response.status_code >= 400:
        raise RuntimeError(
            f"wiki page fetch failed for {owner}/{repo}/{page_name}: "
            f"HTTP {response.status_code} {response.text[:200]}"
        )
    payload = response.json()
    encoded = payload.get("content_base64", "")
    if not encoded:
        raise RuntimeError(f"wiki page {owner}/{repo}/{page_name} returned empty body")
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"wiki page {owner}/{repo}/{page_name} returned invalid "
            f"content_base64: {exc}"
        ) from exc


def _load_skill(url: str, base_url: str, token: str) -> WikiSkill:
    """Validate a single skill URL by fetching and parsing it.

    Raises:
        ValueError: On URL parse / frontmatter validation failure.
        RuntimeError: On wiki fetch failure.
    """
    owner, repo, page_name = parse_wiki_url(url)
    body = _fetch_wiki_page(base_url, token, owner, repo, page_name)
    frontmatter = _parse_frontmatter(body)
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            f"wiki page {owner}/{repo}/{page_name} frontmatter is missing "
            f"non-empty `name` (got {name!r})"
        )
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"wiki page {owner}/{repo}/{page_name} frontmatter is missing "
            f"non-empty `description` (got {description!r})"
        )
    return WikiSkill(
        name=name.strip(),
        description=description.strip(),
        url=url,
        owner=owner,
        repo=repo,
        page_name=page_name,
    )


def make_skills_capability(
    urls: list[str],
    base_url: str,
    token: str,
) -> WikiSkillCapability:
    """Build a ``WikiSkillCapability`` from a list of wiki page URLs.

    Args:
        urls: Wiki page URLs declared in the profile (or global) YAML.
        base_url: Gitea base URL (matches ``GiteaGlobalConfig.base_url``).
        token: Gitea access token (matches ``GiteaProfileConfig.token``).

    Returns:
        A capability whose ``get_instructions()`` returns the YAML skill
        list and a fetch hint. Empty ``urls`` returns an empty capability
        that injects no instructions.

    Raises:
        ValueError: On any URL parse / frontmatter validation failure.
        RuntimeError: On any wiki fetch failure.
    """
    skills = [_load_skill(url, base_url, token) for url in urls]
    return WikiSkillCapability(skills=skills)
