"""Bounded real operator tests and scene-by-scene input staging for Otto."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from azure.containerapps.sandbox import SandboxGroupClient
from azure.identity import AzureCliCredential

from scripts.operator_client import invoke_response
from scripts.penta_toolbox import CONFIG, settings
from scripts.sandbox_runtime import endpoint_for_region
from scripts.sandbox_services import DISK_IMAGE_AUDIENCE, require_exec_success
from scripts.setup_agent365 import load_json
from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import REPO_ROOT


STATE = REPO_ROOT / ".local" / "otto" / "validation.sqlite3"
REMOTE_ROOT = "/data/hermes/workspace/budget-season"
# In-world assets per demo scene, relative to `content.agent_assets`. The budget-season assets are not built yet.
SCENES: dict[str, tuple[str, ...]] = {}
# Mirrors the gateway allowlist in bridge.assignments.REHEARSAL_PERSONAS; the gateway remains the authority.
PERSONAS = ("adam", "emma", "piotr")


def require_window(config: dict[str, Any], now: datetime | None = None) -> None:
    deadline = datetime.fromisoformat(config["prototype"]["deadline_utc"].replace("Z", "+00:00"))
    if (now or datetime.now(timezone.utc)) >= deadline:
        raise RuntimeError("The authorized 48-hour execution window has ended.")


def reserve_turn(
    path: Path, config: dict[str, Any], label: str, conversation: str, message: str, persona: str = "",
    teams_reference: str = "",
) -> dict[str, Any]:
    require_window(config)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", label):
        raise ValueError("Use a bounded lowercase test label.")
    if not message.strip() or not conversation.startswith("otto-operator-"):
        raise ValueError("A real message and an explicitly labelled operator conversation are required.")
    if persona and persona not in PERSONAS:
        raise ValueError("Unknown rehearsal persona.")
    if teams_reference and not re.fullmatch(r"teams-[0-9a-f]{32}", teams_reference):
        raise ValueError("Use a stored Teams delivery reference key.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS turns "
            "(label TEXT PRIMARY KEY, request_id TEXT UNIQUE, body TEXT, status TEXT, result TEXT, created_utc TEXT)"
        )
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM turns WHERE label=?", (label,)).fetchone():
            raise ValueError("This test label already has a durable request. Inspect or poll it; do not resubmit.")
        if db.execute("SELECT 1 FROM turns WHERE status='pending'").fetchone():
            raise RuntimeError("A previous real request is pending. Resolve it before another test.")
        count = db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        if count >= config["limits"]["maximum_live_test_turns"]:
            raise RuntimeError("The live-test turn ceiling has been reached.")
        body = {"requestId": uuid.uuid4().hex, "conversationId": conversation, "message": message}
        if persona:
            body["rehearsalPersona"] = persona
        if teams_reference:
            body["deliveryReferenceKey"] = teams_reference
        db.execute(
            "INSERT INTO turns VALUES (?, ?, ?, 'pending', NULL, ?)",
            (label, body["requestId"], json.dumps(body), datetime.now(timezone.utc).isoformat()),
        )
    return body


def save_result(label: str, response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    with closing(sqlite3.connect(STATE)) as db:
        expected = db.execute("SELECT request_id FROM turns WHERE label=?", (label,)).fetchone()
    if expected is None or (
        response.status_code in {200, 202} or payload.get("operationStatus")
    ) and payload.get("requestId") != expected[0]:
        raise ValueError("The live result does not match the reserved operator request.")
    terminal = response.status_code == 200 or payload.get("operationStatus") == "failed"
    if terminal:
        with closing(sqlite3.connect(STATE)) as db, db:
            db.execute(
                "UPDATE turns SET status=?, result=? WHERE label=?",
                ("completed" if response.status_code == 200 else "failed", json.dumps(payload), label),
            )
    response.raise_for_status()
    return {"source": "live-operator-api", "label": label, "httpStatus": response.status_code, "result": payload}


def invoke(label: str, conversation: str, message: str, persona: str = "", teams_reference: str = "") -> dict[str, Any]:
    config = settings()
    outputs = load_json(runtime_outputs_path("hermes", "otto"))
    tfvars = load_json(runtime_app_tfvars_path("hermes", "otto"))
    if outputs["worker_id"] != "otto":
        raise ValueError("Captured deployment belongs to another worker.")
    body = reserve_turn(STATE, config, label, conversation, message, persona, teams_reference)
    with httpx.Client(timeout=60) as client:
        response = invoke_response(
            client, outputs["bridge_url"].rstrip("/") + "/invoke", body=body,
            headers={"X-Autopilot-Key": tfvars["api_server_key"]},
            timeout=config["limits"]["maximum_test_duration_seconds"],
        )
    return save_result(label, response)


def poll(label: str) -> dict[str, Any]:
    with closing(sqlite3.connect(STATE)) as db:
        row = db.execute("SELECT request_id FROM turns WHERE label=?", (label,)).fetchone()
    if row is None:
        raise ValueError("Unknown test label.")
    outputs = load_json(runtime_outputs_path("hermes", "otto"))
    tfvars = load_json(runtime_app_tfvars_path("hermes", "otto"))
    with httpx.Client(timeout=60) as client:
        response = client.get(
            outputs["bridge_url"].rstrip("/") + "/invocations/" + row[0],
            headers={"X-Autopilot-Key": tfvars["api_server_key"]},
        )
    return save_result(label, response)


def scene_files(scene: str) -> list[tuple[str, Path]]:
    root = (CONFIG.parent / settings()["content"]["agent_assets"]).resolve()
    result = []
    for relative in SCENES[scene]:
        path = root.joinpath(*relative.split("/")).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Missing or unsafe in-world asset: {relative}")
        result.append((relative, path))
    return result


def stage(scene: str) -> dict[str, Any]:
    config = settings()
    require_window(config)
    outputs = load_json(runtime_outputs_path("hermes", "otto"))
    if outputs["worker_id"] != "otto":
        raise ValueError("Captured deployment belongs to another worker.")
    receipts = []
    with AzureCliCredential(tenant_id=config["azure"]["tenant_id"], process_timeout=150) as credential:
        with SandboxGroupClient(
            endpoint_for_region(outputs["sandbox_location"]), credential,
            subscription_id=outputs["subscription_id"], resource_group=outputs["resource_group_name"],
            sandbox_group=outputs["sandbox_groups"]["runtime"]["name"], audience=DISK_IMAGE_AUDIENCE,
        ) as group:
            sandbox = group.get_sandbox_client(outputs["runtime_sandbox_id"])
            sandbox.ensure_running(timeout=600)
            for relative, local in scene_files(scene):
                remote = REMOTE_ROOT + "/" + relative
                content = local.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                check = (
                    "import pathlib,hashlib; "
                    f"p=pathlib.Path({remote!r}); "
                    "p.parent.mkdir(parents=True,exist_ok=True); "
                    "print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'missing')"
                )
                observed = sandbox.exec("python3 -c " + shlex.quote(check))
                require_exec_success(observed, "Checking staged asset")
                current = observed.stdout.strip()
                if current not in {"missing", digest}:
                    raise RuntimeError(f"Refusing to overwrite an agent-edited asset: {relative}")
                if current == "missing":
                    sandbox.write_file(remote, content, mode="0600")
                access_check = f"""
import os, pathlib, pwd
root = pathlib.Path({REMOTE_ROOT!r})
path = pathlib.Path({remote!r})
assert path.resolve().is_relative_to(root.resolve()), "Asset escaped its staging directory"
owner = pwd.getpwnam("hermes")
for directory in (path.parent, *path.parent.parents):
    if directory == root or directory.is_relative_to(root):
        os.chown(directory, owner.pw_uid, owner.pw_gid)
os.chown(path, owner.pw_uid, owner.pw_gid)
os.chmod(path, 0o600)
os.setgid(owner.pw_gid)
os.setuid(owner.pw_uid)
assert os.access(path, os.R_OK | os.W_OK), "Hermes cannot read and edit the asset"
assert os.access(path.parent, os.W_OK | os.X_OK), "Hermes cannot save files in the asset directory"
""".strip()
                observed = sandbox.exec("python3 -c " + shlex.quote(access_check))
                require_exec_success(observed, "Granting and verifying asset access as the Hermes service user")
                read_back = sandbox.read_file(remote)
                if hashlib.sha256(read_back).hexdigest() != digest:
                    raise RuntimeError(f"Staged asset hash mismatch: {relative}")
                receipts.append({"path": remote, "sha256": digest, "bytes": len(content)})
    receipt = REPO_ROOT / ".local" / "otto" / f"staged-{scene}.json"
    receipt.write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    return {"scene": scene, "source": "real-sandbox-files", "files": receipts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser("stage")
    staging.add_argument("scene", choices=SCENES)
    call = commands.add_parser("invoke")
    call.add_argument("--label", required=True)
    call.add_argument("--conversation", required=True)
    call.add_argument("--message", required=True)
    call.add_argument("--persona", choices=PERSONAS, default="", help="Allowlisted rehearsal persona")
    call.add_argument("--teams-reference", default="",
                      help="Stored Teams delivery reference; scheduled jobs created in the turn deliver there")
    polling = commands.add_parser("poll")
    polling.add_argument("label")
    args = parser.parse_args()
    if args.command == "stage":
        result = stage(args.scene)
    elif args.command == "poll":
        result = poll(args.label)
    else:
        result = invoke(args.label, args.conversation, args.message, args.persona, args.teams_reference)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
