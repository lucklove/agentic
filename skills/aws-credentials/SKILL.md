---
name: aws-credentials
description: Obtain AWS credentials via OIDC token from GitHub Actions when the current environment has no valid AWS identity.
---

# AWS Credentials

Use this skill when you need valid AWS credentials and `aws sts get-caller-identity` fails (no current identity).

## When to use

- Before running any AWS CLI command and you suspect credentials may be missing.
- When a task requires AWS access and the environment has not been set up yet.

## Prerequisites

- `gh` CLI authenticated with access to `tidbcloud/autonomous-airflow`.
- `gpg` installed with the private key that corresponds to the PGP public key in the OIDC workflow.
- `aws` CLI installed.

## Step-by-step workflow

### 1. Check existing AWS identity

Run `aws sts get-caller-identity`.

- **If it succeeds**: credentials are already available. Skip the remaining steps.
  - If an `account-id` is relevant, also run `aws sts get-caller-identity --profile <account-id>`.
    If this second call fails, the user has not logged into that specific account.
    **Stop immediately and notify the user.** Do not attempt to work around it.
- **If it fails**: proceed to step 2.

### 2. Obtain credentials via the script

Run the helper script:

```python
await run_skill_script(
    skill_name="aws-credentials",
    script_name="scripts/obtain_credentials.py",
    args={
        "reason": "Brief explanation of why AWS credentials are needed",
        "usage": "How the credentials will be used",
    },
)
```

Arguments:

- `reason` (required): A human-readable explanation of why credentials are needed. Shown in the confirmation dialog.
- `usage` (required): Description of how the credentials will be used. Shown in the confirmation dialog.

The script will:

1. Show a confirmation dialog to the user via a NiceGUI popup, describing the reason and intended usage.
2. If the user confirms, trigger the `show-oidc-token.yaml` workflow on GitHub via `gh`.
3. Wait for the workflow to complete and extract the PGP-encrypted OIDC token from the logs.
4. Decrypt the token with `gpg` and write it to `~/.oidc/id-token`.

### 3. Verify

Run `aws sts get-caller-identity` again. It should now succeed.

If it still fails, report the error to the user.

## Common pitfalls

- **Do not bypass user confirmation.** The script must always ask the user before triggering the workflow.
- **Do not attempt to handle account-specific login failures.** If `aws sts get-caller-identity` works but `--profile <account-id>` does not, that is the user's responsibility.
- **GPG decryption requires the matching private key.** If `gpg -d` fails, the local GPG keyring does not have the correct private key.
- **The workflow log may contain `gpg: ...` lines mixed into the PGP message block.** The script handles this by stripping lines that start with `gpg:`.

## Verification

After the script completes successfully, run:

```bash
aws sts get-caller-identity
```

A successful JSON response confirms that credentials are working.
