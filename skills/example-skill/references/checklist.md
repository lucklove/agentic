# Example Skill Checklist

Use this checklist when creating or reviewing a skill.

- The directory name matches the `name` field in `SKILL.md`.
- The frontmatter includes `name` and `description`.
- The description is concise and explains when to use the skill.
- The Markdown body gives concrete instructions, not generic advice.
- Reference files live under `references/` and are linked from `SKILL.md`.
- Scripts live under `scripts/` and document their arguments in `SKILL.md`.
- Scripts avoid hidden dependencies unless they declare them with PEP 723.
- The skill directory does not contain secrets, caches, or generated state.
