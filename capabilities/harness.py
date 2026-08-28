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
from pydantic_core import to_jsonable_python

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
        except Exception as e:
            ctx.deps.run_code_errored = True
            # Append a memory hint so the model is nudged to recall
            # similar fixes before retrying. `add_note()` does NOT
            # reach the model for ``ModelRetry`` (the path
            # ``run_code`` actually uses): pydantic-ai's
            # ``RetryPromptPart.from_error`` reads
            # ``ModelRetry.message`` directly and ignores
            # ``__notes__``. Mutate ``.message`` instead so the
            # hint lands inside the description
            # ``RetryPromptPart.model_response()`` then hands to
            # the model; its own ``"\n\nFix the errors and try
            # again."`` suffix is appended below our hint.
            hint = "You can recall memories to help you fix the errors."
            if isinstance(e, ModelRetry):
                e.message += "\n" + hint
            else:
                e.add_note(hint)
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
