---
name: example-skill
description: Demonstrates the skill format used by agentic agents.
---

# Example Skill

This is a placeholder skill that shows the expected SKILL.md format.

## When to use

Use this skill when you need a concrete example of how to structure a skill
for the agentic framework.

## How skills work

Skills are discovered by `SkillsCapability` from the `skills_dir` configured
in `agentic.yaml`. Each skill lives in its own subdirectory and must have a
`SKILL.md` with YAML front-matter containing at minimum:

```yaml
---
name: my-skill-name
description: One-line description shown to the agent during discovery.
---
```

The `name` field is what you reference in a profile's `capabilities.skills.names` list.

## Adding a real skill

1. Create `skills/<skill-name>/SKILL.md` with the front-matter above.
2. Write the skill body in Markdown — this is the text injected into the
   agent's context when the skill is loaded.
3. Optionally add executable scripts in `skills/<skill-name>/scripts/`
   (use `#!/usr/bin/env -S uv run -q` shebangs with PEP 723 inline metadata).
4. Add the skill name to the profile's `capabilities.skills.names` list.
