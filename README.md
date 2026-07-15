## agentic

Gitea-notification-driven agent runner. Each profile in `~/.agentic/<profile>/profile.yaml` becomes an independent agent with its own token, model, instructions, capabilities, and polling interval.

## Running

```bash
uv run main.py                                        # poll every ~/.agentic/*/profile.yaml
uv run main.py <profile-name> [<profile-name> ...]  # poll one or more profiles concurrently
uv run main.py <profile-name> -i "instruction"      # run one profile once, print model output, do not poll
uv run main.py <profile-name> -i "instruction" -a autonomous/agentic/issues/160
                                                # attach the one-shot run to an existing issue/PR and load saved history
uv run main.py --config /path/to/agentic.yaml       # override the global config file path
uv run main.py --profiles-root /path/to/profiles    # override the root containing named profile subdirectories
uv run main.py --help                               # verify CLI shape after entrypoint edits
uv run python -m py_compile main.py                 # focused syntax check
make check                                          # flake8 via uvx
make test                                           # pytest with PEP 723 deps parsed from main.py
make typecheck                                      # mypy with PEP 723 deps parsed from main.py
make fmt                                            # autoflake, isort, black over local .py files
```

There is no `pyproject.toml`; runtime dependencies and Python `>=3.14` live in the PEP 723 metadata block at the top of `main.py`. Add or remove runtime deps there, not in `Makefile`.

## Configuration

`~/.agentic/agentic.yaml` is the default global Gitea/MCP config. Per-agent config lives at `~/.agentic/<profile>/profile.yaml` by default. You can override these locations with `--config /path/to/agentic.yaml` and `--profiles-root /path/to/profiles`, where the profiles root is the directory containing named profile subdirectories. Keep shared examples in `profiles/example.yaml.template`.

`working_dir` controls the `LocalBackend` cwd used by command/code execution. It defaults to `.` in `~/.agentic/agentic.yaml` (the `main.py` directory), and can be overridden per profile with `working_dir` in `~/.agentic/<profile>/profile.yaml`. Relative paths are resolved from the `main.py` directory.

Profile `model` values use explicit `<kind>:<name>` strings. Supported kinds are currently `openai-chat`, `openai-responses`, and `anthropic`.

With no profile arguments, `main.py` scans `<profiles-root>/*/profile.yaml`, sorted by directory name. The default profiles root is `~/.agentic`.

Profile instruction templating uses `string.Template.safe_substitute`; use `$gitea_username` for substitution. Brace form `{gitea_username}` is not substituted by current code.

## Runtime Flow

`main.py` loads `~/.agentic/agentic.yaml`, loads each requested profile, resolves the token's Gitea login with `GET /api/v1/user`, builds an agent, then either starts `poll_forever` or runs the direct `--instruction` path. When `--attach/-a` is paired with `--instruction`, the one-shot run targets an existing issue or pull request, validates that the subject exists even if it is already closed or merged, and preloads the saved message history for that thread.

Polling opens `async with agent` once per profile, which starts profile-scoped capability lifecycles such as the Gitea MCP subprocess. A poll reads unread notifications, keeps only `Issue` and `Pull`, skips closed or dependency-blocked subjects, then checks whether the last issue comment mentions the current agent or whether the agent is otherwise relevant by role before calling `agent.run(...)`.

Notification threads are marked read only after they are successfully handled or intentionally skipped. Poller errors are not retried internally; they propagate and leave the notification unread for retry or human intervention.

## Conversation via Web UI Comments

Issue/PR comments serve as the Web UI conversation channel between humans and agents. Agent comments start with `<!-- agentic:@<agent-name> last_seen_comment_id=<n> -->`, where the marker records the highest comment id already delivered back into the agent conversation. Conversation-type comments (those containing the marker) are:

- Used directly as `agent.run()` input when they are the last comment
- Filtered out from `gitea_*` tool reads to avoid double-injection
- Accompanied by per-issue/PR persisted message history (`messages/<md5>.pkl`)

After every `agent.run()`, the output is automatically posted as a comment with the marker prepended, enabling the next round of Web UI dialogue.

## Capabilities

`~/.agentic/agentic.yaml` can define global capabilities that are enabled for all profiles; by default it enables `code_exec`, `gitea`, `harness`, privacy redaction, and model compaction. Profile capability entries are merged by name and replace same-named global entries completely; capability options are not recursively merged.

Configurable capability keys are `code_exec`, `gitea`, `console`, `skills`, `memory`, `harness`, `privacy`, `openai_compaction`, and `anthropic_compaction`. Unknown capability keys now raise a `ValueError` during agent construction so configuration typos fail fast instead of silently disabling capabilities.

`allow` and `deny` filtering is shared by Gitea MCP tools through `capabilities/base.py`: allow wins first, then deny subtracts from it; deny-only exposes everything except denied names. Skills are configured as a list of Gitea wiki page URLs (see [Skills](#skills) below), so they have no `allow`/`deny` knob — the list itself is the enablement set.

`console.include_execute` defaults to `false`. Memory file stores should use paths under `memories/`, which is gitignored.

## Skills

Skills are reusable workflows the agent can opt into. The list is declared as a YAML list of Gitea wiki page URLs in `capabilities.skills`, and the capability validates every URL at agent start (bad URL / missing page / missing frontmatter all fail loudly).

```yaml
capabilities:
  skills:
    - http://gitea.ai/autonomous/agentic/wiki/Issue-Triage
    - http://gitea.ai/autonomous/agentic/wiki/Writing-Plans
```

Each wiki page must start with a YAML frontmatter block that defines `name` and `description`:

```markdown
---
name: issue-triage
description: |
  Triage Gitea issues with an agentic-native workflow that classifies the
  request, extracts known context, identifies missing information, and turns
  the thread into clear next actions instead of premature conclusions.
---

# Issue Triage

...skill body...
```

Long skills can be split into separate flat pages under the wiki root (for example, a main page and a `...-Template` reference page). All skill pages sit flat at the wiki root, with no `Skills/` prefix and no `/` in the page name; the page name is the human-readable title with each space replaced by `-`. Link them from the main page with relative wiki links such as `[Template](Writing-Plans-Template)`. The agent reads the linked page on demand via `gitea_wiki_read`.

At startup the capability injects a small YAML index of `{name, url, description}` for each skill into the agent prompt, plus a one-line hint pointing the agent at the Gitea MCP `gitea_wiki_read` tool. The agent reads the full skill body on demand by parsing `owner` / `repo` / `pageName` out of the URL and calling `gitea_wiki_read(owner, repo, pageName)`. No new tool is needed — `gitea_wiki_read` is already exposed by the Gitea MCP capability.

Profile `capabilities.skills` replaces the global value entirely (the same replace-not-merge rule that applies to all other capabilities). An empty / unset list means no skills and no prompt injection.

To author a new skill or migrate an existing one, see the [Agentic Skill Authoring](http://gitea.ai/autonomous/agentic/wiki/Agentic-Skill-Authoring) wiki page in the `autonomous/agentic` repo.

## Notes

- Pull-request relevance still reads the last regular issue comment from `/issues/{number}/comments`.
- Pull-request review history is consulted when determining reviewer-based relevance.
- Notification threads are marked read only after the notification is intentionally skipped or successfully handled.
- The default `agent_request_limit` is 100 model requests per notification-handling run.
