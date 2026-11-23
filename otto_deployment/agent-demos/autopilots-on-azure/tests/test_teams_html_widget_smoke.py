import base64
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from microsoft_agents.activity import Activity

from scripts.teams_html_widget_smoke import READ_PERSONAL_REFERENCE, widget_activity


class WidgetSmokeTests(unittest.TestCase):
    def test_activity_sdk_preserves_wire_format_and_offline_host_bootstrap(self):
        activity = Activity.model_validate(widget_activity("abc123"))
        encoded = activity.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(encoded["textFormat"], "extendedmarkdown")
        payload = json.loads(encoded["text"].split("```html-widget\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(payload["type"], "widget/mcp-ui")
        self.assertEqual(payload["domain"], "https://teams.microsoft.com")
        markup = payload["html"]
        self.assertIn("ui/initialize", markup)
        self.assertIn("ui/notifications/initialized", markup)
        self.assertIn("ui/notifications/size-changed", markup)
        self.assertNotIn("<button", markup)
        self.assertNotIn("fetch(", markup)
        self.assertNotIn("<script src=", markup)
        self.assertIn("Fictional Javorna Group demo data", markup)

    def test_counter_is_local_and_waits_for_host_initialization(self):
        encoded = widget_activity("abc123", interactive=True)["text"]
        markup = json.loads(encoded.split("```html-widget\n", 1)[1].split("\n```", 1)[0])["html"]
        self.assertIn('id="increment" disabled', markup)
        self.assertGreater(markup.index(".disabled=false"), markup.index("ready=true"))
        self.assertIn('"tokens": {}', markup)

    def test_probe_id_cannot_inject_html_or_a_fence(self):
        for value in ("", "<script>", "```", "číslo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                widget_activity(value)

    def test_reference_requires_the_explicit_existing_key_and_expected_worker(self):
        config = {"environment": {"WORKER_ID": "otto"}}
        reference = {"boundary": "one_to_one", "conversation": {"id": "owner-chat"}}
        references = {"teams-owner-chat": reference, "other-chat": {"boundary": "shared"}}
        for worker, key, error in (
            ("otto", "teams-owner-chat", None),
            ("otto", "missing", KeyError),
            ("another-worker", "teams-owner-chat", RuntimeError),
        ):
            with self.subTest(worker=worker, key=key), patch(
                "pathlib.Path.read_text", side_effect=[json.dumps(config), json.dumps(references)],
            ), patch("sys.argv", ["probe", worker, key]), contextlib.redirect_stdout(io.StringIO()) as output:
                if error:
                    with self.assertRaises(error):
                        exec(READ_PERSONAL_REFERENCE, {})
                else:
                    exec(READ_PERSONAL_REFERENCE, {})
                    self.assertEqual(json.loads(base64.b64decode(output.getvalue())), reference)
