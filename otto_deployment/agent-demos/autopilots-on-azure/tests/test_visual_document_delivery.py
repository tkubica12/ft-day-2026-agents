from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from autopilots_identity import visual_artifact_staging as staging
from autopilots_identity import visual_artifact_renderer as renderer
from bridge import app
from bridge import visual_document_delivery as delivery
from bridge.runtime.base import AgentAuthContext, AgentResponse

SCOPE = "1" * 32
USER = "1e785486-e7fc-4ed1-bf96-c2a8eac32f98"
OWNER = "4174bddd-ea90-41d0-a5aa-1b2b25864e9d"
SPEC = {
    "kind": "report", "title": "Budget", "subtitle": "Fictional data",
    "closing": "Review the figures.", "source": "Snapshot November 12, 2026",
    "sections": [{"heading": "Romania", "body": "EBITDA €44.66m.", "cues": ["Below plan"]}],
}


class VisualStagingTests(unittest.TestCase):
    def test_scoped_staging_and_rejection(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HERMES_HOME": directory}):
            (Path(directory) / "workspace").mkdir()
            marker = staging.stage(SCOPE, json.dumps(SPEC))
            self.assertIn(SCOPE, marker["marker"])
            self.assertEqual(staging.read(SCOPE), SPEC)
            for scope in ("../escape", "A" * 32, ""):
                with self.assertRaises(ValueError):
                    staging.read(scope)
            with self.assertRaises(ValueError):
                staging.stage(SCOPE, json.dumps({**SPEC, "title": "<script>bad</script>"}))

    def test_symlink_draft_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HERMES_HOME": directory}):
            (Path(directory) / "workspace").mkdir()
            path = staging.staged_path(SCOPE, create=True)
            target = Path(directory) / "outside.json"
            target.write_text(json.dumps(SPEC), encoding="utf-8")
            try:
                path.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaises((ValueError, OSError)):
                staging.read(SCOPE)
            with self.assertRaises((ValueError, OSError)):
                staging.stage(SCOPE, json.dumps(SPEC))

    def test_expired_draft_is_removed_without_touching_fresh_drafts(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HERMES_HOME": directory}):
            (Path(directory) / "workspace").mkdir()
            staging.stage(SCOPE, json.dumps(SPEC))
            staging.stage("2" * 32, json.dumps(SPEC))
            expired = staging.staged_path(SCOPE)
            os.utime(expired, (1, 1))
            self.assertEqual(staging.cleanup_expired(), 1)
            self.assertFalse(expired.exists())
            self.assertEqual(staging.read("2" * 32), SPEC)

    def test_hardlinked_draft_does_not_read_or_overwrite_target(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HERMES_HOME": directory}):
            root = Path(directory)
            (root / "workspace").mkdir()
            path = staging.staged_path(SCOPE, create=True)
            target = root / "outside.json"
            target.write_text(json.dumps(SPEC), encoding="utf-8")
            try:
                os.link(target, path)
            except OSError:
                self.skipTest("hard links unavailable")
            with self.assertRaises(ValueError):
                staging.read(SCOPE)
            staging.stage(SCOPE, json.dumps(SPEC))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), SPEC)
            self.assertEqual(staging.read(SCOPE), SPEC)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"), "POSIX FIFO required")
    def test_fifo_draft_fails_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            with patch.dict(os.environ, {"HERMES_HOME": directory}):
                path = staging.staged_path(SCOPE, create=True)
            os.mkfifo(path)
            code = (
                "from autopilots_identity.visual_artifact_staging import read\n"
                "try:\n read('" + SCOPE + "')\n"
                "except (ValueError, OSError):\n raise SystemExit(0)\n"
                "raise SystemExit(1)\n"
            )
            result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "HERMES_HOME": directory},
                                    timeout=3, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_marker_and_aad_binding(self):
        message = f"Ready. {delivery.START}{SCOPE}{delivery.END}"
        self.assertEqual(delivery.extract(message, SCOPE), ("Ready.", True))
        with self.assertRaises(ValueError):
            delivery.extract(message, "2" * 32)
        with self.assertRaises(ValueError):
            delivery.extract(message, "")
        self.assertEqual(app.authenticated_aad_id({"from": {"aadObjectId": USER}}), USER)
        self.assertEqual(app.authenticated_aad_id({"from": {"id": USER}}), "")

    def test_runtime_render_is_one_shot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HERMES_HOME": directory}):
            (Path(directory) / "workspace").mkdir()
            staging.stage(SCOPE, json.dumps(SPEC))
            with patch.object(renderer, "validate_article") as check:
                result = renderer.render(SCOPE)
            check.assert_called_once()
            self.assertEqual(base64.b64decode(result["contentBase64"]), staging.builder().expected_document(SPEC))
            self.assertFalse(staging.staged_path(SCOPE).exists())
            with self.assertRaises(FileNotFoundError):
                renderer.render(SCOPE)

    def test_slide_overflow_identifies_surface_without_exposing_markup(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            for surface, expected in (("closing", "shorten the closing"),
                                      ("card-4", "shorten the section 4 heading or cues"),
                                      ("opening", "shorten the title or subtitle")):
                result = f'<body data-otto-layout="overflow" data-otto-surface="{surface}"></body>'.encode()
                with self.subTest(surface=surface), patch.object(renderer, "chrome", return_value=result):
                    with self.assertRaisesRegex(ValueError, expected):
                        renderer.validate_article(b"<body></body>", scratch)
            with patch.object(renderer, "chrome", return_value=b'<body data-otto-layout="overflow" data-otto-surface="bad"></body>'):
                with self.assertRaisesRegex(ValueError, r"did not fit \(overflow\)"):
                    renderer.validate_article(b"<body></body>", scratch)

    def test_pdf_rejects_overflow_and_accepts_only_complete_single_page(self):
        brief = {**SPEC, "kind": "brief", "source": "Fictional financial brief"}
        text = " ".join([brief["title"], brief["subtitle"], brief["closing"], brief["source"],
                         staging.builder().BRAND["disclaimer"], "Romania", "EBITDA €44.66m."])
        fake_pdf = SimpleNamespace(PdfReader=lambda _: SimpleNamespace(
            pages=[SimpleNamespace(extract_text=lambda: text.replace("fi", "\ufb01"))]
        ))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"pypdf": fake_pdf}):
            scratch = Path(directory)
            with patch.object(renderer, "chrome", return_value=b'<body data-otto-layout="overflow"></body>'):
                with self.assertRaisesRegex(ValueError, "layout"):
                    renderer.render_pdf(brief, staging.builder().expected_document(brief), scratch)

            def chrome(_scratch, args):
                if "--dump-dom" in args:
                    return b'<body data-otto-layout="ok"></body>'
                self.assertNotIn("<script", (scratch / "sheet.html").read_text(encoding="utf-8").lower())
                (scratch / "brief.pdf").write_bytes(b"%PDF-1.7\n")
                return b""

            with patch.object(renderer, "chrome", side_effect=chrome):
                self.assertTrue(renderer.render_pdf(brief, staging.builder().expected_document(brief), scratch).startswith(b"%PDF-"))
                with patch.object(fake_pdf, "PdfReader", return_value=SimpleNamespace(
                    pages=[SimpleNamespace(extract_text=lambda: text.replace("44.66", ""))]
                )):
                    with self.assertRaisesRegex(ValueError, "missing or clipped"):
                        renderer.render_pdf(brief, staging.builder().expected_document(brief), scratch)


class VisualPublishingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.result = {
            "scope": SCOPE, "spec": SPEC,
            "contentBase64": base64.b64encode(staging.builder().expected_document(SPEC)).decode(),
        }

    async def test_shares_only_with_authenticated_recipient_read_only(self):
        item = {"webUrl": "https://tenant.sharepoint.com/one/file.html", "id": "item"}
        with patch.object(delivery, "child_folder", AsyncMock(side_effect=[{"id": "root"}, {"id": "day"}])) as folders, \
             patch.object(delivery, "upload_agent_results_copy", AsyncMock(return_value=item)) as upload, \
             patch.object(delivery, "check_permissions", AsyncMock()) as permissions, \
             patch.object(delivery, "invite_drive_item_user", AsyncMock()) as invite:
            url = await delivery.publish(self.result, expected_scope=SCOPE, recipient=USER)
        self.assertEqual(url, item["webUrl"])
        invite.assert_awaited_once_with(item, USER, role="read")
        self.assertEqual(upload.await_args.kwargs["folder"], {"id": "day"})
        self.assertEqual(folders.await_args_list[0].args, ("", "Otto Artifacts"))
        self.assertEqual([call.kwargs["shared"] for call in permissions.await_args_list], [False, False, True])

    async def test_rejects_tampered_content_or_scope_before_upload(self):
        for change in ({"scope": "2" * 32}, {"contentBase64": base64.b64encode(b"<script>x</script>").decode()}):
            with self.subTest(change=change), \
                 patch.object(delivery, "child_folder", AsyncMock()) as folder:
                with self.assertRaises(ValueError):
                    await delivery.publish({**self.result, **change}, expected_scope=SCOPE, recipient=USER)
                folder.assert_not_awaited()

    async def test_failed_invitation_deletes_uploaded_file(self):
        item = {"webUrl": "https://tenant.sharepoint.com/one/file.html", "id": "item"}
        with patch.object(delivery, "child_folder", AsyncMock(side_effect=[{"id": "root"}, {"id": "day"}])), \
             patch.object(delivery, "upload_agent_results_copy", AsyncMock(return_value=item)), \
             patch.object(delivery, "check_permissions", AsyncMock()), \
             patch.object(delivery, "invite_drive_item_user", AsyncMock(side_effect=RuntimeError("denied"))), \
             patch.object(delivery, "delete_drive_item", AsyncMock()) as cleanup:
            with self.assertRaisesRegex(RuntimeError, "denied"):
                await delivery.publish(self.result, expected_scope=SCOPE, recipient=USER)
            cleanup.assert_awaited_once_with(item)

    async def test_permission_inventory_rejects_other_users_and_links(self):
        item = {"id": "i", "parentReference": {"driveId": "d"}}
        owner = {"roles": ["owner"], "grantedToV2": {"user": {"id": OWNER}}}
        reader = {"roles": ["read"], "grantedToV2": {"user": {"id": USER}}}
        async def permissions(values):
            with patch.dict(os.environ, {"AGENT365_AGENT_USER_ID": OWNER}), \
                 patch.object(delivery, "graph_request", AsyncMock(return_value=SimpleNamespace(json=lambda: {"value": values}))):
                await delivery.check_permissions(item, USER, shared=True)
        await permissions([owner, reader])
        for extra in ({"roles": ["read"], "link": {"scope": "anonymous"}},
                      {"roles": ["write"], "grantedToV2": {"user": {"id": USER}}},
                      {"roles": ["read"], "grantedToV2": {"group": {"id": "g"}}}):
            with self.subTest(extra=extra), self.assertRaises(RuntimeError):
                await permissions([owner, reader, extra])

    async def test_current_activity_chooses_recipient_not_model(self):
        ctx = SimpleNamespace(
            activity={"from": {"aadObjectId": USER}},
            send_activity=AsyncMock(return_value=SimpleNamespace(id="sent")),
        )
        auth = AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",), conversation_boundary="one_to_one")

        async def answer(**kwargs):
            scope = kwargs["metadata"]["visualArtifactScope"]
            return AgentResponse(text=f"Here is the artifact. {delivery.START}{scope}{delivery.END}", raw={})

        runtime = SimpleNamespace(render_visual_artifact=AsyncMock(return_value={}))
        with patch.object(app, "agent_auth_context", return_value=auth), \
             patch.object(app, "delivery_reference_metadata", return_value={}), \
             patch.object(app, "invoke_agent_runtime", side_effect=answer), \
             patch.object(app, "runtime_adapter", return_value=runtime), \
             patch.object(app, "publish_visual_document", AsyncMock(return_value="https://tenant.sharepoint.com/a")) as publish, \
             patch.object(app, "teams_runtime_source", return_value="teams_personal"), \
             patch.object(app, "teams_conversation_type", return_value="personal"), \
             patch.object(app, "supports_typing_indicators", return_value=False), \
             patch.object(app, "owner_personal_chat", return_value=False):
            await app.run_agent_runtime_for_teams(ctx, conversation_id="chat", session_key="s", message="Create a report")
        self.assertEqual(publish.await_args.kwargs["recipient"], USER)
        self.assertEqual(publish.await_args.kwargs["expected_scope"], runtime.render_visual_artifact.await_args.args[0])
        self.assertIn("[Open shared artifact](https://tenant.sharepoint.com/a)", ctx.send_activity.await_args.args[0])

    async def test_no_aad_identity_does_not_authorize_publication(self):
        ctx = SimpleNamespace(activity={"from": {"id": USER}}, send_activity=AsyncMock())
        auth = AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",), conversation_boundary="one_to_one")
        async def answer(**kwargs):
            self.assertFalse(kwargs["metadata"]["visualArtifactScope"])
            return AgentResponse(text=f"{delivery.START}{SCOPE}{delivery.END}", raw={})
        with patch.object(app, "agent_auth_context", return_value=auth), \
             patch.object(app, "delivery_reference_metadata", return_value={}), \
             patch.object(app, "invoke_agent_runtime", side_effect=answer), \
             patch.object(app, "publish_visual_document", AsyncMock()) as publish, \
             patch.object(app, "teams_conversation_type", return_value="personal"), \
             patch.object(app, "teams_runtime_source", return_value="teams_personal"), \
             patch.object(app, "supports_typing_indicators", return_value=False):
            await app.run_agent_runtime_for_teams(ctx, conversation_id="chat", session_key="s", message="report")
        publish.assert_not_awaited()
