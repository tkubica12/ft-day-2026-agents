from __future__ import annotations

import base64
import io
import json
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.durable_transport import (
    MAX_MESSAGE_BYTES, Journal, NativeBus, TransportSettings, canonical, decode_callback, identity,
)


class DurableTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = TransportSettings(
            "worker", "gateway", "client", "bus.servicebus.windows.net",
            "/connector/connections/gateway", "activities",
            "schedule", Path(self.temporary.name) / "journal.db",
        )
        self.message_id = identity("activity", "one")
        self.body = self.settings.message("activity", {"text": "hello"}, self.message_id)
        self.envelope = {
            "MessageId": self.message_id, "LockToken": "lease-one",
            "Properties": {"DeliveryCount": 1},
            "ContentData": base64.b64encode(canonical(self.body).encode()).decode(),
        }
        self.journal = Journal(self.settings.journal_path)

    def encoded(self):
        return base64.b64encode(json.dumps(self.envelope).encode()).decode()

    def test_exact_native_envelope_is_accepted(self):
        envelope, body = decode_callback(self.encoded(), "activities", self.settings)
        self.assertEqual(body, self.body)
        self.assertEqual(envelope["Properties"]["DeliveryCount"], 1)

    def test_wrong_actor_cannot_settle_runtime_queue(self):
        with self.assertRaisesRegex(ValueError, "settling actor"):
            decode_callback(self.encoded(), "requests", self.settings)

    def test_worker_mismatch_is_rejected(self):
        self.body["worker"] = "another-worker"
        self.envelope["ContentData"] = base64.b64encode(canonical(self.body).encode()).decode()
        with self.assertRaisesRegex(ValueError, "worker"):
            decode_callback(self.encoded(), "activities", self.settings)

    def test_boolean_delivery_count_is_not_an_integer(self):
        self.envelope["Properties"]["DeliveryCount"] = True
        with self.assertRaisesRegex(ValueError, "delivery count"):
            decode_callback(self.encoded(), "activities", self.settings)

    def test_native_schedule_keeps_trace_and_rejects_private_carrier_fields(self):
        body = {
            "version": "1.0", "type": "hermes.cron.fire", "workerId": "worker",
            "jobId": "job", "revision": "revision",
            "trace": {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
        }
        self.envelope["ContentData"] = base64.b64encode(canonical(body).encode()).decode()
        _, decoded = decode_callback(self.encoded(), "schedule", self.settings)
        self.assertEqual(decoded["trace"], body["trace"])
        body["trace"]["baggage"] = "private"
        self.envelope["ContentData"] = base64.b64encode(canonical(body).encode()).decode()
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            decode_callback(self.encoded(), "schedule", self.settings)

    def test_native_schedule_rejects_another_worker_or_missing_document_operation(self):
        for body in (
            {"version": "1.0", "type": "hermes.cron.fire", "workerId": "another"},
            {"version": "1.0", "type": "document.publish.retry", "workerId": "worker"},
            {"version": "2.0", "type": "hermes.cron.fire", "workerId": "worker"},
        ):
            with self.subTest(body=body):
                self.envelope["ContentData"] = base64.b64encode(canonical(body).encode()).decode()
                with self.assertRaises(ValueError):
                    decode_callback(self.encoded(), "schedule", self.settings)

    def test_changed_duplicate_payload_cannot_start_effects(self):
        self.journal.admit("activities", self.envelope, self.body, "connection")
        self.body["payload"]["text"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed its payload"):
            self.journal.admit("activities", self.envelope, self.body, "connection")

    def test_same_lease_cannot_change_connection(self):
        self.journal.admit("activities", self.envelope, self.body, "connection")
        with self.assertRaisesRegex(ValueError, "owning connection"):
            self.journal.admit("activities", self.envelope, self.body, "other")

    def test_trace_changes_do_not_turn_exact_redelivery_into_a_different_effect(self):
        self.journal.admit("activities", self.envelope, self.body, "connection")
        self.body["trace"] = {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}
        row = self.journal.admit("activities", self.envelope, self.body, "connection")
        self.assertEqual(row["phase"], "pending")

    def test_redelivery_keeps_result_and_reopens_only_settlement(self):
        self.journal.admit("activities", self.envelope, self.body, "connection")
        self.journal.update(
            "activities", self.message_id, phase="completed", result='{"done":true}', settled=1,
        )
        self.envelope.update(LockToken="lease-two", Properties={"DeliveryCount": 2})
        row = self.journal.admit("activities", self.envelope, self.body, "connection")
        self.assertEqual(row["phase"], "completed")
        self.assertEqual(row["result"], '{"done":true}')
        self.assertEqual(row["settled"], 0)
        self.assertEqual(row["delivery_count"], 2)

    def test_response_replay_is_durable_and_immutable(self):
        self.journal.save_response(self.message_id, {"text": "answer"})
        reopened = Journal(self.settings.journal_path)
        self.assertEqual(reopened.response(self.message_id), {"text": "answer"})
        with self.assertRaisesRegex(ValueError, "changed"):
            reopened.save_response(self.message_id, {"text": "different"})

    def test_backup_is_an_independent_valid_database(self):
        self.journal.admit("activities", self.envelope, self.body, "connection")
        destination = Path(self.temporary.name) / "backup" / "journal.db"
        self.journal.backup(destination)
        restored = Journal(destination)
        self.assertEqual(restored.get("activities", self.message_id)["payload"], canonical(self.body))

    def test_backup_never_opens_sqlite_on_blob_destination(self):
        destination = Path(self.temporary.name) / "blob" / "journal.db"
        original = sqlite3.connect
        opened = []

        def connect(path, *args, **kwargs):
            opened.append(Path(path))
            self.assertNotEqual(Path(path).parent, destination.parent)
            return original(path, *args, **kwargs)

        with patch("bridge.durable_transport.sqlite3.connect", side_effect=connect):
            self.journal.backup(destination)
        self.assertIn(self.journal.path, opened)
        self.assertTrue(destination.is_file())

    def test_concurrent_checkpoint_publications_are_serialized(self):
        destination = Path(self.temporary.name) / "backup" / "journal.db"
        active = 0
        maximum = 0
        guard = threading.Lock()
        copy = shutil.copyfile

        def publish(source, target):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.01)
                return copy(source, target)
            finally:
                with guard:
                    active -= 1

        with patch("bridge.durable_transport.shutil.copyfile", side_effect=publish):
            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(lambda _: self.journal.backup(destination), range(18)))
        self.assertEqual(maximum, 1)
        with closing(sqlite3.connect(destination)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_failed_checkpoint_copy_keeps_previous_backup_and_removes_staging(self):
        destination = Path(self.temporary.name) / "backup" / "journal.db"
        self.journal.backup(destination)
        before = destination.read_bytes()
        self.journal.save_state("newer", {"value": "must not corrupt prior checkpoint"})
        original = shutil.copyfile

        def fail_copy(source, target):
            original(source, target)
            raise OSError("Injected publication failure")

        with patch("bridge.durable_transport.shutil.copyfile", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "publication failure"):
                self.journal.backup(destination)
        self.assertEqual(destination.read_bytes(), before)
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_payload_limit_is_enforced_before_broker_send(self):
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            self.settings.message("activity", {"text": "x" * MAX_MESSAGE_BYTES}, self.message_id)

    def test_large_document_is_private_payload_not_a_large_broker_message(self):
        payload = {"prompt": "document " * 20000}
        reference = self.journal.save_payload(payload)
        message = self.settings.message("activity", reference, self.message_id)
        self.assertLess(len(canonical(message).encode()), MAX_MESSAGE_BYTES)
        self.assertEqual(self.journal.load_payload(reference), payload)
        self.assertNotIn("document", canonical(message))

    def test_operator_identity_cannot_be_reused_with_another_prompt(self):
        first = self.journal.save_payload({"prompt": "one"})
        second = self.journal.save_payload({"prompt": "two"})
        self.journal.reserve_outbound(self.message_id, first, first["payloadSha256"])
        with self.assertRaisesRegex(ValueError, "different authenticated content"):
            self.journal.reserve_outbound(self.message_id, second, second["payloadSha256"])

    def test_missing_receipt_cannot_be_updated(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.journal.update("activities", self.message_id, phase="completed")

    def test_invalid_receipt_columns_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.journal.update("activities", self.message_id, lock_token="other")

    def test_same_id_in_other_queue_is_independent(self):
        self.journal.admit("activities", self.envelope, self.body, "connection")
        self.journal.admit("results", self.envelope, self.body, "connection")
        self.journal.update("results", self.message_id, phase="completed")
        self.assertEqual(self.journal.get("activities", self.message_id)["phase"], "pending")

    def test_quarantine_uses_owning_connection_deadletter_action_without_private_error_text(self):
        credential = SimpleNamespace(get_token=lambda *args: SimpleNamespace(token="unit-test-credential"))
        with patch(
            "bridge.durable_transport.urllib.request.urlopen",
            return_value=io.BytesIO(b'{"response":{"statusCode":204}}'),
        ) as send:
            NativeBus(self.settings, credential).settle("activities", "deadletter", "lease")
        request = send.call_args.args[0]
        self.assertIn(self.settings.connection_id + "/dynamicInvoke", request.full_url)
        operation = json.loads(request.data)["request"]
        self.assertEqual((operation["method"], operation["path"]), ("POST", "/activities/messages/deadletter"))
        self.assertEqual(operation["queries"]["lockToken"], "lease")
        self.assertEqual(operation["queries"]["deadLetterReason"], "autopilots_quarantined")


if __name__ == "__main__":
    unittest.main()
