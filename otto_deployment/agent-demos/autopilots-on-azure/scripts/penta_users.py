"""Provision only the two human Penta demo accounts; never modify Tomas."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scripts.provision_agent365_instance import GraphClient, GraphError
from scripts.setup_agent365 import load_json, write_json
from scripts.tf_helpers import REPO_ROOT


TENANT_ID = "6ce4f237-667f-43f5-aafd-cbef954adf97"
PROTECTED_USER_ID = "e011e2a1-33d6-418f-b2ed-35f7d2281f45"
PROTECTED_UPN = "tomas@tomasonline.net"
PEOPLE = (
    ("ladislav", "Ladislav Lála", "Group CFO, budget owner (demo role)"),
    ("emma", "Emma Hartmann", "Group FP&A Lead (demo role)"),
)
LICENSES = ("Microsoft_365_E3_(no_Teams)", "Microsoft_Teams_Enterprise_New")
SELECT = "id,displayName,userPrincipalName,accountEnabled,assignedLicenses,usageLocation"
STATE_PATH = REPO_ROOT / ".local" / "penta" / "users.json"


def snapshot(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "userPrincipalName": user["userPrincipalName"],
        "displayName": user["displayName"],
        "accountEnabled": user["accountEnabled"],
        "licenses": sorted(item["skuId"] for item in user.get("assignedLicenses", [])),
    }


def get_user(graph: GraphClient, identifier: str) -> dict[str, Any] | None:
    try:
        return graph.request("GET", f"/users/{quote(identifier)}?$select={SELECT}")
    except GraphError as exc:
        if exc.status == 404:
            return None
        raise


def validate_owned_user(user: dict[str, Any], nickname: str, state: dict[str, Any]) -> None:
    expected = state.get("users", {}).get(nickname, {})
    if user["id"] == PROTECTED_USER_ID or user["userPrincipalName"].lower() == PROTECTED_UPN:
        raise ValueError("Tomas is protected: no account mutation is permitted.")
    if user["id"] != expected.get("id"):
        raise ValueError(f"{nickname} exists but is not owned by this prototype; refusing to alter it.")
    if user["userPrincipalName"].lower() != f"{nickname}@tomasonline.net":
        raise ValueError("Stored demo account and live principal name differ.")


def protect_password(path: Path, password: str) -> None:
    import win32crypt

    encrypted = win32crypt.CryptProtectData(
        password.encode("utf-16-le"), "Penta demo initial password", None, None, None, 1
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encrypted)


def provision(graph: GraphClient, *, execute: bool, credential_dir: Path) -> dict[str, Any]:
    organizations = graph.request("GET", "/organization?$select=id")["value"]
    if [org["id"] for org in organizations] != [TENANT_ID]:
        raise ValueError("Refusing to provision outside the identified demo tenant.")
    original = get_user(graph, PROTECTED_USER_ID)
    if not original or original["userPrincipalName"].lower() != PROTECTED_UPN:
        raise ValueError("The protected Tomas account could not be verified.")
    state = load_json(STATE_PATH) if STATE_PATH.exists() else {"users": {}, "tomas": snapshot(original)}
    if state["tomas"] != snapshot(original):
        raise ValueError("Tomas changed since the initial snapshot; inspect before proceeding.")
    skus = {
        item["skuPartNumber"]: item
        for item in graph.request("GET", "/subscribedSkus")["value"]
    }
    users = {name: get_user(graph, f"{name}@tomasonline.net") for name, _, _ in PEOPLE}
    for name, user in users.items():
        if user:
            validate_owned_user(user, name, state)
    for sku_name in LICENSES:
        sku = skus[sku_name]
        required = sum(
            not user or sku["skuId"] not in {item["skuId"] for item in user.get("assignedLicenses", [])}
            for user in users.values()
        )
        if sku["prepaidUnits"]["enabled"] - sku["consumedUnits"] < required:
            raise ValueError(f"Insufficient existing {sku_name} licenses; no purchases are permitted.")
    plan = {
        "execute": execute,
        "protectedAccount": snapshot(original),
        "accounts": [
            {"upn": f"{name}@tomasonline.net", "displayName": display, "role": role}
            for name, display, role in PEOPLE
        ],
        "licenses": list(LICENSES),
        "passwordStorage": "Windows DPAPI, operator-only, force change on first sign-in",
    }
    if not execute:
        return plan
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(STATE_PATH, state)
    for name, display, role in PEOPLE:
        user = users[name]
        if not user:
            password = "Ot!" + secrets.token_urlsafe(27) + "8z"
            password_path = credential_dir / f"{name}.password.dpapi"
            protect_password(password_path, password)
            user = graph.request(
                "POST", "/users",
                body={
                    "accountEnabled": True,
                    "displayName": display,
                    "mailNickname": name,
                    "userPrincipalName": f"{name}@tomasonline.net",
                    "usageLocation": "CZ",
                    "jobTitle": role,
                    "passwordProfile": {
                        "password": password,
                        "forceChangePasswordNextSignIn": True,
                    },
                },
            )
            del password
            state["users"][name] = {
                "id": user["id"],
                "upn": user["userPrincipalName"],
                "passwordFile": str(password_path.resolve()),
            }
            write_json(STATE_PATH, state)
            user = get_user(graph, user["id"])
            if user is None:
                raise RuntimeError(f"Created account {name} is not readable yet; retry provisioning later.")
        validate_owned_user(user, name, state)
        assigned = {item["skuId"] for item in user.get("assignedLicenses", [])}
        missing = [
            {"skuId": skus[sku]["skuId"], "disabledPlans": []}
            for sku in LICENSES if skus[sku]["skuId"] not in assigned
        ]
        if missing:
            graph.request(
                "POST", f"/users/{user['id']}/assignLicense",
                body={"addLicenses": missing, "removeLicenses": []},
            )
        verified = get_user(graph, user["id"])
        if verified is None:
            raise RuntimeError(f"Account {name} cannot be read after provisioning.")
        actual = {item["skuId"] for item in verified.get("assignedLicenses", [])}
        if not all(skus[sku]["skuId"] in actual for sku in LICENSES):
            raise RuntimeError(f"License verification failed for {name}.")
        state["users"][name]["verified"] = snapshot(verified)
        write_json(STATE_PATH, state)
    current = get_user(graph, PROTECTED_USER_ID)
    if current is None or snapshot(current) != state["tomas"]:
        raise RuntimeError("Protected Tomas account verification failed.")
    return {**plan, "users": state["users"], "tomasUnchanged": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--credential-dir", type=Path, required=True)
    args = parser.parse_args()
    result = provision(GraphClient.from_az_cli(), execute=args.execute, credential_dir=args.credential_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
