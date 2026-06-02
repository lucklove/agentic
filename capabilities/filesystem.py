"""Filesystem capability via pydantic-ai-backends.

Wraps ``ConsoleCapability`` with agent-appropriate instructions.
Shell execution (execute tool) is controlled by the ``include_execute``
option configured per-profile in the capabilities block:

    capabilities:
      filesystem:
        include_execute: false   # default — no shell access
"""

from __future__ import annotations

from pydantic_ai_backends import ConsoleCapability
from pydantic_ai_backends.permissions.presets import PERMISSIVE_RULESET

from capabilities.base import CapabilityWithTools

__all__ = ["make_fs_capability"]


class _FSCapability(CapabilityWithTools, ConsoleCapability):
    """ConsoleCapability subclass with agent-appropriate instructions."""

    def _instructions_header(self) -> str:
        return """\
## Filesystem & Shell Functions

Call these as `await function_name(...)` inside `run_code`.

Use these functions to read and write files. Do NOT use stdlib file I/O
(`open()`, `os`, `pathlib`) — only these sandbox functions are available.

When shell execution is available, use it for normal local repository work such
as reading the checkout, creating worktrees, running tests, and invoking local
`git` commands.

Do NOT use shell commands to call the Gitea HTTP API or to replace available
`gitea_*` functions for issue, pull request, review, or other server-side
actions.\
"""


def make_fs_capability(include_execute: bool = False) -> _FSCapability:
    """Return an FSCapability configured with the given execute permission."""
    return _FSCapability(
        include_execute=include_execute,
        permissions=PERMISSIVE_RULESET,
    )
