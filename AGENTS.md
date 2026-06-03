# agentic

Gitea-notification-driven agent runner. Each profile in `~/.agentic/<profile>/profile.yaml` becomes an independent agent with its own token, model, instructions, capabilities, and polling interval.

## Commands

```bash
uv run main.py                                        # poll every ~/.agentic/*/profile.yaml
uv run main.py <profile-name> [<profile-name> ...]  # poll one or more profiles concurrently
uv run main.py <profile-name> -i "instruction"      # run once, print model output, do not poll
uv run main.py --help                               # verify CLI shape after entrypoint edits
uv run python -m py_compile main.py                 # focused syntax check
make check                                          # flake8 via uvx
make test                                           # pytest with PEP 723 deps parsed from main.py
make typecheck                                      # mypy with PEP 723 deps parsed from main.py
make fmt                                            # autoflake, isort, black over local .py files
```

There is no `pyproject.toml`; runtime dependencies and Python `>=3.14` live in the PEP 723 metadata block at the top of `main.py`. Add or remove runtime deps there, not in `Makefile`.

## Runtime Flow

`main.py` loads `agentic.yaml`, loads each requested profile, resolves the token's Gitea login with `GET /api/v1/user`, builds an agent, then either starts `poll_forever` or runs the direct `--instruction` path.

Polling opens `async with agent` once per profile, which starts profile-scoped capability lifecycles such as the Gitea MCP subprocess. A poll reads unread notifications, keeps only `Issue` and `Pull`, skips closed/unrelated/dependency-blocked subjects, then calls `agent.run(...)`.

Notification threads are marked read in `poller._handle_notification` only after they are successfully handled or intentionally skipped. Poller errors are not retried internally; they propagate and can terminate the process, and the notification remains unread for retry or human intervention.

## Configuration

`agentic.yaml` is global Gitea/MCP/skills config. Profile files are discovered from `~/.agentic/<profile-name>/profile.yaml`. Keep shared examples in `profiles/example.yaml.template`.

`working_dir` controls the `LocalBackend` cwd used by command/code execution. It defaults to `.` in `agentic.yaml` (the `main.py` directory), and can be overridden per profile with `working_dir` in `~/.agentic/<profile-name>/profile.yaml`. Relative paths are resolved from the `main.py` directory.

Profile `model` values use explicit `<kind>:<name>` strings. Supported kinds are currently `openai-chat`, `openai-responses`, and `anthropic`.

With no profile arguments, `main.py` scans `~/.agentic/*/profile.yaml`. The example template is excluded because `profiles/example.yaml.template` lives in the repository and is not part of discovery.

Profile instruction templating uses `string.Template.safe_substitute`; use `$gitea_username` for substitution. Brace form `{gitea_username}` was documented previously but is not substituted by current code, so existing profiles using it should be updated.

The Gitea MCP command from `agentic.yaml` receives `GITEA_HOST`, `GITEA_ACCESS_TOKEN`, `GOPRIVATE`, `GONOSUMDB`, and `GOINSECURE` per profile in `capabilities/gitea.py`.

## Data Directory Structure

Per-profile data lives under `~/.agentic/<profile-name>/`:

```
~/.agentic/<profile-name>/
    profile.yaml          # agent config (model, token, instructions, capabilities)
    memory.json           # persistent memory store (fixed path, not configurable)
    messages/<md5>.pkl    # per-issue/PR message history for conversation continuity
    skills/               # optional per-profile skills directory
```

The `memory` capability defaults to `backend: file` with path `~/.agentic/<profile-name>/memory.json`. The `messages/` directory is managed by the poller for message history persistence.

## Conversation via Web UI Comments

Issue/PR comments serve as the Web UI conversation channel between humans and agents.

### Comment Marker Convention

Agent comments on issue/PR threads start with a hidden HTML marker:

```
<!-- agentic:@<agent-name> -->
```

Both agent-authored and human-authored comments that contain the marker are treated as "conversation-type comments".

### How It Works

1. **Poller wakeup**: The poller reads raw comments. If the last comment contains the current agent's marker, the comment body is passed directly as `agent.run()` input (marker is preserved as-is). Otherwise, the poller falls back to the notification context prompt.

   Relevance checks treat a trailing agent-authored self-marker comment such as `<!-- agentic:@review_agent -->` as conversation metadata rather than a normal comment. That comment is skipped when choosing the last comment for `@mention`-based relevance, so another agent's marker does not make an assigned/review-requested agent look unrelated.

2. **Message history**: Each issue/PR has a persisted message history (`messages/<md5>.pkl`). History is loaded before `agent.run(message_history=...)` and saved after the run completes.

3. **Auto-post**: After every successful `agent.run()`, the agent output is posted as a comment with the conversation marker prepended.

4. **Tool-layer filtering**: When the agent reads comments via `gitea_*` tools, conversation-type comments (those containing the agent's marker) are automatically filtered out by `HarnessCapability.after_tool_execute`. This prevents the same dialogue from entering the context twice — once via message history and once via tool reads.

5. **Harness consistency**: `HarnessCapability.before_output_process` skips the `@mention` retry logic for conversation-type comments, since these are handled by the poller's direct-input path.

## Capabilities

`agentic.yaml` can define global capabilities that are enabled for all profiles; by default it enables `code_exec`, which maps to `CodeMode`. Profile capability entries are merged by name and replace same-named global entries completely; capability options are not recursively merged.

Configurable capability keys are `code_exec`, `gitea`, `filesystem`, `skills`, `memory`, `privacy`, `openai_compaction`, and `anthropic_compaction`. Unknown capability keys are silently ignored by the registry comprehension in `agent_factory.py`.

`allow` and `deny` filtering is shared by Gitea MCP tools and skills through `capabilities/base.py`: allow wins first, then deny subtracts from it; deny-only exposes everything except denied names.

`filesystem.include_execute` defaults to `false`. Memory file stores should use paths under `memories/`, which is gitignored.

To add a capability, add one factory entry to `_build_registry` in `agent_factory.py`; avoid new condition chains elsewhere.

## Related Documentation

- [Top-level overview](README.md)
- [Profile schema template](profiles/example.yaml.template)
- [Skill authoring guide](skills/create-skill/SKILL.md)
