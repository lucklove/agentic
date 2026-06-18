# Weekly Test Failure Categories (auto-deploy)

Use this table when classifying a failed sub-job in a weekly test run.

## Failure categories

| Category | Log signature | Root cause | Action |
|---|---|---|---|
| Crossplane provider race | `kubectl wait --for create provider.pkg.crossplane.io/<name> ... timed out` | `--wait-workflow-success false` causes kubectl wait to start before ArgoCD has reconciled the provider CR | Architectural: add explicit wait or change flag |
| Azure RG not found | `ResourceGroupNotFound: Resource group 'dev-seed-*' could not be found` | Environment prereq missing or drifted | Remediate prereq, re-run |
| AliCloud KMS conflict | `409 Rejected.ResourceExist` | Leftover resource from a prior failed or partial run | Clean up the leftover resource, re-run |
| app-delivery skip (idempotent) | `check-deployed.sh` exits 1, workflow skipped | Already registered — intentional idempotency guard | Not a bug; check if the outer job still failed downstream |
| Transient flake | Same step fails on attempt 1, passes on attempt 2 with no code change | Timing or network transient | Re-run; no fix needed unless frequency increases |

## Common misclassifications

- **check-deployed.sh exit 1 is NOT a failure.** It means the resource was
  already registered. Only report it as an issue if the outer auto-deploy job
  also fails downstream.

- **Terraform exit 1 alone is ambiguous.** Check whether the root cause is the
  Crossplane race (kubectl wait timed out before ArgoCD reconcile), environment
  drift (missing prereq), or a leftover conflict. The log signature distinguishes
  them.

- **A retry success does not rule out a race.** The Crossplane race can be
  intermittent depending on ArgoCD queue depth. Track frequency across runs
  before closing as a flake.

## Log grep commands for quick classification

```bash
# Crossplane race
gh run view <INNER_RUN_ID> --repo tidbcloud/auto-deploy --log 2>&1 \
  | grep -E "kubectl wait|for create|timed out|wait-workflow-success"

# Azure RG not found
gh run view <INNER_RUN_ID> --repo tidbcloud/auto-deploy --log 2>&1 \
  | grep "ResourceGroupNotFound"

# AliCloud KMS conflict
gh run view <INNER_RUN_ID> --repo tidbcloud/auto-deploy --log 2>&1 \
  | grep "Rejected.ResourceExist"

# app-delivery idempotency check
gh run view <APP_DELIVERY_RUN_ID> --repo tidbcloud/app-delivery --log 2>&1 \
  | grep -A5 "Check if deployed"
```
