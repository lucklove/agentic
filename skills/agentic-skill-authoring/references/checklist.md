# Agentic Skill Authoring Checklist

Use this checklist when creating or revising a skill in this repository.

## Fit and scope

- The workflow is repeated, error-prone, or valuable enough to justify a skill.
- The content is reusable and not tied only to one temporary conversation.
- There is no existing skill that already covers the same workflow well enough.

## Structure

- The skill lives at `skills/<name>/SKILL.md`.
- The frontmatter `name` matches the directory exactly.
- The frontmatter `description` explains when to use the skill.
- Optional static materials live under `references/`.
- Optional deterministic automation lives under `scripts/`.

## Content quality

- `SKILL.md` clearly states when to use the skill.
- The body gives ordered, concrete steps.
- Repository-specific facts and constraints are included where needed.
- Common pitfalls are called out.
- Verification guidance is included.
- The writing is actionable, not generic motivational advice.

## Tooling and assumptions

- Referenced tools, paths, and commands were confirmed in the repository.
- The skill does not depend on Hermes-only terminology or capabilities.
- Any scripts mentioned by the skill actually exist and use the exact `script_name`
  path that `load_skill(...)` exposes, such as `scripts/example.py`.
- Any references mentioned by the skill use the exact `resource_name` path that
  `load_skill(...)` exposes, such as `references/checklist.md`.

## Final check

- Another agent could load the skill and act without major guesswork.
- The skill matches the tone and structure of the rest of `skills/`.
- The skill improves future execution quality, not just repository completeness.
