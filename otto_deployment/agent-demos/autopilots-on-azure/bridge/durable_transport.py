from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from azure.identity import ManagedIdentityCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from bridge.local_state import locked_file


CONNECTOR_API = "2026-05-01-preview"
WIRE_SCHEMA = 2
MAX_MESSAGE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
logger = logging.getLogger(__name__)
delivery_identity: ContextVar[str] = ContextVar("transport_delivery", default="")
delivery_queue: ContextVar[str] = ContextVar("transport_queue", default="")


class TransportConflictError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def identity(*parts: str) -> str:
    return hashlib.sha256(canonical(parts).encode("utf-8")).hexdigest()


def effect_payload(body: dict[str, Any]) -> str:
    value = json.loads(canonical(body))
    value.pop("trace", None)
    if value.get("kind") == "activity":
        conversation = value.get("payload", {}).get("conversation")
        if isinstance(conversation, dict):
            conversation.pop("claims", None)
    return canonical(value)


def enabled() -> bool:
    return bool(os.getenv("TRANSPORT_CONNECTION_ID", "").strip())


@dataclass(frozen=True)
class TransportSettings:
    worker: str
    role: str
    identity_client_id: str
    namespace: str
    connection_id: str
    activities_queue: str
    schedule_queue: str
    journal_path: Path

    @classmethod
    def from_environment(cls) -> TransportSettings:
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required for native event transport.")
            return value

        role = required("TRANSPORT_ROLE")
        if role != "gateway":
            raise ValueError("Managed recovery processing belongs to the gateway.")
        return cls(
            worker=required("WORKER_ID"),
            role=role,
            identity_client_id=required("AZURE_CLIENT_ID"),
            namespace=required("SCHEDULER_SERVICEBUS_NAMESPACE"),
            connection_id=required("TRANSPORT_CONNECTION_ID"),
            activities_queue=required("TRANSPORT_ACTIVITIES_QUEUE"),
            schedule_queue=os.getenv("SCHEDULER_SERVICEBUS_QUEUE", "").strip(),
            journal_path=Path(required("TRANSPORT_JOURNAL_PATH")),
        )

    @property
    def receiving_queues(self) -> tuple[str, ...]:
        return tuple(queue for queue in (self.activities_queue, self.schedule_queue) if queue)

    def message(self, kind: str, payload: dict[str, Any], correlation: str) -> dict[str, Any]:
        from bridge.telemetry import runtime_trace_headers

        if kind != "activity":
            raise ValueError(f"Unsupported transport message kind: {kind!r}.")
        result = {
            "schema": WIRE_SCHEMA, "worker": self.worker, "message_id": correlation,
            "kind": kind, "payload": payload,
            "trace": runtime_trace_headers(),
        }
        if not re.fullmatch(r"[a-f0-9]{64}", correlation):
            raise ValueError("Transport correlation must be a SHA256 identity.")
        if len(canonical(result).encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("Transport envelope exceeds the 64 KiB limit; publish an authenticated payload reference.")
        return result


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS deliveries (
                    queue TEXT NOT NULL, message_id TEXT NOT NULL,
                    payload TEXT NOT NULL, lock_token TEXT NOT NULL,
                    connection_id TEXT NOT NULL, delivery_count INTEGER NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'pending',
                    result TEXT NOT NULL DEFAULT '',
                    settled INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    native_scope TEXT NOT NULL DEFAULT '', admission_at REAL NOT NULL DEFAULT 0,
                    native_run_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(queue, message_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS responses (
                    message_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS conversation_state (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL
                )
            """)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def admit(
        self, queue: str, envelope: dict[str, Any], body: dict[str, Any], connection_id: str,
    ) -> dict[str, Any]:
        message_id = str(envelope["MessageId"])
        payload = canonical(body)
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM deliveries WHERE queue=? AND message_id=?", (queue, message_id),
            ).fetchone()
            if row:
                if effect_payload(json.loads(row["payload"])) != effect_payload(body):
                    raise ValueError("A duplicate broker MessageId changed its payload.")
                if row["lock_token"] == envelope["LockToken"] and row["connection_id"] != connection_id:
                    raise ValueError("The same PeekLock lease cannot change its owning connection.")
                connection.execute("""
                    UPDATE deliveries SET
                    settled=CASE WHEN lock_token<>? THEN 0 ELSE settled END,
                    lock_token=?, connection_id=?, delivery_count=?
                    WHERE queue=? AND message_id=?
                """, (envelope["LockToken"], envelope["LockToken"], connection_id,
                      envelope["Properties"]["DeliveryCount"], queue, message_id))
            else:
                pending = connection.execute(
                    "SELECT count(*) FROM deliveries WHERE phase NOT IN ('completed','failed','unknown')",
                ).fetchone()[0]
                if pending >= 32:
                    raise RuntimeError("The bounded transport inbox is full; leave the lease for redelivery.")
                connection.execute("""
                    INSERT INTO deliveries(queue,message_id,payload,lock_token,connection_id,delivery_count)
                    VALUES (?,?,?,?,?,?)
                """, (queue, message_id, payload, envelope["LockToken"], connection_id,
                      envelope["Properties"]["DeliveryCount"]))
        return self.get(queue, message_id)

    def reserve_work(self, queue: str, body: dict[str, Any], connection_id: str) -> dict[str, Any]:
        message_id = body["message_id"]
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload,connection_id FROM deliveries WHERE queue=? AND message_id=?", (queue, message_id),
            ).fetchone()
            if row is not None:
                if effect_payload(json.loads(row["payload"])) != effect_payload(body) or row["connection_id"] != connection_id:
                    raise TransportConflictError("An existing work identity changed its payload or owning connection.")
            else:
                count = connection.execute(
                    "SELECT count(*) FROM deliveries WHERE phase NOT IN ('completed','failed','unknown')",
                ).fetchone()[0]
                if count >= 32:
                    raise RuntimeError("The bounded durable work inbox is full.")
                connection.execute(
                    "INSERT INTO deliveries(queue,message_id,payload,lock_token,connection_id,delivery_count) "
                    "VALUES (?,?,?,?,?,0)", (queue, message_id, canonical(body), "", connection_id),
                )
        return self.get(queue, message_id)

    def get(self, queue: str, message_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE queue=? AND message_id=?", (queue, message_id),
            ).fetchone()
        if row is None:
            raise ValueError("No durable receipt exists for this delivery.")
        return dict(row)

    def update(self, queue: str, message_id: str, **values: Any) -> None:
        permitted = {
            "phase", "result", "settled", "error",
            "native_scope", "admission_at", "native_run_id",
        }
        if not values or set(values) - permitted:
            raise ValueError("Invalid transport receipt update.")
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE deliveries SET " + ",".join(f"{key}=?" for key in values)
                + " WHERE queue=? AND message_id=?", (*values.values(), queue, message_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot update a missing transport receipt.")

    def save_response(self, message_id: str, payload: dict[str, Any]) -> None:
        serialized = canonical(payload)
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM responses WHERE message_id=?", (message_id,),
            ).fetchone()
            if row and row["payload"] != serialized:
                raise ValueError("A correlated result changed after durable delivery.")
            connection.execute(
                "INSERT OR IGNORE INTO responses(message_id,payload) VALUES (?,?)",
                (message_id, serialized),
            )

    def response(self, message_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM responses WHERE message_id=?", (message_id,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(self.path.with_suffix(self.path.suffix + ".backup.lock")):
            descriptor, name = tempfile.mkstemp(prefix="transport-checkpoint-", suffix=".tmp")
            os.close(descriptor)
            local = Path(name)
            staged = destination.with_name(f".{destination.name}-{local.name}.next")
            try:
                with closing(self.connect()) as source, closing(sqlite3.connect(local)) as target:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Transport journal backup failed SQLite integrity validation.")
                shutil.copyfile(local, staged)
                staged.replace(destination)
            finally:
                local.unlink(missing_ok=True)
                staged.unlink(missing_ok=True)

    def load_state(self, key: str) -> Any:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM conversation_state WHERE key=?", (key,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_state(self, key: str, payload: Any) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO conversation_state(key,payload) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload", (key, canonical(payload)),
            )

    def save_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("A private transport payload must be an object.")
        serialized = canonical(payload)
        raw = serialized.encode("utf-8")
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise ValueError("Private transport payload exceeds 256 MiB.")
        digest = hashlib.sha256(raw).hexdigest()
        key = "payload:" + digest
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM conversation_state WHERE key=?", (key,),
            ).fetchone()
            if row and row["payload"] != serialized:
                raise ValueError("An existing content-addressed payload failed integrity validation.")
            connection.execute(
                "INSERT OR IGNORE INTO conversation_state(key,payload) VALUES (?,?)", (key, serialized),
            )
        return {"payloadSha256": digest, "payloadBytes": len(raw)}

    def payload_bytes(self, digest: str) -> bytes | None:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid content-addressed payload identity.")
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM conversation_state WHERE key=?", ("payload:" + digest,),
            ).fetchone()
        if row is None:
            return None
        raw = row["payload"].encode("utf-8")
        if len(raw) > MAX_PAYLOAD_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("The private transport payload failed checksum validation.")
        return raw

    def load_payload(self, reference: dict[str, Any]) -> dict[str, Any] | None:
        if set(reference) != {"payloadSha256", "payloadBytes"}:
            raise ValueError("A queued payload must be an exact content-addressed reference.")
        size = reference["payloadBytes"]
        if type(size) is not int or not 0 < size <= MAX_PAYLOAD_BYTES:
            raise ValueError("Invalid private transport payload size.")
        raw = self.payload_bytes(reference["payloadSha256"])
        if raw is None:
            return None
        if len(raw) != size:
            raise ValueError("Private transport payload length differs from its reference.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Private transport payload is not an object.")
        return value

    def reserve_outbound(
        self, message_id: str, reference: dict[str, Any], effect_sha256: str,
    ) -> dict[str, Any]:
        key = "outbound:" + message_id
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM conversation_state WHERE key=?", (key,),
            ).fetchone()
            if row:
                existing = json.loads(row["payload"])
                if existing["effectSha256"] != effect_sha256:
                    raise TransportConflictError("An idempotency identity was reused with different authenticated content.")
                return existing["reference"]
            connection.execute(
                "INSERT INTO conversation_state(key,payload) VALUES (?,?)",
                (key, canonical({"effectSha256": effect_sha256, "reference": reference})),
            )
        return reference


def decode_callback(
    encoded: str, queue: str, settings: TransportSettings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if queue not in settings.receiving_queues:
        raise ValueError("Native callback queue does not belong to this settling actor.")
    if len(encoded) > MAX_MESSAGE_BYTES * 3:
        raise ValueError("Native callback exceeds the bounded envelope size.")
    envelope = json.loads(base64.b64decode(encoded, validate=True))
    body = json.loads(base64.b64decode(envelope["ContentData"], validate=True))
    message_id = envelope.get("MessageId")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
        raise ValueError("Native callback has no bounded broker MessageId.")
    if not isinstance(envelope.get("LockToken"), str) or not envelope["LockToken"]:
        raise ValueError("Native callback has no PeekLock token.")
    count = envelope.get("Properties", {}).get("DeliveryCount")
    if isinstance(count, bool) or not str(count).isdigit() or int(count) < 1:
        raise ValueError("Native callback has no valid broker delivery count.")
    envelope["Properties"]["DeliveryCount"] = int(count)
    if not isinstance(body, dict):
        raise ValueError("Native callback body must be an object.")
    carrier = body.get("trace", {})
    if not isinstance(carrier, dict) or any(
        name not in {"traceparent", "tracestate", "x-autopilot-otel-session", "x-autopilot-otel-operation"}
        or not isinstance(value, str)
        for name, value in carrier.items()
    ):
        raise ValueError("The transport trace carrier contains unsupported fields.")
    if queue == settings.schedule_queue:
        if body.get("version") != "1.0" or body.get("type") not in {
            "hermes.cron.fire", "system.dream", "document.publish.retry",
        }:
            raise ValueError("Native schedule callback contains an unsupported job.")
        if body.get("workerId") != settings.worker:
            raise ValueError("Native scheduled job belongs to another Worker.")
        if body["type"] == "document.publish.retry" and not body.get("operationId"):
            raise ValueError("Native document retry requires its operation identity.")
    else:
        if (
            body.get("schema") != WIRE_SCHEMA or body.get("worker") != settings.worker
            or body.get("message_id") != message_id or body.get("kind") != "activity"
            or not isinstance(body.get("payload"), dict)
        ):
            raise ValueError("Callback identity, worker, kind or schema does not match its queue.")
    return envelope, body


class NativeBus:
    def __init__(self, settings: TransportSettings, credential: Any) -> None:
        self.settings = settings
        self.credential = credential

    def send(self, queue: str, body: dict[str, Any]) -> None:
        allowed = (self.settings.activities_queue,)
        if queue not in allowed:
            raise ValueError("This actor cannot publish to the requested transport queue.")
        serialized = canonical(body)
        if len(serialized.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("Transport message exceeds the explicit broker size budget.")
        with ServiceBusClient(self.settings.namespace, self.credential) as client:
            with client.get_queue_sender(queue) as sender:
                sender.send_messages(ServiceBusMessage(
                    serialized, message_id=body["message_id"], correlation_id=body["message_id"],
                    content_type="application/json",
                ))

    def settle(self, queue: str, operation: str, lock_token: str) -> None:
        if queue not in self.settings.receiving_queues or operation not in {"renewlock", "complete", "deadletter"}:
            raise ValueError("Unsupported queue or native settlement operation.")
        queries = {"lockToken": lock_token, "sessionId": ""}
        if operation == "deadletter":
            queries.update(
                deadLetterReason="autopilots_quarantined",
                deadLetterErrorDescription=(
                    "Execution outcome requires operator inspection of the actor journal. "
                    "Automatic replay is blocked."
                ),
            )
        else:
            queries["queueType"] = "Main"
        token = self.credential.get_token("https://management.azure.com/.default").token
        request = urllib.request.Request(
            f"https://management.azure.com{self.settings.connection_id}/dynamicInvoke"
            f"?api-version={CONNECTOR_API}",
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=canonical({"request": {
                "method": "DELETE" if operation == "complete" else "POST",
                "path": f"/{quote(queue, safe='')}/messages/{operation}",
                "queries": queries,
            }}).encode("utf-8"),
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
        status = result.get("response", {}).get("statusCode")
        if status not in (200, 204, "OK", "NoContent"):
            raise RuntimeError(f"Native {operation} failed with nested status {status!r}.")


@cache
def settings() -> TransportSettings:
    return TransportSettings.from_environment()


@cache
def journal() -> Journal:
    return Journal(settings().journal_path)


@contextmanager
def bus() -> Iterator[NativeBus]:
    with ManagedIdentityCredential(client_id=settings().identity_client_id) as credential:
        yield NativeBus(settings(), credential)
