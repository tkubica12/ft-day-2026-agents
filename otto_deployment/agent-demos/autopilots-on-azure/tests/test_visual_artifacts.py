import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import bridge.app as app
from bridge.runtime.base import AgentAuthContext, AgentResponse

from bridge.visual_artifacts import (
    Pending, consent_activity, extract_artifact_request, microsoft_url, pending, render_html, take_consent,
    upload_consent_file,
)


SPEC = {"title": "Budget report", "sections": [
    {"heading": "Overview", "body": "Synthetic results."},
    {"heading": "Outlook", "body": "Requires a decision."},
]}


class VisualArtifactTests(unittest.TestCase):
    def tearDown(self):
        pending.clear()

    def test_artifact_is_offline_and_escapes_data(self):
        spec = {"title": "<script>steal()</script>", "sections": [
            {"heading": "Safe", "body": '<img src="https://attacker.example">'}
        ]}
        visible, parsed = extract_artifact_request(
            "Report <VISUAL_ARTIFACT_REQUEST>" + json.dumps(spec) + "</VISUAL_ARTIFACT_REQUEST>"
        )
        content = render_html(parsed).decode()
        self.assertEqual(visible, "Report")
        self.assertIn("&lt;script&gt;steal()&lt;/script&gt;", content)
        self.assertNotIn('<img src="https://attacker.example">', content)
        self.assertIn("default-src 'none'", content)
        self.assertIn("Slides", content)
        self.assertIn('<html lang="en">', content)
        self.assertIn("Fictional Medivia demo data — not Dr.Max data", content)

    def test_rejects_malformed_and_oversized_input(self):
        for value in ({"title": "a", "sections": []}, {"title": "a", "sections": SPEC["sections"] * 5}):
            with self.assertRaises(ValueError):
                extract_artifact_request("<VISUAL_ARTIFACT_REQUEST>" + json.dumps(value) + "</VISUAL_ARTIFACT_REQUEST>")
        with self.assertRaises(ValueError):
            extract_artifact_request("<VISUAL_ARTIFACT_REQUEST>{}")

    def test_consent_binds_user_and_chat_and_is_single_use(self):
        async def run():
            with patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "WORKER_ID": "otto"}):
                activity = consent_activity(SPEC, "", user_id="user-a", conversation_id="chat-a")
                card = activity.attachments[0]
                self.assertEqual(card.content_type, "application/vnd.microsoft.teams.card.file.consent")
                value = {"action": "accept", "context": card.content["acceptContext"], "uploadInfo": {}}
                with self.assertRaises(ValueError):
                    take_consent(value, user_id="user-b", conversation_id="chat-a")
                with self.assertRaises(ValueError):
                    take_consent(value, user_id="user-a", conversation_id="chat-b")
                action, item, _ = take_consent(value, user_id="user-a", conversation_id="chat-a")
                self.assertEqual(action, "accept")
                self.assertEqual(len(item.content), card.content["sizeInBytes"])
                with self.assertRaises(ValueError):
                    take_consent(value, user_id="user-a", conversation_id="chat-a")
        asyncio.run(run())

    def test_decline_discards_pending_bytes(self):
        async def run():
            with patch.dict(os.environ, {"API_SERVER_KEY": "test-key"}):
                activity = consent_activity(SPEC, "", user_id="u", conversation_id="c")
                value = {"action": "decline", "context": activity.attachments[0].content["declineContext"]}
                action, _, _ = take_consent(value, user_id="u", conversation_id="c")
                self.assertEqual(action, "decline")
                self.assertFalse(pending)
        asyncio.run(run())

    def test_upload_urls_are_restricted(self):
        self.assertEqual(microsoft_url("https://tenant.sharepoint.com/path"), "https://tenant.sharepoint.com/path")
        for value in ("http://tenant.sharepoint.com/path", "https://tenant.sharepoint.com.evil.test/", "https://127.0.0.1/"):
            with self.assertRaises(ValueError):
                microsoft_url(value)

    def test_gateway_offers_file_and_handles_confirmed_recipient(self):
        async def run():
            ctx = SimpleNamespace(activity=object(), send_activity=AsyncMock(return_value=SimpleNamespace(id="sent")))
            auth = AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",), conversation_boundary="one_to_one")
            answer = "Ready to offer. <VISUAL_ARTIFACT_REQUEST>" + json.dumps(SPEC) + "</VISUAL_ARTIFACT_REQUEST>"
            with patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "TEAMS_VISUAL_ARTIFACTS_ENABLED": "true"}), \
                patch.object(app, "agent_auth_context", return_value=auth), \
                patch.object(app, "delivery_reference_metadata", return_value={}), \
                patch.object(app, "invoke_agent_runtime", AsyncMock(return_value=AgentResponse(text=answer, raw={}))), \
                patch.object(app, "teams_runtime_source", return_value="teams_personal"), \
                patch.object(app, "user_id", return_value="user-a"), \
                patch.object(app, "teams_conversation_type", return_value="personal"), \
                patch.object(app, "supports_typing_indicators", return_value=False):
                await app.run_agent_runtime_for_teams(ctx, conversation_id="chat-a", session_key="s", message="make a report")
                card = ctx.send_activity.await_args.args[0]
                value = {"action": "accept", "context": card.attachments[0].content["acceptContext"], "uploadInfo": {}}
                invoke = SimpleNamespace(
                    activity=SimpleNamespace(name="fileConsent/invoke", value=value),
                    send_activity=AsyncMock(), turn_state={},
                )
                upload = AsyncMock(return_value="file-card")
                with patch.object(app, "teams_conversation_id", return_value="chat-a"), \
                    patch.object(app, "upload_consent_file", upload):
                    await app.handle_teams_invoke(invoke, None)
                upload.assert_awaited_once()
                invoke.send_activity.assert_awaited_once_with("file-card")
        asyncio.run(run())

    def test_upload_sends_exact_bytes_without_redirects_and_checks_status(self):
        async def run(status):
            class Response:
                async def __aenter__(self):
                    return SimpleNamespace(status=status)
                async def __aexit__(self, *args):
                    return False
            class Session:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    return False
                def put(self, url, **kwargs):
                    self_test.assertEqual(url, "https://tenant.sharepoint.com/upload")
                    self_test.assertEqual(kwargs["data"], b"html")
                    self_test.assertFalse(kwargs["allow_redirects"])
                    self_test.assertEqual(kwargs["headers"]["Content-Range"], "bytes 0-3/4")
                    return Response()
            self_test = self
            with patch("bridge.visual_artifacts._PinnedResolver") as resolver, \
                patch("bridge.visual_artifacts.aiohttp.TCPConnector"), \
                patch("bridge.visual_artifacts.aiohttp.ClientSession", return_value=Session()):
                info = {"uploadUrl": "https://tenant.sharepoint.com/upload",
                        "contentUrl": "https://tenant.sharepoint.com/file", "uniqueId": "id"}
                if status == 201:
                    activity = await upload_consent_file(Pending(b"html", "u", "c", 1), info)
                    self.assertEqual(activity.attachments[0].content_type, "application/vnd.microsoft.teams.card.file.info")
                    resolver.return_value.pin.assert_called_once()
                else:
                    with self.assertRaises(RuntimeError):
                        await upload_consent_file(Pending(b"html", "u", "c", 1), info)
        asyncio.run(run(201))
        asyncio.run(run(202))


if __name__ == "__main__":
    unittest.main()
