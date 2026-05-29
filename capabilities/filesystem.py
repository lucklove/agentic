"""Filesystem capability via pydantic-ai-backends.

Wraps ``ConsoleCapability`` with agent-appropriate instructions.
Shell execution (execute tool) is controlled by the ``include_execute``
option configured per-profile in the capabilities block:

    capabilities:
      filesystem:
        include_execute: false   # default — no shell access
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai_backends import ConsoleCapability, LocalBackend
from pydantic_ai_backends.permissions.presets import PERMISSIVE_RULESET

from capabilities.base import CapabilityWithTools

__all__ = ["AgentDeps", "make_fs_capability"]


@dataclass
class AgentDeps:
    """Runtime deps passed to every agent.run() call.

    ``backend`` satisfies the ``ConsoleDeps`` protocol expected by the
    filesystem toolset so the agent can read/write files.
    """

    backend: LocalBackend


class _FSCapability(CapabilityWithTools, ConsoleCapability):
    """ConsoleCapability subclass with agent-appropriate instructions."""

    def _instructions_header(self) -> str:
        return """\
## Filesystem & Shell Functions

Call these as `await function_name(...)` inside `run_code`.

Use these functions to read and write files. Do NOT use stdlib file I/O
(`open()`, `os`, `pathlib`) — only these sandbox functions are available.\
"""


def make_fs_capability(include_execute: bool = False) -> _FSCapability:
    """Return an FSCapability configured with the given execute permission."""
    return _FSCapability(
        include_execute=include_execute,
        permissions=PERMISSIVE_RULESET,
    )
