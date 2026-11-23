"""Provision the Javorna demo personas: create Adam and Piotr, retitle Emma only.

Tomas, Ladislav and the Otto agent user are never modified. Emma is the existing
account created for the earlier demo; only her job title is changed.
"""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any

from scripts.penta_users import (
    LICENSES, PROTECTED_UPN, PROTECTED_USER_ID, TENANT_ID, get_user, protect_password, snapshot,
)
from scripts.provision_agent365_instance import GraphClient
from scripts.setup_agent365 import load_json, write_json
from scripts.tf_helpers import REPO_ROOT

DOMAIN = "tomasonline.net"
NEW_PEOPLE = (
    ("adam.vavra", "Adam Vávra", "Group CFO", "CZ"),
    ("piotr.nowak", "Piotr Nowak", "Managing Director, Poland", "PL"),
)
EMMA = ("emma", "Emma Hartmann", "Group FP&A Lead")
NEVER_TOUCH = ("ladislav", "otto")
STATE_PATH = REPO_ROOT / ".local" / "javorna" / "users.json"
PENTA_STATE_PATH = REPO_ROOT / ".local" / "penta" / "users.json"


def upn(nickname: str) -> str:
    return f"{nickname}@{DOMAIN}"


def owned(user: dict[str, Any], nickname: str, state: dict[str, Any]) -> None:
    if user["id"] == PROTECTED_USER_ID or user["userPrincipalName"].lower() == PROTECTED_UPN:
        raise ValueError("Tomas is protected: no account mutation is permitted.")
    if user["id"] != state["users"].get(nickname, {}).get("id"):
        raise ValueError(f"{nickname} exists but is not owned by this prototype; refusing to alter it.")
    if user["userPrincipalName"].lower() != upn(nickname):
        raise ValueError("Stored demo account and live principal name differ.")


def provision(graph: GraphClient, *, execute: bool, credential_dir: Path) -> dict[str, Any]:
    if [org["id"] for org in graph.request("GET", "/organization?$select=id")["value"]] != [TENANT_ID]:
        raise ValueError("Refusing to provision outside the identified demo tenant.")
    protected = {"tomas": get_user(graph, PROTECTED_USER_ID)}
    protected.update({name: get_user(graph, upn(name)) for name in NEVER_TOUCH})
    if not protected["tomas"] or protected["tomas"]["userPrincipalName"].lower() != PROTECTED_UPN:
        raise ValueError("The protected Tomas account could not be verified.")
    before = {name: snapshot(user) for name, user in protected.items() if user}
    penta = load_json(PENTA_STATE_PATH)
    emma = graph.request("GET", f"/users/{upn(EMMA[0])}?$select=id,userPrincipalName,jobTitle")
    if emma["id"] != penta["users"]["emma"]["id"]:
        raise ValueError("Emma is not the account created by the earlier demo; refusing to retitle it.")
    state = load_json(STATE_PATH) if STATE_PATH.exists() else {"users": {}, "protected": before}
    if state["protected"] != before:
        raise ValueError("A protected account changed since the first snapshot; inspect before proceeding.")
    skus = {item["skuPartNumber"]: item for item in graph.request("GET", "/subscribedSkus")["value"]}
    users = {name: get_user(graph, upn(name)) for name, *_ in NEW_PEOPLE}
    for name, user in users.items():
        if user:
            owned(user, name, state)
    for sku_name in LICENSES:
        sku = skus[sku_name]
        needed = sum(
            not user or sku["skuId"] not in {item["skuId"] for item in user.get("assignedLicenses", [])}
            for user in users.values()
        )
        if sku["prepaidUnits"]["enabled"] - sku["consumedUnits"] < needed:
            raise ValueError(f"Insufficient existing {sku_name} licenses; no purchases are permitted.")
    plan = {
        "execute": execute,
        "create": [{"upn": upn(n), "displayName": d, "jobTitle": t, "usageLocation": c} for n, d, t, c in NEW_PEOPLE],
        "retitle": {"upn": upn(EMMA[0]), "from": emma.get("jobTitle"), "to": EMMA[2]},
        "licenses": list(LICENSES),
        "unchanged": sorted(before),
        "passwordStorage": "Windows DPAPI, operator-only, force change on first sign-in",
    }
    if not execute:
        return plan
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(STATE_PATH, state)
    for name, display, title, country in NEW_PEOPLE:
        user = users[name]
        if not user:
            password = "Jv!" + secrets.token_urlsafe(27) + "8z"
            password_path = credential_dir / f"{name}.password.dpapi"
            protect_password(password_path, password)
            user = graph.request("POST", "/users", body={
                "accountEnabled": True,
                "displayName": display,
                "mailNickname": name.replace(".", ""),
                "userPrincipalName": upn(name),
                "usageLocation": country,
                "jobTitle": title,
                "passwordProfile": {"password": password, "forceChangePasswordNextSignIn": True},
            })
            del password
            state["users"][name] = {"id": user["id"], "upn": user["userPrincipalName"],
                                    "passwordFile": str(password_path.resolve())}
            write_json(STATE_PATH, state)
            user = get_user(graph, user["id"])
            if user is None:
                raise RuntimeError(f"Created account {name} is not readable yet; rerun later.")
        owned(user, name, state)
        assigned = {item["skuId"] for item in user.get("assignedLicenses", [])}
        missing = [{"skuId": skus[s]["skuId"], "disabledPlans": []} for s in LICENSES if skus[s]["skuId"] not in assigned]
        if missing:
            graph.request("POST", f"/users/{user['id']}/assignLicense",
                          body={"addLicenses": missing, "removeLicenses": []})
        verified = get_user(graph, user["id"])
        actual = {item["skuId"] for item in (verified or {}).get("assignedLicenses", [])}
        if not all(skus[s]["skuId"] in actual for s in LICENSES):
            raise RuntimeError(f"License verification failed for {name}.")
        state["users"][name]["verified"] = snapshot(verified)
        write_json(STATE_PATH, state)
    if emma.get("jobTitle") != EMMA[2]:
        graph.request("PATCH", f"/users/{emma['id']}", body={"jobTitle": EMMA[2]})
    state["users"]["emma"] = {"id": emma["id"], "upn": emma["userPrincipalName"], "jobTitle": EMMA[2],
                              "passwordFile": penta["users"]["emma"]["passwordFile"]}
    write_json(STATE_PATH, state)
    after = {"tomas": get_user(graph, PROTECTED_USER_ID)}
    after.update({name: get_user(graph, upn(name)) for name in NEVER_TOUCH})
    if {name: snapshot(user) for name, user in after.items() if user} != before:
        raise RuntimeError("Protected account verification failed.")
    return {**plan, "users": state["users"], "protectedUnchanged": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--credential-dir", type=Path, required=True)
    args = parser.parse_args()
    result = provision(GraphClient.from_az_cli(), execute=args.execute, credential_dir=args.credential_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
