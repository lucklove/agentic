---
name: gitea-pr-review
description: Review Gitea pull requests with a structured, agentic-native workflow
  that reads PR metadata, diffs, review history, and workflow status before
  producing actionable feedback.
---

# Gitea PR Review

Use this skill when you are asked to review a pull request on Gitea or prepare a
structured review comment for a PR thread.

This skill is for real review work, not diff summarization. The goal is to find
correctness, risk, and maintainability issues, explain their impact, and separate
blocking findings from non-blocking suggestions.

## When to use

Use this skill when any of the following are true:

- A user asks you to review a Gitea PR.
- You are mentioned in a PR thread and need to provide review feedback.
- You need to inspect changed files, diffs, prior reviews, or review comments on
  a Gitea PR.
- You need to decide whether feedback should be blocking, warning-level, or only
  a suggestion.

Do not use this skill only to restate what changed. Review means deciding
whether the change is safe, correct, and maintainable.

## Review workflow

Follow this workflow in order. Skip only steps that are clearly unnecessary.

1. Read PR metadata.
   - Use Gitea tools to read the PR title, body, state, base branch, head
     branch, author, mergeability cues, and status summary.
   - Confirm what problem the PR claims to solve and whether there is linked
     issue context, acceptance criteria, or rollout notes.

2. Read the changed files and diff.
   - Inspect the file list to understand scope before reading detailed patches.
   - Read the diff and identify the risk hotspots: business logic, auth,
     persistence, config, workflows, deployment files, migrations, public APIs,
     and tests.
   - If one file depends on surrounding code, read the related file contents
     from the repository at the PR ref instead of reviewing the patch in
     isolation.

3. Read existing review history when relevant.
   - Check prior reviews and review comments to avoid repeating resolved points.
   - Pay special attention to change requests, unresolved reviewer concerns, and
     comments that imply hidden context or prior incidents.
   - If the PR discussion already explains a tradeoff, incorporate that context
     into your review instead of ignoring it.

4. Expand context beyond the diff when needed.
   - Read nearby modules, tests, configs, or call sites from the repository when
     correctness cannot be judged from the patch alone.
   - If the PR touches generated output, confirm the source files and generation
     path rather than commenting only on generated artifacts.
   - If the change affects interfaces, inspect the major consumers you can reach
     from repository context.

5. Verify what the current workflow actually exposes.
   - Treat Gitea PR metadata, diff context, linked issues, prior reviews, and
     workflow status as the primary evidence.
   - Check whether status checks are passing, failing, pending, or absent, and
     factor that into your confidence.
   - If the current review environment does not provide local checkout or test
     execution, do not invent a local validation path. Instead, state what you
     could and could not verify from PR context.

6. Classify findings by severity.
   - Blocking: likely bug, security issue, data-loss risk, unsafe rollout,
     missing required migration, failed required checks, or a change that
     contradicts the PR's stated intent.
   - Warning: real concern, but not clearly severe enough to block merge without
     more context.
   - Suggestion: improvement idea, simplification, style cleanup, or optional
     test enhancement.

7. Produce a structured review summary.
   - Lead with the highest-severity findings.
   - Reference exact files, functions, modules, configs, or behaviors.
   - Explain why the issue matters and what change would resolve it.
   - If no problems are found, say what you checked before saying it looks good.

## Review checklist

Check the following dimensions. Not every PR needs deep analysis in every
category, but you should consider each one.

### Correctness

- Does the new logic do what the PR claims?
- Are edge cases, nil/empty inputs, retries, partial failures, and ordering
  handled correctly?
- Are error paths, return values, and state transitions still valid?
- Does the code accidentally change semantics outside the intended scope?

### Security

- Does the change weaken auth, authz, input validation, secret handling, or data
  exposure boundaries?
- Could it introduce command injection, path traversal, SSRF, unsafe deserialization,
  privilege escalation, or trust of unvalidated input?
- Are logs, comments, or config changes leaking sensitive information?

### Config / infra risk

- Does the PR change CI, workflows, deployment config, env vars, permissions,
  networking, storage, or operational defaults?
- Could rollout fail because of missing secrets, mismatched environment setup,
  or incompatible infra assumptions?
- Are monitoring, alerting, or operational safety nets affected?

### Backward compatibility

- Does it change public APIs, CLIs, schemas, config formats, database behavior,
  or file formats?
- Will existing callers, persisted data, or old deployments keep working?
- If compatibility breaks, does the PR clearly document the migration path?

### Tests and workflow evidence

- Are tests added or updated for the changed behavior?
- Do status checks provide evidence for the main success and failure paths?
- Are important edge cases still unverified from the available PR context?
- If no tests were added or no checks ran, is there a credible reason?

### Code quality / readability

- Is the implementation understandable without hidden assumptions?
- Are names, function boundaries, and comments clear?
- Is the code consistent with local style and patterns?
- Are there dead branches, misleading comments, or avoidable coupling?

### Duplication / unnecessary complexity

- Is the PR re-implementing logic that already exists elsewhere?
- Could a simpler approach achieve the same goal with less risk?
- Does abstraction meaningfully help, or does it obscure behavior?

### Documentation / migration notes

- If behavior changes, are README, runbook, comments, or operator notes updated
  where needed?
- Are required migration, backfill, or rollout steps documented?
- If users or operators must act, is that visible in the PR description or code
  comments?

## Tool usage guidance

Use service-side reads as the normal path for this skill.

### Preferred primary path

- Use Gitea MCP tools to read PR metadata, changed files, diff, status, reviews,
  and review comments.
- Use issue or PR comments from the thread only as conversation context; do not
  manually duplicate the same information again if tool reads already provide it.
- If review comments are needed, read reviews first, then fetch review comments
  per review id.
- If you need repository context outside the patch, read file contents from the
  PR ref or base ref through repository read tools.

### When to read more than the diff

Read additional repository context when:

- The patch changes shared helpers or framework code.
- The diff omits surrounding invariants or setup/teardown details.
- A renamed symbol or config key may have external consumers.
- Generated files changed and you need to inspect the source of truth.
- Checks are green, but the behavior still seems suspicious.

### Verification guidance for this environment

- Prefer verification that is observable from the PR itself: status checks,
  diff coverage, linked issue context, and surrounding repository files.
- If checks are pending, wait for them instead of concluding immediately.
- If checks fail, incorporate that into the review and inspect the relevant PR
  context before deciding whether the failure is blocking.
- Always report what you checked and what remained unverifiable.

## Output format

Use a consistent structure so the PR thread is easy to scan.

### If you found blocking issues

```markdown
Blocking
- `path/to/file`: concise statement of the problem, impact, and expected fix.
- `another/file`: concise statement of the problem, impact, and expected fix.

Warnings
- Optional concern that is real but not clearly merge-blocking.

Suggestions
- Optional improvement that is non-blocking.

Verification
- Checked: diff, changed files, related repository context, workflow status
- Not checked: <anything you could not verify>
```

### If there are no blocking issues

```markdown
Looks Good
- Reviewed <key files / modules>.
- Verified <workflow status or code reasoning>.
- No blocking issues found.

Suggestions
- Optional non-blocking improvements.

Verification
- Checked: ...
- Not checked: ...
```

### Severity guidance

Request changes when:

- You found at least one likely correctness or security bug.
- The change has serious rollout, migration, or compatibility risk.
- Required checks are failing or required evidence is missing for a risky change.
- A reviewer cannot reasonably approve without additional fixes or evidence.

Leave a comment without requesting changes when:

- Feedback is advisory, clarifying, or low-confidence.
- The concern is real but may depend on missing context.
- You have suggestions or warnings but not a clear merge blocker.

Approve or say LGTM only when:

- You reviewed the meaningful risk areas for the PR scope.
- Available workflow checks, code evidence, or repository context support the
  change.
- You are not aware of unresolved blocking comments or failed checks.

## Common pitfalls

Avoid these mistakes:

- Only summarizing the diff instead of judging whether it is correct.
- Giving LGTM without checking status or behavior-sensitive context.
- Ignoring config, rollout, migration, or operational risk.
- Escalating a minor style preference into a blocking issue.
- Raising vague concerns without naming the file, line, module, or behavior.
- Repeating comments already resolved in the thread.
- Trusting a green check alone when the code still looks unsafe.
- Reviewing generated output while ignoring the source files that produced it.
- Referring to local worktrees, shell commands, or test execution in an
  environment that is explicitly review-only.

## Verification checklist

Before finishing, confirm all of the following:

- The trigger for using this skill is clear and Gitea-specific.
- The workflow uses Gitea MCP as the primary source of PR state.
- Repository context is read through available server-side tools when needed.
- The checklist covers correctness, security, config/infra risk, compatibility,
  tests/workflow evidence, quality, complexity, and docs.
- The output format distinguishes blocking findings, warnings, suggestions, and
  looks-good outcomes.
- The instructions fit agentic's comment-thread workflow rather than GitHub-only
  review commands.
- The final review guidance is actionable and points to concrete code locations
  or behaviors.

## Minimal example

If you receive a request like "review PR #42", a good path is:

1. Read PR metadata and status from Gitea.
2. Read changed files and diff.
3. Read prior reviews if the thread is active.
4. Open nearby repository files for the risky parts of the patch.
5. Check workflow status and note any remaining uncertainty.
6. Return a structured review with blocking issues first, or "Looks Good" plus
   verification notes if no blockers are found.
