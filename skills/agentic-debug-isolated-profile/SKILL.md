---
name: agentic-debug-isolated-profile
description: Debug agentic with temporary --config and --profiles-root overrides so reproduction stays isolated from the default ~/.agentic setup.
---

# Agentic Debugging with Isolated Config and Profiles

Use this skill when you need to debug `agentic` behavior without modifying the
normal `~/.agentic` setup. The goal is to spin up a throwaway agent using a
temporary global config file and a temporary profiles root, then reproduce the
problem with the smallest possible setup.

This is useful for debugging:

- whether a profile is loading correctly
- whether global config is being read from the expected file
- skills, MCP, Gitea, or capability wiring issues
- prompt, instruction, or profile-isolation problems
- behavioral differences between profiles
- one-off reproduction cases where you do not want to touch your normal local
  agent state

## Default layout to remember

By default, `main.py` loads:

- global config from `~/.agentic/agentic.yaml`
- profile configs from `~/.agentic/<profile-name>/profile.yaml`

When no profile name is passed, `main.py` scans:

```text
<profiles-root>/*/profile.yaml
```

The default `profiles_root` is `~/.agentic`.

That means `--profiles-root` must point to the directory that contains named
profile subdirectories, not to a single profile directory.

## Core override flags

Use these two flags together for isolated debugging:

- `--config /path/to/agentic.yaml`
- `--profiles-root /path/to/profiles`

`--config` chooses the global config file.

`--profiles-root` chooses the directory that contains one or more profile
subdirectories. For a profile named `debug`, the runtime expects:

```text
<profiles-root>/debug/profile.yaml
```

## Minimal reproducible debug workflow

Create a temporary directory tree like this:

```text
/tmp/agentic-debug/
  agentic.yaml
  profiles/
    debug/
      profile.yaml
      messages/
```

Then run a one-shot instruction against the temporary profile:

```bash
uv run main.py debug   --config /tmp/agentic-debug/agentic.yaml   --profiles-root /tmp/agentic-debug/profiles   --instruction "Use the smallest possible input to reproduce the problem"
```

This gives you an isolated debug agent that reads only the temporary global
config and temporary profile tree.

## Minimal setup playbook

### 1. Create a throwaway debug directory

Example:

```bash
mkdir -p /tmp/agentic-debug/profiles/debug/messages
```

### 2. Write a temporary global config

Create `/tmp/agentic-debug/agentic.yaml` with only the settings needed for the
reproduction. Keep it minimal so it is obvious which values are affecting the
run.

Use this file to test things like:

- `working_dir`
- global capability configuration
- shared Gitea or MCP configuration
- skills directory wiring

### 3. Create a temporary debug profile

Create:

```text
/tmp/agentic-debug/profiles/debug/profile.yaml
```

This profile should contain only the token, model, instructions, and capability
settings needed to reproduce the issue.

### 4. Run one profile once with `--instruction`

For targeted debugging, prefer a one-shot run over polling:

```bash
uv run main.py debug   --config /tmp/agentic-debug/agentic.yaml   --profiles-root /tmp/agentic-debug/profiles   --instruction "Describe which config and profile data were loaded"
```

This is the fastest loop for checking whether the correct config and profile are
being used.

### 5. Expand only after the minimal case works

Once the isolated reproduction works, add complexity one piece at a time:

- add more capabilities
- add or change skills
- switch tokens or credentials
- compare two temporary profiles under the same temporary profiles root
- remove `--instruction` and let the profile poll if the bug only happens in
  notification handling

## Good debugging patterns

### Compare default vs isolated behavior

If something works in `~/.agentic` but fails in the temporary setup, compare:

- global config values
- profile capability overrides
- token or credential availability
- local files referenced by relative paths
- skill directories available to the profile

That often reveals hidden dependencies on the default environment.

### Use multiple temporary profiles for contrast

You can create more than one profile under the same temporary profiles root:

```text
/tmp/agentic-debug/
  agentic.yaml
  profiles/
    baseline/profile.yaml
    debug/profile.yaml
```

Then run either one explicitly:

```bash
uv run main.py baseline --config /tmp/agentic-debug/agentic.yaml --profiles-root /tmp/agentic-debug/profiles -i "Check baseline behavior"
uv run main.py debug --config /tmp/agentic-debug/agentic.yaml --profiles-root /tmp/agentic-debug/profiles -i "Check changed behavior"
```

This is useful for controlled comparison experiments.

## Common pitfalls

### 1. Pointing `--profiles-root` at the wrong directory

Wrong:

```bash
--profiles-root /tmp/agentic-debug/profiles/debug
```

Right:

```bash
--profiles-root /tmp/agentic-debug/profiles
```

The flag expects the profiles root directory, not the individual profile
subdirectory.

### 2. Expecting the profile file in the wrong place

For profile `debug`, the expected path is:

```text
<profiles-root>/debug/profile.yaml
```

Not:

```text
<profiles-root>/profile.yaml
```

### 3. Forgetting scan behavior when no profile name is passed

If you do not pass a profile name, `main.py` scans:

```text
<profiles-root>/*/profile.yaml
```

If nothing is found, the issue may be directory structure, not profile content.

### 4. Assuming temporary credentials behave like the default environment

A temporary debug tree is intentionally isolated. If the default environment
works but the temporary one fails, check whether the temporary config/profile is
missing:

- tokens
- MCP-related config
- skills configuration
- local filesystem paths
- any other credentials normally available in `~/.agentic`

This isolation is a feature, but it can make the temporary setup fail until you
copy the minimum required settings.

### 5. Forgetting that relative paths still matter

If your reproduction depends on `working_dir` or other relative paths, make sure
those paths still resolve correctly in the isolated run. A clean temporary setup
can expose hidden assumptions about the current repository location or existing
local files.

## Why this workflow is valuable

Use this isolated workflow because it:

- avoids polluting the default `~/.agentic` setup
- makes one-off experiments cheap and reversible
- helps reproduce bugs with a minimal config surface
- makes it easier to validate whether config, profile, or capability wiring is
  the real problem
- supports side-by-side comparison between temporary profiles

## Recommended debug loop

1. Create a fresh temporary directory.
2. Add the smallest possible `agentic.yaml`.
3. Add one minimal profile at `<profiles-root>/<name>/profile.yaml`.
4. Run a one-shot `--instruction` command first.
5. Confirm the expected config/profile are being loaded.
6. Add only the next piece needed to reproduce the real issue.
7. If needed, compare against another temporary profile or the default setup.

When a bug disappears in the isolated setup, the missing ingredient is often the
most important clue.
