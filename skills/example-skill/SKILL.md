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

Example:

```bash
skills/example-skill/scripts/summarize.py --name my-skill --include-reference
```

## Minimum SKILL.md Format

Each skill lives in its own subdirectory and must have a `SKILL.md` with YAML
frontmatter containing at minimum:

```yaml
---
name: my-skill-name
description: One-line description shown to the agent during discovery.
---
```

The `name` field is what you reference in a profile's `capabilities.skills.names` list.

## Adding a real skill

1. Create `skills/<skill-name>/SKILL.md` with the front-matter above.
2. Write the skill body in Markdown - this is the text injected into the
   agent's context when the skill is loaded.
3. Optionally add reference files in `skills/<skill-name>/references/`.
4. Optionally add executable scripts in `skills/<skill-name>/scripts/`
   (use `#!/usr/bin/env -S uv run -q` shebangs with PEP 723 inline metadata).
