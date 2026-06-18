"""Patched CodeMode that resets the run_code REPL on every call by default.

Background / root cause
-----------------------
``run_code`` uses a persistent REPL-style namespace that survives across tool
calls within the same agent run.  Any top-level assignment in one call (e.g.
``gitea_get_file_contents = {...}``) silently shadows the injected tool
callable, causing a ``TypeError: object is not callable`` in later calls.

Temporary fix
-------------
``RestartingCodeMode`` subclasses ``CodeMode`` and inserts ``restart=True``
into every ``run_code`` invocation that does not already supply an explicit
``restart`` argument.  This guarantees a fresh namespace on each call, so
accumulated namespace pollution can never carry over.

The fix is intentionally shallow:
* ``_RestartingCodeModeToolset.call_tool`` is the single interception point.
* When the caller explicitly passes ``restart=False`` the value is left
  unchanged — the escape hatch remains available.
* ``for_run`` / ``for_run_step`` use ``dataclasses.replace(self, ...)``
  which preserves the subclass type automatically, so no further overrides
  are needed.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from pydantic_ai import AbstractToolset
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai_harness import CodeMode
from pydantic_ai_harness.code_mode._toolset import CodeModeToolset


@dataclass
class _RestartingCodeModeToolset(CodeModeToolset[AgentDepsT]):
    """CodeModeToolset that injects ``restart=True`` when the caller omits it.

    Injecting at the toolset level (rather than patching the tool schema) means
    the model's JSON arguments are untouched — the default only applies when
    ``restart`` is genuinely absent from the call.
    """

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDepsT],
        tool: ToolsetTool[AgentDepsT],
    ) -> Any:
        # Only intercept run_code; leave all other tools untouched.
        if name == "run_code" and "restart" not in tool_args:
            # Shallow-copy to avoid mutating the dict that pydantic-ai holds.
            tool_args = {**tool_args, "restart": True}
        return await super().call_tool(name, tool_args, ctx, tool)


@dataclass
class RestartingCodeMode(CodeMode[AgentDepsT]):
    """``CodeMode`` variant that resets the REPL namespace on every ``run_code`` call.

    Drop-in replacement for ``CodeMode``; swap in ``agent_factory.py``::

        from capabilities.code_mode import RestartingCodeMode

        # in _build_registry:
        "code_exec": lambda opts: RestartingCodeMode(
            **opts,
            mount=MountDir(str(working_dir), str(working_dir), mode="read-write"),
        ),
    """

    def get_wrapper_toolset(
        self, toolset: AbstractToolset[AgentDepsT]
    ) -> AbstractToolset[AgentDepsT] | None:
        # Let the parent build a CodeModeToolset with all its fields populated,
        # then re-construct as _RestartingCodeModeToolset using the same field
        # values.  This way we never need to enumerate constructor args manually:
        # if the base class gains new fields they are automatically forwarded.
        parent = super().get_wrapper_toolset(toolset)
        assert isinstance(parent, CodeModeToolset)
        field_values = {
            f.name: getattr(parent, f.name) for f in dataclasses.fields(parent)
        }
        return _RestartingCodeModeToolset(**field_values)
