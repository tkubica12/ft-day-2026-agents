import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.budget_season_ops import require_backup
from scripts.penta_ops import SCENES, require_window, reserve_turn, stage


CONFIG = {
    "prototype": {"deadline_utc": "2099-01-01T00:00:00Z"},
    "limits": {"maximum_live_test_turns": 2},
}


class PentaOpsTests(unittest.TestCase):
    def test_staging_requires_hermes_user_access_before_claiming_success(self):
        outputs = {
            "worker_id": "otto", "sandbox_location": "region", "subscription_id": "sub",
            "resource_group_name": "rg", "sandbox_groups": {"runtime": {"name": "group"}},
            "runtime_sandbox_id": "sandbox",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.penta_ops.settings", return_value={**CONFIG, "azure": {"tenant_id": "tenant"}}),
            patch("scripts.penta_ops.load_json", return_value=outputs),
            patch("scripts.penta_ops.AzureCliCredential"),
            patch("scripts.penta_ops.SandboxGroupClient") as group_type,
        ):
            local = Path(directory) / "role.pdf"
            local.write_bytes(b"test asset")
            sandbox = group_type.return_value.__enter__.return_value.get_sandbox_client.return_value
            sandbox.exec.side_effect = [
                SimpleNamespace(exit_code=0, stdout="missing", stderr=""),
                SimpleNamespace(exit_code=1, stdout="", stderr="Permission denied"),
            ]
            with patch("scripts.penta_ops.scene_files", return_value=[("01_onboarding/role.pdf", local)]):
                with self.assertRaisesRegex(RuntimeError, "asset access"):
                    stage("onboarding")
            access_command = sandbox.exec.call_args.args[0]
            self.assertIn('pwd.getpwnam("hermes")', access_command)
            self.assertIn("os.setuid", access_command)
            self.assertIn("os.access", access_command)
            sandbox.read_file.assert_not_called()

    def test_deadline_is_exclusive(self):
        with self.assertRaisesRegex(RuntimeError, "window"):
            require_window(CONFIG, datetime(2099, 1, 1, tzinfo=timezone.utc))

    def test_one_pending_request_blocks_concurrent_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tests.db"
            body = reserve_turn(path, CONFIG, "one", "otto-operator-onboarding", "Read the documents.")
            with closing(sqlite3.connect(path)) as db:
                persisted = json.loads(db.execute("SELECT body FROM turns").fetchone()[0])
            self.assertEqual(body, persisted)
            with self.assertRaisesRegex(RuntimeError, "pending"):
                reserve_turn(path, CONFIG, "two", "otto-operator-onboarding", "Next.")
            with self.assertRaisesRegex(ValueError, "already"):
                reserve_turn(path, CONFIG, "one", "otto-operator-onboarding", "Retry.")

    def test_completed_and_failed_requests_both_count_toward_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tests.db"
            for label, status in (("one", "completed"), ("two", "failed")):
                reserve_turn(path, CONFIG, label, "otto-operator-test", "Hello.")
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute("UPDATE turns SET status=? WHERE label=?", (status, label))
            with self.assertRaisesRegex(RuntimeError, "ceiling"):
                reserve_turn(path, CONFIG, "three", "otto-operator-test", "Hello.")

    def test_tests_cannot_present_as_human_conversations(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "operator"):
                reserve_turn(Path(directory) / "tests.db", CONFIG, "one", "ladislav-teams", "Hello.")

    def test_rehearsal_persona_is_allowlisted_and_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tests.db"
            with self.assertRaisesRegex(ValueError, "persona"):
                reserve_turn(path, CONFIG, "one", "otto-operator-budget", "Hello.", persona="mallory")
            body = reserve_turn(path, CONFIG, "one", "otto-operator-budget", "Hello.", persona="emma")
            self.assertEqual("emma", body["rehearsalPersona"])
            with self.assertRaisesRegex(ValueError, "delivery reference"):
                reserve_turn(path, CONFIG, "two", "otto-operator-budget", "Hello.", teams_reference="../x")

    def test_reset_requires_a_fresh_matching_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            files = {}
            for name in ("hermes-home.zip", "gateway-journal.sqlite3"):
                (path / name).write_bytes(name.encode())
                files[name] = {"sha256": hashlib.sha256(name.encode()).hexdigest()}
            created = datetime(2099, 1, 1, tzinfo=timezone.utc)
            (path / "receipt.json").write_text(json.dumps({"createdUtc": created.isoformat(), "files": files}))
            require_backup(path, created + timedelta(hours=1))
            with self.assertRaisesRegex(RuntimeError, "too old"):
                require_backup(path, created + timedelta(hours=7))
            (path / "hermes-home.zip").write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                require_backup(path, created)

    def test_only_in_world_assets_are_staged(self):
        all_files = {file for files in SCENES.values() for file in files}
        self.assertTrue(all(not path.startswith(("/", ".")) for path in all_files))
        excluded = {"demo-budget-season.md", "message-for-ladislav.cs.md"}
        self.assertFalse(any(Path(path).name in excluded for path in all_files))

    def test_staging_refuses_to_overwrite_an_edited_workbook(self):
        outputs = {
            "worker_id": "otto", "sandbox_location": "region", "subscription_id": "sub",
            "resource_group_name": "rg", "sandbox_groups": {"runtime": {"name": "group"}},
            "runtime_sandbox_id": "sandbox",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.penta_ops.settings", return_value={**CONFIG, "azure": {"tenant_id": "tenant"}}),
            patch("scripts.penta_ops.load_json", return_value=outputs),
            patch("scripts.penta_ops.AzureCliCredential"),
            patch("scripts.penta_ops.SandboxGroupClient") as group_type,
        ):
            local = Path(directory) / "budget-workbook.xlsx"
            local.write_text("original", encoding="utf-8")
            sandbox = group_type.return_value.__enter__.return_value.get_sandbox_client.return_value
            sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout="edited", stderr="")
            with patch("scripts.penta_ops.scene_files", return_value=[("working/budget-workbook.xlsx", local)]):
                with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite an agent-edited asset"):
                    stage("kickoff")
            sandbox.write_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
