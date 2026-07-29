"""Shared harness rules and comment guardrails."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic_ai import ModelRetry
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_core import to_jsonable_python

from conversation import visible_comments
from deps import AgentDeps, WikiRead

_MENTION_PATTERN = re.compile(r"(?:^|[^\w`])@[A-Za-z0-9._-]+(?=\W|$)")

# Cap on the per-wiki preview that lands in the compaction summary. The
# preview is meant to remind the agent which page it consulted, not to
# reproduce the body -- if the page looks relevant again, the agent is
# expected to re-read it via ``gitea_wiki_read``.
_WIKI_SUMMARY_MAX_CHARS = 300


@dataclass
class HarnessCapability(AbstractCapability[AgentDeps]):
    """Inject shared harness instructions and block invalid tool comments."""

    def get_toolset(self) -> FunctionToolset[AgentDeps]:
        async def sleep(seconds: int | float) -> str:
            """Sleep for a short number of seconds before re-checking status."""
            await asyncio.sleep(seconds)
            return f"Slept for {seconds} second(s)."

        return FunctionToolset([Tool(sleep, takes_ctx=False)])

    async def before_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: Any,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_def.name == "gitea_issue_write" and args.get("method") == "add_comment":
            self._validate_comment_mentions(ctx, tool_def.name, args)

        return args

    async def after_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: Any,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        if tool_def.name in ("save_memory", "delete_memory"):
            ctx.deps.memory_modified = True

        if tool_def.name == "gitea_wiki_read":
            self._record_wiki_read(ctx, args, result)

        if tool_def.name == "gitea_issue_read" and args.get("method") == "get_comments":
            return self._filter_comment_read_result(ctx, result, args)

        return result

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: Any,
        args: dict[str, Any],
        handler: Any,
    ) -> Any:
        # ``run_code`` reports runtime errors by raising ``ModelRetry``, which
        # bypasses ``on_tool_execute_error``. Record the failure before
        # re-raising so ``before_output_process`` can force a memory update
        # if the agent tries to produce a final answer without one.
        #
        # ``run_code`` also returns a ``ToolReturn`` whose ``return_value``
        # is sent verbatim to the model and to the OTel span as a span
        # attribute. If the sandbox code produced a non-JSON-serializable
        # value (e.g. ``type(1)`` → ``<class 'int'>``), pydantic-ai's
        # instrumentation raises ``PydanticSerializationError`` while
        # dumping that span, which crashes the whole agent run with no
        # ModelRetry. Sanitize unserializable leaves to ``str()`` so the
        # model still sees a useful message and the agent loop continues.
        if tool_def.name != "run_code":
            return await handler(args)
        try:
            result = await handler(args)
        except Exception:
            ctx.deps.run_code_errored = True
            raise

        result.return_value = to_jsonable_python(result.return_value, fallback=str)
        return result

    async def before_output_process(
        self,
        ctx: RunContext[AgentDeps],
        *,
        output_context: Any,
        output: Any,
    ) -> Any:
        if ctx.deps.run_code_errored and not ctx.deps.memory_modified:
            # When run_code errors, we force a ModelRetry to require the agent to update memory
            # before producing its final answer. However, the agent may have already generated
            # a valid output in this turn — save it here so it isn't lost when we raise.
            # On the subsequent retry (after memory is updated), before_output_process will return
            # this saved output instead of the memory-update acknowledgement the agent produces.
            if ctx.deps.output is None:
                ctx.deps.output = output
            raise ModelRetry(
                "run_code encountered an error during this run, but you have not "
                "updated memory. Before producing your final answer, please do one "
                "of the following:\n"
                "- Save a new memory with the lesson learned from this error, using a "
                "meaningful key that describes the lesson.\n"
                "- If a relevant memory already exists but you didn't recall it "
                "(which may have caused the error), recall it and increase its "
                "importance.\n"
                "- If an outdated memory caused the error, delete that memory.\n"
                "- If the error was caused by incorrect guidance from a wiki "
                "page, first correct that wiki page (if you know how to fix it), "
                "then delete a non-existent memory key to acknowledge you've "
                "addressed the underlying guidance.\n"
                "- If the error is trivial or not worth remembering, "
                "delete a non-existent memory key to acknowledge you've considered it."
            )
        if ctx.deps.output is not None:
            return ctx.deps.output
        return output

    def _validate_comment_mentions(
        self,
        ctx: RunContext[AgentDeps],
        tool_name: str,
        args: dict[str, Any],
    ) -> None:
        if not ctx.deps.has_mentioned_comments:
            raise ModelRetry(
                "The current delivered message did not include any direct mention "
                "to you. Do not post an issue comment with `gitea_issue_write`; "
                "respond normally in your final answer instead."
            )

        body = args.get("body")
        if not isinstance(body, str):
            return

        mentions = {
            match.group(0).lstrip(" \t\r\n.,;:!?()[]{}<>")[1:]
            for match in _MENTION_PATTERN.finditer(body)
        }
        mentions.discard(ctx.deps.gitea_username)
        if mentions:
            return

        raise ModelRetry(
            f"Comments created with `{tool_name}` must @mention at least one "
            "person other than yourself. Use your final response for comments "
            "that do not need to notify another user."
        )

    def _filter_comment_read_result(
        self,
        ctx: RunContext[AgentDeps],
        result: Any,
        args: dict[str, Any],
    ) -> Any:
        """Filter conversation-type comments from Gitea comment reads.

        Only filters when the read target matches the current
        ``notification_subject``. Reading comments on a different issue
        must preserve the agent's own markers, since those are
        context rather than ongoing dialogue.
        """
        if not isinstance(result, list):
            return result

        agent_name = ctx.deps.gitea_username
        if not agent_name:
            return result

        subject = ctx.deps.notification_subject
        if subject is None:
            return result

        if args.get("owner") != subject.owner:
            return result
        if args.get("repo") != subject.repo:
            return result
        if str(args.get("issue_number")) != str(subject.number):
            return result

        return visible_comments(result, agent_name)

    @staticmethod
    def _extract_wiki_content(result: Any) -> str | None:
        """Return the raw markdown body from a ``gitea_wiki_read`` result.

        The MCP tool returns the page either as a plain-text string or as
        a dict whose ``content`` field holds the body. The dict shape is
        the one the production MCP server emits; the string shape covers
        test doubles and direct invocations. ``None`` when no body is
        available (e.g. an error response).
        """
        if isinstance(result, str):
            return result or None
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, str) and content:
                return content
        return None

    @staticmethod
    def _summarize_wiki_content(content: str, max_chars: int) -> str:
        """Produce a short preview of a wiki page body.

        Prefers the frontmatter ``description`` when present -- that is a
        human-curated one-liner the wiki author wrote on purpose -- and
        falls back to the first ``max_chars`` characters of the body
        otherwise. Whitespace is normalized so the preview stays on one
        logical line inside the compaction summary.
        """
        description: str | None = None
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                fm_text = content[3:end].lstrip("\n")
                try:
                    parsed = yaml.safe_load(fm_text)
                except yaml.YAMLError:
                    parsed = None
                if isinstance(parsed, dict):
                    raw_desc = parsed.get("description")
                    if isinstance(raw_desc, str) and raw_desc.strip():
                        description = raw_desc.strip()
        preview_source = description if description is not None else content
        preview = re.sub(r"\s+", " ", preview_source).strip()
        if len(preview) > max_chars:
            preview = preview[: max_chars - 1].rstrip() + "\u2026"
        return preview

    @classmethod
    def _record_wiki_read(
        cls,
        ctx: RunContext[AgentDeps],
        args: dict[str, Any],
        result: Any,
    ) -> None:
        """Append a ``WikiRead`` entry to ``ctx.deps.wiki_reads``.

        Skips the call when the args or result don't carry enough
        information to attribute the read to a concrete page (e.g.
        ``method='list'`` -- we only track actual page reads, not
        enumerations). Re-reading the same page replaces the prior
        entry so the compaction summary reflects the agent's most
        recent view of the page.
        """
        owner = args.get("owner")
        repo = args.get("repo")
        page_name = args.get("pageName")
        if not (
            isinstance(owner, str)
            and isinstance(repo, str)
            and isinstance(page_name, str)
            and page_name
        ):
            return

        content = cls._extract_wiki_content(result)
        if content is None:
            return

        summary = cls._summarize_wiki_content(content, _WIKI_SUMMARY_MAX_CHARS)

        existing_index = next(
            (
                index
                for index, prior in enumerate(ctx.deps.wiki_reads)
                if prior.owner == owner
                and prior.repo == repo
                and prior.page_name == page_name
            ),
            None,
        )
        new_entry = WikiRead(
            owner=owner, repo=repo, page_name=page_name, summary=summary
        )
        if existing_index is None:
            ctx.deps.wiki_reads.append(new_entry)
        else:
            ctx.deps.wiki_reads[existing_index] = new_entry
