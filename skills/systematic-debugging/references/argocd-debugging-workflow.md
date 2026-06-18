# ArgoCD CLI Debugging Workflow

Use this workflow when investigating ArgoCD application anomalies such as
unexpected Sync Status, Health degradations, or ComparisonErrors.

## Authentication

ArgoCD CLI tokens expire. Before any investigation, confirm the active context
and login state:

```bash
# Check current context
argocd context

# SSO login (recommended when SSO is configured)
argocd login <argocd-server> --grpc-web --sso

# Token login
argocd login <argocd-server> --grpc-web --auth-token <TOKEN>
```

**Always add `--grpc-web` to every `argocd` command** to suppress gRPC warnings.

If you see the following error, the token has expired -- stop and ask the user to
re-login immediately. Do not attempt workarounds:

```
level=fatal msg="oauth2: \"invalid_request\" \"Refresh token is invalid or has already been claimed by another client.\""
```

## Inspecting app state

```bash
# Full app detail: Sync Status, Health Status, Conditions, resource list
argocd app get <app-name> --grpc-web

# Include ongoing operation detail (e.g. active sync)
argocd app get <app-name> --grpc-web --show-operation
```

Key fields to check in the output:

- `Sync Status`: `Synced` / `OutOfSync` / `Unknown`
- `Health Status`: `Healthy` / `Degraded` / `Progressing` / `Missing`
- `CONDITION` section: lists abnormal conditions and their messages

## Common failure: ComparisonError -- field not declared in schema

```
ComparisonError: failed to calculate diff: error calculating structured merge diff:
error building typed value from live resource: .status.terminatingReplicas: field not declared in schema
```

**Cause**: ArgoCD's bundled OpenAPI schema cache does not include fields added by
newer CRD versions. This is common when a Helm chart upgrade introduces new CRD
fields but ArgoCD has not been upgraded.

**Effect**: Sync Status becomes `Unknown` while the application itself may remain
`Healthy`.

**Fixes**:

1. **Permanent**: Upgrade ArgoCD to v2.10+. Starting from v2.10, ArgoCD reads
   schema dynamically from cluster CRDs instead of a static bundle.
2. **Temporary workaround**: Add `ignoreDifferences` to the Application manifest:

```yaml
spec:
  ignoreDifferences:
    - group: monitoring.coreos.com   # replace with actual group
      kind: Prometheus                # replace with actual kind
      jsonPointers:
        - /status/terminatingReplicas # replace with actual field path
```

## ArgoCD version and CRD schema compatibility

| ArgoCD version | Schema source               | Notes                           |
|----------------|-----------------------------|---------------------------------|
| v2.8.x and below | Static bundled schema     | Prone to drift with newer CRDs  |
| v2.10+           | Dynamic from cluster CRDs | Recommended                     |

Confirm the running version with:

```bash
argocd version --grpc-web
```

## Other commonly used commands

```bash
# List all apps
argocd app list --grpc-web

# Manually trigger a sync
argocd app sync <app-name> --grpc-web

# View app history
argocd app history <app-name> --grpc-web

# Stream app logs (requires ArgoCD v2.4+)
argocd app logs <app-name> --grpc-web -n <namespace> --kind <Kind> --name <resource-name>
```

See `references/argocd-failure-patterns.md` for a quick reference of common
ArgoCD failure patterns and their log signatures.
