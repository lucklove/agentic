"""Runtime dependencies passed to every agent.run() call."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai_backends import LocalBackend


@dataclass
class NotificationSubject:
    """Current issue/PR subject being handled during a poller-driven run."""

    owner: str
    repo: str
    number: str
    subject_type: str


@dataclass(frozen=True)
class WikiRead:
    """One ``gitea_wiki_read`` call observed during the current run.

    Recorded by :class:`HarnessCapability` so the anchored-compaction
    summarizer can list which wiki pages the agent consulted before old
    turns are collapsed into a summary. Without this, a long run that
    read a wiki early on will see that wiki's contents vanish into the
    summary and may lose access to guidance the human author meant to
    keep sticky for the task.

    The ``summary`` is a short preview (frontmatter description when
    available, otherwise the first ``summary_max_chars`` of the body)
    that fits inside the new ``## Already-Read Wikis`` section of the
    compaction summary without blowing up the summarizer's output
    budget. The full body is intentionally not preserved here -- the
    section is a breadcrumb, not a copy.
    """

    owner: str
    repo: str
    page_name: str
    summary: str


@dataclass
class AgentDeps:
    """Shared runtime context for agent runs and polling decisions."""

    backend: LocalBackend
    gitea_username: str
    gitea_base_url: str
    gitea_token: str
    http_client_factory: Callable[[str, str], Any] | None = None
    notification_subject: NotificationSubject | None = None
    profile_name: str = ""
    messages_dir: Path | None = None
    run_code_errored: bool = False
    memory_modified: bool = False
    has_mentioned_comments: bool = False
    output: Any = None
    # ``gitea_wiki_read`` calls observed during the current run, in call
    # order. The list is reset by ``_handle_notification`` for every new
    # notification via ``dataclasses.replace`` so reads from one task do
    # not bleed into another. The anchored-compaction summarizer reads
    # this list to populate the new ``## Already-Read Wikis`` section of
    # the compaction summary; re-reading a wiki replaces the entry
    # rather than appending, so the section reflects the agent's most
    # recent view of the page.
    wiki_reads: list[WikiRead] = field(default_factory=list)
