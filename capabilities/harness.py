"""Shared harness rules and comment guardrails."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.toolsets.function import FunctionToolset

from conversation import visible_comments
from deps import AgentDeps

_MENTION_PATTERN = re.compile(r"(?:^|[^\w`])@[A-Za-z0-9._-]+(?=\W|$)")

_HARNESS_INSTRUCTIONS = """## Harness Rules

- When you want to notify someone, use gitea_issue_write to leave a comment with @someone.
- To mention someone without notifying them, wrap it in backticks like `@someone`.
"""


@dataclass
class HarnessCapability(AbstractCapability[AgentDeps]):
    """Inject shared harness instructions and block invalid tool comments."""

    def get_instructions(self) -> str:
        return _HARNESS_INSTRUCTIONS

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
        if tool_def.name == "gitea_issue_read" and args.get("method") == "get_comments":
            return self._filter_comment_read_result(ctx, result)

        return result

    def _validate_comment_mentions(
        self,
        ctx: RunContext[AgentDeps],
        tool_name: str,
        args: dict[str, Any],
    ) -> None:
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
    ) -> Any:
        """Filter conversation-type comments from Gitea comment reads."""
        if not isinstance(result, list):
            return result

        agent_name = ctx.deps.gitea_username
        if not agent_name:
            return result

        return visible_comments(result, agent_name)
