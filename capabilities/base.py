"""Base classes and shared utilities for all capability modules."""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["make_name_filter"]


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
