---
name: agentic-skill-authoring
description: Guide for creating, revising, and maintaining skills in this agentic repository when a workflow is worth codifying as a reusable local skill.
---

# Agentic Skill Authoring

Use this skill when you need to add, update, or substantially improve a skill in
this `agentic` repository.

This skill is about authoring skills that future agents can actually use. It is
not enough to produce a directory with valid frontmatter. A good skill captures
repeatable judgment, concrete steps, common failure modes, and verification so
that another agent can load it and execute the workflow with less guesswork.

## When to use

Use this skill when any of the following is true:

- You are asked to create a new skill under `skills/`.
- You are asked to revise an existing skill's instructions, references, or
  scripts.
- You completed a non-trivial workflow and the same workflow is likely to recur.
- A repeated issue, review, investigation, planning, or maintenance pattern
  would benefit from a reusable checklist or procedure.
- An existing skill is too vague, missing examples, missing pitfalls, or no
  longer matches the repository's current capabilities.

Do not use this skill as a substitute for doing the underlying technical work.
Use it when the task is specifically about improving the reusable instructions
that agents in this repository will load later.

## What makes a workflow worth turning into a skill

A workflow is usually worth a skill when most of these are true:

- It is complex enough that agents can miss steps or take the wrong path.
- It is likely to appear again across issues, PRs, reviews, or operations.
- It has a stable sequence of actions, a decision tree, or a checklist.
- Concrete examples, templates, references, or helper scripts would improve
  success rate.
- Capturing the workflow would noticeably improve speed, consistency, or output
  quality for future agents.

Strong candidates include:

- Review workflows with specific investigation steps.
- Debugging or release procedures with recurring checks.
- Repository-specific authoring or maintenance conventions.
- Browser or API workflows that require a consistent sequence and careful tool
  choice.

## What is usually *not* worth a skill

Avoid creating a new skill when the content is mostly one of these:

- A one-off task tied to temporary context.
- Purely abstract advice without executable steps.
- Knowledge that belongs in an issue comment, PR description, or memory entry
  instead of a reusable workflow.
- Guidance that duplicates an existing skill with only minor wording changes.
- Instructions that depend on tools, directories, or automation that do not
  exist in this repository.

If the workflow is real but small, prefer improving an existing skill instead of
creating a near-duplicate one.

## Authoring workflow

Follow this sequence when writing or updating a skill.

### 1. Confirm the repository facts first

Before writing anything:

- Read `AGENTS.md`, `README.md`, and any repository files that define the actual
  workflow you want to codify.
- Inspect the current `skills/` directory so you understand naming patterns,
  tone, and existing coverage.
- Read closely related skills before deciding on a new one.
- Verify any referenced tools, commands, directories, or capabilities in the
  codebase or configuration.

Do not invent a workflow from memory if the repository can confirm it.

### 2. Check for overlap

Ask:

- Does a similar skill already exist?
- Can I extend an existing skill with a new section, reference, or script?
- Would a separate skill be clearer than expanding an existing one?

Prefer updating an existing skill when the new material is the same workflow
family. Create a new skill when the workflow has a distinct trigger, audience,
or procedure.

### 3. Choose the skill name and location

Use a lowercase, hyphenated directory name:

```text
skills/<skill-name>/
```

Requirements:

- The directory name must match the frontmatter `name` exactly.
- The name should describe the workflow, not a vague aspiration.
- Prefer concise names that reflect when the skill should be loaded.

Examples of good naming:

- `agentic-skill-authoring`
- `gitea-pr-review`
- `systematic-debugging`

### 4. Create the expected skill layout

Skills in this repository should follow this structure:

```text
skills/<skill-name>/
|-- SKILL.md
|-- references/   # optional static supporting material
`-- scripts/      # optional deterministic helper automation
```

Use:

- `SKILL.md` for the main operating manual.
- `references/` for static materials such as checklists, templates, worked
  examples, schemas, or policy notes.
- `scripts/` for repeatable automation that future agents should actually run.

Do not put generated artifacts, caches, secrets, or machine-local state in the
skill directory.

### 5. Write the frontmatter

Every skill must start with YAML frontmatter:

```yaml
---
name: my-skill
description: Clear one-sentence description that says when to use this skill.
---
```

Frontmatter rules:

- `name` is required and must match the directory name.
- `description` is required.
- The description should describe the trigger or usage context, not dump the
  whole workflow.
- Keep it specific enough that an agent can decide to load the skill.

A good description answers: "When should I use this skill?"

### 6. Write the body as an operating manual

The body should help another agent complete the workflow with minimal
interpretation. Prefer sections like:

- `## When to use`
- `## Prerequisites` or `## Confirm the facts`
- `## Step-by-step workflow`
- `## Decision points`
- `## Common pitfalls`
- `## Verification`

Write concrete actions, for example:

- what to inspect first
- what tool to use
- what order to follow
- what not to assume
- how to know the work is correct

Do not stop at principles like "be careful" or "follow project conventions".
Translate those principles into observable actions.

### 7. Split supporting material when it improves reuse

Use `references/` for static material such as:

- checklists
- templates
- example outputs
- policy notes
- schemas
- worked examples

When you document a reference, show the exact `resource_name` that callers must
pass to `read_skill_resource(...)`. Resource names are relative to `SKILL.md`,
so a file at `skills/my-skill/references/checklist.md` should be referenced as
`references/checklist.md`, not just `checklist.md`.

Use `scripts/` only for deterministic, reusable automation.

Choose carefully:

- Put durable explanation in `SKILL.md`.
- Put reusable static detail in `references/` when it would otherwise clutter
  the main instructions.
- Put automation in `scripts/` only when future agents should actually execute
  it.

Do not create references or scripts just to make the skill look sophisticated.
Add them only when they increase clarity or repeatability.

### 8. Follow script conventions when automation is needed

Treat `scripts/` as an implementation detail of the skill package. In normal
agent use, a skill script should be invoked through `run_skill_script(...)`
after `load_skill(...)`, not by telling the agent to shell into
`skills/<skill-name>/scripts/...` directly.

When you document a script, show the runtime-facing invocation shape:

```python
await run_skill_script(
    skill_name="my-skill",
    script_name="scripts/summarize.py",
    args={
        "region": "us-east-1",
        "dry_run": True,
    },
)
```

Authoring rules for script guidance:

- Use the exact relative path that future agents should pass as `script_name`,
  such as `scripts/summarize.py`.
- Match the names exposed by `load_skill(...)`; callers should be able to copy
  the path directly from the loaded skill output.
- Describe inputs in terms of the `args={...}` object, not raw shell quoting.
- Document what the script prints, returns, or changes so callers know how to
  use the result.
- Do not require callers to discover `SKILL_PATH`, compute script directories,
  or invoke `bash skills/.../scripts/...` unless that direct shell execution is
  itself the workflow being documented.

Supported script styles depend on the runtime, but these conventions are safe
for this repository:

| Extension | Interpreter |
|---|---|
| `.py` | Python, preferably through `uv` |
| `.sh`, `.bash`, `.zsh`, `.fish` | Corresponding shell |
| executable with no extension | Direct execution on macOS/Linux |

For Python helper scripts with dependencies, prefer a `uv` shebang plus a PEP
723 metadata block so the skill stays self-contained:

```python
#!/usr/bin/env -S uv run -qs
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

If a script depends on direct execution behavior, say so explicitly in the skill
instructions and explain why `run_skill_script(...)` is not enough in that case.

### 9. Prefer predictable CLI argument conventions

Prefer named flags over positional arguments so agent invocation is easier to
understand and less error-prone.

| Python value | CLI result |
|---|---|
| `"us-east-1"` | `--region us-east-1` |
| `True` | `--dry-run` |
| `False` / `None` | omitted |
| `["a", "b"]` | `--item a --item b` |

Document each script argument in `SKILL.md`, including defaults, accepted
values, and any required environment variables.

### 10. Include pitfalls and validation

A high-quality skill should name the mistakes agents are likely to make and how
to avoid them.

It should also explain how to verify success, such as:

- files or paths that must exist
- formatting or structure that must match repository conventions
- commands to run
- examples of the expected result

### 11. Add a minimal example when helpful

If the workflow is easy to misunderstand, include a small example or a reference
file showing:

- input situation
- chosen actions
- resulting artifact shape

A minimal example is often better than extra theory.

Example minimal layout:

```text
skills/my-skill/
|-- SKILL.md
|-- references/
|   `-- checklist.md
`-- scripts/
    `-- summarize.py
```

Example minimum frontmatter:

```yaml
---
name: my-skill-name
description: One-line description shown to the agent during discovery.
---
```

## Tool usage guidance

When authoring a skill, follow these tool-use rules:

- Inspect the existing `skills/` directory first to avoid duplicates.
- Read nearby skills to match current repository style and abstraction level.
- Read the relevant project docs or code before describing a workflow.
- If a skill depends on a capability, command, path, or runtime behavior,
  confirm that fact in the repository.
- Do not claim a script exists unless you create it.
- Do not claim a tool is available unless the repository or runtime actually
  provides it.
- Do not import assumptions from another framework or agent runtime unless you
  restate them in terms that are true for this repository.

This repository may contain skills inspired by other systems, but the skill you
write must stand on facts that are valid for `agentic` itself.

## Content quality checklist

Before considering the skill done, confirm all of these:

- The trigger for using the skill is explicit.
- The scope is clear: create, revise, investigate, review, release, debug, or
  another concrete workflow.
- The steps are ordered and executable.
- Repository-specific constraints are named where relevant.
- Common pitfalls or wrong assumptions are called out.
- Verification steps exist and are practical.
- The guidance matches current `agentic` capabilities and file layout.
- The writing avoids empty platform-agnostic slogans.
- Any references or scripts are clearly introduced from `SKILL.md`.

## Common pitfalls

Watch for these mistakes:

- Writing a manifesto instead of an operating guide.
- Omitting the trigger for when the skill should be loaded.
- Using a frontmatter `name` that does not match the directory.
- Keeping the description too vague to drive skill selection.
- Describing workflows that rely on Hermes-only assumptions or tooling.
- Creating overlapping skills instead of improving an existing one.
- Dumping long checklists into the body when a `references/` file would be
  clearer.
- Adding scripts without documenting their purpose and arguments.
- Forgetting to explain how the agent should verify successful completion.
- Encoding temporary issue-specific context as if it were a reusable method.

## Verification checklist

Before finishing, verify that the skill:

- matches the repository's existing skill layout
- can be discovered and understood from its frontmatter
- gives enough detail for another agent to act without guessing
- uses only repository-supported assumptions and terminology
- explains references and scripts if they exist
- includes concrete verification guidance
- is specific to `agentic` rather than generic advice that could live anywhere

## Recommended final self-review

Use this quick self-review before committing:

1. Could another agent tell *when* to load this skill from the description
   alone?
2. Would that agent know the first three actions to take after loading it?
3. Does the skill explain at least one likely mistake or bad assumption?
4. Does it tell the agent how to verify the result?
5. If I removed the repository name, would the content become generic fluff? If
   yes, add more repository-specific facts.

## Optional references for this skill

If you want a compact review aid, call
`read_skill_resource(skill_name="agentic-skill-authoring", resource_name="references/checklist.md")`
after drafting or editing a skill. It is meant for final validation, not as a
substitute for reading this full authoring guide.
