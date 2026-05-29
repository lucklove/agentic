"""Base classes and shared utilities for all capability modules."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Callable

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

__all__ = ["AbstractCapability", "CapabilityWithTools", "make_name_filter"]


def make_name_filter(
    opts: dict[str, Any],
) -> Callable[[Any], bool] | None:
    """Return a name-based filter callable from an allow/deny options dict.

    Works for any object with a ``.name`` attribute (skills, MCP tools, …).

    Resolution order:
      allow + deny  →  effective = allow - deny  →  item.name in effective
      allow only    →  effective = allow          →  item.name in effective
      deny only     →                             →  item.name not in deny
      neither       →  None  (caller keeps everything)
    """
    allow = frozenset(opts.get("allow", []))
    deny = frozenset(opts.get("deny", []))

    if allow:
        effective = allow - deny
        return lambda item: item.name in effective
    if deny:
        return lambda item: item.name not in deny
    return None


class CapabilityWithTools(AbstractCapability[Any]):
    """Base for capabilities whose instructions auto-append the tool list.

    Subclasses implement ``_instructions_header()`` with static context text.
    ``get_instructions()`` appends a formatted list of tools from
    ``get_toolset()`` automatically, so instructions stay in sync with the
    actual toolset.
    """

    @abstractmethod
    def _instructions_header(self) -> str:
        """Return the static preamble for this capability's instructions."""

    def get_instructions(self) -> str:
        header = self._instructions_header()
        toolset = self.get_toolset()
        if not isinstance(toolset, FunctionToolset) or not toolset.tools:
            return header
        lines = [f"- `{name}`" for name in toolset.tools]
        return header + "\n\n" + "\n".join(lines)
