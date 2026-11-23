from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.identity import AzureCliCredential

from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.operator_client import invoke_response


STOPPED = {"stopped", "suspended", "idle"}
ROLES = ("gateway", "runtime", "private-mcp", "public-mcp")


def json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
    ).hexdigest()


def transcript_digest(messages: list[dict[str, Any]]) -> str:
    fields = ("id", "role", "content", "tool_calls", "tool_call_id", "tool_name", "finish_reason")
    return json_digest([
        {key: message.get(key) for key in fields}
        for message in messages if message.get("role") in {"user", "assistant", "tool", "system"}
    ])


def tool_evidence(messages: list[dict[str, Any]]) -> dict[str, Any]:
    calls: dict[str, list[str]] = {}
    results: dict[str, dict[str, Any]] = {}
    for message in messages:
        raw_calls = message.get("tool_calls") or []
        if isinstance(raw_calls, str):
            raw_calls = json.loads(raw_calls)
        if not isinstance(raw_calls, list):
            raise ValueError("Native transcript returned invalid structured tool calls.")
        for call in raw_calls:
            if not isinstance(call, dict):
                raise ValueError("Native tool call is not a structured record.")
            name = (call.get("function") or {}).get("name")
            identifier = call.get("id")
            if isinstance(name, str) and isinstance(identifier, str):
                if name == "tool_call":
                    arguments = (call.get("function") or {}).get("arguments")
                    dispatched = json.loads(arguments) if isinstance(arguments, str) else arguments
                    if not isinstance(dispatched, dict) or not isinstance(dispatched.get("calls"), list):
                        raise ValueError("Native tool_call has no structured execution list.")
                    calls[identifier] = [
                        item["name"] for item in dispatched["calls"]
                        if isinstance(item, dict) and isinstance(item.get("name"), str)
                    ]
                else:
                    calls[identifier] = [name]
        if message.get("role") == "tool" and message.get("tool_call_id"):
            content = message.get("content")
            if isinstance(content, str) and content.startswith("<untrusted_tool_result "):
                _, separator, content = content.partition("\n\n")
                if not separator:
                    raise ValueError("Native untrusted result wrapper has no structured data boundary.")
                content = content.rsplit("</untrusted_tool_result>", 1)[0].strip()
            parsed = json.loads(content) if isinstance(content, str) else content
            if isinstance(parsed, dict):
                results[str(message["tool_call_id"])] = parsed
    expected = ("list_services", "list_demo_shipments")
    matched = {
        suffix: [
            identifier for identifier, names in calls.items()
            if any(name.endswith(suffix) for name in names) and identifier in results
            and not results[identifier].get("is_error") and not results[identifier].get("isError")
            and not results[identifier].get("error")
            and isinstance((results[identifier].get("structuredContent") or {}).get("result"), list)
            and bool(results[identifier]["structuredContent"]["result"])
        ]
        for suffix in expected
    }
    if any(not identifiers for identifiers in matched.values()):
        raise RuntimeError(f"Native transcript lacks correlated real MCP calls/results: {matched}")
    return {"toolCalls": calls, "correlatedResults": matched}


class EventTransportSmoke:
    def __init__(self, state_name: str, *, timeout: int, idle_timeout: int, max_turn_seconds: float = 60) -> None:
        if not math.isfinite(max_turn_seconds) or max_turn_seconds <= 0 or timeout <= 0 or idle_timeout <= 0:
            raise ValueError("Smoke observation budgets and the measured response limit must be finite and positive.")
        self.path = runtime_outputs_path("hermes", state_name)
        self.outputs = json.loads(self.path.read_text(encoding="utf-8"))
        self.variables = json.loads(runtime_app_tfvars_path("hermes", state_name).read_text(encoding="utf-8"))
        self.key = str(self.variables["api_server_key"])
        self.client = httpx.Client(timeout=timeout)
        self.credential = AzureCliCredential(subscription=self.outputs["subscription_id"], process_timeout=150)
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.max_turn_seconds = max_turn_seconds
        self.result: dict[str, Any] = {
            "worker": self.outputs["worker_id"], "startedAt": datetime.now(timezone.utc).isoformat(),
            "runtimeApplicationRelease": self.outputs.get("runtime_application_release"),
            "services": self.outputs["sandbox_services"], "checks": {}, "attempts": [],
        }
        self.evidence = self.path.parent / "event-transport-smoke.json"

    def save(self) -> None:
        self.evidence.write_text(json.dumps(self.result, indent=2) + "\n", encoding="utf-8")

    def close(self) -> None:
        self.client.close()
        self.credential.close()

    def metadata(self, role: str) -> dict[str, Any]:
        group = self.outputs["sandbox_groups"][role]
        identifier = (
            self.outputs["runtime_sandbox_id"] if role == "runtime"
            else self.outputs["sandbox_services"][role]["sandbox_id"]
        )
        uuid.UUID(identifier)
        with SandboxGroupClient(
            endpoint_for_region(self.outputs["sandbox_location"]), self.credential,
            subscription_id=self.outputs["subscription_id"], resource_group=self.outputs["resource_group_name"],
            sandbox_group=group["name"], audience="https://management.azuredevcompute.io/.default",
        ) as client:
            sandbox = client.get_sandbox_client(identifier).get()
        if sandbox.labels.get("worker") != self.outputs["worker_id"]:
            raise ValueError("A smoke target does not belong to the captured Worker.")
        policy = sandbox.lifecycle.auto_suspend if sandbox.lifecycle else None
        if not policy or not policy.enabled or policy.mode != "Memory" or policy.interval != 300:
            raise RuntimeError(f"{role} does not have the declared five-minute Memory suspend policy.")
        return {"id": identifier, "state": sandbox.state, "mode": policy.mode, "idleSeconds": policy.interval}

    def request(self, method: str, base: str, path: str, *, body: dict[str, Any] | None = None) -> httpx.Response:
        headers = {"X-Autopilot-Key": self.key}
        if base.rstrip("/") == str(self.outputs["runtime_url"]).rstrip("/") and path.startswith("/api/"):
            headers["Authorization"] = f"Bearer {self.key}"
        if base.rstrip("/") == str(self.outputs["runtime_url"]).rstrip("/") and path.startswith("/v1/"):
            headers["Authorization"] = f"Bearer {self.key}"
        return self.client.request(method, base.rstrip("/") + path, headers=headers, json=body)

    def execute(self, role: str, script: str) -> dict[str, Any]:
        identifier = (
            self.outputs["runtime_sandbox_id"] if role == "runtime"
            else self.outputs["sandbox_services"][role]["sandbox_id"]
        )
        python = (
            "/app/.venv/bin/python" if role == "runtime"
            else "/app/releases/" + self.outputs["sandbox_services"][role]["application_release"] + "/.venv/bin/python"
        )
        with SandboxGroupClient(
            endpoint_for_region(self.outputs["sandbox_location"]), self.credential,
            subscription_id=self.outputs["subscription_id"], resource_group=self.outputs["resource_group_name"],
            sandbox_group=self.outputs["sandbox_groups"][role]["name"],
            audience="https://management.azuredevcompute.io/.default", read_timeout=180,
        ) as group:
            result = group.get_sandbox_client(identifier).exec(python + " -c " + shlex.quote(script))
        if result.exit_code != 0:
            raise RuntimeError(f"{role} protocol inspection failed: {result.stderr}")
        return json.loads(result.stdout)

    def native_callback(self) -> None:
        marker = "TRANSPORT-ONLY-" + uuid.uuid4().hex
        correlation = hashlib.sha256(marker.encode()).hexdigest()
        prepare = """
import asyncio,json,os,sys
config=json.load(open('/app/.sandbox-service.json'))
os.environ.update(config['environment']); sys.path.insert(0,config['workingDirectory'])
from bridge.event_worker import checkpoint
from bridge.durable_transport import bus,settings,journal,canonical
payload={'transportProbe':MARKER,'modelExecuted':False}
reference=journal().save_payload(payload)
message=settings().message('activity',reference,CORRELATION)
journal().reserve_work(settings().activities_queue,message,settings().connection_id)
journal().update(settings().activities_queue,CORRELATION,phase='completed',result=canonical(payload))
asyncio.run(checkpoint())
with bus() as sender:
 sender.send(settings().activities_queue,message)
print(json.dumps({'messageId':CORRELATION,'reference':reference,'modelExecuted':False}))
""".replace("MARKER", repr(marker)).replace("CORRELATION", repr(correlation)).strip()
        published = self.execute("gateway", prepare)
        inspect = """
import json,os,sys
config=json.load(open('/app/.sandbox-service.json'))
os.environ.update(config['environment']); sys.path.insert(0,config['workingDirectory'])
from bridge.durable_transport import journal,settings
try:
 row=journal().get(settings().activities_queue,CORRELATION)
except ValueError:
 print(json.dumps({'phase':'not-admitted'}))
else:
 print(json.dumps({'phase':row['phase'],'settled':row['settled'],'deliveryCount':row['delivery_count'],
                   'response':json.loads(row['result']) if row['result'] else None,'error':row['error']}))
""".replace("CORRELATION", repr(correlation)).strip()
        deadline = time.monotonic() + 180
        observed = None
        while time.monotonic() < deadline:
            observed = self.execute("gateway", inspect)
            if observed.get("settled") == 1:
                if observed.get("response") != {"transportProbe": marker, "modelExecuted": False}:
                    raise RuntimeError("The real native callback changed its transport-only payload.")
                self.result["checks"]["nativeCallback"] = {
                    "ok": True, "modelExecuted": False, "callerOwnedSettlement": True,
                    "published": published, "receipt": observed,
                }
                self.save()
                return
            time.sleep(5)
        self.result["checks"]["nativeCallback"] = {"ok": False, "published": published, "lastReceipt": observed}
        self.save()
        raise TimeoutError("The actual native result callback/settlement did not complete; no model was invoked.")

    def mcp_health(self) -> dict[str, Any]:
        endpoints = {
            "private-mcp": self.outputs["private_mcp_url"].removesuffix("/mcp") + "/health",
            "public-mcp": self.outputs["public_shipments_mcp_url"].removesuffix("/mcp") + "/health",
        }
        script = """
import json,os,urllib.request
config=json.load(open('/app/.sandbox-service.json'))
os.environ.update(config['environment'])
health={}
for role,url in ENDPOINTS.items():
 with urllib.request.urlopen(url,timeout=120) as response:
  health[role]=json.load(response)
print(json.dumps(health))
""".replace("ENDPOINTS", repr(endpoints)).strip()
        return self.execute("runtime", script)

    def preflight(self) -> None:
        native = self.outputs.get("native_transport")
        if not native or not native.get("triggers"):
            raise RuntimeError("The Worker has no captured native wake receivers.")
        for role in ROLES:
            self.metadata(role)
        gateway = str(self.outputs["bridge_url"])
        runtime = str(self.outputs["runtime_url"])
        health = {}
        for role, base in (("gateway", gateway), ("runtime", runtime)):
            response = self.client.get(base + "/health")
            response.raise_for_status()
            health[role] = response.json()
            if role == "gateway" and health[role].get("transportSchema") != 2:
                raise RuntimeError(f"{role} has not loaded the current private-payload wire protocol.")
            if role == "runtime" and health[role].get("nativeRuns") is not True:
                raise RuntimeError("The runtime has not loaded the direct native Runs interface.")
        health.update(self.mcp_health())
        for role, status in health.items():
            if (
                not isinstance(status.get("processId"), int) or status["processId"] < 1
                or not isinstance(status.get("memoryNonce"), str) or not status["memoryNonce"]
            ):
                raise RuntimeError(f"{role} has no observable process identity for Memory retention verification.")
        self.result["healthBeforeIdle"] = health
        denied = self.client.post(gateway + "/invoke", json={"conversationId": "denied", "message": "Do not execute."})
        if denied.status_code != 401:
            raise RuntimeError(f"Anonymous operator admission was not refused: {denied.status_code}")
        callback = self.client.post(gateway + "/internal/transport/callback", json={})
        if callback.status_code != 401:
            raise RuntimeError("Anonymous native-callback admission was not refused.")
        unauthenticated = self.client.get(runtime + "/v1/models")
        if unauthenticated.status_code != 401:
            raise RuntimeError("Anonymous native model API access was not refused.")
        for role, base in (("gateway", gateway), ("runtime", runtime)):
            path = "/internal/transport/status" if role == "gateway" else "/internal/runtime/status"
            response = self.request("GET", base, path)
            response.raise_for_status()
            status = response.json()
            self.result.setdefault("transportBeforeIdle", {})[role] = status
            if status["active"] or (role == "gateway" and status["unfinished"]):
                raise RuntimeError(f"{role} has unfinished work; do not overlap an exclusive model smoke.")
        self.result["checks"]["authentication"] = {"ok": True}
        self.save()

    def natural_idle(self) -> None:
        samples = []
        started = time.monotonic()
        while time.monotonic() - started < self.idle_timeout:
            states = {role: self.metadata(role) for role in ROLES}
            samples.append({"elapsedSeconds": round(time.monotonic() - started, 2), "states": states})
            if all(str(item["state"]).lower() in STOPPED for item in states.values()):
                self.result["checks"]["naturalIdle"] = {
                    "ok": True, "observation": "management metadata only; no Stop/Resume/exec/files/health polling",
                    "samples": samples,
                }
                self.save()
                return
            time.sleep(10)
        self.result["checks"]["naturalIdle"] = {"ok": False, "samples": samples}
        self.save()
        raise TimeoutError("The configured natural idle policy did not stop every owned service within the budget.")

    def invoke_once(self, label: str, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        body = {
            "requestId": uuid.uuid4().hex,
            "conversationId": f"native-transport-{label}-{uuid.uuid4().hex}",
            "message": prompt, "persistenceDisabled": True,
        }
        attempt = {"label": label, "requestId": body["requestId"], "admission": "attempted", "request": body}
        self.result["attempts"].append(attempt)
        self.save()
        started = time.monotonic()
        response = invoke_response(
            self.client, str(self.outputs["bridge_url"]).rstrip("/") + "/invoke",
            body=body, headers={"X-Autopilot-Key": self.key}, timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        response_seconds = round(time.monotonic() - started, 2)
        if not result.get("correlationId") or not result.get("runId"):
            raise RuntimeError("The real invocation returned no durable transport/native run correlation.")
        attempt.update(admission="completed", runId=result["runId"], correlationId=result["correlationId"])
        native = self.request(
            "GET", str(self.outputs["runtime_url"]), "/v1/runs/" + quote(result["runId"], safe=""),
        )
        native.raise_for_status()
        run = native.json()
        if run.get("status") != "completed" or run.get("run_id") != result["runId"]:
            raise RuntimeError("The actual correlated native run is not completed.")
        if not isinstance(run.get("session_id"), str) or not run["session_id"]:
            raise RuntimeError("Native completion has no API-owned session.")
        self.result["checks"][label] = {
            "ok": response_seconds <= self.max_turn_seconds,
            "responseSeconds": response_seconds, "maxTurnSeconds": self.max_turn_seconds,
            "elapsedSeconds": round(time.monotonic() - started, 2),
            "runId": run["run_id"], "sessionId": run["session_id"],
            "usage": run.get("usage"), "runtime": run.get("runtime"),
        }
        self.save()
        if response_seconds > self.max_turn_seconds:
            raise RuntimeError(
                f"The completed {label} took {response_seconds}s, above the {self.max_turn_seconds}s interactive limit."
            )
        return body, result

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        response = self.request(
            "GET", str(self.outputs["runtime_url"]),
            "/api/sessions/" + quote(session_id, safe="") + "/messages",
        )
        response.raise_for_status()
        messages = response.json().get("data")
        if not isinstance(messages, list):
            raise ValueError("The native transcript did not return its structured message list.")
        return [
            message for message in messages
            if isinstance(message, dict) and message.get("role") in {"user", "assistant", "tool", "system"}
        ]

    def billing_evidence(self, session_id: str) -> dict[str, Any]:
        script = """
import json,sqlite3
config=json.load(open('/app/.sandbox-service.json'))
environment=config['environment']
connection=sqlite3.connect('file:/data/hermes/state.db?mode=ro',uri=True)
connection.row_factory=sqlite3.Row
rows=[dict(row) for row in connection.execute(
 "SELECT model,billing_provider,billing_base_url,api_call_count,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,cost_status FROM session_model_usage WHERE session_id=? AND task=''",
 (SESSION,))]
connection.close()
expected_model=environment['HERMES_MODEL']
expected_url=environment['FOUNDRY_OPENAI_BASE_URL'].rstrip('/')
if not any(row['model']==expected_model and row['billing_provider']=='azure-foundry'
           and row['billing_base_url'].rstrip('/')==expected_url and row['api_call_count']>=1 for row in rows):
 raise RuntimeError('No real correlated inference on the expected model/Foundry endpoint')
print(json.dumps({'model':expected_model,'baseUrl':expected_url,'nativeUsageLedger':rows,
                  'costNote':'Token evidence is real; unknown/zero native price fields are not an Azure bill.'}))
""".replace("SESSION", repr(session_id)).strip()
        return self.execute("runtime", script)

    def run(self, *, skip_idle: bool) -> dict[str, Any]:
        self.result["idleObservationRequested"] = not skip_idle
        self.preflight()
        self.native_callback()
        if not skip_idle:
            self.natural_idle()
        marker = "NATIVE-TRANSPORT-" + uuid.uuid4().hex[:16]
        body, response = self.invoke_once(
            "toolTurn",
            "Call the private-incidents list_services tool and the public-shipments list_demo_shipments tool. "
            "Use their real returned results. Start your final answer with exactly "
            f"{marker} on its own line, then briefly list the results. Do not modify or persist anything.",
        )
        if not str(response.get("response") or "").startswith(marker):
            raise RuntimeError("The actual tool turn did not return its unique output marker.")
        resumed = {}
        for role, base in (
            ("gateway", str(self.outputs["bridge_url"])), ("runtime", str(self.outputs["runtime_url"])),
        ):
            health = self.client.get(base + "/health")
            health.raise_for_status()
            resumed[role] = health.json()
        resumed.update(self.mcp_health())
        if not skip_idle:
            for role in ROLES:
                for field in ("processId", "memoryNonce"):
                    if resumed[role].get(field) != self.result["healthBeforeIdle"][role][field]:
                        raise RuntimeError(f"{role} restarted instead of preserving its Memory snapshot.")
        self.result["healthAfterWake"] = resumed
        session_id = self.result["checks"]["toolTurn"]["sessionId"]
        messages_before = self.messages(session_id)
        self.result["checks"]["toolTurn"]["tools"] = tool_evidence(messages_before)
        self.result["checks"]["toolTurn"]["billingEvidence"] = self.billing_evidence(session_id)
        replay = invoke_response(
            self.client, str(self.outputs["bridge_url"]).rstrip("/") + "/invoke",
            body=body, headers={"X-Autopilot-Key": self.key}, timeout=self.timeout,
        )
        replay.raise_for_status()
        if replay.json() != response:
            raise RuntimeError("An exact operator retry changed its response or native run identity.")
        messages_after = self.messages(session_id)
        if transcript_digest(messages_before) != transcript_digest(messages_after):
            raise RuntimeError("An exact operator retry appended another native transcript turn.")
        self.result["checks"]["idempotentReplay"] = {
            "ok": True, "sameRunId": response["runId"], "nativeTranscriptSha256": transcript_digest(messages_after),
        }
        exact = "EXACT-" + uuid.uuid4().hex[:16]
        _, plain = self.invoke_once("exactReply", f"Reply with exactly this text and nothing else: {exact}")
        if str(plain.get("response") or "").strip() != exact:
            raise RuntimeError("The no-tool model smoke did not satisfy its exact response contract.")
        self.result["ok"] = True
        self.result["finishedAt"] = datetime.now(timezone.utc).isoformat()
        self.save()
        return self.result


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise real native wake, tool execution, and duplicate-safe replies.")
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--idle-timeout", type=int, default=900)
    parser.add_argument("--max-turn-seconds", type=float, default=60)
    parser.add_argument("--skip-idle", action="store_true", help="Run warm checks only; never records an idle pass.")
    args = parser.parse_args()
    smoke = EventTransportSmoke(
        args.state_name, timeout=args.timeout, idle_timeout=args.idle_timeout, max_turn_seconds=args.max_turn_seconds,
    )
    try:
        result = smoke.run(skip_idle=args.skip_idle)
        print(json.dumps({"ok": result["ok"], "worker": result["worker"], "checks": result["checks"]}, indent=2))
    except Exception as error:
        smoke.result.update(
            ok=False, failure={"type": type(error).__name__, "message": str(error)[:2000]},
            finishedAt=datetime.now(timezone.utc).isoformat(),
        )
        smoke.save()
        raise
    finally:
        smoke.close()


if __name__ == "__main__":
    main()
