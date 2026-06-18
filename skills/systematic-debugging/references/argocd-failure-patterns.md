# ArgoCD Failure Patterns

Quick reference for common ArgoCD application failure patterns and their
distinguishing log signatures.

## ComparisonError: field not declared in schema

**Symptom**: Sync Status = `Unknown`, Health Status = `Healthy`

**Log signature**:
```
ComparisonError: failed to calculate diff: error calculating structured merge diff:
error building typed value from live resource: .<field-path>: field not declared in schema
```

**Root cause**: ArgoCD's bundled static OpenAPI schema does not include a field
that exists in the live cluster CRD (e.g. after a Helm chart upgrade added a new
CRD field while ArgoCD version is still v2.8.x or below).

**Fix**: Upgrade ArgoCD to v2.10+, or add `ignoreDifferences` for the
unrecognized field path.

---

## Token expired / invalid refresh token

**Symptom**: All `argocd` commands fail immediately.

**Log signature**:
```
level=fatal msg="oauth2: "invalid_request" "Refresh token is invalid or has already been claimed by another client.""
```

**Root cause**: SSO or auth token has expired.

**Fix**: Re-login with `argocd login <server> --grpc-web --sso` (or `--auth-token`).
Ask the user for credentials — do not attempt to bypass authentication.

---

## Sync stuck in Progressing

**Symptom**: Health Status = `Progressing` for an extended period.

**Common causes**:
- A Kubernetes resource is not reaching its ready state (e.g. Deployment rollout
  stuck, PVC pending, Job running).
- ArgoCD is waiting for a hook (PreSync, Sync, PostSync) to complete.

**Investigation**:
```bash
argocd app get <app-name> --grpc-web --show-operation
```
Check the `CONDITION` and `Operation` sections for the blocking resource.

---

## OutOfSync after a successful sync

**Symptom**: Sync Status flips back to `OutOfSync` shortly after a successful
sync completes.

**Common causes**:
- A controller is mutating the resource in-cluster after ArgoCD writes it
  (e.g. admission webhooks, operators).
- The source manifest includes a field that Kubernetes normalizes differently
  on write.

**Investigation**:
- Use `argocd app diff <app-name> --grpc-web` to see the exact diff.
- Check whether the differing fields are being set by an in-cluster controller.
