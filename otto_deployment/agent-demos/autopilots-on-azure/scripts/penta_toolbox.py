"""Provision and independently probe Otto's keyless Web IQ toolbox."""

from __future__ import annotations

import argparse
import asyncio
import json
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from azure.identity import AzureCliCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from scripts.setup_agent365 import load_json, write_json
from scripts.tf_helpers import REPO_ROOT


CONFIG = Path(__file__).resolve().parents[3] / "prototype.toml"
STATE = REPO_ROOT / ".local" / "otto" / "toolbox.json"
WEBIQ_ENDPOINT = "https://api.microsoft.ai/v3/mcp"
CONNECTION_NAME = "otto-webiq"


def settings() -> dict[str, Any]:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def connection_properties() -> dict[str, Any]:
    return {
        "authType": "ProjectManagedIdentity",
        "category": "RemoteTool",
        "target": WEBIQ_ENDPOINT,
        "isSharedToAll": False,
        "audience": "https://api.microsoft.ai",
        "metadata": {"ApiType": "Azure"},
    }


def toolbox_tools(connection_id: str) -> list[dict[str, Any]]:
    return [{
        "type": "mcp",
        "server_label": "webiq",
        "server_url": WEBIQ_ENDPOINT,
        "project_connection_id": connection_id,
        "require_approval": "never",
        "allowed_tools": ["web", "news"],
        "description": (
            "Search public web and news with Web IQ. Never include confidential "
            "documents, private messages, or personal data in a search query."
        ),
    }]


def is_web_tool(name: str) -> bool:
    return name in {"web", "webiq.web", "webiq___web"}


def checked_json(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} failed with "
            f"HTTP {response.status_code}: {response.text[:1500]}"
        )
    return response.json()


def provision(*, execute: bool) -> dict[str, Any]:
    config = settings()
    azure = config["azure"]
    project_id = (
        f"/subscriptions/{azure['subscription_id']}/resourceGroups/{azure['resource_group']}"
        "/providers/Microsoft.CognitiveServices/accounts/autopilots-ehvw/projects/autopilots-project"
    )
    connection_id = f"{project_id}/connections/{CONNECTION_NAME}"
    plan = {
        "execute": execute,
        "connectionId": connection_id,
        "connectionProperties": connection_properties(),
        "toolboxName": azure["toolbox_name"],
        "tools": toolbox_tools(connection_id),
        "requiredExternalBinding": (
            "Bind the Foundry project managed identity's application/client ID to "
            "the existing tomwebiq profile before this connection can call Web IQ."
        ),
    }
    if not execute:
        return plan
    deadline = datetime.fromisoformat(config["prototype"]["deadline_utc"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) >= deadline:
        raise RuntimeError("The authorized 48-hour provisioning window has ended.")
    if STATE.exists():
        return load_json(STATE)
    credential = AzureCliCredential(tenant_id=azure["tenant_id"], process_timeout=60)
    with httpx.Client(timeout=60) as client:
        arm_headers = {"Authorization": f"Bearer {credential.get_token('https://management.azure.com/.default').token}"}
        connection_url = f"https://management.azure.com{connection_id}?api-version=2025-10-01-preview"
        existing = client.get(connection_url, headers=arm_headers)
        if existing.status_code == 404:
            checked_json(client.put(
                connection_url, headers=arm_headers,
                json={"name": CONNECTION_NAME, "properties": connection_properties()},
            ))
        else:
            actual = checked_json(existing)["properties"]
            if any(actual.get(key) != value for key, value in connection_properties().items() if key != "metadata"):
                raise ValueError("An existing connection differs; refusing to overwrite it.")
        data_headers = {"Authorization": f"Bearer {credential.get_token('https://ai.azure.com/.default').token}"}
        toolbox_url = f"{azure['foundry_project']}/toolboxes/{azure['toolbox_name']}"
        existing = client.get(f"{toolbox_url}?api-version=v1", headers=data_headers)
        if existing.status_code != 404:
            checked_json(existing)
            raise ValueError("A toolbox already exists without a local receipt; inspect before creating a version.")
        version = checked_json(client.post(
            f"{toolbox_url}/versions?api-version=v1", headers=data_headers,
            json={
                "description": "Otto public research; keyless Web IQ, web and news only.",
                "tools": toolbox_tools(connection_id),
            },
        ))
    result = {
        **plan,
        "version": version["version"],
        "endpoint": f"{toolbox_url}/versions/{version['version']}/mcp?api-version=v1",
        "verified": False,
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    write_json(STATE, result)
    return result


async def probe() -> dict[str, Any]:
    state = load_json(STATE)
    credential = AzureCliCredential(tenant_id=settings()["azure"]["tenant_id"], process_timeout=60)
    token = credential.get_token("https://ai.azure.com/.default").token
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=120
    ) as client:
        async with streamable_http_client(state["endpoint"], http_client=client) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120)) as session:
                await session.initialize()
                available = await session.list_tools()
                tools = [tool.model_dump(mode="json", exclude_none=True) for tool in available.tools]
    state["toolsObserved"] = tools
    state["verified"] = False
    state["lastProbeUtc"] = datetime.now(timezone.utc).isoformat()
    write_json(STATE, state)
    names = {tool["name"] for tool in tools}
    if not any(is_web_tool(name) for name in names):
        raise RuntimeError(
            f"Toolbox returned no Web IQ web tool ({sorted(names)}). "
            "The downstream binding/authentication is not verified; do not enable it on Otto."
        )
    return {"endpoint": state["endpoint"], "tools": tools, "liveSearchVerified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["provision", "probe"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(probe()) if args.command == "probe" else provision(execute=args.execute)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
