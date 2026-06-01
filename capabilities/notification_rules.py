"""Shared notification-handling rules and output guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import RunContext

from deps import AgentDeps, NotificationSubject

_NOTIFICATION_RULES_INSTRUCTIONS = """\
## Notification-Handling Rules

- Highest priority:
  - if the last message in the issue or pull request @mentions you, reply with an @mention back to that person unless the task is completed during this turn; if it is completed, finish by applying the appropriate final state change for the issue or PR instead of posting a separate follow-up reply.
  - if the last message in the issue or pull request @mentions someone else, do nothing.
- Do not react to your own comments, except that you are menthioned in the last message.
- Read the project's AGENTS.md.
- Read the full relevant thread and supporting context before acting.
- If no action is required, explain why.
"""


def _comments_path(subject: NotificationSubject) -> str:
    return f"/api/v1/repos/{subject.owner}/{subject.repo}/issues/{subject.number}/comments"


@dataclass
class NotificationRulesCapability(AbstractCapability[AgentDeps]):
    """Inject shared notification instructions and block premature final output."""

    def get_instructions(self) -> str:
        return _NOTIFICATION_RULES_INSTRUCTIONS

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

        last_comment = await self._get_last_comment(subject, ctx.deps)
        if not last_comment:
            return output

        mention = f"@{ctx.deps.gitea_username}"
        if mention not in last_comment.get("body", ""):
            return output

        raise RuntimeError(
            f"The last comment on {subject.owner}/{subject.repo} "
            f"{subject.subject_type.lower()} #{subject.number} mentions {mention}; "
            "the agent must respond in the thread before producing final output."
        )

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
