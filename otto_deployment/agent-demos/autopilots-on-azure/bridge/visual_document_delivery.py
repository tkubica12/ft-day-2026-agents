from __future__ import annotations

import base64
import binascii
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from autopilots_identity.collaboration_mcp import (
    delete_drive_item, drive_coordinates, graph_request, invite_drive_item_user, upload_agent_results_copy,
)
from autopilots_identity.visual_artifact_staging import builder

from bridge.visual_artifacts import microsoft_url


START = "<VISUAL_DOCUMENT_REQUEST>"
END = "</VISUAL_DOCUMENT_REQUEST>"
MAX_BYTES = 10 * 1024 * 1024


def extract(response: str, expected_scope: str) -> tuple[str, bool]:
    if START not in response and END not in response:
        return response, False
    if response.count(START) != 1 or response.count(END) != 1:
        raise ValueError("Invalid visual document marker")
    start, end = response.index(START), response.index(END)
    if end < start or not expected_scope or response[start + len(START):end].strip() != expected_scope:
        raise ValueError("Visual document scope does not match the current turn")
    return (response[:start] + response[end + len(END):]).strip(), True


def aad_id(value: str) -> str:
    try:
        result = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("A valid authenticated Entra object ID is required") from exc
    return result


async def child_folder(parent: str, name: str) -> dict:
    base = "/me/drive/root" if not parent else f"/me/drive/items/{quote(parent, safe='')}"
    lookup = f"{base}:/{quote(name, safe='')}?$select=id,name,folder,parentReference"
    try:
        response = await graph_request("GET", lookup)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        try:
            response = await graph_request(
                "POST", f"{base}/children",
                json_body={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
            )
        except httpx.HTTPStatusError as create_error:
            if create_error.response.status_code != 409:
                raise
            response = await graph_request("GET", lookup)
    item = response.json()
    if not isinstance(item, dict) or not item.get("id") or not isinstance(item.get("folder"), dict):
        raise RuntimeError("Graph did not return the artifact folder")
    return item


async def check_permissions(item: dict, recipient: str, *, shared: bool) -> None:
    owner = aad_id(os.getenv("AGENT365_AGENT_USER_ID", ""))
    drive, item_id = drive_coordinates(item)
    response = await graph_request(
        "GET", f"/drives/{quote(drive, safe='')}/items/{quote(item_id, safe='')}/permissions",
    )
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), list) or payload.get("@odata.nextLink"):
        raise RuntimeError("Graph did not return a complete artifact permission inventory")
    found = False
    for permission in payload["value"]:
        if not isinstance(permission, dict) or permission.get("link"):
            raise RuntimeError("Artifact has an unexpected link permission")
        roles = permission.get("roles")
        if not isinstance(roles, list) or not roles:
            raise RuntimeError("Artifact permission has no roles")
        identities = []
        for field in ("grantedToV2", "grantedTo"):
            if permission.get(field):
                identities.append(permission[field])
                break
        if not identities:
            identities = permission.get("grantedToIdentitiesV2") or permission.get("grantedToIdentities") or []
        if not isinstance(identities, list) or not identities:
            raise RuntimeError("Artifact permission has no verifiable user identity")
        for identity in identities:
            if not isinstance(identity, dict) or set(identity) - {"user", "siteUser"}:
                raise RuntimeError("Artifact permission is not a user-only grant")
            user = identity.get("user") or identity.get("siteUser")
            if not isinstance(user, dict):
                raise RuntimeError("Artifact permission has no verifiable user")
            try:
                principal = aad_id(str(user.get("id") or ""))
            except ValueError as exc:
                raise RuntimeError("Artifact permission has no verifiable Entra object ID") from exc
            if principal == owner:
                continue
            if not shared or principal != recipient or set(roles) != {"read"}:
                raise RuntimeError("Artifact has an unexpected recipient or access role")
            found = True
    if shared and not found:
        raise RuntimeError("Graph did not confirm the requested read-only permission")


async def publish(result: dict, *, expected_scope: str, recipient: str) -> str:
    recipient = aad_id(recipient)
    if not isinstance(result, dict) or result.get("scope") != expected_scope:
        raise ValueError("Runtime artifact does not belong to this turn")
    trusted = builder()
    spec = trusted.validate(result.get("spec"))
    raw = result.get("contentBase64")
    if not isinstance(raw, str) or len(raw) > (MAX_BYTES * 4 // 3 + 8):
        raise ValueError("Invalid or oversized artifact")
    try:
        content = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid artifact encoding") from exc
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Invalid or oversized artifact")
    pdf = spec["kind"] == "brief"
    if pdf:
        if not content.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF artifact")
    elif content != trusted.expected_document(spec):
        raise ValueError("HTML artifact differs from the trusted html-docs bundle")

    root = await child_folder("", "Otto Artifacts")
    folder = await child_folder(str(root["id"]), datetime.now(UTC).strftime("%Y-%m-%d"))
    await check_permissions(folder, recipient, shared=False)
    suffix, mime = (".pdf", "application/pdf") if pdf else (".html", "text/html; charset=utf-8")
    with tempfile.TemporaryDirectory(prefix="otto-delivery-") as directory:
        local = Path(directory) / ("artifact" + suffix)
        local.write_bytes(content)
        item = await upload_agent_results_copy(
            local, mime, folder=folder, copy_name=f"Otto-{expected_scope}{suffix}",
        )
    try:
        url = microsoft_url(str(item.get("webUrl") or ""))
        await check_permissions(item, recipient, shared=False)
        await invite_drive_item_user(item, recipient, role="read")
        await check_permissions(item, recipient, shared=True)
    except Exception as exc:
        try:
            await delete_drive_item(item)
        except Exception as cleanup:
            raise RuntimeError(f"Artifact sharing failed and cleanup failed: {cleanup}") from exc
        raise
    return url
