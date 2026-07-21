# agentic

- `main.py` is the only entrypoint. It loads `~/.agentic/agentic.yaml`, discovers profiles from `~/.agentic/*/profile.yaml`, resolves each profile's Gitea username via `GET /api/v1/user`, then either polls forever or runs the one-shot `--instruction` path.
- Runtime deps are not managed by `pyproject.toml`; the source of truth is the PEP 723 dependency block at the top of `main.py`. If a runtime package changes, update that block, not `Makefile`.
- `Makefile` derives `uv run --with ...` flags by parsing `main.py`, so `make test` / `make typecheck` only see dependencies declared there.
- `~/.agentic/agentic.yaml` global capability configs are replaced wholesale by same-named profile capability entries; capability options are not recursively merged.
- Profile instruction templating uses `string.Template.safe_substitute`, so profiles must use `$gitea_username`. `${gitea_username}` also works; bare `{gitea_username}` does not.
- Instructions are assembled as `profile.instructions + "\n\n" + global_cfg.instructions` (profile first). Global instructions append after.
- Relative `working_dir` values are resolved from this directory (`main.py`'s directory), not from the profile directory.
- Per-profile persistent state lives under `~/.agentic/<profile>/`. The default file-backed memory path is `~/.agentic/<profile>/memory.json`; setting a relative `capabilities.memory.path` writes relative to the process cwd instead.
- Per-profile skills can be dropped into `~/.agentic/<profile>/skills/` — that subdirectory is discovered automatically and merged with the global `./skills/` dir. Profile-local skills shadow global skills of the same name.
- Each profile acquires an exclusive `fcntl` lock on `~/.agentic/<profile>/profile.yaml.lock` at startup. Starting the same profile twice raises `ProfileLockError` (exit code 1, printed to stderr with pid and hostname).
- Polling keeps one `async with agent` open per profile, so profile-scoped capability lifecycles such as the Gitea MCP subprocess persist across polling iterations.
- Notification handling only considers `Issue` and `Pull` threads. Notifications are marked read **before** the agent runs (and on every skip path) so that the agent's reply comment does not create a fresh unread notification for the same thread that would re-poll immediately.
- The Web UI conversation channel is comment-based: if the last thread comment carries the current agent's marker, the poller passes that exact comment body to `agent.run(...)`; otherwise it falls back to a notification-context prompt.
- Conversation comments are auto-posted after every successful run. When the agent run raises, an error comment carrying the same marker is posted instead and the exception is logged and swallowed at the `_handle_notification` boundary — a single agent failure never kills the polling process or takes down sibling agents (issue #237 layer 1). `HarnessCapability.after_tool_execute` filters those marker comments out of `gitea_issue_read(method="get_comments")` results so the same dialogue does not enter context twice.
- `--attach/-a` accepts `<owner>/<repo>/issues/<number>`, `<owner>/<repo>/pulls/<number>`, or a full URL. It requires `--instruction`, validates the subject exists (even if closed/merged), and preloads saved pickle history for that thread. History is re-saved after the run.
- Unknown capability keys raise `ValueError` at agent construction (fast-fail on typos).
- `make fmt` runs `autoflake`, `isort`, and `black` across every local `.py` file outside `.venv`. Expect unrelated formatting churn if the tree is dirty.
- Focused verification shortcuts:
  - `uv run main.py --help` for CLI-shape changes
  - `uv run python -m py_compile main.py` for quick entrypoint syntax checks
  - `make check` for flake8 style checks
  - `make test` for the full suite
  - `make typecheck` only checks `main.py`

## Related Documentation

- [README](README.md)
- [Profile template](profiles/example.yaml.template)
- [Skill authoring guide](http://gitea.ai/agentic/agentic/wiki/Agentic-Skill-Authoring)
