from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile

import jwt
import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier

os.environ.setdefault("MCP_AUTH_MODE", "none")
os.environ.setdefault("BUDGET_STATE_PATH", str(Path(tempfile.mkdtemp()) / "budget.sqlite3"))

import budget_planning_mcp.server as server_module
from budget_planning_mcp.server import create_auth_provider


def test_http_app_disables_host_origin_protection_for_aca_ingress() -> None:
    assert server_module.app is not None
    source = Path(server_module.__file__).read_text(encoding="utf-8")
    assert source.count("host_origin_protection=False") == 2
    assert source.count('allowed_hosts=["*"]') == 2


@pytest.mark.asyncio
async def test_entra_agent_identity_uses_fastmcp_jwt_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_MODE", "entra_agent_identity")
    monkeypatch.setenv("MCP_JWT_SECRET", "test-secret")
    monkeypatch.setenv("MCP_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("MCP_JWT_ISSUER", "https://sts.windows.net/demo/")
    monkeypatch.setenv("MCP_JWT_AUDIENCE", "api://budget-planning-mcp")
    provider = create_auth_provider()
    token = jwt.encode(
        {
            "aud": "api://budget-planning-mcp",
            "iss": "https://sts.windows.net/demo/",
            "azp": "agent-identity-client-id",
            "oid": "agent-identity-object-id",
            "roles": ["Incidents.Read.All"],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "test-secret",
        algorithm="HS256",
    )

    assert isinstance(provider, JWTVerifier)
    access_token = await provider.verify_token(token)
    assert access_token is not None
    assert access_token.claims["oid"] == "agent-identity-object-id"


@pytest.mark.asyncio
async def test_mcp_lists_season_tools_with_on_behalf_of() -> None:
    tools = {tool.name: tool for tool in await server_module.mcp.list_tools()}

    assert {"season_overview", "validate_submission", "grant_exception", "model_capex_options", "board_pack_data"} <= set(tools)
    assert all("on_behalf_of" in tool.parameters["properties"] for name, tool in tools.items())
