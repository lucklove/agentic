## agentic

Gitea-notification-driven agent runner. Each profile in `profiles/*.yaml` becomes an independent agent with its own token, model, instructions, capabilities, and polling interval.

## Running

```bash
uv run main.py                                        # poll every profiles/*.yaml file
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

`agentic.yaml` is global Gitea/MCP/skills config. `profiles/<name>.yaml` is per-agent config. Real `profiles/*.yaml` files are gitignored because they contain tokens; keep shared examples in `profiles/example.yaml.template`.

`working_dir` controls the `LocalBackend` cwd used by command/code execution. It defaults to `.` in `agentic.yaml` (the `main.py` directory), and can be overridden per profile with `working_dir` in `profiles/<name>.yaml`. Relative paths are resolved from the `main.py` directory.

Profile `model` values use explicit `<kind>:<name>` strings. Supported kinds are currently `openai-chat`, `openai-responses`, and `anthropic`.

With no profile arguments, `main.py` scans only `profiles/*.yaml`, sorted by filename stem. The example template is excluded because `profiles/example.yaml.template` has the `.template` extension, not `.yaml`.

Profile instruction templating uses `string.Template.safe_substitute`; use `$gitea_username` for substitution. Brace form `{gitea_username}` is not substituted by current code.

## Runtime Flow

`main.py` loads `agentic.yaml`, loads each requested profile, resolves the token's Gitea login with `GET /api/v1/user`, builds an agent, then either starts `poll_forever` or runs the direct `--instruction` path.

Polling opens `async with agent` once per profile, which starts profile-scoped capability lifecycles such as the Gitea MCP subprocess. A poll reads unread notifications, keeps only `Issue` and `Pull`, skips closed or dependency-blocked subjects, then checks whether the last issue comment mentions the current agent or whether the agent is otherwise relevant by role before calling `agent.run(...)`.

Notification threads are marked read only after they are successfully handled or intentionally skipped. Poller errors are not retried internally; they propagate and leave the notification unread for retry or human intervention.

## Capabilities

`agentic.yaml` can define global capabilities that are enabled for all profiles; by default it enables `code_exec`, `gitea`, `harness`, privacy redaction, and model compaction. Profile capability entries are merged by name and replace same-named global entries completely; capability options are not recursively merged.

Configurable capability keys are `code_exec`, `gitea`, `filesystem`, `skills`, `memory`, `harness`, `privacy`, `openai_compaction`, and `anthropic_compaction`. Unknown capability keys are silently ignored by the registry comprehension in `agent_factory.py`.

`allow` and `deny` filtering is shared by Gitea MCP tools and skills through `capabilities/base.py`: allow wins first, then deny subtracts from it; deny-only exposes everything except denied names.

`filesystem.include_execute` defaults to `false`. Memory file stores should use paths under `memories/`, which is gitignored.

## Core Notification Rules

These rules are core invariants for notification handling. They must not be bypassed, weakened, or reordered casually.

For each unread notification, the poller only considers subjects with type `Issue` or `Pull`. It then applies these gates in order:

1. If the issue or pull request is closed, do not process it.
2. If the issue has any open dependencies, do not process it.
3. Inspect the last issue comment.
4. If the last comment was written by the current agent, do not process it.
5. If the last comment mentions anyone, only process it when it mentions the current agent. If it mentions someone else instead, do not process it.
6. If the last comment mentions nobody, fall back to subject-role matching.
7. Process the subject when the current agent is one of the following:
   - creator
   - assignee
   - requested reviewer
   - reviewer from the subject payload
   - a recorded pull-request reviewer from review history
8. If none of the rules above match, do not process it.

Only after all gates pass does the poller call `agent.run(...)`.

## Harness Rules

The built-in harness capability adds shared safety rules to every notification-driven run:

- If the last message @mentions the agent, it must either do the requested work and reply, post helpful context, or apply the final close/merge action directly when the work is already complete.
- If the last message @mentions someone else, the agent must do nothing.
- The agent must read the full issue or pull request context before acting.
- Review requests are blocked until pull-request checks are passing.
- If a PR is complete and already approved, the agent should merge it with squash and delete the branch.
- If an issue is complete and has no open PR associated with it, the agent should close the issue.

## Notes

- Pull-request relevance still reads the last regular issue comment from `/issues/{number}/comments`.
- Pull-request review history is consulted when determining reviewer-based relevance.
- Notification threads are marked read only after the notification is intentionally skipped or successfully handled.
- The default `agent_request_limit` is 100 model requests per notification-handling run.
