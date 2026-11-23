from __future__ import annotations

import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from bridge import event_worker
from bridge.durable_transport import Journal, TransportSettings, identity
from scripts.native_transport import connection_body, expected_trigger, trigger_matches


class EventWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = TransportSettings(
            "worker", "gateway", "client", "bus.servicebus.windows.net",
            "/connector/connections/gateway", "activities",
            "schedule", Path(self.temporary.name) / "journal.db",
        )
        self.journal = Journal(self.settings.journal_path)
        self.correlation = identity("one")
        self.envelope = {
            "MessageId": self.correlation, "LockToken": "lease",
            "Properties": {"DeliveryCount": 1},
        }
        self.bus = MagicMock()
        self.bus.__enter__.return_value = self.bus
        self.patches = [
            patch.object(event_worker, "settings", return_value=self.settings),
            patch.object(event_worker, "journal", return_value=self.journal),
            patch.object(event_worker, "bus", return_value=self.bus),
            patch.object(event_worker, "checkpoint", new_callable=AsyncMock),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def admit(self, queue, kind, payload):
        body = self.settings.message(kind, self.journal.save_payload(payload), self.correlation)
        self.journal.admit(queue, self.envelope, body, self.settings.connection_id)

    async def test_fast_work_finishes_before_the_recovery_message_arrives(self):
        body = self.settings.message("activity", self.journal.save_payload({"activity": {}}), self.correlation)
        self.journal.reserve_work("activities", body, self.settings.connection_id)
        handler = AsyncMock(return_value={"status": "completed"})
        async def guard(work, queue, message_id):
            return await work
        with patch.object(event_worker, "_guarded", side_effect=guard):
            await event_worker._process("activities", self.correlation, handler, None)
        row = self.journal.get("activities", self.correlation)
        self.assertEqual(row["phase"], "completed")
        self.assertEqual(row["lock_token"], "")
        self.assertEqual(row["settled"], 0)
        self.bus.settle.assert_not_called()
        self.journal.admit("activities", self.envelope, body, self.settings.connection_id)
        await event_worker._process("activities", self.correlation, handler, None)
        handler.assert_awaited_once()
        self.bus.settle.assert_called_once_with("activities", "complete", "lease")
        self.assertEqual(self.journal.get("activities", self.correlation)["settled"], 1)

    async def test_live_fast_work_and_its_native_recovery_callback_share_one_execution(self):
        body = self.settings.message("activity", self.journal.save_payload({"activity": {}}), self.correlation)
        self.journal.reserve_work("activities", body, self.settings.connection_id)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def handler(payload):
            calls.append(payload)
            started.set()
            await release.wait()
            return {"status": "completed"}

        async def guard(work, queue, message_id):
            return await work

        with patch.object(event_worker, "_guarded", side_effect=guard):
            event_worker.start_work("activities", self.correlation, handler, None)
            await started.wait()
            self.journal.admit("activities", self.envelope, body, self.settings.connection_id)
            event_worker.start_work("activities", self.correlation, handler, None)
            task = event_worker._active[("activities", self.correlation)]
            release.set()
            await task
            await asyncio.sleep(0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.journal.get("activities", self.correlation)["settled"], 1)

    async def test_completed_activity_redelivery_never_repeats_external_effects(self):
        self.admit("activities", "activity", {"activity": {}})
        self.journal.update("activities", self.correlation, phase="completed", result='{"done":true}')
        handler = AsyncMock()
        await event_worker._process("activities", self.correlation, handler, None)
        handler.assert_not_awaited()
        self.bus.settle.assert_called_once_with("activities", "complete", "lease")

    async def test_interrupted_activity_is_unknown_not_automatically_replayed(self):
        self.admit("activities", "activity", {"activity": {}})
        self.journal.update("activities", self.correlation, phase="running")
        handler = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            await event_worker._process("activities", self.correlation, handler, None)
        handler.assert_not_awaited()
        self.bus.settle.assert_called_once_with("activities", "deadletter", "lease")
        self.assertEqual(self.journal.get("activities", self.correlation)["phase"], "unknown")
        self.assertEqual(self.journal.get("activities", self.correlation)["settled"], 1)

    async def test_channel_failure_is_quarantined_not_replayed_or_reported_completed(self):
        self.admit("activities", "activity", {"activity": {}})
        handler = AsyncMock(side_effect=RuntimeError("channel rejected reply"))
        async def guard(work, queue, message_id):
            return await work
        with patch.object(event_worker, "_guarded", side_effect=guard):
            with self.assertRaisesRegex(RuntimeError, "channel rejected"):
                await event_worker._process("activities", self.correlation, handler, None)
        self.bus.settle.assert_called_once_with("activities", "deadletter", "lease")
        row = self.journal.get("activities", self.correlation)
        self.assertEqual(row["phase"], "unknown")
        self.assertEqual(row["result"], "")
        self.assertEqual(row["settled"], 1)
        self.assertIn("channel rejected", row["error"])

    async def test_payload_failure_before_gateway_effects_keeps_delivery_retryable(self):
        self.admit("activities", "activity", {"activity": {}})
        handler = AsyncMock()
        async def guard(work, queue, message_id):
            return await work
        with (
            patch.object(event_worker, "_guarded", side_effect=guard),
            patch.object(event_worker, "load_work_payload", side_effect=OSError("private payload unavailable")),
        ):
            with self.assertRaisesRegex(OSError, "private payload unavailable"):
                await event_worker._process("activities", self.correlation, handler, None)
        self.bus.settle.assert_not_called()
        handler.assert_not_awaited()
        self.assertEqual(self.journal.get("activities", self.correlation)["phase"], "pending")

    async def test_gateway_keepalive_uses_its_native_health_url_exactly_once(self):
        response = MagicMock()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        with (
            patch.dict("os.environ", {"TRANSPORT_SELF_URL": "https://gateway.example"}),
            patch.object(event_worker.httpx, "AsyncClient", return_value=client),
            patch.object(event_worker.asyncio, "sleep", side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await event_worker._keep_active()
        client.get.assert_awaited_once_with("https://gateway.example/health")

    def keepalive_client(self, *results):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=list(results))
        return client

    async def test_gateway_keepalive_survives_isolated_failures_after_a_resume_stall(self):
        healthy = MagicMock()
        client = self.keepalive_client(
            httpx.ConnectTimeout(""), healthy, httpx.ConnectTimeout(""), healthy,
        )
        sleep = AsyncMock(side_effect=[None, None, None, asyncio.CancelledError])
        with (
            patch.dict("os.environ", {"TRANSPORT_SELF_URL": "https://gateway.example"}),
            patch.object(event_worker.httpx, "AsyncClient", return_value=client),
            patch.object(event_worker.asyncio, "sleep", sleep),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await event_worker._keep_active()
        self.assertEqual(client.get.await_count, 4)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [event_worker.KEEPALIVE_RETRY_SECONDS, event_worker.KEEPALIVE_INTERVAL_SECONDS] * 2,
        )

    async def test_gateway_keepalive_stops_work_after_consecutive_failures(self):
        unavailable = MagicMock()
        unavailable.raise_for_status.side_effect = httpx.HTTPStatusError(
            "unavailable", request=MagicMock(), response=MagicMock(status_code=503),
        )
        limit = event_worker.KEEPALIVE_FAILURE_LIMIT
        client = self.keepalive_client(*[unavailable] * limit)
        sleep = AsyncMock()
        with (
            patch.dict("os.environ", {"TRANSPORT_SELF_URL": "https://gateway.example"}),
            patch.object(event_worker.httpx, "AsyncClient", return_value=client),
            patch.object(event_worker.asyncio, "sleep", sleep),
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                await event_worker._keep_active()
        self.assertEqual(client.get.await_count, limit)
        self.assertEqual(sleep.await_count, limit - 1)


class NativeTransportContractTests(unittest.TestCase):
    def outputs(self):
        return {
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group_name": "rg-owned", "sandbox_location": "swedencentral",
            "sandbox_groups": {"gateway": {"name": "worker-gateway"}, "runtime": {"name": "worker-runtime"}},
        }

    def test_gateway_callback_contains_only_base64_interpolation_not_user_shell_text(self):
        result = expected_trigger(self.outputs(), "gateway", "worker-activities", "sandbox-id")
        callback = result["properties"]["notificationDetails"]
        command = callback["body"]["command"]
        self.assertIn('@{base64(string(triggerBody()))}', command)
        self.assertEqual(command.count("triggerBody()"), 1)
        self.assertEqual(callback["body"]["activationMode"], "OnDemand")
        self.assertEqual(callback["authentication"]["audience"], "https://management.azuredevcompute.io/")
        self.assertEqual(result["properties"]["connectionDetails"]["connectionName"], "gateway-servicebus-mi")

    def test_runtime_does_not_receive_queued_interactive_turns(self):
        with self.assertRaisesRegex(ValueError, "Only the gateway"):
            expected_trigger(self.outputs(), "runtime", "worker-requests", "sandbox-id")

    def test_connection_uses_managed_identity_without_stored_key(self):
        result = connection_body("bus.servicebus.windows.net", "swedencentral", "gateway")
        self.assertEqual(
            result["properties"]["connectionProperties"]["authentication"],
            {"type": "ManagedServiceIdentity"},
        )
        self.assertNotIn("connectionString", json.dumps(result))

    def test_native_trigger_readback_normalizes_only_observed_server_fields(self):
        desired = expected_trigger(self.outputs(), "gateway", "worker-activities", "sandbox-id")["properties"]
        actual = json.loads(json.dumps(desired))
        actual["notificationDetails"]["httpMethod"] = "Post"
        actual["notificationDetails"].pop("authentication")
        actual.update(provisioningState="Succeeded", type="NotSpecified")
        self.assertTrue(trigger_matches(actual, desired))
        actual["notificationDetails"]["body"]["command"] += " changed"
        self.assertFalse(trigger_matches(actual, desired))

    def test_an_explicit_wrong_callback_identity_is_not_treated_as_redaction(self):
        desired = expected_trigger(self.outputs(), "gateway", "worker-activities", "sandbox-id")["properties"]
        actual = json.loads(json.dumps(desired))
        actual["notificationDetails"]["authentication"] = {"type": "None"}
        self.assertFalse(trigger_matches(actual, desired))


if __name__ == "__main__":
    unittest.main()
