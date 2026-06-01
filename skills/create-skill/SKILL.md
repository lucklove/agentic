---
name: create-skill
description: Guide for creating a new agentic skill, including SKILL.md format, frontmatter requirements, reference files, executable scripts, and argument conventions.
---

# Create a Skill

Use this guide when a user asks you to create a new skill for this agentic
project.

A skill is a directory under `skills/` containing a `SKILL.md` file and
optionally one or more reference files or supporting scripts.

## Step 1 - Choose a Name and Location

Skill names must use lowercase letters, numbers, and hyphens only. Keep names
short and descriptive; the directory name should match the frontmatter name.

```text
skills/<skill-name>/
|-- SKILL.md          # required
|-- references/       # optional, static docs or examples
|   `-- notes.md
`-- scripts/          # optional, repeatable automation
    `-- my-script.py
```

Keep supporting files in predictable directories. Use `references/` for static
material the agent should read, and `scripts/` for deterministic automation the
agent can run.

## Step 2 - Write SKILL.md

`SKILL.md` has two sections.

YAML frontmatter is required:

````markdown
---
name: my-skill
description: One-sentence description of what this skill does.
---
```

Both `name` and `description` are required. The `name` must match the directory
name.

The Markdown body contains the instructions loaded for the agent when the skill
is used. Write concrete, step-by-step guidance. Put detailed workflow notes,
examples, command syntax, and references in the body, not in the description.

## Step 3 - Add References Optional

Add reference files when the skill needs examples, checklists, schemas, policy
notes, command references, or other static material that would make `SKILL.md`
too long.

Recommended layout:

```text
skills/my-skill/references/checklist.md
skills/my-skill/references/payload-example.json
```

Reference files should be linked from `SKILL.md` with a short note about when to
read them. Keep them text-first and portable; avoid generated output, caches,
secrets, or machine-local paths.

## Step 4 - Add Scripts Optional

Add scripts only when they provide repeatable automation the agent should run.
Keep scripts local to the skill directory so the skill stays portable.

Supported script styles depend on the skills runtime, but these conventions are
safe for this project:

| Extension | Interpreter |
|---|---|
| `.py` | Python, preferably through `uv` |
| `.sh`, `.bash`, `.zsh`, `.fish` | Corresponding shell |
| executable with no extension | Direct execution on macOS/Linux |

### Python Scripts With Dependencies

Use a `#!/usr/bin/env -S uv run -q` shebang and a PEP 723 inline metadata block
so the script manages its own dependencies independently of the agent runtime:

```python
#!/usr/bin/env -S uv run -q
# /// script
# requires-python = ">=3.14"
# dependencies = ["httpx>=0.28"]
# ///
"""Short description of what this script does."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"done: {args.region}, dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
```

Make extensionless scripts executable when the execute bit matters:

```bash
chmod +x skills/my-skill/scripts/my-script
```

## Step 5 - Argument Conventions

Prefer named CLI flags over positional arguments. This keeps script invocation
self-documenting and easier for agents to call correctly.

| Python value | CLI result |
|---|---|
| `"us-east-1"` | `--region us-east-1` |
| `True` | `--dry-run` |
| `False` / `None` | omitted |
| `["a", "b"]` | `--item a --item b` |

Document each script argument in the skill body, including defaults, accepted
values, and any required environment variables.

## Step 6 - Create the Files

When editing this repository, create the skill under the configured skills
directory from `agentic.yaml`:

```yaml
skills_dir: "./skills"
```

For this project, the final path should normally be:

```text
skills/my-skill/SKILL.md
```

Use normal repository editing tools to create the directory, `SKILL.md`, and any
reference or script files. Keep generated artifacts, caches, credentials, and
local state out of the skill directory.

## Full Example

Use the repository's `example-skill` as the minimal reference shape:

```text
skills/example-skill/
|-- SKILL.md
|-- references/
|   `-- checklist.md
`-- scripts/
    `-- summarize.py
```

`skills/example-skill/SKILL.md`:

````markdown
---
name: example-skill
description: Demonstrates the skill format used by agentic agents.
---

# Example Skill

This skill demonstrates the expected layout for an agentic skill with
instructions, a reference file, and a helper script.

## When to use

Use this skill when you need a concrete example of how to structure a skill
for the agentic framework.

## Skill Layout

```text
skills/example-skill/
|-- SKILL.md
|-- references/
|   `-- checklist.md
`-- scripts/
    `-- summarize.py
```

## How to Use This Example

Read `references/checklist.md` when you need a compact checklist for reviewing
or authoring a skill.

Run `scripts/summarize.py` when you need a deterministic example of a skill
helper script.

Arguments:
- `--name`: skill name to include in the output; defaults to `example-skill`.
- `--include-reference`: include the reference file path in the output.

## Minimum SKILL.md Format

Each skill lives in its own subdirectory and must have a `SKILL.md` with YAML
frontmatter containing at minimum:

```yaml
---
name: my-skill-name
description: One-line description shown to the agent during discovery.
---
```

For real skills, add practical workflow instructions after the frontmatter, add
reference files when static context is useful, and add scripts only when they
are needed for repeatable automation.
````

`skills/example-skill/references/checklist.md`:

```markdown
# Example Skill Checklist

Use this checklist when creating or reviewing a skill.

- The directory name matches the `name` field in `SKILL.md`.
- The frontmatter includes `name` and `description`.
- The description is concise and explains when to use the skill.
- The Markdown body gives concrete instructions, not generic advice.
- Reference files live under `references/` and are linked from `SKILL.md`.
- Scripts live under `scripts/` and document their arguments in `SKILL.md`.
```

`skills/example-skill/scripts/summarize.py`:

```python
#!/usr/bin/env -S uv run -q
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Print a small deterministic summary for the example skill."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="example-skill")
    parser.add_argument("--include-reference", action="store_true")
    args = parser.parse_args()

    print(f"skill: {args.name}")
    print("purpose: demonstrate SKILL.md, references, and scripts")
    if args.include_reference:
        print("reference: references/checklist.md")


if __name__ == "__main__":
    main()
```
