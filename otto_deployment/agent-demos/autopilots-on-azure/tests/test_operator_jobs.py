import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from microsoft_agents.activity import Activity

from bridge import app as bridge_app, durable_transport, event_worker
from bridge.durable_transport import Journal, TransportSettings
from bridge.runtime.base import AgentRequest, AgentResponse
from bridge.runtime.hermes import HermesRuntimeAdapter, NativeRunInterventionError


class OperatorJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = TransportSettings(
            "worker", "gateway", "client", "bus.servicebus.windows.net", "/connections/gateway",
            "activities", "schedule", Path(self.directory.name) / "journal.db",
        )
        self.journal = Journal(self.settings.journal_path)
        sender = MagicMock()
        self.bus = sender.__enter__.return_value
        patches = [
            patch.dict("os.environ", {"API_SERVER_KEY": "operator-key"}),
            patch.object(durable_transport, "enabled", return_value=True),
            patch.object(durable_transport, "settings", return_value=self.settings),
            patch.object(durable_transport, "journal", return_value=self.journal),
            patch.object(event_worker, "settings", return_value=self.settings),
            patch.object(event_worker, "journal", return_value=self.journal),
            patch.object(event_worker, "bus", return_value=sender),
            patch.object(event_worker, "checkpoint", new_callable=AsyncMock),
            patch.object(event_worker, "start_work"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://gateway",
        )
        self.addAsyncCleanup(self.client.aclose)
        self.headers = {"X-Autopilot-Key": "operator-key"}
        self.body = {
            "requestId": "request-one", "conversationId": "conversation-one", "message": "hello",
            "persistenceDisabled": True,
        }

    async def test_operator_admission_is_durable_before_the_model_runs(self):
        with patch.object(bridge_app, "invoke_agent_runtime", new_callable=AsyncMock) as model:
            admitted = await self.client.post("/invoke", headers=self.headers, json=self.body)
        self.assertEqual(admitted.status_code, 202)
        model.assert_not_awaited()
        job_id = admitted.json()["jobId"]
        binding = self.journal.load_state("outbound:" + job_id)
        specification = self.journal.load_payload(binding["reference"])["operatorRequest"]
        self.assertTrue(specification["payload"]["persistenceDisabled"])
        pending = await self.client.get("/invocations/request-one", headers=self.headers)
        self.assertEqual(pending.status_code, 202)
        with patch.object(bridge_app, "invoke_agent_runtime", new_callable=AsyncMock) as model:
            model.return_value = AgentResponse(
                text="answer", raw={"payload": {"run_id": "run-native"}, "transportMessageId": "native-correlation"},
            )
            token = durable_transport.delivery_identity.set(job_id)
            try:
                await bridge_app.process_operator_job(specification)
                await bridge_app.process_operator_job(specification)
            finally:
                durable_transport.delivery_identity.reset(token)
            model.assert_awaited_once()
            self.assertEqual(model.await_args.kwargs["metadata"], {"persistenceDisabled": True})
        completed = await self.client.get("/invocations/request-one", headers=self.headers)
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["response"], "answer")
        self.assertEqual(completed.json()["runId"], "run-native")

    async def test_changed_payload_cannot_reuse_an_operator_identity(self):
        first = await self.client.post("/invoke", headers=self.headers, json=self.body)
        changed = await self.client.post("/invoke", headers=self.headers, json={**self.body, "message": "changed"})
        self.assertEqual(first.status_code, 202)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.bus.send.call_count, 1)

    async def test_rehearsal_persona_is_allowlisted_gated_and_labelled_as_operator_asserted(self):
        body = {**self.body, "rehearsalPersona": "ladislav"}
        with patch.dict("os.environ", {"WORKER_ASSIGNMENT_PROFILE": "budget-season"}):
            disabled = await self.client.post("/invoke", headers=self.headers, json=body)
        self.assertEqual(disabled.status_code, 403)
        enabled = {"WORKER_ASSIGNMENT_PROFILE": "budget-season", "OPERATOR_REHEARSAL_PERSONAS_ENABLED": "true"}
        with patch.dict("os.environ", enabled):
            unknown = await self.client.post(
                "/invoke", headers=self.headers, json={**body, "requestId": "request-two", "rehearsalPersona": "sim"},
            )
            admitted = await self.client.post("/invoke", headers=self.headers, json=body)
            self.assertEqual(unknown.status_code, 400)
            self.assertEqual(admitted.status_code, 202)
            job_id = admitted.json()["jobId"]
            binding = self.journal.load_state("outbound:" + job_id)
            specification = self.journal.load_payload(binding["reference"])["operatorRequest"]
            with patch.object(bridge_app, "invoke_agent_runtime", new_callable=AsyncMock) as model:
                model.return_value = AgentResponse(text="answer", raw={})
                token = durable_transport.delivery_identity.set(job_id)
                try:
                    await bridge_app.process_operator_job(specification)
                finally:
                    durable_transport.delivery_identity.reset(token)
        arguments = model.await_args.kwargs
        self.assertEqual(arguments["user_id"], "c767e8ee-2ef8-446f-a293-cf69661c6d69")
        self.assertEqual(arguments["auth_context"].invoking_user_name, "Ladislav Lála")
        self.assertIn("not a Teams sign-in", arguments["message"])
        self.assertIn("Invoking human: Ladislav Lála (c767e8ee-2ef8-446f-a293-cf69661c6d69)", arguments["message"])
        self.assertTrue(arguments["message"].endswith("Message text:\nhello"))

    async def test_direct_native_admission_is_stored_in_the_original_gateway_work_receipt(self):
        admitted = await self.client.post("/invoke", headers=self.headers, json=self.body)
        job_id = admitted.json()["jobId"]
        calls = []

        def native(request):
            calls.append((request.method, request.headers.get("Idempotency-Key")))
            if request.method == "POST":
                return httpx.Response(202, json={"run_id": "original-native-run"})
            return httpx.Response(200, json={
                "run_id": "original-native-run", "status": "completed", "output": "answer",
            })

        adapter = HermesRuntimeAdapter(
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(native)),
        )
        request = AgentRequest("hello", "conversation", "operator", "invoke", True)
        token = durable_transport.delivery_identity.set(job_id)
        queue_token = durable_transport.delivery_queue.set(self.settings.activities_queue)
        try:
            with (
                patch.object(adapter, "_resolve_transcript_id", return_value="frozen-transcript") as transcript,
                patch.object(adapter, "_ensure_session", new_callable=AsyncMock),
            ):
                first = await adapter._native_run("https://runtime.example", "fixed-key", request)
                second = await adapter._native_run("https://runtime.example", "fixed-key", request)
                with self.assertRaises(NativeRunInterventionError):
                    await adapter._native_run("https://runtime.example", "rotated-key", request)
            transcript.assert_awaited_once()
        finally:
            durable_transport.delivery_queue.reset(queue_token)
            durable_transport.delivery_identity.reset(token)
        self.assertEqual(first, second)
        self.assertEqual([method for method, _ in calls], ["POST", "GET", "GET"])
        self.assertEqual(calls[0][1], "autopilots-" + job_id)
        receipt = self.journal.get(self.settings.activities_queue, job_id)
        self.assertEqual(receipt["native_run_id"], "original-native-run")
        self.assertTrue(receipt["native_scope"])

    async def _retired_session_run(self, job_id, native_scope=None):
        calls = []

        def native(request):
            body = json.loads(request.content) if request.content else {}
            calls.append((request.method, request.url.path, body.get("id") or body.get("session_id")))
            if request.url.path.startswith("/api/sessions/"):
                return httpx.Response(404, json={})
            if request.url.path == "/api/sessions":
                if body["id"] == "frozen-transcript":
                    return httpx.Response(409, json={"error": {"code": "session_deleted"}})
                return httpx.Response(201, json={})
            if request.method == "POST":
                return httpx.Response(202, json={"run_id": "fresh-run"})
            return httpx.Response(200, json={"run_id": "fresh-run", "status": "completed", "output": "answer"})

        adapter = HermesRuntimeAdapter(
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(native)),
        )
        self.journal.save_state("native-transcript:" + job_id, "frozen-transcript")
        if native_scope:
            self.journal.update(
                self.settings.activities_queue, job_id, native_scope=native_scope, admission_at=time.time(),
            )
        request = AgentRequest("hello", "conversation", "operator", "invoke", True)
        token = durable_transport.delivery_identity.set(job_id)
        queue_token = durable_transport.delivery_queue.set(self.settings.activities_queue)
        try:
            return calls, await adapter._native_run("https://runtime.example", "fixed-key", request)
        finally:
            durable_transport.delivery_queue.reset(queue_token)
            durable_transport.delivery_identity.reset(token)

    async def test_retired_session_moves_to_next_generation_only_before_admission(self):
        admitted = await self.client.post("/invoke", headers=self.headers, json=self.body)
        job_id = admitted.json()["jobId"]
        calls, result = await self._retired_session_run(job_id)
        self.assertEqual(result["output"], "answer")
        self.assertIn(("POST", "/v1/runs", "frozen-transcript:new:g1"), calls)
        self.assertEqual(self.journal.load_state("native-transcript:" + job_id), "frozen-transcript:new:g1")

    async def test_retired_session_never_changes_identity_after_uncertain_admission(self):
        admitted = await self.client.post("/invoke", headers=self.headers, json=self.body)
        job_id = admitted.json()["jobId"]
        scope = hashlib.sha256(b"fixed-key").hexdigest()
        with self.assertRaises(NativeRunInterventionError):
            await self._retired_session_run(job_id, native_scope=scope)
        self.assertEqual(self.journal.load_state("native-transcript:" + job_id), "frozen-transcript")
        receipt = self.journal.get(self.settings.activities_queue, job_id)
        self.assertEqual(receipt["native_run_id"], "")

    async def test_anonymous_admission_is_refused_without_publishing(self):
        response = await self.client.post("/invoke", json=self.body)
        self.assertEqual(response.status_code, 401)
        self.bus.send.assert_not_called()

    async def test_waiting_controls_keep_the_original_native_run_without_approving_it(self):
        for waiting in ("waiting_for_approval", "waiting_for_clarification"):
            with self.subTest(waiting=waiting):
                admitted = await self.client.post(
                    "/invoke", headers=self.headers, json={**self.body, "requestId": waiting},
                )
                job_id = admitted.json()["jobId"]
                calls = []
                statuses = iter((waiting, "completed"))

                def native(request):
                    calls.append((request.method, request.url.path))
                    if request.method == "POST":
                        return httpx.Response(202, json={"run_id": "original-native-run"})
                    return httpx.Response(200, json={
                        "run_id": "original-native-run", "status": next(statuses), "output": "answer",
                    })

                adapter = HermesRuntimeAdapter(
                    client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(native)),
                )
                token = durable_transport.delivery_identity.set(job_id)
                queue_token = durable_transport.delivery_queue.set(self.settings.activities_queue)
                try:
                    with (
                        patch.object(adapter, "_resolve_transcript_id", return_value="frozen-transcript"),
                        patch.object(adapter, "_ensure_session", new_callable=AsyncMock),
                        patch("bridge.runtime.hermes.asyncio.sleep", new_callable=AsyncMock),
                    ):
                        result = await adapter._native_run(
                            "https://runtime.example", "fixed-key",
                            AgentRequest("hello", "conversation", "operator", "invoke", True),
                        )
                finally:
                    durable_transport.delivery_queue.reset(queue_token)
                    durable_transport.delivery_identity.reset(token)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(calls, [
                    ("POST", "/v1/runs"), ("GET", "/v1/runs/original-native-run"),
                    ("GET", "/v1/runs/original-native-run"),
                ])
                pending = await self.client.get("/invocations/" + waiting, headers=self.headers)
                self.assertEqual(pending.status_code, 202)
                self.assertEqual(pending.json()["runId"], "original-native-run")

    async def test_dream_is_admitted_without_running_under_an_http_deadline(self):
        with patch.object(bridge_app, "execute_operator_dream", new_callable=AsyncMock) as model:
            response = await self.client.post(
                "/internal/dream",
                headers={**self.headers, "Idempotency-Key": "dream-one"},
                json={"focus": "recent work", "maxRecords": 2},
            )
        self.assertEqual(response.status_code, 202)
        model.assert_not_awaited()

    async def test_business_failure_is_not_reported_as_a_successful_result(self):
        admitted = await self.client.post("/invoke", headers=self.headers, json=self.body)
        job_id = admitted.json()["jobId"]
        reference = self.journal.load_state("outbound:" + job_id)["reference"]
        specification = self.journal.load_payload(reference)["operatorRequest"]
        token = durable_transport.delivery_identity.set(job_id)
        try:
            with patch.object(bridge_app, "invoke_agent_runtime", side_effect=RuntimeError("Native execution failed.")):
                await bridge_app.process_operator_job(specification)
        finally:
            durable_transport.delivery_identity.reset(token)
        response = await self.client.get("/invocations/request-one", headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertIn("Native execution failed", response.json()["error"])

    async def test_operator_turn_carries_a_stored_teams_delivery_reference_for_scheduling(self):
        body = {**self.body, "deliveryReferenceKey": "teams-0123"}
        admitted = await self.client.post("/invoke", headers=self.headers, json=body)
        job_id = admitted.json()["jobId"]
        reference = self.journal.load_state("outbound:" + job_id)["reference"]
        specification = self.journal.load_payload(reference)["operatorRequest"]
        stored = {"boundary": "one_to_one", "conversation": {"conversation_reference": {}}}
        adapter = MagicMock()
        adapter.get_delivery_reference = AsyncMock(return_value={"referenceKey": "teams-0123", "deliveryReference": stored})
        token = durable_transport.delivery_identity.set(job_id)
        try:
            with (
                patch.object(bridge_app, "runtime_adapter", return_value=adapter),
                patch.object(bridge_app, "invoke_agent_runtime", new_callable=AsyncMock) as model,
            ):
                model.return_value = AgentResponse(text="scheduled", raw={})
                await bridge_app.process_operator_job(specification)
        finally:
            durable_transport.delivery_identity.reset(token)
        adapter.get_delivery_reference.assert_awaited_once_with("teams-0123")
        self.assertEqual(model.await_args.kwargs["metadata"], {
            "persistenceDisabled": True, "deliveryReference": {**stored, "referenceKey": "teams-0123"},
        })

    async def test_unknown_operator_delivery_reference_fails_without_running_the_model(self):
        admitted = await self.client.post(
            "/invoke", headers=self.headers, json={**self.body, "deliveryReferenceKey": "teams-missing"},
        )
        job_id = admitted.json()["jobId"]
        reference = self.journal.load_state("outbound:" + job_id)["reference"]
        specification = self.journal.load_payload(reference)["operatorRequest"]
        adapter = MagicMock()
        adapter.get_delivery_reference = AsyncMock(return_value={})
        token = durable_transport.delivery_identity.set(job_id)
        try:
            with (
                patch.object(bridge_app, "runtime_adapter", return_value=adapter),
                patch.object(bridge_app, "invoke_agent_runtime", new_callable=AsyncMock) as model,
            ):
                await bridge_app.process_operator_job(specification)
        finally:
            durable_transport.delivery_identity.reset(token)
        model.assert_not_awaited()
        response = await self.client.get("/invocations/request-one", headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertIn("delivery reference was not found", response.json()["error"])

    async def test_an_observation_timeout_keeps_the_exact_operator_job_pending_and_recoverable(self):
        admitted = await self.client.post("/invoke", headers=self.headers, json=self.body)
        job_id = admitted.json()["jobId"]
        reference = self.journal.load_state("outbound:" + job_id)["reference"]
        specification = self.journal.load_payload(reference)["operatorRequest"]
        token = durable_transport.delivery_identity.set(job_id)
        try:
            with patch.object(
                bridge_app, "invoke_agent_runtime",
                side_effect=event_worker.InvocationPendingError("Exact native admission is unfinished"),
            ):
                with self.assertRaises(event_worker.InvocationPendingError):
                    await bridge_app.process_operator_job(specification)
            self.assertIsNone(self.journal.response(job_id))
            pending = await self.client.get("/invocations/request-one", headers=self.headers)
            self.assertEqual(pending.status_code, 202)
            with patch.object(
                bridge_app, "invoke_agent_runtime",
                return_value=AgentResponse(
                    text="recovered", raw={"payload": {"run_id": "original-run"}, "transportMessageId": "original-correlation"},
                ),
            ):
                await bridge_app.process_operator_job(specification)
        finally:
            durable_transport.delivery_identity.reset(token)
        completed = await self.client.get("/invocations/request-one", headers=self.headers)
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["runId"], "original-run")

    async def test_authentication_protocol_frames_are_not_persisted(self):
        context = SimpleNamespace(activity=Activity(
            type="invoke", name="signin/tokenExchange", value={"token": "synthetic-unit-test-value"},
        ))
        with (
            patch.object(bridge_app.AgentApplication, "on_turn", new_callable=AsyncMock) as immediate,
            patch.object(event_worker, "enqueue_activity", new_callable=AsyncMock) as queued,
        ):
            await bridge_app.agent365_app.on_turn(context)
        immediate.assert_awaited_once()
        queued.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
