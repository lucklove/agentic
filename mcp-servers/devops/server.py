# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.28.1",
#     "mcp>=2.0.0",
# ]
# ///

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
from mcp.server.mcpserver import MCPServer

TERMINAL_PHASES = frozenset({"created", "expired", "notapproved", "canceled"})
ENVIRONMENTS = {
    "prod": "https://prod.devops.pingcap.com",
    "staging": "https://staging.devops.pingcap.com",
    "dev": "https://dev.devops.pingcap.com",
}


class DevOpsApiError(RuntimeError):
    pass


def _phase(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("phase")
    if isinstance(value, str):
        return value
    authorization = payload.get("authorization")
    if isinstance(authorization, dict) and isinstance(authorization.get("phase"), str):
        return authorization["phase"]
    return None


def _gateway_kubeconfig(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    connection = payload.get("connection")
    if not isinstance(connection, dict):
        return None
    gateway = connection.get("gateway")
    if not isinstance(gateway, dict):
        return None
    kubeconfig = gateway.get("kubeconfig")
    if not isinstance(kubeconfig, str) or not kubeconfig.strip():
        return None
    return kubeconfig


def _check_ok(payload: Any) -> None:
    """Validate a minimal `{errcode, errmsg, trace_id}` response.

    Returns silently when `errcode == "OK"`. Otherwise raises
    `DevOpsApiError` with the response's `errmsg` as the message.
    """
    if isinstance(payload, dict) and payload.get("errcode") == "OK":
        return
    errmsg = ""
    if isinstance(payload, dict) and isinstance(payload.get("errmsg"), str):
        errmsg = payload["errmsg"]
    raise DevOpsApiError(errmsg)


class DevOpsClient:
    def __init__(self) -> None:
        token = os.getenv("DEVOPS_API_TOKEN", "").strip()
        if not token:
            raise DevOpsApiError("DEVOPS_API_TOKEN is required")
        environment = os.getenv("DEVOPS_ENV", "prod").strip().lower()
        base_domain = os.getenv("DEVOPS_BASE_DOMAIN", "").strip().rstrip("/")
        if not base_domain:
            try:
                base_domain = ENVIRONMENTS[environment]
            except KeyError as exc:
                raise DevOpsApiError(
                    "DEVOPS_ENV must be one of: dev, prod, staging"
                ) from exc
        try:
            timeout = float(os.getenv("DEVOPS_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise DevOpsApiError("DEVOPS_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0:
            raise DevOpsApiError("DEVOPS_TIMEOUT_SECONDS must be greater than zero")
        self.api_root = f"{base_domain}/api/v1"
        self.base_url = f"{self.api_root}/devops"
        self.token = token
        self.timeout = timeout

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        prefix: str = "/devops",
    ) -> Any:
        normalized_path = "/" + path.lstrip("/")
        url = f"{self.api_root}{prefix}{normalized_path}"
        headers = {
            "Accept": "application/json",
            "X-API-TOKEN": self.token,
        }
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=self.timeout
            ) as client:
                response = await client.request(method, url, params=params, json=body)
        except httpx.HTTPError as exc:
            raise DevOpsApiError(f"DevOps API request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if not 200 <= response.status_code < 300:
            message = "request failed"
            if isinstance(payload, dict) and isinstance(payload.get("errmsg"), str):
                message = payload["errmsg"]
            raise DevOpsApiError(
                f"DevOps API returned HTTP {response.status_code}: {message}"
            )
        return payload


def _client() -> DevOpsClient:
    return DevOpsClient()


class JumpDestinationsResponse(TypedDict):
    trace_id: str
    destinations: list[str] | None


class CreateJumpAuthorizationResponse(TypedDict):
    trace_id: str
    authorization_id: int


class JumpAuthorization(TypedDict, total=False):
    id: int
    applicant: str
    reason: str
    privilege: str
    expiration: str
    enable_gateway_access: bool
    phase: str
    approval_id: int
    created_at: int
    closed_at: int | None
    expire_at: int | None
    jumps: list[dict[str, Any]]


class GetJumpAuthorizationResponse(TypedDict):
    trace_id: str
    authorization: JumpAuthorization


class ListJumpAuthorizationsResponse(TypedDict):
    trace_id: str
    total: int
    items: list[JumpAuthorization]


class UserProfile(TypedDict):
    operatorName: str
    operatorType: str
    userEmail: str


mcp = MCPServer("devops-jump", log_level="CRITICAL")


@mcp.tool()
async def list_jump_destinations(
    destination_type: Literal[
        "controlplane",
        "dedicated",
        "serverless",
        "byoc",
        "premium",
        "premium_resource_pool",
    ],
) -> JumpDestinationsResponse:
    """List available destination IDs for controlplane or serverless jumps.

    The `destinations` field is `None` when no destinations are available
    for the requested `destination_type`.
    """
    payload = await _client().request("GET", f"/jump/destination/{destination_type}")
    _check_ok(payload)
    return {
        "trace_id": payload["trace_id"],
        "destinations": payload.get("destinations"),
    }


@mcp.tool()
async def create_jump_authorization(
    destination_type: Literal[
        "controlplane",
        "dedicated",
        "serverless",
        "byoc",
        "premium",
        "premium_resource_pool",
    ],
    destinations: list[str],
    privilege: Literal["read_only", "read_write", "admin"] = "read_only",
    expiration: Literal["1h", "3h", "6h", "12h", "24h", "3d", "7d", "30d"] = "12h",
    reason: str = "oncall",
    enable_gateway_access: bool = True,
) -> CreateJumpAuthorizationResponse:
    """Create a Jump authorization request for one or more clusters.

    Returns the created `authorization_id` on success.
    """
    destinations = [
        destination.strip() for destination in destinations if destination.strip()
    ]
    if not destinations:
        raise ValueError("destinations must contain at least one cluster ID or name")
    if expiration == "30d" and destination_type != "controlplane":
        raise ValueError("30d expiration is only supported for controlplane")
    if not reason.strip():
        raise ValueError("reason must not be empty")
    body = {
        "destination_type": destination_type,
        "destinations": destinations,
        "expiration": expiration,
        "privilege": privilege,
        "enable_gateway_access": enable_gateway_access,
        "reason": reason.strip(),
    }
    payload = await _client().request("POST", "/jump/authorizations", body=body)
    _check_ok(payload)
    return {
        "trace_id": payload["trace_id"],
        "authorization_id": payload["authorization_id"],
    }


@mcp.tool()
async def list_jump_authorizations() -> ListJumpAuthorizationsResponse:
    """List Jump authorization requests created by the API token owner.

    Filters by the owner's email and returns the first page (up to 100
    records). Server-side `show_unaccessible` is fixed to `false`.
    """
    profile = await _client().request("GET", "/me/profile", prefix="/core")
    params: dict[str, Any] = {
        "applicant": profile["userEmail"],
        "show_unaccessible": "false",
        "page": 1,
        "per_page": 100,
    }
    payload = await _client().request("GET", "/jump/authorizations", params=params)
    _check_ok(payload)
    return {
        "trace_id": payload["trace_id"],
        "total": payload["total"],
        "items": payload["items"],
    }


@mcp.tool()
async def get_jump_authorization(
    authorization_id: int,
) -> GetJumpAuthorizationResponse:
    """Get the current phase and details of a Jump authorization."""
    if authorization_id < 1:
        raise ValueError("authorization_id must be greater than zero")
    payload = await _client().request("GET", f"/jump/authorizations/{authorization_id}")
    _check_ok(payload)
    return {"trace_id": payload["trace_id"], "authorization": payload["authorization"]}


@mcp.tool()
async def wait_for_jump_authorization(
    authorization_id: int,
    timeout_seconds: int = 600,
    poll_interval_seconds: int = 10,
) -> GetJumpAuthorizationResponse | None:
    """Poll a Jump authorization until it reaches a terminal phase.

    Returns the latest `GetJumpAuthorizationResponse` on success, or `None`
    if `timeout_seconds` elapsed before a terminal phase was reached.

    API errors propagate immediately (no retry); the timeout is the only
    path that returns `None`.
    """
    if authorization_id < 1:
        raise ValueError("authorization_id must be greater than zero")
    if timeout_seconds < 1 or timeout_seconds > 3600:
        raise ValueError("timeout_seconds must be between 1 and 3600")
    if poll_interval_seconds < 1 or poll_interval_seconds > 60:
        raise ValueError("poll_interval_seconds must be between 1 and 60")

    started = time.monotonic()
    while True:
        latest = await get_jump_authorization(authorization_id)
        if _phase(latest) in TERMINAL_PHASES:
            return latest
        if time.monotonic() - started >= timeout_seconds:
            return None
        await asyncio.sleep(poll_interval_seconds)


@mcp.tool()
async def get_jump_connection(
    authorization_id: int,
    output_path: str,
) -> None:
    """Save the gateway kubeconfig for a Jump authorization to disk.

    `output_path` must be a non-empty filesystem path; the file is created
    with permissions `0o600`. Raises if the authorization does not have a
    gateway kubeconfig yet (e.g. it has not reached the `created` phase).
    """
    if authorization_id < 1:
        raise ValueError("authorization_id must be greater than zero")
    if not output_path:
        raise ValueError("output_path must not be empty")

    payload = await _client().request(
        "GET",
        f"/jump/authorizations/{authorization_id}/connection",
        params={"method": "gateway"},
    )
    _check_ok(payload)

    kubeconfig = _gateway_kubeconfig(payload)
    if kubeconfig is None:
        raise DevOpsApiError("gateway kubeconfig is missing")

    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kubeconfig, encoding="utf-8")
    path.chmod(0o600)


@mcp.tool()
async def cancel_jump_authorization(
    authorization_id: int,
    force: bool = False,
) -> None:
    """Cancel a Jump authorization request."""
    if authorization_id < 1:
        raise ValueError("authorization_id must be greater than zero")
    payload = await _client().request(
        "POST",
        f"/jump/authorizations/{authorization_id}/cancel",
        body={"force": force},
    )
    _check_ok(payload)


@mcp.tool()
async def get_me_profile() -> UserProfile:
    """Return the profile of the API token's owner (operator).

    Includes `operatorName`, `operatorType` (e.g. `api_token`), `userEmail`,
    the aggregated role scope, and the per-role scopes.
    """
    return await _client().request("GET", "/me/profile", prefix="/core")


def main() -> None:
    parser = argparse.ArgumentParser(description="TiDB Cloud DevOps Jump MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "stdio":
        if args.host != "127.0.0.1" or args.port != 8000:
            parser.error("--host and --port are only valid with HTTP transports")
        mcp.run(transport="stdio")
        return

    mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
