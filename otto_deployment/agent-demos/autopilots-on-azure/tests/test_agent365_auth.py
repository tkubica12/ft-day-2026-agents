import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests
from microsoft_agents.activity import RoleTypes
from urllib3.exceptions import ProtocolError

import bridge.app as bridge_app
from bridge.agent365_auth import AssertionRetryMsalAuth


CONFIG = {
    "CONNECTIONS": {
        "SERVICE_CONNECTION": {
            "SETTINGS": {
                "CLIENTID": "blueprint",
                "TENANTID": "tenant",
                "AUTHTYPE": "FederatedCredentials",
                "FEDERATEDCLIENTID": "identity",
            }
        }
    }
}


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result.url = "http://identity.test/msi/token"
    return result


def reset():
    return requests.ConnectionError(ProtocolError("aborted", ConnectionResetError(104, "reset")))


class Agent365AssertionTests(unittest.TestCase):
    def run_gateway_auth(self, outcomes):
        state = {"identity_attempts": 0, "downstream": [], "timeouts": [], "handler_calls": 0, "admissions": 0}

        class FakeConfidentialClient:
            def __init__(self, *, client_credential, **_kwargs):
                self.credential = client_credential["client_assertion"]

            def acquire_token_for_client(self, *_args, **_kwargs):
                if callable(self.credential):
                    assertion = self.credential()
                    state["downstream"].append(("agent", assertion))
                    return {"access_token": "agent-token"}
                state["downstream"].append(("instance", self.credential))
                return {"access_token": "instance-token"}

        def send(_session, _request, **kwargs):
            state["identity_attempts"] += 1
            state["timeouts"].append(kwargs["timeout"])
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with (
            patch.object(bridge_app, "agent365_auth_configured", return_value=True),
            patch.object(bridge_app, "load_configuration_from_env", return_value=CONFIG),
            patch.dict(os.environ, {"IDENTITY_ENDPOINT": "http://identity.test/msi/token", "IDENTITY_HEADER": "test"}),
            patch("bridge.agent365_auth.ConfidentialClientApplication", FakeConfidentialClient),
            patch("microsoft_agents.authentication.msal.msal_auth.ConfidentialClientApplication", FakeConfidentialClient),
            patch("requests.Session.send", send),
            patch("bridge.agent365_auth.time.sleep") as sleep,
        ):
            agent, adapter = bridge_app.create_agent365_app()
            factory = adapter._channel_service_client_factory
            connection = factory._connection_manager.get_default_connection()
            self.assertIsInstance(connection, AssertionRetryMsalAuth)
            activity = SimpleNamespace(
                recipient=SimpleNamespace(role=RoleTypes.agentic_identity),
                get_agentic_instance_id=lambda: "instance",
                get_agentic_tenant_id=lambda: "tenant",
            )
            context = SimpleNamespace(identity=object(), activity=activity)
            async def process(_request, _agent):
                state["handler_calls"] += 1
                result = await factory._get_agentic_token(context, "https://service.test")
                state["admissions"] += 1
                return result

            async def receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}

            request = bridge_app.Request(
                {"type": "http", "method": "POST", "path": "/api/messages", "headers": []},
                receive,
            )
            try:
                with (
                    patch.object(bridge_app, "agent365_app", agent),
                    patch.object(bridge_app, "agent365_adapter", adapter),
                    patch.object(adapter, "process", side_effect=process),
                ):
                    token = asyncio.run(bridge_app.agent365_messages(request))
                error = None
            except Exception as exc:
                token, error = None, exc
            state["sleeps"] = [call.args[0] for call in sleep.call_args_list]
        return token, error, state

    def test_gateway_auth_retries_reset_only_before_downstream_exchange(self):
        token, error, state = self.run_gateway_auth([
            reset(),
            response({"access_token": "assertion", "expires_on": int(time.time()) + 3600}),
        ])
        self.assertIsNone(error)
        self.assertEqual(token, "instance-token")
        self.assertEqual(state["identity_attempts"], 2)
        self.assertEqual(state["sleeps"], [0.2])
        self.assertEqual(state["timeouts"], [(2, 3), (2, 3)])
        self.assertEqual(state["downstream"], [("agent", "assertion"), ("instance", "agent-token")])
        self.assertEqual((state["handler_calls"], state["admissions"]), (1, 1))

    def test_exhaustion_preserves_final_reset_and_does_not_exchange(self):
        last = reset()
        token, error, state = self.run_gateway_auth([reset(), reset(), last])
        self.assertIsNone(token)
        self.assertIs(error, last)
        self.assertEqual(state["identity_attempts"], 3)
        self.assertEqual(state["sleeps"], [0.2, 0.4])
        self.assertEqual(state["downstream"], [])
        self.assertEqual((state["handler_calls"], state["admissions"]), (1, 0))

    def test_other_connection_and_identity_failures_are_not_retried(self):
        for outcome in (
            requests.ConnectionError("unrelated connection failure"),
            response({"statusCode": 401, "message": "unauthorized"}, 401),
        ):
            with self.subTest(outcome=type(outcome).__name__):
                token, error, state = self.run_gateway_auth([outcome])
                self.assertIsNone(token)
                self.assertIsNotNone(error)
                self.assertEqual(state["identity_attempts"], 1)
                self.assertEqual(state["sleeps"], [])
                self.assertEqual(state["downstream"], [])
                self.assertEqual((state["handler_calls"], state["admissions"]), (1, 0))
