# Weekly Test Debugging Workflow (auto-deploy)

Use this workflow when a `autonomous/auto-deploy` weekly test fails and you need
to trace failures across the 3-level run nesting: outer weekly-test run ->
inner dispatched run -> app-delivery run.

## Background: run nesting model

```
outer weekly-test run  (tidbcloud/auto-deploy)
  └── job N (dispatches inner run)
        └── inner deploy run  (tidbcloud/auto-deploy)
              └── step: app_ops.py deploy  (dispatches app-delivery run)
                    └── app-delivery workflow  (tidbcloud/app-delivery)
```

Each level requires a different lookup strategy because only the outer run ID is
directly visible from the CI notification.

## Step 1. Identify the outer run and its sub-jobs

```bash
gh api /repos/tidbcloud/auto-deploy/actions/runs/<RUN_ID>/jobs --paginate \
  | jq '.jobs[] | {id,name,conclusion,steps_count: (.steps | length)}'
```

Focus on jobs where `conclusion` is `failure`. Note the job IDs for the next
step.

## Step 2. Find the inner (dispatched) run ID from job logs

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

## Step 3. Find the failed step in the inner run

```bash
gh run view <INNER_RUN_ID> --repo tidbcloud/auto-deploy --log-failed 2>&1 \
  | head -200
```

Look for `exit code 1`, `Error:`, or `FAILED`. The step name tells you which
layer failed (Terraform, kubectl, app_ops.py, etc.).

## Step 4. Correlate with app-delivery runs by timestamp

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

## Step 5. Check the `Check if deployed` step in app-delivery

```bash
gh run view <APP_DELIVERY_RUN_ID> --repo tidbcloud/app-delivery --log 2>&1 \
  | grep -A5 "Check if deployed"
```

**Key**: `check-deployed.sh` exiting 1 means "already registered, skip" -- this
is NOT a bug. A fresh first-deploy run exits 0 and proceeds. Do not treat this
as a failure unless the outer job also reports a failure downstream.

## Step 6. Identify `--wait-workflow-success false` as the race condition root cause

When `app_ops.py deploy` is called with `--wait-workflow-success false` (used
for Crossplane provider installs), auto-deploy starts `kubectl wait` immediately
after dispatching app-delivery, without waiting for app-delivery to finish. The
ArgoCD reconcile lag is typically 1-3 minutes; if `kubectl wait` times out
before the provider CR is installed, Terraform fails.

Race condition pattern:
```
T+0s   auto-deploy dispatches app-delivery
T+0s   auto-deploy starts: kubectl wait --for=create provider.pkg.crossplane.io/<name>
T+60s  app-delivery PR created and merged
T+2min ArgoCD reconciles, Crossplane installs provider CR
-> kubectl wait already timed out -> Terraform exit code 1
```

To detect this in logs:

```bash
gh run view <INNER_RUN_ID> --repo tidbcloud/auto-deploy --log 2>&1 \
  | grep -E "wait-workflow-success|kubectl wait|for create|timed out"
```

## Step 7. Classify the failure

| Failure pattern | Log signature | Root cause |
|---|---|---|
| Crossplane provider not ready | `kubectl wait --for create ... timed out` | Race: `--wait-workflow-success false` + ArgoCD reconcile lag |
| Azure RG not found | `ResourceGroupNotFound: Resource group 'dev-seed-*'` | Missing prereq / environment drift |
| AliCloud KMS conflict | `409 Rejected.ResourceExist` | Leftover resources from previous run |
| Retry succeeded | Same step passes on attempt 2 | Transient timing issue, not a code bug |

## Step 8. Reconstruct the timeline

For any failed job, write out:

```
[T+Xs] auto-deploy: step dispatches app-delivery run -> run ID=Y
[T+Xs] auto-deploy: kubectl wait for provider CR (immediately after dispatch)
[T+Xs] app-delivery Y: PR created, merged
[T+Xs+reconcile_lag] ArgoCD: provider CR installed
-> if kubectl wait timeout < reconcile_lag -> FAIL
```

This reconstruction determines whether the failure is a race (architectural,
needs a fix to `--wait-workflow-success` usage), environment drift (needs
prereq remediation), or a transient flake (can be re-run).

## Key files to read when investigating auto-deploy weekly failures

- `terraform/modules/app/app_ops.py` -- `deploy` subcommand, `--wait-workflow-success` flag
- `autopilot/scripts/run-weekly-test.py` -- outer orchestration
- `app-delivery` repo: `deploy-new-region-next-gen-upbound-aws-provider.yaml`, `check-deployed.sh`

See `references/weekly-test-failure-categories.md` for a full failure category
reference with example log signatures.
