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

        if tool_def.name == "gitea_issue_read" and args.get("method") == "get_comments":
            return self._filter_comment_read_result(ctx, result)

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
        # run_code reports runtime errors by raising ModelRetry, which bypasses
        # on_tool_execute_error. Wrap the execution so any failure (ModelRetry
        # included) is recorded before being re-raised.
        if tool_def.name != "run_code":
            return await handler(args)
        try:
            return await handler(args)
        except Exception:
            ctx.deps.run_code_errored = True
            raise

    async def before_output_process(
        self,
        ctx: RunContext[AgentDeps],
        *,
        output_context: Any,
        output: Any,
    ) -> Any:
        if ctx.deps.run_code_errored and not ctx.deps.memory_modified:
            raise ModelRetry(
                "run_code encountered an error during this run, but you have not "
                "updated memory. Before producing your final answer, please do one "
                "of the following:\n"
                "- Save a new memory with the lesson learned from this error.\n"
                "- If a relevant memory already exists but you didn't recall it "
                "(which may have caused the error), recall it and increase its "
                "importance.\n"
                "- If an outdated memory caused the error, delete that memory.\n"
                "- If the error is trivial or not worth remembering, "
                "delete a non-existent memory key to acknowledge you've considered it."
            )
        return output

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
