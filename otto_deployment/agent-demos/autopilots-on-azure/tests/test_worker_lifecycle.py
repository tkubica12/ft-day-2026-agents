import unittest
from unittest.mock import Mock

from scripts.worker_lifecycle import (
    bootstrap_agent365_config,
    endpoint_registration_needed,
    inheritance_complete,
    owned_sandbox_groups,
    redact_secrets,
    reconcile_blueprint_grants,
    release_pin_arguments,
    RELEASE_PIN_FLAGS,
    strip_blueprint_secrets,
    validate_state_name,
    worker_display_name,
)


class WorkerLifecycleTests(unittest.TestCase):
    def test_missing_grant_is_created_and_read_back_despite_cli_success(self):
        graph = Mock()
        graph.request.side_effect = [
            {"id": "client-sp", "appId": "blueprint"},
            {"id": "resource-sp", "appId": "resource", "displayName": "Resource",
             "oauth2PermissionScopes": [{"value": "Read", "isEnabled": True}]},
            {"value": []},
            {"id": "grant"},
            {"value": [{"scope": "Read"}]},
        ]
        reconcile_blueprint_grants(graph, "blueprint", {
            "resourceConsents": [{"resourceAppId": "resource", "scopes": ["Read"], "consentGranted": True}],
        })
        graph.request.assert_any_call("POST", "/oauth2PermissionGrants", body={
            "clientId": "client-sp", "resourceId": "resource-sp",
            "consentType": "AllPrincipals", "scope": "Read",
        })
        self.assertNotIn(" ", graph.request.call_args.args[1])

    def test_reconciliation_preserves_existing_scopes(self):
        graph = Mock()
        graph.request.side_effect = [
            {"id": "client-sp", "appId": "blueprint"},
            {"id": "resource-sp", "appId": "resource", "displayName": "Resource",
             "oauth2PermissionScopes": [{"value": "Read", "isEnabled": True}]},
            {"value": [{"id": "grant", "scope": "Existing"}]},
            {},
            {"value": [{"scope": "Existing Read"}]},
        ]
        reconcile_blueprint_grants(graph, "blueprint", {
            "resourceConsents": [{"resourceAppId": "resource", "scopes": ["Read"]}],
        })
        graph.request.assert_any_call(
            "PATCH", "/oauth2PermissionGrants/grant",
            body={"scope": "Existing Read"}, empty_ok=True,
        )

    def test_reconciliation_rejects_mismatched_resource(self):
        graph = Mock()
        graph.request.side_effect = [
            {"id": "client-sp", "appId": "blueprint"},
            {"id": "resource-sp", "appId": "wrong-resource"},
        ]
        with self.assertRaisesRegex(ValueError, "does not match"):
            reconcile_blueprint_grants(graph, "blueprint", {
                "resourceConsents": [{"resourceAppId": "resource", "scopes": ["Read"]}],
            })
        self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_state_name_is_a_safe_worker_identifier(self):
        self.assertEqual(validate_state_name("g08b"), "g08b")
        self.assertEqual(validate_state_name("demo-anna"), "demo-anna")
        for invalid in ("G08", "8worker", "ab", "worker_1", "worker-", "a" * 25):
            with self.assertRaises(ValueError):
                validate_state_name(invalid)
        self.assertEqual(worker_display_name("g08b"), "G08b")

    def test_bootstrap_config_defers_endpoint_and_pins_cli_app(self):
        config = bootstrap_agent365_config(
            state_name="g08b",
            agent_name="G08b",
            tenant_id="tenant-1",
            sponsor_upn="admin@example.com",
            agent_upn="g08b@example.onmicrosoft.com",
            client_app_id="cli-app",
        )

        self.assertNotIn("messagingEndpoint", config)
        self.assertTrue(config["aiteammate"])
        self.assertEqual(config["agentBlueprintDisplayName"], "G08b Blueprint")
        self.assertEqual(config["managerEmail"], "admin@example.com")
        self.assertEqual(config["agentUserPrincipalName"], "g08b@example.onmicrosoft.com")
        self.assertEqual(config["clientAppId"], "cli-app")

    def test_release_pins_copy_every_release_and_image_value(self):
        tfvars = {key: f"value-{index}" for index, key in enumerate(RELEASE_PIN_FLAGS)}
        arguments = release_pin_arguments(tfvars)

        self.assertEqual(len(arguments), 2 * len(RELEASE_PIN_FLAGS))
        self.assertEqual(arguments[:2], ["--role-blueprint", "value-0"])
        self.assertNotIn("--bridge-image", arguments)
        del tfvars["runtime_image"]
        with self.assertRaisesRegex(ValueError, "runtime_image"):
            release_pin_arguments(tfvars)

    def test_inheritance_requires_every_resource(self):
        self.assertTrue(inheritance_complete("Summary: 7 of 7 resources have inheritance"))
        self.assertFalse(inheritance_complete("Summary: 6 of 7 resources have inheritance"))
        self.assertFalse(inheritance_complete("Summary: 0 of 0 resources"))
        self.assertFalse(inheritance_complete("no summary"))

    def test_endpoint_is_registered_once_and_never_replaced(self):
        endpoint = "https://worker.example/api/messages"
        self.assertTrue(endpoint_registration_needed("", endpoint))
        self.assertFalse(endpoint_registration_needed(endpoint + "/", endpoint))
        with self.assertRaisesRegex(RuntimeError, "different messaging endpoint"):
            endpoint_registration_needed("https://other.example/api/messages", endpoint)

    def test_blueprint_secret_is_removed_from_generated_config(self):
        generated = {
            "agentBlueprintId": "blueprint-1",
            "agentBlueprintClientSecret": "abc8Q~secret",
            "agentBlueprintClientSecretProtected": False,
        }

        self.assertEqual(
            strip_blueprint_secrets(generated),
            {"agentBlueprintId": "blueprint-1", "agentBlueprintClientSecretProtected": False},
        )

    def test_command_output_redacts_client_secrets_and_tokens(self):
        line = (
            "secret abc8Q~AbCdEfGhIjKlMnOpQrStUvWxYz0123 token "
            "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl blueprint "
            "7ec96f90-136f-4614-96d6-626bfb1da608"
        )

        redacted = redact_secrets(line)

        self.assertNotIn("AbCdEf", redacted)
        self.assertNotIn("eyJhbGci", redacted)
        self.assertIn("7ec96f90-136f-4614-96d6-626bfb1da608", redacted)

    def test_teardown_refuses_sandbox_groups_of_other_workers(self):
        outputs = {
            "sandbox_groups": {
                "runtime": {"name": "sbg-autopilots-g08b-runtime"},
                "gateway": {"name": "sbg-autopilots-g08b-gateway"},
            }
        }
        self.assertEqual(
            owned_sandbox_groups(outputs, "g08b"),
            {"runtime": "sbg-autopilots-g08b-runtime", "gateway": "sbg-autopilots-g08b-gateway"},
        )
        outputs["sandbox_groups"]["gateway"]["name"] = "sbg-autopilots-hermes2-gateway"
        with self.assertRaisesRegex(ValueError, "hermes2"):
            owned_sandbox_groups(outputs, "g08b")
        self.assertEqual(owned_sandbox_groups({}, "g08b"), {})


if __name__ == "__main__":
    unittest.main()
