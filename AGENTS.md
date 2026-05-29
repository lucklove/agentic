# agentic

Gitea-notification-driven agent factory. Each profile is an independent agent that polls Gitea for issue/PR comments and acts on them.

## Running

```bash
uv run main.py <profile-name>   # loads profiles/<profile-name>.yaml
```

No `pyproject.toml`. Dependencies are declared via [PEP 723](https://peps.python.org/pep-0723/) inline metadata in `main.py`. Python ≥ 3.14 required.

## Config layers

| File | Scope |
|---|---|
| `agentic.yaml` | Global: Gitea `base_url`, MCP `command`, `skills_dir` |
| `profiles/<name>.yaml` | Per-agent: `model`, `gitea.token`, `instructions`, `capabilities`, `polling.interval` |

See [profiles/example.yaml.template](profiles/example.yaml.template) for the full profile schema.

## Execution flow

1. `main.py` — resolves Gitea username via `GET /api/v1/user` (one-time REST call), builds agent, enters `poll_forever`
2. `poll_forever` — opens `async with agent` (starts MCP subprocess), loops `poll_once → sleep`
3. `poll_once` — `GET /api/v1/notifications?all=false`, filters `subject.type ∈ {Issue, Pull}`
4. Per notification — runs `agent.run(context_message)` inside a logfire span; **`PATCH /notifications/threads/{id}` (mark-read) always fires in `finally`**, even on agent error
5. Errors propagate uncaught — process exits; no internal retry loop

## Capabilities

`CodeMode` (`code_exec`) is **always on** and not configurable in the profile. Do not add `code_exec:` to a profile's capabilities block — it has no effect.

Configurable capabilities (declared under `capabilities:` in a profile):

| Key | Class | Notable options |
|---|---|---|
| `gitea` | `GiteaMCPCapability` | `allow`, `deny` — MCP tool name filter |
| `filesystem` | `_FSCapability` (ConsoleCapability) | `include_execute: bool` (default `false`) |
| `skills` | `SkillsCapability` | `allow`, `deny` — skill name filter |
| `memory` | `Memory` (vendored from pydantic-ai-harness) | `backend: memory\|file`, `path`, `inject_memories_in_instructions`, `max_instructions_memories` |

`memory` stores live in `memories/` (gitignored). Use `backend: file` with `path: memories/<profile>.json` for persistence across restarts. The file is created automatically on first write — no pre-initialization needed.

The `gitea` capability wraps `MCPServerStdio` as a pydantic-ai capability (not a bare `toolsets=` entry). `async with agent` cascades lifecycle management to the MCP subprocess through `GiteaMCPCapability.get_toolset()`.

## allow / deny filter semantics

Used identically for both `capabilities.gitea` (MCP tool names) and `capabilities.skills` (skill names):

- `allow` + `deny` → effective = `allow − deny`
- `allow` only → `allow`
- `deny` only → everything not in `deny`
- neither → load/expose all

Implemented in `make_name_filter` in [`capabilities/base.py`](capabilities/base.py).

## Skills

Each skill lives in `skills/<name>/SKILL.md` with YAML front-matter:

```yaml
---
name: my-skill-name
description: One-line description.
---
```

The `name:` field (not the directory name) is what `allow`/`deny` matches against. See [skills/example-skill/SKILL.md](skills/example-skill/SKILL.md).

## Agent output

The agent returns `str`. The string is recorded to logfire (`logfire.info("agent output", output=...)`). There is no structured output type.

## Instructions template

`{gitea_username}` in a profile's `instructions:` string is substituted at startup with the login name resolved from the Gitea token. Use it to prevent the agent from reacting to its own comments.

## Adding a capability

Add one entry to `_build_registry` in `agent_factory.py`:

```python
"my-cap": lambda opts: MyCapability(opts.get("some_option", default)),
```

`_build_registry` receives both `global_cfg` and `profile` — close over either as needed.

## Related Documentation

- [profiles/example.yaml.template](profiles/example.yaml.template) — full profile schema with inline comments
- [capabilities/base.py](capabilities/base.py) — `make_name_filter`, `CapabilityWithTools`
- [capabilities/gitea.py](capabilities/gitea.py) — `GiteaMCPCapability`, `make_gitea_capability`
- [capabilities/filesystem.py](capabilities/filesystem.py) — `make_fs_capability`, `AgentDeps`
- [capabilities/memory.py](capabilities/memory.py) — `Memory`, `FileMemoryStore`, `DictMemoryStore` (vendored from pydantic-ai-harness PR, pre-merge)
- [skills/example-skill/SKILL.md](skills/example-skill/SKILL.md) — skill authoring guide
