from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import os
import secrets
import shutil
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from bridge.durable_transport import (
    Journal, bus, canonical, decode_callback, delivery_identity, delivery_queue, effect_payload,
    enabled, identity, journal, settings,
)
from bridge.telemetry import operation_span, runtime_correlation_context


logger = logging.getLogger(__name__)
processing_activity: ContextVar[bool] = ContextVar("processing_durable_activity", default=False)
turn_tasks: ContextVar[list[asyncio.Task[Any]] | None] = ContextVar("durable_turn_tasks", default=None)
_active: dict[tuple[str, str], asyncio.Task[None]] = {}
KEEPALIVE_INTERVAL_SECONDS = 60
KEEPALIVE_RETRY_SECONDS = 5
KEEPALIVE_FAILURE_LIMIT = 5


class InvocationPendingError(TimeoutError):
    pass


def initialize_journal() -> Journal:
    config = settings()
    backup = os.getenv("TRANSPORT_JOURNAL_BACKUP", "").strip()
    if not backup:
        raise ValueError("Native transport requires a durable journal backup destination.")
    if not config.journal_path.exists() and Path(backup).exists():
        config.journal_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup, config.journal_path)
    instance = journal()
    with instance.connect() as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The restored transport journal failed SQLite integrity validation.")
    return instance


async def checkpoint() -> None:
    destination = os.getenv("TRANSPORT_JOURNAL_BACKUP", "").strip()
    if not destination:
        raise RuntimeError("Transport journal backup is not configured.")
    await asyncio.to_thread(journal().backup, Path(destination))


def load_work_payload(reference: dict[str, Any]) -> dict[str, Any]:
    payload = journal().load_payload(reference)
    if payload is None:
        raise RuntimeError("The gateway lost its private work payload; restore its verified journal.")
    return payload


async def enqueue_activity(context: Any) -> str:
    from microsoft_agents.hosting.core.app.proactive import Conversation

    activity = context.activity
    if not activity.id or not activity.conversation or not activity.conversation.id:
        raise ValueError("A durable channel activity requires its original activity and conversation IDs.")
    conversation = Conversation.from_turn_context(context)
    conversation.validate()
    payload = {
        "activity": activity.model_dump(mode="json", by_alias=True, exclude_none=True),
        "conversation": conversation.store_item_to_json(),
    }
    correlation = identity(
        settings().worker, str(activity.channel_id), str(activity.conversation.id),
        str(activity.type), str(activity.id),
    )
    reference = journal().save_payload(payload)
    reference = journal().reserve_outbound(
        correlation, reference,
        hashlib.sha256(effect_payload({"kind": "activity", "payload": payload}).encode()).hexdigest(),
    )
    message = settings().message("activity", reference, correlation)
    journal().reserve_work(settings().activities_queue, message, settings().connection_id)
    await checkpoint()
    with bus() as sender:
        await asyncio.to_thread(sender.send, settings().activities_queue, message)
    return correlation


async def enqueue_operator(operation: str, request_id: str, payload: dict[str, Any]) -> str:
    if operation not in {"invoke", "dream", "scheduled_learning"}:
        raise ValueError("Unsupported durable operator operation.")
    correlation = identity(settings().worker, "operator-job", request_id)
    body = {"operatorRequest": {"operation": operation, "requestId": request_id, "payload": payload}}
    reference = journal().save_payload(body)
    reference = journal().reserve_outbound(correlation, reference, reference["payloadSha256"])
    message = settings().message("activity", reference, correlation)
    journal().reserve_work(settings().activities_queue, message, settings().connection_id)
    await checkpoint()
    if journal().response(correlation) is None:
        with bus() as sender:
            await asyncio.to_thread(
                sender.send, settings().activities_queue, message,
            )
    return correlation


async def _renew(queue: str, message_id: str) -> None:
    with bus() as receiver:
        while True:
            row = journal().get(queue, message_id)
            if row["connection_id"] != settings().connection_id:
                raise RuntimeError("A native lease changed its owning connection during processing.")
            if not row["lock_token"]:
                await asyncio.sleep(1)
                continue
            await asyncio.to_thread(receiver.settle, queue, "renewlock", row["lock_token"])
            await asyncio.sleep(40)


async def _keep_active() -> None:
    url = os.getenv("TRANSPORT_SELF_URL", "").rstrip("/")
    if url:
        health_url = url + "/health"
    else:
        health_url = ""
    if not health_url.startswith("https://"):
        raise ValueError("Active transport work requires its native HTTPS self-ingress URL.")
    failures = 0
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            try:
                response = await client.get(health_url)
                response.raise_for_status()
            except httpx.HTTPError as error:
                failures += 1
                if failures >= KEEPALIVE_FAILURE_LIMIT:
                    raise
                logger.warning(
                    "Gateway self-ingress keepalive failed (%s); consecutive failure %d of %d.",
                    type(error).__name__, failures, KEEPALIVE_FAILURE_LIMIT,
                )
                await asyncio.sleep(KEEPALIVE_RETRY_SECONDS)
                continue
            failures = 0
            await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)


async def _guarded(work: Awaitable[dict[str, Any]], queue: str, message_id: str) -> dict[str, Any]:
    worker = asyncio.create_task(work)
    renewer = asyncio.create_task(_renew(queue, message_id))
    keepalive = asyncio.create_task(_keep_active())
    tasks = (worker, renewer, keepalive)
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800")),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError("Native transport work exceeded its bounded active-operation window.")
        if worker in done:
            return await worker
        for task in done:
            await task
        raise RuntimeError("A native transport guard exited before work completed.")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _process(
    queue: str, message_id: str,
    gateway_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
    body = json.loads(journal().get(queue, message_id)["payload"])
    carrier = body.get("trace", {})
    with runtime_correlation_context(carrier), operation_span(
        "transport.process", carrier=carrier, operation_id=message_id,
        attributes={"messaging.system": "servicebus", "messaging.destination.name": queue},
    ):
        await _execute_delivery(queue, message_id, gateway_handler, schedule_handler)


async def _execute_delivery(
    queue: str, message_id: str,
    gateway_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
    token = delivery_identity.set(message_id)
    queue_token = delivery_queue.set(queue)
    gateway_started = False
    gateway_payload: dict[str, Any] | None = None

    def recoverable_gateway() -> bool:
        operator = (gateway_payload or {}).get("operatorRequest")
        return isinstance(operator, dict) and (
            operator.get("operation") == "invoke" or journal().response(message_id) is not None
        )

    try:
        row = journal().get(queue, message_id)
        if row["settled"]:
            return
        body = json.loads(row["payload"])
        if row["phase"] in {"unknown", "failed"}:
            raise RuntimeError(f"Delivery requires inspection: {row['phase']}: {row['error']}")
        if row["phase"] == "running" and queue == settings().activities_queue:
            gateway_payload = load_work_payload(body["payload"])
            if not recoverable_gateway():
                journal().update(
                    queue, message_id, phase="unknown",
                    error=row["error"] or "Gateway activity was interrupted after effects began; automatic replay is blocked.",
                )
                await checkpoint()
                raise RuntimeError("Interrupted gateway activity has an unknown external-effect outcome.")
        if row["phase"] != "completed":
            journal().update(queue, message_id, phase="running")
            await checkpoint()
            if queue == settings().activities_queue and gateway_handler is not None:
                async def receive_activity() -> dict[str, Any]:
                    nonlocal gateway_started, gateway_payload
                    gateway_payload = load_work_payload(body["payload"])
                    gateway_started = True
                    return await gateway_handler(gateway_payload)
                work = receive_activity()
            elif queue == settings().schedule_queue and schedule_handler is not None:
                work = schedule_handler(body)
            else:
                raise ValueError("No handler exists for the actor's admitted queue.")
            result = await _guarded(work, queue, message_id)
            journal().update(queue, message_id, phase="completed", result=canonical(result), error="")
            await checkpoint()
        row = journal().get(queue, message_id)
        with bus() as receiver:
            current = journal().get(queue, message_id)
            if current["lock_token"]:
                await asyncio.to_thread(receiver.settle, queue, "complete", current["lock_token"])
                journal().update(queue, message_id, settled=1)
                await checkpoint()
    except Exception as error:
        logger.exception("Native transport delivery failed: queue=%s correlation=%s", queue, message_id)
        values: dict[str, Any] = {"error": f"{type(error).__name__}: {str(error)[:1500]}"}
        current = journal().get(queue, message_id)
        if current["phase"] in {"unknown", "failed"} and current["error"]:
            values["error"] = current["error"]
        if queue == settings().activities_queue and current["phase"] == "running":
            if gateway_started and not recoverable_gateway():
                values["phase"] = "unknown"
            elif not gateway_started and row["phase"] != "running":
                values["phase"] = "pending"
        journal().update(queue, message_id, **values)
        await checkpoint()
        current = journal().get(queue, message_id)
        if current["phase"] in {"unknown", "failed"} and not current["settled"] and current["lock_token"]:
            with bus() as receiver:
                await asyncio.to_thread(receiver.settle, queue, "deadletter", current["lock_token"])
            journal().update(queue, message_id, settled=1)
            await checkpoint()
        raise
    finally:
        delivery_queue.reset(queue_token)
        delivery_identity.reset(token)

def start_work(
    queue: str, message_id: str,
    gateway_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
    key = (queue, message_id)
    previous = _active.get(key)
    if previous is not None and not previous.done():
        return
    task = asyncio.create_task(_process(queue, message_id, gateway_handler, schedule_handler))
    _active[key] = task

    def completed(done: asyncio.Task[None]) -> None:
        error = None if done.cancelled() else done.exception()
        if error is not None:
            logger.error("Durable work stopped: queue=%s correlation=%s: %s", queue, message_id, error)
        if _active.get(key) is done:
            _active.pop(key, None)
            if error is None and not done.cancelled():
                row = journal().get(queue, message_id)
                if row["phase"] == "completed" and row["lock_token"] and not row["settled"]:
                    start_work(queue, message_id, gateway_handler, schedule_handler)

    task.add_done_callback(completed)


def attach(
    app: FastAPI, *,
    gateway_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> None:
    if not enabled():
        return
    initialize_journal()

    @app.middleware("http")
    async def maintenance_barrier(request: Request, call_next):
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and Path("/app/.application-maintenance").exists()
        ):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"error": "Application maintenance; the request was not admitted."},
                status_code=503, headers={"Retry-After": "10"},
            )
        return await call_next(request)

    def authorize(request: Request) -> None:
        expected = os.getenv("API_SERVER_KEY", "")
        supplied = request.headers.get("x-autopilot-key", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            raise HTTPException(status_code=401, detail="A valid operator key is required.")

    @app.get("/internal/transport/status")
    async def transport_status(request: Request) -> dict[str, Any]:
        authorize(request)
        with journal().connect() as connection:
            unfinished = connection.execute(
                "SELECT count(*) FROM deliveries WHERE phase NOT IN ('completed','failed','unknown')",
            ).fetchone()[0]
            unsettled = connection.execute(
                "SELECT count(*) FROM deliveries WHERE settled=0",
            ).fetchone()[0]
            quarantined = connection.execute(
                "SELECT count(*) FROM deliveries WHERE phase IN ('failed','unknown')",
            ).fetchone()[0]
        return {
            "active": sum(not task.done() for task in _active.values()),
            "unfinished": unfinished, "unsettled": unsettled, "quarantined": quarantined,
            "worker": settings().worker, "role": settings().role,
        }

    @app.post("/internal/transport/callback")
    async def callback(request: Request) -> dict[str, Any]:
        authorize(request)
        payload = await request.json()
        queue = str(payload.get("queue") or "")
        envelope, body = decode_callback(str(payload.get("envelope") or ""), queue, settings())
        row = journal().admit(queue, envelope, body, settings().connection_id)
        await checkpoint()
        start_work(queue, envelope["MessageId"], gateway_handler, schedule_handler)
        return {"messageId": envelope["MessageId"], "phase": row["phase"], "acknowledged": False}

    @app.on_event("shutdown")
    async def stop_workers() -> None:
        tasks = [task for task in _active.values() if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
