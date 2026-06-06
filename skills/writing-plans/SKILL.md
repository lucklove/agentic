---
name: writing-plans
description: Write structured implementation plans for features, bug fixes, refactors, and review threads when the right next step is planning before coding.
---

# Writing Plans

Use this skill when the best immediate output is a concrete plan rather than a
code change.

This skill is for turning an issue, PR thread, bug report, or ambiguous request
into an execution-ready plan that another agent or human can follow with minimal
guesswork. In `agentic`, that usually means producing a structured comment in a
Gitea issue or PR thread after first reading the available context.

## When to use

Use this skill when any of the following are true:

- A user asks for an implementation plan, design outline, or rollout plan.
- A feature, refactor, or bug fix is large enough that coding immediately would
  be premature.
- An issue should first be turned into a step-by-step execution plan.
- A PR review thread would benefit from a phased plan instead of an immediate
  patch.
- The work spans multiple modules, files, systems, or validation steps.
- The requirements are incomplete enough that assumptions and constraints need
  to be surfaced before implementation begins.

Do not use this skill when the task is already small, obvious, and safe to
implement directly. The goal is not to delay coding; the goal is to make the
next execution step clearer.

## Planning principles

A good plan in this repository should let the executor spend time doing the
work, not guessing what the plan meant.

Follow these principles:

- Make the plan actionable. Prefer concrete steps over abstract advice.
- Keep tasks bite-sized. Each step should have a clear result and observable
  completion condition.
- Name the likely files, modules, tests, configs, or workflows involved.
- Separate facts from assumptions. If context is missing, say so explicitly.
- Prefer repository-grounded guidance over generic software-process language.
- Distinguish between what should happen now, what should happen later, and what
  is optional.
- Include validation. A plan without verification steps is incomplete.
- Call out material risks, dependencies, and coordination points early.

## Required context-gathering workflow

Before writing the plan, gather enough evidence to avoid inventing structure.

1. Read the thread context first.
   - For issues, read the issue body and relevant comments.
   - For pull requests, read the PR body, changed files, diff summary, reviews,
     and relevant comments.
2. Use Gitea tools as the primary source of truth for issue/PR metadata.
3. If the plan mentions specific files, modules, workflows, or configuration,
   confirm they exist in the repository before naming them.
4. If repository structure matters, read the relevant code, docs, or config.
5. If the available context is still insufficient, produce a conditional plan
   that clearly labels assumptions instead of pretending certainty.

Do not fabricate file paths, package names, workflow names, or architectural
layers that you have not confirmed.

## Standard output structure

Unless the thread clearly needs a different format, organize the plan with these
sections:

### Goal

State the intended outcome in one or two sentences.

### Constraints / assumptions

List:
- constraints already visible in the issue or PR
- assumptions you had to make
- missing information that could change the plan

### Proposed approach

Describe the implementation direction at a medium level. Explain how the main
pieces fit together without turning this section into the task list itself.

### Task breakdown

Provide an ordered list of concrete steps. For each step, include enough detail
that the executor knows:
- what to change
- where to change it
- why that step exists
- what done looks like

When helpful, split this section into:
- Immediate
- Follow-up
- Optional

### Risks

Call out the most likely failure modes, migration hazards, coupling concerns,
review dependencies, or unknowns.

### Verification

List how to confirm the work is correct. Include relevant tests, linters,
workflow checks, manual validation, or review checkpoints.

## Task granularity guidance

A strong plan step should be small enough to execute in one focused pass and
specific enough to verify.

Prefer tasks like:
- "Update `main.py` profile loading so same-named capability configs replace the
  global entry, then add or adjust tests covering profile override behavior."
- "Add a skill reference template under `skills/writing-plans/references/` and
  link it from `SKILL.md`."

Avoid tasks like:
- "Refactor the planning system"
- "Fix backend and tests"
- "Improve documentation everywhere"

Use these checks for each task:
- Is there one primary outcome?
- Is the scope narrow enough to implement safely?
- Does it identify the likely files or modules?
- Can a reviewer tell when the step is done?
- Is verification attached to the step or to the overall plan?

If one step contains multiple large verbs like "design, implement, migrate, and
validate," split it.

## Tool usage guidance

For `agentic`, planning should be grounded in the same evidence the executor
will use.

### Preferred evidence sources

- Use Gitea tools to read issue descriptions, PR bodies, comments, review
  history, changed files, and status where relevant.
- Read repository files, configuration, and nearby code when the plan depends on
  actual project structure.
- Use existing docs such as `README.md`, `AGENTS.md`, and closely related files
  to confirm conventions.

### File and module references

Before suggesting file paths or modules:
- confirm the path exists, or
- clearly label it as a probable location if it cannot be confirmed

Good:
- "Update `main.py` and the related tests that cover notification handling."
- "If skill references are kept under `skills/<name>/references/`, add a plan
  template there."

Bad:
- "Modify `src/planner/index.ts`" when no such path has been checked.

### When context is incomplete

If the request asks for a plan but the repository context is too thin:
- give the best grounded plan you can
- label assumptions explicitly
- mention the missing facts that would sharpen the plan
- avoid blocking unless human input is truly required

## Output example

A minimal example for a comment like "请先给个实现方案":

```markdown
Goal
- Add a reusable `writing-plans` skill so agents can produce structured,
  execution-ready plans in issue and PR threads before starting code changes.

Constraints / assumptions
- The skill should match the existing `skills/` layout and frontmatter style.
- The repository currently uses `SKILL.md` plus optional `references/` files.
- I have not assumed any Hermes-only tool or workflow.

Proposed approach
- Create a new `skills/writing-plans/` skill focused on planning in Gitea
  issue/PR conversations.
- Define when to use the skill, how to gather context, what a good plan looks
  like, and how to verify the resulting output is actionable.
- Add a small reusable template reference so agents can quickly structure plan
  comments.

Task breakdown
1. Inspect existing skills and repository docs to match naming, frontmatter, and
   instruction style.
2. Add `skills/writing-plans/SKILL.md` with sections for usage triggers,
   planning principles, standard plan structure, pitfalls, and verification.
3. Add a compact reference template that agents can adapt when replying in an
   issue or PR thread.
4. Review the new skill for repository-specific grounding and remove any
   Hermes-specific assumptions.

Risks
- The plan may become too generic if it does not reference the actual `agentic`
  workflow.
- Invented file/module suggestions would reduce trust in the skill.

Verification
- Confirm the new skill appears under `skills/` with valid frontmatter.
- Read the final text to ensure it explicitly references Gitea issue/PR context,
  repository inspection, and actionable task breakdowns.
```

## Common pitfalls

Avoid these failure modes:

- Plans that only restate the request without proposing execution steps.
- Tasks that are too large to assign or implement confidently.
- Missing verification steps.
- No risks, dependencies, or assumptions section.
- Naming files or modules that were never checked.
- Giving generic architecture advice instead of repository-relevant actions.
- Mixing immediate work, future work, and optional improvements into one flat
  list when staging matters.
- Treating planning as a refusal to help, instead of as a concrete deliverable.

## Verification checklist

Before posting the final plan, confirm:

- The plan is clearly structured and easy to scan in an issue or PR comment.
- The goal is explicit.
- Assumptions and constraints are separated from confirmed facts.
- The task breakdown is actionable and reasonably bite-sized.
- Referenced files, modules, and workflows are confirmed or clearly marked as
  tentative.
- Risks and verification steps are present.
- The content is `agentic`-native and does not depend on Hermes-specific modes,
  tools, or terminology.
- The output would help another agent or human start implementation with minimal
  guesswork.

## Reference material

If you want a compact reusable comment skeleton, read:

- `references/plan-template.md`
