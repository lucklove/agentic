---
name: systematic-debugging
description: Investigate bugs, regressions, flaky failures, and unexpected behavior with a root-cause-first workflow before proposing fixes.
---

# Systematic Debugging

Use this skill when the main task is to investigate a problem, explain why it is
happening, or decide what should be checked before changing code. This skill is
for `agentic`'s notification-driven issue and pull request workflow, where the
agent often starts from a Gitea thread, logs, diffs, workflow results, and a
local checkout.

The goal is not to guess a fix quickly. The goal is to build the smallest
credible explanation of the failure, clearly separate facts from hypotheses, and
only then recommend a validation step or minimal fix direction.

## When to use

Use this skill when any of the following are true:

- An issue reports a bug, regression, flaky test, CI failure, or unexpected
  behavior.
- A PR comment or review asks why something broke or behaves strangely.
- A workflow, automation, or integration failed and the first task is to locate
  the failing layer.
- The right code change is not yet obvious.
- You have partial evidence, such as one error snippet or one symptom, and need
  to investigate before recommending action.
- You suspect the problem could come from configuration, environment drift,
  dependency behavior, external services, or recent changes rather than one
  obvious line of code.

Do not use this skill for straightforward implementation work where the request
already includes a clear, confirmed fix and no investigation is needed.

## Core principles

1. Investigate before fixing.
   - Do not jump from symptom to patch.
   - Do not propose code changes until you understand what evidence supports
     them.

2. Treat root cause as the target, not the visible failure.
   - A stack trace, failed assertion, timeout, or missing file is often only the
     last observable symptom.
   - Ask what changed, what assumption became false, and which layer is actually
     responsible.

3. Separate facts, hypotheses, and recommendations.
   - Facts are directly supported by thread context, logs, diffs, files, tests,
     or reproduction.
   - Hypotheses are explanations that still need confirmation.
   - Recommendations are the next checks or fix directions chosen after weighing
     the evidence.

4. Prefer one leading hypothesis over many vague possibilities.
   - It is acceptable to mention alternatives, but rank them.
   - The output should make clear which explanation is most likely and why.

5. Minimize speculative churn.
   - Read more before editing more.
   - If you cannot confirm a cause yet, say what is missing and what to inspect
     next.

## Investigation workflow

Follow this sequence and adapt it to the size of the incident.

### 1. Read the thread completely

Start with the full issue or pull request context:

- issue or PR description
- all relevant comments
- review comments and review summaries when on a PR
- labels, status checks, linked workflow runs, and any pasted logs

Do not infer the task from the title alone. The latest comment may change the
actual debugging question.

### 2. Define the symptom precisely

Extract the externally visible failure:

- what failed
- where it failed
- when it started
- whether it is deterministic, flaky, or environment-specific
- what the expected behavior was

Reduce vague reports like "CI is broken" into something concrete such as
"unit test X fails on Python 3.14 after PR Y" or "deploy workflow cannot read a
required secret in the release job".

### 3. Capture reproduction clues and constraints

Identify whatever narrows the search space:

- exact command, test, workflow job, or endpoint involved
- branch, commit, PR, or release context
- environment details
- files, modules, or services named in the logs
- whether the failure happens locally, only in CI, or only after a recent merge

If reproduction details are missing, call that out explicitly.

### 4. Check recent changes and surrounding context

Look for the most relevant nearby change before inventing explanations:

- changed files in the PR
- recent commits touching the failing area
- workflow or configuration edits
- dependency or version changes
- related issues or earlier comments mentioning the same symptom

Recent change analysis is often the fastest path from symptom to root cause.

### 5. Localize the failing layer

Place the problem in the most likely layer first:

- product or business logic
- test logic
- configuration
- workflow or automation
- dependency or toolchain
- external service or API
- environment or state drift
- data or fixture setup

This keeps the investigation focused and avoids random code changes in the wrong
area.

### 6. Read the source of truth

If the thread alone is insufficient, read the relevant artifacts directly:

- source files
- tests
- workflow YAML
- configuration files
- diffs
- scripts
- logs from failing jobs

Prefer primary evidence over paraphrased descriptions. If a comment says a file
"probably changed", read the file.

### 7. Form a single leading root-cause hypothesis

After enough evidence is gathered, state the best current explanation in one or
two sentences.

A strong hypothesis usually connects these parts:

- triggering change or condition
- broken assumption
- observed symptom

Example pattern:

> Commit A changed assumption B in workflow C, so job D now runs without input E,
> which explains the missing-variable failure in step F.

If multiple hypotheses remain plausible, rank them and explain what evidence
would distinguish them.

### 8. Validate or propose the next validation

When possible, validate the hypothesis by:

- reproducing locally
- running the relevant test
- inspecting the exact workflow job log
- checking the specific commit or diff that introduced the behavior
- confirming the expected config value or file contents

If validation is not possible in the current context, give the smallest next
check that would confirm or falsify the leading hypothesis.

### 9. Only then propose a fix direction

After investigation, recommend the narrowest fix direction that addresses the
root cause.

Good fix directions are:

- tied to the identified cause
- explicit about scope
- clear about what should be verified after the fix

Avoid broad cleanup suggestions unless they are necessary to fix the problem.

## Output format

When replying in an issue or PR thread, prefer this structure:

### Symptoms
- Short statement of the visible failure.
- Include where it appears and under what condition.

### Confirmed facts
- Bullet points backed by logs, diffs, files, comments, or reproduction.
- Do not mix facts with interpretation.

### Hypotheses
- List plausible explanations in priority order.
- Mark one as the leading hypothesis.

### Most likely root cause
- One concise paragraph explaining the best-supported cause.

### Next checks
- Smallest follow-up validations still needed.
- If already validated, say so and move to fix direction.

### Proposed fix direction
- Only include this section after investigation.
- Describe the minimal change or action that follows from the root cause.

If evidence is still thin, explicitly say that the investigation is incomplete
instead of pretending the cause is confirmed.

## Tool usage guidance

Prefer the tools that expose the actual thread and repository state.

### Gitea-first investigation

Use Gitea MCP reads first for thread and PR context:

- issue or PR details
- issue comments
- PR reviews and review comments
- changed files and diffs
- commit status and workflow status
- workflow runs, jobs, and logs when the failure is in CI or automation

This is usually the fastest way to understand what triggered the debugging task.

### Read repository files directly when comments are not enough

If a thread references code, config, workflows, tests, or scripts, read those
files instead of relying on summaries from humans or prior agent comments.

Examples:

- read the failing workflow YAML if CI failed
- read the changed test and implementation if a regression is reported
- read configuration and environment wiring if the failure mentions missing
  variables or credentials

### Use local checkout as a verification path

Use local git and targeted commands when they can reduce uncertainty:

- inspect recent commits
- diff branches
- run a focused test
- execute the failing command locally when feasible

Do not run broad or expensive checks unless they are justified by the
investigation.

### Be explicit about evidence quality

If you only have partial evidence, say so. For example:

- "Confirmed from the PR diff and workflow log"
- "Not yet reproduced locally"
- "Likely, but still needs validation against the failing job log"

## Common pitfalls

Avoid these failure modes:

- proposing a fix before locating the likely cause
- treating the last error message as the root cause without checking upstream
  conditions
- ignoring recent code, workflow, or configuration changes
- presenting several competing fixes with no prioritization
- mixing facts and guesses in the same bullet list
- stopping at "cannot reproduce" without describing what evidence was checked
- overfitting to one comment when the full thread suggests a different problem
- changing code just to test a theory when cheaper read-only checks are still
  available

## Verification checklist

Before finishing the investigation or writing the thread reply, confirm:

- I read the full relevant issue or PR context, not just the title or latest log.
- I clearly identified the symptom and where it appears.
- I separated confirmed facts from hypotheses.
- I checked recent changes or adjacent context before recommending a fix.
- I identified the most likely failing layer.
- I stated one leading root-cause hypothesis, or explained why one cannot yet be
  chosen.
- My next checks are concrete and minimal.
- If I proposed a fix direction, it follows from the investigation rather than a
  guess.
- My response format is suitable for an issue or PR comment thread.
- The reply would help another engineer continue the investigation without
  redoing the same context gathering.

## Optional references

If you need a reusable response shape for issue or PR investigations, read
`references/investigation-reply-template.md`.

If you want a tiny example of how to explain a CI failure in a thread, read
`references/ci-failure-example.md`.

## Weekly test debugging workflow (auto-deploy)

Use this workflow when a `autonomous/auto-deploy` weekly test fails and you need
to trace failures across the 3-level run nesting: outer weekly-test run →
inner dispatched run → app-delivery run.

### Background: run nesting model

```
outer weekly-test run  (tidbcloud/auto-deploy)
  └── job N (dispatches inner run)
        └── inner deploy run  (tidbcloud/auto-deploy)
              └── step: app_ops.py deploy  (dispatches app-delivery run)
                    └── app-delivery workflow  (tidbcloud/app-delivery)
```

Each level requires a different lookup strategy because only the outer run ID is
directly visible from the CI notification.

### Step 1. Identify the outer run and its sub-jobs

```bash
gh api /repos/tidbcloud/auto-deploy/actions/runs/<RUN_ID>/jobs --paginate \
  | jq '.jobs[] | {id,name,conclusion,steps_count: (.steps | length)}'
```

Focus on jobs where `conclusion` is `failure`. Note the job IDs for the next
step.

### Step 2. Find the inner (dispatched) run ID from job logs

Each outer job dispatches an inner workflow run. The inner run ID is emitted in
the job log:

```bash
gh run view <OUTER_RUN_ID> --repo tidbcloud/auto-deploy --log \
  | grep -E "inner_run_id|Triggered workflow"
```

Or read the specific job step logs:

```bash
gh api /repos/tidbcloud/auto-deploy/actions/jobs/<JOB_ID>/logs \
  | grep -E "run_id|workflow_id"
```

### Step 3. Find the failed step in the inner run

```bash
gh run view <INNER_RUN_ID> --repo tidbcloud/auto-deploy --log-failed 2>&1 \
  | head -200
```

Look for `exit code 1`, `Error:`, or `FAILED`. The step name tells you which
layer failed (Terraform, kubectl, app_ops.py, etc.).

### Step 4. Correlate with app-delivery runs by timestamp

`app_ops.py deploy` dispatches an `app-delivery` workflow run, but the run ID is
**not** logged directly. Correlate by timestamp:

1. Note the timestamp of the failing step in the inner run log.
2. List recent app-delivery runs for the relevant workflow file in that window:

```bash
gh run list --repo tidbcloud/app-delivery \
  --workflow deploy-new-region-next-gen-upbound-aws-provider.yaml \
  --created ">YYYY-MM-DDThh:mm:ssZ" \
  --json databaseId,createdAt,conclusion,headBranch \
  --limit 20
```

3. Match the run whose `createdAt` is closest (within seconds to a few minutes)
   to the dispatch time in the auto-deploy log.

### Step 5. Check the `Check if deployed` step in app-delivery

```bash
gh run view <APP_DELIVERY_RUN_ID> --repo tidbcloud/app-delivery --log 2>&1 \
  | grep -A5 "Check if deployed"
```

**Key**: `check-deployed.sh` exiting 1 means "already registered, skip" — this
is NOT a bug. A fresh first-deploy run exits 0 and proceeds. Do not treat this
as a failure unless the outer job also reports a failure downstream.

### Step 6. Identify `--wait-workflow-success false` as the race condition root cause

When `app_ops.py deploy` is called with `--wait-workflow-success false` (used
for Crossplane provider installs), auto-deploy starts `kubectl wait` immediately
after dispatching app-delivery, without waiting for app-delivery to finish. The
ArgoCD reconcile lag is typically 1–3 minutes; if `kubectl wait` times out
before the provider CR is installed, Terraform fails.

Race condition pattern:
```
T+0s   auto-deploy dispatches app-delivery
T+0s   auto-deploy starts: kubectl wait --for=create provider.pkg.crossplane.io/<name>
T+60s  app-delivery PR created and merged
T+2min ArgoCD reconciles, Crossplane installs provider CR
→ kubectl wait already timed out → Terraform exit code 1
```

To detect this in logs:

```bash
gh run view <INNER_RUN_ID> --repo tidbcloud/auto-deploy --log 2>&1 \
  | grep -E "wait-workflow-success|kubectl wait|for create|timed out"
```

### Step 7. Classify the failure

| Failure pattern | Log signature | Root cause |
|---|---|---|
| Crossplane provider not ready | `kubectl wait --for create ... timed out` | Race: `--wait-workflow-success false` + ArgoCD reconcile lag |
| Azure RG not found | `ResourceGroupNotFound: Resource group 'dev-seed-*'` | Missing prereq / environment drift |
| AliCloud KMS conflict | `409 Rejected.ResourceExist` | Leftover resources from previous run |
| Retry succeeded | Same step passes on attempt 2 | Transient timing issue, not a code bug |

### Step 8. Reconstruct the timeline

For any failed job, write out:

```
[T+Xs] auto-deploy: step dispatches app-delivery run → run ID=Y
[T+Xs] auto-deploy: kubectl wait for provider CR (immediately after dispatch)
[T+Xs] app-delivery Y: PR created, merged
[T+Xs+reconcile_lag] ArgoCD: provider CR installed
→ if kubectl wait timeout < reconcile_lag → FAIL
```

This reconstruction determines whether the failure is a race (architectural,
needs a fix to `--wait-workflow-success` usage), environment drift (needs
prereq remediation), or a transient flake (can be re-run).

### Key files to read when investigating auto-deploy weekly failures

- `terraform/modules/app/app_ops.py` — `deploy` subcommand, `--wait-workflow-success` flag
- `autopilot/scripts/run-weekly-test.py` — outer orchestration
- `app-delivery` repo: `deploy-new-region-next-gen-upbound-aws-provider.yaml`, `check-deployed.sh`

See `references/weekly-test-failure-categories.md` for a full failure category
reference with example log signatures.

