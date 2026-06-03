## agentic

Gitea-notification-driven agent runner. Each profile in `~/.agentic/<profile>/profile.yaml` becomes an independent agent with its own token, model, instructions, capabilities, and polling interval.

## Running

```bash
uv run main.py                                        # poll every ~/.agentic/*/profile.yaml
uv run main.py <profile-name> [<profile-name> ...]  # poll one or more profiles concurrently
uv run main.py <profile-name> -i "instruction"      # run one profile once, print model output, do not poll
uv run main.py --help                               # verify CLI shape after entrypoint edits
uv run python -m py_compile main.py                 # focused syntax check
make check                                          # flake8 via uvx
make test                                           # pytest with PEP 723 deps parsed from main.py
make typecheck                                      # mypy with PEP 723 deps parsed from main.py
make fmt                                            # autoflake, isort, black over local .py files
```

There is no `pyproject.toml`; runtime dependencies and Python `>=3.14` live in the PEP 723 metadata block at the top of `main.py`. Add or remove runtime deps there, not in `Makefile`.

## Configuration

`agentic.yaml` is global Gitea/MCP/skills config. Per-agent config lives at `~/.agentic/<profile>/profile.yaml`. Keep shared examples in `profiles/example.yaml.template`.

`working_dir` controls the `LocalBackend` cwd used by command/code execution. It defaults to `.` in `agentic.yaml` (the `main.py` directory), and can be overridden per profile with `working_dir` in `~/.agentic/<profile>/profile.yaml`. Relative paths are resolved from the `main.py` directory.

Profile `model` values use explicit `<kind>:<name>` strings. Supported kinds are currently `openai-chat`, `openai-responses`, and `anthropic`.

With no profile arguments, `main.py` scans `~/.agentic/*/profile.yaml`, sorted by directory name.

Profile instruction templating uses `string.Template.safe_substitute`; use `$gitea_username` for substitution. Brace form `{gitea_username}` is not substituted by current code.

## Runtime Flow

`main.py` loads `agentic.yaml`, loads each requested profile, resolves the token's Gitea login with `GET /api/v1/user`, builds an agent, then either starts `poll_forever` or runs the direct `--instruction` path.

Polling opens `async with agent` once per profile, which starts profile-scoped capability lifecycles such as the Gitea MCP subprocess. A poll reads unread notifications, keeps only `Issue` and `Pull`, skips closed or dependency-blocked subjects, then checks whether the last issue comment mentions the current agent or whether the agent is otherwise relevant by role before calling `agent.run(...)`.

Notification threads are marked read only after they are successfully handled or intentionally skipped. Poller errors are not retried internally; they propagate and leave the notification unread for retry or human intervention.

## Conversation via Web UI Comments

Issue/PR comments serve as the Web UI conversation channel between humans and agents. Agent comments start with `<!-- agentic:@<agent-name> -->`. Conversation-type comments (those containing the marker) are:

- Used directly as `agent.run()` input when they are the last comment
- Filtered out from `gitea_*` tool reads to avoid double-injection
- Accompanied by per-issue/PR persisted message history (`messages/<md5>.pkl`)

After every `agent.run()`, the output is automatically posted as a comment with the marker prepended, enabling the next round of Web UI dialogue.

## Capabilities

`agentic.yaml` can define global capabilities that are enabled for all profiles; by default it enables `code_exec`, `gitea`, `harness`, privacy redaction, and model compaction. Profile capability entries are merged by name and replace same-named global entries completely; capability options are not recursively merged.

Configurable capability keys are `code_exec`, `gitea`, `filesystem`, `skills`, `memory`, `harness`, `privacy`, `openai_compaction`, and `anthropic_compaction`. Unknown capability keys are silently ignored by the registry comprehension in `agent_factory.py`.

`allow` and `deny` filtering is shared by Gitea MCP tools and skills through `capabilities/base.py`: allow wins first, then deny subtracts from it; deny-only exposes everything except denied names.

`filesystem.include_execute` defaults to `false`. Memory file stores should use paths under `memories/`, which is gitignored.

## Notes

- Pull-request relevance still reads the last regular issue comment from `/issues/{number}/comments`.
- Pull-request review history is consulted when determining reviewer-based relevance.
- Notification threads are marked read only after the notification is intentionally skipped or successfully handled.
- The default `agent_request_limit` is 100 model requests per notification-handling run.
