import unittest

from scripts.penta_toolbox import WEBIQ_ENDPOINT, connection_properties, is_web_tool, toolbox_tools


class PentaToolboxTests(unittest.TestCase):
    def test_connection_contains_no_credentials(self):
        properties = connection_properties()
        self.assertEqual(properties["authType"], "ProjectManagedIdentity")
        self.assertEqual(properties["audience"], "https://api.microsoft.ai")
        self.assertEqual(properties["target"], WEBIQ_ENDPOINT)
        self.assertFalse(properties["isSharedToAll"])
        self.assertNotIn("credentials", properties)

    def test_only_read_only_web_and_news_are_exposed(self):
        tool = toolbox_tools("connection")[0]
        self.assertEqual(tool["allowed_tools"], ["web", "news"])
        self.assertEqual(tool["project_connection_id"], "connection")
        self.assertIn("Never include confidential", tool["description"])

    def test_recognizes_foundry_web_tool_names(self):
        for name in ("web", "webiq.web", "webiq___web"):
            self.assertTrue(is_web_tool(name), name)
        for name in ("webiq___news", "other.web", "unrelated___web"):
            self.assertFalse(is_web_tool(name), name)


if __name__ == "__main__":
    unittest.main()
