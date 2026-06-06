---
name: requesting-code-review
description: Prepare an agentic change for review with a Gitea-native self-check workflow that verifies scope, validation, risks, and reviewer-facing summary before requesting review or posting a delivery update.
---

# Requesting Code Review

Use this skill when you have already completed an implementation, fix, or refactor
and the next step is to hand the change to a reviewer in a way that is easy to
trust, easy to scan, and honest about what was and was not verified.

This skill is for the final pre-review pass. It helps you confirm the change is
actually ready, then produce a reviewer-friendly summary for a Gitea issue or PR
comment. It is not a substitute for implementation, debugging, or review itself.

## When to use

Use this skill when any of the following are true:

- You finished a bug fix and are about to request review.
- A feature or refactor has reached a reviewable state.
- You need to post an issue or PR comment explaining what changed, how it was
  verified, and what risks remain.
- You want to do a final self-check before telling others the work is ready.
- You need to turn local implementation work into a concise, reviewer-friendly
  handoff.

Do not use this skill at the start of a task. Use it after the main coding work
is done and before claiming the result is ready for review.

## Pre-review self-check workflow

Follow this workflow in order. If any step fails, go back and fix the work before
requesting review.

1. Reconfirm the original goal.
   - Re-read the issue, PR description, comments, or acceptance criteria.
   - Restate the problem in concrete terms: what was broken, missing, or being
     improved.
   - Confirm that your branch still matches that goal and did not drift into a
     different task.

2. Check whether the implementation really solves the right problem.
   - Compare the final change to the reported bug, request, or design goal.
   - Verify that the root cause or intended workflow is covered, not just the
     visible symptom.
   - If you added a workaround instead of a full fix, be explicit about that in
     the handoff.

3. Review the scope of the diff.
   - Inspect changed files and confirm each one is relevant.
   - Look for accidental edits, debug output, commented code, generated churn, or
     unrelated formatting changes.
   - Remove anything that does not belong before asking for review.

4. Re-check tests and validation.
   - Run the most relevant tests, linters, builds, or focused verification steps
     for the change.
   - Confirm that the validation actually covers the behavior you changed.
   - If you could not run an important check, record that clearly instead of
     implying full verification.

5. Check compatibility and operational impact.
   - Consider backward compatibility, config changes, migrations, workflow
     changes, secrets handling, deployment assumptions, and user-visible
     behavior changes.
   - If operators, users, or downstream callers must do anything differently,
     capture that in the summary.

6. Identify remaining risks and follow-ups.
   - Note any known limitations, edge cases not fully covered, or follow-up work
     that should happen later.
   - Separate known risk from speculation. Only list risks you can justify from
     the code, tests, or environment.

7. Prepare the reviewer-facing summary.
   - Summarize the goal, main changes, touched files or modules, validation, and
     risks in a structure that is quick to review.
   - Highlight where you want extra reviewer attention if a part of the change is
     subtle, high-risk, or based on a tradeoff.

8. Only then request review or post the handoff.
   - Do not ask for review until the branch is in the state you want reviewed.
   - If the repository workflow requires waiting for checks, wait until the
     relevant checks pass before posting the final review-request comment.

## Self-review checklist

Consider each area before you say the work is ready.

### Correctness

- Does the change solve the actual issue, not just a nearby symptom?
- Are success paths, failure paths, and edge cases handled?
- Did you preserve existing behavior outside the intended scope?
- Is any logic relying on an unstated assumption that should be called out?

### Tests / validation

- Did you run the most relevant test, lint, build, or targeted verification?
- Does the chosen validation exercise the changed behavior directly?
- Are there important scenarios still untested?
- If validation was partial, did you say so explicitly?

### Backward compatibility

- Does the change alter APIs, config keys, file formats, data shape, or CLI
  behavior?
- Will existing callers, automation, or saved data still work?
- If compatibility changed, did you document the impact and migration path?

### Config / infra impact

- Did you change workflows, environment variables, secrets usage, permissions,
  deployment files, or operational defaults?
- Could this fail in CI or deployment because of missing setup outside the code?
- Does the change require rollout sequencing or coordination?

### Docs / migration notes

- Should README, runbooks, comments, or usage docs change along with the code?
- Are migration or operator notes needed?
- If no docs changed, is that clearly acceptable for this scope?

### Security / secret exposure

- Did the change weaken validation, auth, authz, or trust boundaries?
- Could logs, comments, or test data expose secrets or sensitive values?
- Did you avoid adding unsafe shortcuts just to make the fix pass?

### Unnecessary complexity / scope creep

- Is the solution larger or more abstract than the problem requires?
- Did you add refactors that are unrelated to the task?
- Would a reviewer have to understand extra machinery that could have been
  avoided?

### Unrelated formatting churn

- Did any formatter, generator, or bulk edit change unrelated lines?
- If unavoidable unrelated churn remains, did you call it out so the reviewer is
  not surprised?

## Tool usage guidance

Use repository and thread evidence before writing the review request.

### Preferred context sources

- Read the Gitea issue or PR body and comments to confirm the original request
  and any reviewer expectations.
- Use Gitea reads for PR metadata, diff, changed files, and status when the work
  is already in a PR.
- Use local git diff, file reads, and test output to confirm what actually
  changed and what was verified.

### Evidence discipline

- Do not say "verified" unless you actually ran or observed the relevant check.
- Do not say "fixed" if the change only narrows the problem or adds partial
  coverage.
- Do not hide missing validation; say exactly what was not checked.
- Do not omit compatibility, rollout, or risk information just because the code
  looks small.

### Agentic-specific workflow fit

- Prefer Gitea-native issue and PR context over GitHub-only workflows or `gh`
  command assumptions.
- If posting in an issue before or alongside a PR, summarize the change in a way
  that makes sense without local branch access.
- If posting in a PR, tailor the summary to help the reviewer scan the diff and
  focus on the risky parts.

## Output format

Use a consistent structure for the review request or delivery handoff.

```markdown
Goal / original issue
- <what problem this change solves>

What changed
- <main implementation change>
- <secondary change if relevant>

Files / modules touched
- `<path>`: <why it changed>
- `<path>`: <why it changed>

Verification performed
- <test, lint, build, manual check, or workflow evidence>
- <additional validation>
- Not run: <important check not run, if any>

Risks / follow-ups
- <known limitation, compatibility note, rollout note, or follow-up>
- None identified beyond normal review scope.

Reviewer focus
- <where you want extra attention>
- <subtle tradeoff or risk hotspot>
```

Guidance:
- Keep each bullet factual and specific.
- If a section has nothing notable, say that plainly instead of padding.
- If there are no known risks, do not invent one; say none were identified.
- If you already opened a PR for an issue, include `Closes #<issue>` in the PR
  description rather than closing the issue manually.

## Common pitfalls

Avoid these mistakes:

- Saying only "已修复" or "done" without explaining what changed.
- Claiming the change was verified without listing the actual checks run.
- Ignoring known risks, limitations, or follow-up work.
- Hiding unrelated changes in the diff without mentioning them.
- Presenting an untested assumption as a confirmed conclusion.
- Asking for review before the final branch state is pushed.
- Writing a long implementation diary instead of a concise review handoff.
- Using reviewer-facing summaries that depend on Hermes-only or GitHub-only
  tooling assumptions.

## Verification checklist

Before finishing, confirm all of the following:

- The skill is explicitly adapted to Gitea issue/PR comment workflows in
  `agentic`.
- The workflow emphasizes self-check before requesting review.
- The checklist covers correctness, validation, compatibility, config/infra,
  docs, security, complexity, and unrelated churn.
- The tool guidance prefers Gitea context plus local diff/test evidence when
  available.
- The output format helps a reviewer quickly understand scope, validation, and
  remaining risk.
- The instructions do not depend on Hermes-specific capabilities.
- The final handoff is honest about what was and was not verified.

## Minimal example

If you fixed issue #123 and opened a PR, a good final handoff would:

1. Re-state the original failure or requested behavior.
2. Summarize the key code paths changed.
3. Name the focused tests or checks you ran.
4. Call out any remaining limitation or why no extra migration is needed.
5. Point the reviewer to the most subtle part of the patch.

## Reference material

Read `references/review-request-template.md` when you want a compact template to
fill in for the actual issue or PR comment.
