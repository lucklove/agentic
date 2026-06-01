# agentic

Gitea-notification-driven agent runner. Each profile in `profiles/*.yaml` becomes an independent agent with its own token, model, instructions, capabilities, and polling interval.

## Commands

```bash
uv run main.py                                        # poll every profiles/*.yaml file
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

`agentic.yaml` is global Gitea/MCP/skills config. `profiles/<name>.yaml` is per-agent config. Real `profiles/*.yaml` files are gitignored because they contain tokens; keep shared examples in `profiles/example.yaml.template`.

`working_dir` controls the `LocalBackend` cwd used by command/code execution. It defaults to `.` in `agentic.yaml` (the `main.py` directory), and can be overridden per profile with `working_dir` in `profiles/<name>.yaml`. Relative paths are resolved from the `main.py` directory.

Profile `model` values use explicit `<kind>:<name>` strings. Supported kinds are currently `openai-chat`, `openai-responses`, and `anthropic`.

With no profile arguments, `main.py` scans only `profiles/*.yaml`, sorted by filename stem. The example template is excluded because `profiles/example.yaml.template` has the `.template` extension, not `.yaml`.

Profile instruction templating uses `string.Template.safe_substitute`; use `$gitea_username` for substitution. Brace form `{gitea_username}` was documented previously but is not substituted by current code, so existing profiles using it should be updated.

The Gitea MCP command from `agentic.yaml` receives `GITEA_HOST`, `GITEA_ACCESS_TOKEN`, `GOPRIVATE`, `GONOSUMDB`, and `GOINSECURE` per profile in `capabilities/gitea.py`.

## Capabilities

`agentic.yaml` can define global capabilities that are enabled for all profiles; by default it enables `code_exec`, which maps to `CodeMode`. Profile capability entries are merged by name and replace same-named global entries completely; capability options are not recursively merged.

Configurable capability keys are `code_exec`, `gitea`, `filesystem`, `skills`, and `memory`. Unknown capability keys are silently ignored by the registry comprehension in `agent_factory.py`.

`allow` and `deny` filtering is shared by Gitea MCP tools and skills through `capabilities/base.py`: allow wins first, then deny subtracts from it; deny-only exposes everything except denied names.

`filesystem.include_execute` defaults to `false`. Memory file stores should use paths under `memories/`, which is gitignored.

To add a capability, add one factory entry to `_build_registry` in `agent_factory.py`; avoid new condition chains elsewhere.

## Related Documentation

- [Profile schema template](profiles/example.yaml.template)
- [Skill authoring guide](skills/create-skill/SKILL.md)
