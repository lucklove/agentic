---
name: issue-triage
description: Triage Gitea issues with an agentic-native workflow that classifies
  the request, extracts known context, identifies missing information, and turns
  the thread into clear next actions instead of premature conclusions.
---

# Issue Triage

Use this skill when you are handling a Gitea issue and the right first move is to
understand, classify, and organize the problem before jumping into coding.

This skill is for first-round issue handling. Its job is to produce a stable,
useful triage response: what type of issue this is, what is already known, what
is still missing, what area is probably affected, and what next step makes the
most sense.

Do not use this skill to pretend a diagnosis is complete when the issue is still
underspecified. If the thread is ready for implementation work, debugging, or a
PR review, use the more specific workflow or skill that matches that stage.

## When to use

Use this skill when any of the following are true:

- A new issue arrives and you need to produce the first structured response.
- You are entering an issue thread for the first time and need to understand it
  before acting.
- The issue description is scattered across the body and comments.
- The request might be a bug, feature, question, ops incident, or maintenance
  task, and that classification matters for the next step.
- You need to identify missing reproduction details, logs, environment context,
  links, or acceptance criteria.
- The right outcome is a triage summary and follow-up questions, not an
  immediate code change.

Do not use this skill as an excuse to stall obvious work. If the issue already
contains enough clear context and the expected next action is implementation,
use triage briefly, then move into execution.

## Triage workflow

Follow this workflow in order. Skip a step only when it is clearly not needed.

1. Read the full issue context.
   - Read the issue title and body carefully.
   - Read all thread comments, not just the latest mention.
   - Separate facts from guesses, opinions, and requests.

2. Identify the issue type.
   - Classify the thread as one primary type: `bug`, `feature`, `question`,
     `ops`, or `maintenance`.
   - If the issue contains mixed goals, note the primary type and list any
     secondary concerns separately.

3. Extract what is already known.
   - Capture the reported symptom, desired outcome, current behavior, and any
     explicit constraints.
   - Pull out environment details, versions, branches, configs, screenshots,
     failing commands, logs, links, issue references, or related PRs.
   - Record any stated urgency, impact, or affected users if the thread provides
     that information.

4. Identify what is missing.
   - Note missing reproduction steps, expected vs actual behavior, scope,
     environment details, logs, timelines, ownership, or acceptance criteria.
   - Be concrete. Say exactly what is absent and why it blocks a stronger
     conclusion.

5. Estimate suspected scope.
   - Identify the likely subsystem, workflow, file area, or operational surface
     involved.
   - Treat this as a hypothesis, not a confirmed root cause.
   - If the issue mentions a file, PR, failing workflow, or release, read that
     related context before finalizing the summary.

6. Decide the most useful next step.
   - Choose the next action that best matches the evidence: `request info`,
     `investigate`, `plan`, `implement`, `answer directly`, or `monitor / ops
     follow-up`.
   - Prefer `request info` when key context is missing.
   - Prefer `investigate` when enough data exists to start debugging but not yet
     enough to commit to a fix.
   - Prefer `plan` for design-heavy feature work.

7. Produce a structured triage summary.
   - Keep it concise but complete.
   - Distinguish clearly between confirmed facts, inferred scope, and suggested
     next actions.
   - Include follow-up questions only when they are necessary and actionable.

## Classification guidance

### Bug

Use `bug` when the thread reports broken behavior, regressions, crashes,
unexpected output, failing tests, or mismatch between expected and actual
behavior.

Typical next steps:
- Reproduce or investigate.
- Gather logs, environment details, and recent changes.
- Narrow likely component ownership and failure surface.

### Feature

Use `feature` when the thread requests a new capability, workflow, or behavior
that does not yet exist.

Typical next steps:
- Clarify the user goal and acceptance criteria.
- Confirm constraints, non-goals, and integration points.
- Move to planning or design before implementation.

### Question

Use `question` when the thread mainly asks how something works, whether behavior
is expected, or what a supported workflow is.

Typical next steps:
- Answer directly if the repository context already supports a reliable answer.
- Otherwise ask for the exact scenario or point to missing context.
- Avoid turning a simple question into a speculative debugging exercise.

### Ops

Use `ops` when the issue is about CI failures, deployment health, credentials,
workflow runs, environment drift, infrastructure, or production-like operational
symptoms.

Typical next steps:
- Check workflow status, logs, config, environment assumptions, and recent
  changes.
- Identify blast radius, urgency, and whether the issue is ongoing or already
  transient.
- Prefer investigation and evidence gathering before claiming a fix.

### Maintenance

Use `maintenance` when the issue is about dependency updates, cleanup,
refactoring, docs alignment, automation upkeep, or internal quality work not
framed as a user-facing feature.

Typical next steps:
- Clarify scope and intended outcome.
- Check whether the work is preventive, corrective, or routine.
- Decide whether implementation can begin immediately or needs a short plan.

## Tool usage guidance

Use Gitea-native context as your primary evidence source.

### Preferred primary path

- Read the issue body and comments through Gitea MCP tools.
- If the issue links a PR, related issue, workflow run, label context, or other
  repository object, read that object instead of relying on paraphrased thread
  summaries.
- If the issue mentions specific files, modules, workflows, commits, or release
  tags, inspect them through the available repository or Actions read tools.

### Read more context when needed

Expand beyond the issue body when:

- The title is too vague to support classification.
- Comments add key reproduction details or constraints.
- The issue references a failing workflow, PR, or commit.
- The reported problem depends on nearby code or configuration.
- A claimed regression needs recent-change context.

### Evidence discipline

- Do not infer a root cause from the title alone.
- Do not claim a component is definitely responsible unless the thread or code
  actually supports that conclusion.
- If evidence is weak, say the scope is suspected rather than confirmed.
- If information is missing, request it explicitly instead of filling the gap
  with guesswork.

## Output format

Use a consistent structure so the issue thread is easy to scan.

```markdown
Type
- <bug | feature | question | ops | maintenance>

What is known
- <confirmed fact>
- <confirmed fact>

What is missing
- <missing detail and why it matters>
- <missing detail and why it matters>

Suspected scope
- <likely subsystem / workflow / file area, marked as tentative if needed>

Recommended next step
- <request info / investigate / plan / implement / answer directly / monitor>

Suggested follow-up questions
- <question>
- <question>
```

Notes:
- If nothing important is missing, say `- None identified for initial triage.`
- If no follow-up questions are needed, say `- None.`
- Keep `What is known` limited to evidence from the issue, comments, and any
  directly read related context.

## Common pitfalls

Avoid these mistakes:

- Starting root-cause analysis when the issue lacks even basic reproduction or
  environment details.
- Treating a feature request like a bug report, or vice versa.
- Failing to name the exact missing context needed for progress.
- Writing suspected causes as if they were already confirmed.
- Giving a vague next step like "look into it" instead of a concrete action.
- Ignoring important information added in comments after the original issue
  body.
- Turning triage into implementation when the immediate need is clarification.
- Producing a summary that restates the title but does not convert the issue
  into actionable follow-up work.

## Verification checklist

Before finishing, confirm all of the following:

- The skill is explicitly framed for Gitea issue triage in `agentic`.
- The trigger conditions focus on first-round issue handling and thread entry.
- The workflow covers reading the issue, classification, known facts, missing
  information, suspected scope, and recommended next step.
- The issue types include bug, feature, question, ops, and maintenance.
- The output format is structured and reusable in an issue comment.
- The tool guidance prefers Gitea MCP and related repository reads over guesses.
- The instructions distinguish hypotheses from confirmed conclusions.
- The skill does not depend on Hermes-specific workflow assumptions.
- The result helps the agent turn a messy issue into executable follow-up
  actions.

## Minimal example

If a new issue says only "CI failed on main after yesterday's merge", a good
first triage response would:

1. Classify it as `ops` or `bug` depending on whether the evidence points to
   infrastructure or repository behavior.
2. Read the failing workflow status and logs if available.
3. Summarize which workflow failed, on which branch or commit, and whether the
   failure looks new or flaky.
4. Call out missing context such as the failing run link, exact job name, or
   whether the failure is still happening.
5. Recommend `investigate` if enough evidence exists, or `request info` if the
   report is too vague.

## Reference material

Read `references/triage-template.md` when you want a compact fill-in template for
writing the actual triage comment.
