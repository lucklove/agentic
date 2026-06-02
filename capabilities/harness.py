"""Shared harness rules and output guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai import ModelRetry
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import RunContext

from deps import AgentDeps, NotificationSubject

_HARNESS_INSTRUCTIONS = """\
## Harness Rules

- Highest priority:
  - if the last message in the issue or pull request @mentions you, choose exactly one of these actions:
    - if the work is complete and the subject already meets the close or merge condition, apply that final state change now and do not post any reply.
    - if the mention asks you to do something, do it first, then reply in the thread by calling `gitea_issue_write` and @mention the requester whether the task succeeded or failed, even when the notification subject is a pull request.
    - if the mention only references you without asking for action, post a helpful comment with relevant information by calling `gitea_issue_write` and do not @mention anyone, even when the notification subject is a pull request.
  - if the last message in the issue or pull request @mentions someone else, do nothing.
- Do not react to your own comments, except that you are mentioned in the last message.
- Read the full relevant issue or pull request context before acting.
- If the work is complete and the subject is a PR that has already been approved, merge it with `gitea_pull_request_write` using `method: "merge"`, `merge_style: "squash"`, and `delete_branch: true`.
- If the work is complete and the subject is an issue that is not associated with an open PR, and you judge the issue is resolved, close the issue directly.
- If no action is required, explain why.
"""


def _subject_path(subject: NotificationSubject) -> str:
    path = "pulls" if subject.subject_type == "Pull" else "issues"
    return f"/api/v1/repos/{subject.owner}/{subject.repo}/{path}/{subject.number}"


def _comments_path(subject: NotificationSubject) -> str:
    return f"/api/v1/repos/{subject.owner}/{subject.repo}/issues/{subject.number}/comments"


def _is_closed(item: dict[str, Any]) -> bool:
    return item.get("state") == "closed" or item.get("closed_at") is not None


@dataclass
class HarnessCapability(AbstractCapability[AgentDeps]):
    """Inject shared harness instructions and block premature final output."""

    def get_instructions(self) -> str:
        return _HARNESS_INSTRUCTIONS

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
            "then reply in the thread and @mention the requester whether the task "
            "succeeded or failed; (3) if the mention only references you without "
            "asking for action, post a helpful comment with relevant information "
            "and do not @mention anyone."
        )

    async def _is_subject_closed(
        self,
        subject: NotificationSubject,
        deps: AgentDeps,
    ) -> bool:
        headers = {
            "Authorization": f"token {deps.gitea_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=deps.gitea_base_url,
            headers=headers,
        ) as http:
            resp = await http.get(_subject_path(subject))
            resp.raise_for_status()
            item: dict[str, Any] = resp.json()
        return _is_closed(item)

    async def _get_last_comment(
        self,
        subject: NotificationSubject,
        deps: AgentDeps,
    ) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"token {deps.gitea_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=deps.gitea_base_url,
            headers=headers,
        ) as http:
            resp = await http.get(_comments_path(subject))
            resp.raise_for_status()
            comments: list[dict[str, Any]] = resp.json()
        return comments[-1] if comments else None
