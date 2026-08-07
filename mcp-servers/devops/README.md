# devops

MCP server for the [TiDB Cloud DevOps Jump API](http://gitea.ai/tidbcloud/nutshell-skills/wiki/devops-api) (a.k.a. Ops Portal bastion service). Wraps the oncall / devops workflow for requesting, polling, connecting to, and cancelling Jump authorizations.

## Tools

| Tool | Purpose |
|---|---|
| `list_jump_destinations` | List destination IDs available for a `destination_type` — accepts all six types (`controlplane`, `dedicated`, `serverless`, `byoc`, `premium`, `premium_resource_pool`), but only `controlplane` and `serverless` return non-empty lists. |
| `create_jump_authorization` | Create a Jump authorization for one or more clusters with a chosen `privilege`, `expiration`, and `reason`. |
| `list_jump_authorizations` | List the caller's Jump authorizations (filtered server-side by the API token owner's email; first page only, up to 100). |
| `get_jump_authorization` | Fetch the current phase and details of a single authorization. |
| `wait_for_jump_authorization` | Poll an authorization until it reaches a terminal phase (`created` / `expired` / `notapproved` / `canceled`). Returns `None` on timeout. |
| `get_jump_connection` | Write the gateway kubeconfig for a `created` authorization to disk (permissions `0o600`). |
| `cancel_jump_authorization` | Cancel an authorization (set `force=true` to override). |
| `get_me_profile` | Return the API token owner's profile (`operatorName`, `operatorType`, `userEmail`). |

## Configuration

| Env var | Required | Default | Description |
|---|---|---|---|
| `DEVOPS_API_TOKEN` | yes | — | API token used as `X-API-TOKEN` header. |
| `DEVOPS_ENV` | no | `prod` | Target environment: `prod`, `staging`, or `dev`. |
| `DEVOPS_BASE_DOMAIN` | no | derived from `DEVOPS_ENV` | Override the API base domain (e.g. for testing against a custom deployment). |
| `DEVOPS_TIMEOUT_SECONDS` | no | `30` | Per-request HTTP timeout. |

## Running locally

```bash
# stdio (default; what the agentic runner uses)
uv run mcp-servers/devops/server.py

# HTTP transports
uv run mcp-servers/devops/server.py --transport streamable-http --host 127.0.0.1 --port 8000
```

## Profile configuration

Add to `~/.agentic/agentic.yaml` (global) or a profile's `profile.yaml`:

```yaml
mcp:
  devops:
    python-runner:
      command: uv
      args: [run, mcp-servers/devops/server.py]
      env:
        DEVOPS_API_TOKEN: your-token-here
      include_instructions: true
```

Tool names from this server are exposed under the `devops_` prefix (e.g. `devops_list_jump_destinations`).
