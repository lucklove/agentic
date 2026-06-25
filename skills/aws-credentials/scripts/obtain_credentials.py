#!/usr/bin/env -S uv run -qs
# /// script
# requires-python = ">=3.14"
# dependencies = ["nicegui>=2.0"]
# ///
"""Obtain AWS OIDC credentials by triggering a GitHub Actions workflow,
extracting the PGP-encrypted token from logs, decrypting it, and writing
it to ~/.oidc/id-token.

Usage:
  obtain_credentials.py --reason "..." --usage "..."
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = "tidbcloud/autonomous-airflow"
WORKFLOW = "show-oidc-token.yaml"
TOKEN_DIR = Path.home() / ".oidc"
TOKEN_PATH = TOKEN_DIR / "id-token"


def confirm(message: str, detail: str = "") -> bool:
    from nicegui import app, ui

    result = {"confirmed": False}

    def on_confirm():
        result["confirmed"] = True
        ui.run_javascript("window.close()")
        app.shutdown()

    def on_cancel():
        ui.run_javascript("window.close()")
        app.shutdown()

    @ui.page("/")
    def index():
        with ui.column().classes("items-center justify-center w-full h-screen"):
            with ui.card():
                ui.label(message)
                if detail:
                    ui.html(detail)
                with ui.row():
                    ui.button("取消", on_click=on_cancel).props("outline")
                    ui.button("确认", on_click=on_confirm)

    ui.run(title=message, reload=False, show=True, show_welcome_message=False)
    return result["confirmed"]


def setup_token(encrypted_token: str) -> None:
    p = subprocess.run(
        ["gpg", "-d"],
        input=encrypted_token.encode(),
        capture_output=True,
    )
    if p.returncode != 0:
        print(f"gpg decryption failed: {p.stderr.decode()}", file=sys.stderr)
        sys.exit(1)

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_bytes(p.stdout)
    print(f"Token written to {TOKEN_PATH}")


def trigger_workflow() -> str:
    """Trigger the workflow and return the run ID."""
    print("Triggering workflow...")
    subprocess.run(
        ["gh", "workflow", "run", WORKFLOW, "--repo", REPO],
        check=True,
    )

    # Wait a moment for the run to register
    time.sleep(5)

    # Get the most recent run ID
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            REPO,
            "--workflow",
            WORKFLOW,
            "--limit",
            "1",
            "--json",
            "databaseId,status",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    runs = json.loads(result.stdout)
    if not runs:
        print("Error: no workflow runs found after triggering.", file=sys.stderr)
        sys.exit(1)

    return str(runs[0]["databaseId"])


def wait_for_completion(run_id: str, timeout: int = 300) -> None:
    """Wait for the workflow run to complete."""
    print(f"Waiting for run {run_id} to complete...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            [
                "gh",
                "run",
                "view",
                run_id,
                "--repo",
                REPO,
                "--json",
                "status,conclusion",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(result.stdout)
        status = info.get("status", "")
        conclusion = info.get("conclusion", "")

        if status == "completed":
            if conclusion == "success":
                print("Workflow completed successfully.")
                return
            else:
                print(
                    f"Workflow completed with conclusion: {conclusion}", file=sys.stderr
                )
                sys.exit(1)

        time.sleep(10)

    print("Timeout waiting for workflow to complete.", file=sys.stderr)
    sys.exit(1)


def extract_pgp_message(run_id: str) -> str:
    """Extract the PGP encrypted message from workflow logs."""
    result = subprocess.run(
        ["gh", "run", "view", run_id, "--repo", REPO, "--log"],
        capture_output=True,
        text=True,
        check=True,
    )
    log = result.stdout

    # Extract PGP block, then strip any gpg: lines that may be mixed in
    match = re.search(
        r"-----BEGIN PGP MESSAGE-----.*?-----END PGP MESSAGE-----",
        log,
        re.DOTALL,
    )
    if not match:
        print("Error: PGP message not found in workflow logs.", file=sys.stderr)
        sys.exit(1)

    raw_block = match.group(0)

    # The log format has a prefix per line (job name + step + timestamp),
    # extract just the payload after the last tab on each line
    lines = raw_block.split("\n")
    cleaned_lines = []
    for line in lines:
        # Strip log prefix: everything up to and including the last tab
        if "\t" in line:
            line = line.rsplit("\t", 1)[-1]
        # Strip ANSI timestamp prefix (e.g., "2026-06-24T13:32:49.9630548Z ")
        line = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*", "", line)
        # Skip gpg: diagnostic lines
        if line.startswith("gpg:"):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Obtain AWS OIDC credentials")
    parser.add_argument("--reason", required=True, help="Why credentials are needed")
    parser.add_argument("--usage", required=True, help="How credentials will be used")
    args = parser.parse_args()

    # Step 1: Confirm with user
    detail = (
        f"<p><b>原因：</b>{args.reason}</p>"
        f"<p><b>用途：</b>{args.usage}</p>"
        f"<p>将触发 GitHub Actions workflow 获取 OIDC token，"
        f"解密后写入 <code>~/.oidc/id-token</code></p>"
    )
    if not confirm("获取 AWS Credentials", detail):
        print("User cancelled. Aborting.")
        sys.exit(0)

    # Step 2: Trigger workflow and wait
    run_id = trigger_workflow()
    wait_for_completion(run_id)

    # Step 3: Extract PGP message from logs
    encrypted_token = extract_pgp_message(run_id)
    print("Extracted PGP-encrypted token from logs.")

    # Step 4: Decrypt and write token
    setup_token(encrypted_token)

    # Step 5: Verify
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"AWS identity verified: {result.stdout.strip()}")
    else:
        print(
            f"Warning: aws sts get-caller-identity still fails: {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
