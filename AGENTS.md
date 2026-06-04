# agentic

- `main.py` is the only entrypoint. It loads `agentic.yaml`, discovers profiles from `~/.agentic/*/profile.yaml`, resolves each profile's Gitea username via `GET /api/v1/user`, then either polls forever or runs the one-shot `--instruction` path.
- Runtime deps are not managed by `pyproject.toml`; the source of truth is the PEP 723 dependency block at the top of `main.py`. If a runtime package changes, update that block, not `Makefile`.
- `Makefile` derives `uv run --with ...` flags by parsing `main.py`, so `make test` / `make typecheck` only see dependencies declared there.
- `agentic.yaml` global capability configs are replaced wholesale by same-named profile capability entries; capability options are not recursively merged.
- Profile instruction templating uses `string.Template.safe_substitute`, so profiles must use `$gitea_username`. `${gitea_username}` also works; bare `{gitea_username}` does not.
- Relative `working_dir` values are resolved from this directory (`main.py`'s directory), not from the profile directory.
- Per-profile persistent state lives under `~/.agentic/<profile>/`. The default file-backed memory path is `~/.agentic/<profile>/memory.json`; setting a relative `capabilities.memory.path` writes relative to the process cwd instead.
- Polling keeps one `async with agent` open per profile, so profile-scoped capability lifecycles such as the Gitea MCP subprocess persist across polling iterations.
- Notification handling only considers `Issue` and `Pull` threads. Notifications are marked read only after successful handling or an intentional skip; unhandled errors leave them unread.
- The Web UI conversation channel is comment-based: if the last thread comment carries the current agent's marker, the poller passes that exact comment body to `agent.run(...)`; otherwise it falls back to a notification-context prompt.
- Conversation comments are auto-posted after every successful run. `HarnessCapability.after_tool_execute` filters those marker comments out of `gitea_issue_read(method="get_comments")` results so the same dialogue does not enter context twice.
- `make fmt` runs `autoflake`, `isort`, and `black` across every local `.py` file outside `.venv`. Expect unrelated formatting churn if the tree is dirty.
- Focused verification shortcuts:
  - `uv run main.py --help` for CLI-shape changes
  - `uv run python -m py_compile main.py` for quick entrypoint syntax checks
  - `make test` for the full suite
  - `make typecheck` only checks `main.py`

## Related Documentation

- [README](README.md)
- [Profile template](profiles/example.yaml.template)
- [Skill authoring guide](skills/create-skill/SKILL.md)
