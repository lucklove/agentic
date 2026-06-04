"""Shared harness rules and output guardrails."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai import ModelRetry
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.toolsets.function import FunctionToolset

from conversation import is_conversation_comment, visible_comments
from deps import AgentDeps, NotificationSubject

_MENTION_PATTERN = re.compile(r"(?:^|\W)@[A-Za-z0-9._-]+(?=\W|$)")

_HARNESS_INSTRUCTIONS = """## Harness Rules

- Highest priority:
   - your final response from each notification run is automatically posted as a Gitea issue/PR comment.
   - do not use `gitea_*` tools to post a normal reply/comment on the current thread; use your final response for that.
   - if you use `gitea_*` tools to create a comment, that comment must @mention at least one person other than yourself.
   - your final response may @mention people, but it is not required to @mention anyone.
   - if a message in the issue or pull request @mentions you, choose exactly one of these actions:
     - if the work is complete and the subject already meets the close or merge condition, apply that final state change now and do not post any reply.
     - if the mention asks you to do something, do it first, then reply in the thread by calling `gitea_issue_write` and @mention the requester whether the task succeeded or failed, even when the notification subject is a pull request.
     - if the mention only references you without asking for action, provide helpful relevant information in your final response.
   - if new messages in the issue or pull request only @mention someone else, do nothing.
- Do not react to your own comments, except that you are mentioned in the last message.
- Read the full relevant issue or pull request context before acting.
- If checks are pending, wait and poll again instead of concluding immediately.
  When you need to wait briefly before checking again, prefer the `sleep` function.
- If a PR is blocked by failing checks or requested changes, do the required
  follow-up work first; do not treat the inability to request review or merge
  yet as a final blocker by itself.
- If the work is complete and the subject is a PR that has already been approved, merge it with `gitea_pull_request_write` using `method: "merge"`, `merge_style: "squash"`, and `delete_branch: true`.
- If the work is complete and the subject is an issue that is not associated with an open PR, and you judge the issue is resolved, close the issue directly.
- If no action is required, explain why.
"""

# Gitea MCP tool + method combinations that return issue/PR comments.
# The harness filters conversation-type markers from these results so the
# agent does not re-ingest Web UI dialogue through the tool layer.
_COMMENT_TOOL_METHODS = frozenset(
    {
        "get_comments",
        "list_issue_comments",
        "get_issue_comments",
        "list_comments",
    }
)


def _subject_path(subject: NotificationSubject) -> str:
    path = "pulls" if subject.subject_type == "Pull" else "issues"
    return f"/api/v1/repos/{subject.owner}/{subject.repo}/{path}/{subject.number}"


def _comments_path(subject: NotificationSubject) -> str:
    return (
        f"/api/v1/repos/{subject.owner}/{subject.repo}/issues/{subject.number}/comments"
    )


def _is_closed(item: dict[str, Any]) -> bool:
    return item.get("state") == "closed" or item.get("closed_at") is not None


@dataclass
class HarnessCapability(AbstractCapability[AgentDeps]):
    """Inject shared harness instructions and block premature final output."""

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

    async def before_output_process(
        self,
        ctx: RunContext[AgentDeps],
        *,
        output_context: Any,
        output: Any,
    ) -> Any:
        if ctx.partial_output:
            return output

        subject = ctx.deps.notification_subject
        if subject is None:
            return output

        if await self._is_subject_closed(subject, ctx.deps):
            return output

        last_comment = await self._get_last_comment(subject, ctx.deps)
        if not last_comment:
            return output

        # Conversation-type comments are handled by the poller's direct-input
        # path.  Do not trigger the @mention retry for them.
        if is_conversation_comment(
            last_comment.get("body", ""), ctx.deps.gitea_username
        ):
            return output

        mention = f"@{ctx.deps.gitea_username}"
        if mention not in last_comment.get("body", ""):
            return output

        raise ModelRetry(
            f"The last comment on {subject.owner}/{subject.repo} "
            f"{subject.subject_type.lower()} #{subject.number} mentions {mention}; "
            "before producing final output, choose exactly one of these actions: "
            "(1) if the work is complete and the subject already meets the close "
            "or merge condition, apply that final state change now and do not post "
            "any reply; (2) if the mention asks you to do something, do it first, "
            "then reply in the thread by calling `gitea_issue_write` and @mention "
            "the requester whether the task succeeded or failed; (3) if the "
            "mention only references you without asking for "
            "action, provide helpful relevant information in your final response "
            "without @mentioning anyone."
        )

    async def after_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: Any,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        """Filter conversation-type comments from Gitea comment reads."""
        if not isinstance(result, list):
            return result
        method = args.get("method")
        if method not in _COMMENT_TOOL_METHODS:
            return result
        agent_name = ctx.deps.gitea_username
        if not agent_name:
            return result
        if result:
            try:
                ctx.deps.last_seen_comment_id = int(result[-1]["id"])
            except (KeyError, TypeError, ValueError):
                pass
        return visible_comments(result, agent_name)

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

    async def _is_subject_closed(
        self,
        subject: NotificationSubject,
        deps: AgentDeps,
    ) -> bool:
        async with self._gitea_client(deps) as http:
            resp = await http.get(_subject_path(subject))
            resp.raise_for_status()
            item: dict[str, Any] = resp.json()
        return _is_closed(item)

    async def _get_last_comment(
        self,
        subject: NotificationSubject,
        deps: AgentDeps,
    ) -> dict[str, Any] | None:
        async with self._gitea_client(deps) as http:
            resp = await http.get(_comments_path(subject))
            resp.raise_for_status()
            comments: list[dict[str, Any]] = resp.json()
        return comments[-1] if comments else None

    async def _get_non_successful_checks(
        self,
        *,
        owner: str,
        repo: str,
        pull_number: int | float | str,
        deps: AgentDeps,
    ) -> list[str]:
        async with self._gitea_client(deps) as http:
            pr_resp = await http.get(
                f"/api/v1/repos/{owner}/{repo}/pulls/{pull_number}"
            )
            pr_resp.raise_for_status()
            pr: dict[str, Any] = pr_resp.json()
            head = pr.get("head") or {}
            sha = head.get("sha")
            if not sha:
                return []

            status_resp = await http.get(
                f"/api/v1/repos/{owner}/{repo}/commits/{sha}/status"
            )
            status_resp.raise_for_status()
            payload: dict[str, Any] = status_resp.json()

        statuses = payload.get("statuses") or []
        failing: list[str] = []
        for status in statuses:
            state = status.get("status")
            if state == "success":
                continue
            name = status.get("context") or status.get("target_url") or "unknown"
            failing.append(f"{name} ({state or 'unknown'})")
        return failing

    def _gitea_client(self, deps: AgentDeps) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"token {deps.gitea_token}",
            "Content-Type": "application/json",
        }
        return httpx.AsyncClient(base_url=deps.gitea_base_url, headers=headers)
