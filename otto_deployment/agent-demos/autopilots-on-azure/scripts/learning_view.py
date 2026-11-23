"""Capture and present what Otto has learned (memory, user profile, skills) for the demo presenter.

Run from the ``autopilots-on-azure`` directory with the Azure CLI profile already selected (``AZURE_CONFIG_DIR``).
``render``, ``summary`` and ``serve --sample`` never touch Azure.

  capture --label "After scene 1"   read Otto's learned state once and save it as a step of the timeline
  serve                             local viewer at http://127.0.0.1:8765 with a Capture button
  render --out viewer.html          static single-file viewer of the saved steps
  summary                           text overview of the saved steps

A capture runs one read-only script inside Otto's sandbox. That counts as activity and restarts the idle timer, so a
suspended Otto is woken only when ``--wake`` (or "Wake Otto and capture" in the viewer) is chosen.
"""

from __future__ import annotations

import argparse
import json
import shlex
import threading
import time
import webbrowser
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from scripts.learning_state import (
    COLLECT_SCRIPT, assemble_timeline, build_snapshot, decode_collected, json_for_script, load_snapshots,
    next_snapshot_id, render_html, rename_snapshot, save_snapshot, timeline_summary,
)


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "otto"
TIMELINE_DIR = LOCAL / "learning-timeline"
DEFAULT_OUTPUT = LOCAL / "otto-memory-viewer.html"
DEFAULT_PORT = 8765
AUTO_CHOICES = (0, 30, 60, 120)
STATE_POLL_SECONDS = 20
MAX_BODY = 4096
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class OttoAsleep(RuntimeError):
    """Otto is not running and the caller did not agree to wake it."""

    def __init__(self, state: str) -> None:
        super().__init__(
            f"Otto is not running (state: {state or 'unknown'}). Capturing would wake it and restart its idle timer."
        )
        self.state = state


def state_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


@contextmanager
def open_sandbox() -> Iterator[Any]:
    # Azure imports stay inside so that render, summary and --sample work without loading the Azure SDK.
    from azure.containerapps.sandbox import SandboxGroupClient
    from azure.identity import AzureCliCredential

    from scripts.penta_ops import require_window
    from scripts.penta_toolbox import settings
    from scripts.sandbox_runtime import endpoint_for_region
    from scripts.sandbox_services import DISK_IMAGE_AUDIENCE
    from scripts.setup_agent365 import load_json
    from scripts.setup_app_tfvars import runtime_outputs_path

    config = settings()
    require_window(config)
    outputs = load_json(runtime_outputs_path("hermes", "otto"))
    if outputs["worker_id"] != "otto":
        raise ValueError("Captured deployment belongs to another worker.")
    with AzureCliCredential(tenant_id=config["azure"]["tenant_id"], process_timeout=150) as credential:
        with SandboxGroupClient(
            endpoint_for_region(outputs["sandbox_location"]), credential,
            subscription_id=outputs["subscription_id"], resource_group=outputs["resource_group_name"],
            sandbox_group=outputs["sandbox_groups"]["runtime"]["name"], audience=DISK_IMAGE_AUDIENCE,
        ) as group:
            yield group.get_sandbox_client(outputs["runtime_sandbox_id"])


def collect(sandbox: Any) -> dict[str, Any]:
    from scripts.sandbox_services import require_exec_success

    observed = sandbox.exec("python3 -c " + shlex.quote(COLLECT_SCRIPT))
    require_exec_success(observed, "Collecting learned state")
    return decode_collected(observed.stdout)


class Capturer(Protocol):
    def state(self) -> str: ...

    def collect(self, *, wake: bool) -> tuple[dict[str, Any], str]: ...

    def close(self) -> None: ...


class SandboxCapturer:
    """Keeps one sandbox client open for the lifetime of the viewer and reopens it after any error."""

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._stack: ExitStack | None = None
        self._sandbox: Any = None

    def _client(self) -> Any:
        if self._sandbox is None:
            stack = ExitStack()
            try:
                self._sandbox = stack.enter_context(open_sandbox())
            except BaseException:
                stack.close()
                raise
            self._stack = stack
        return self._sandbox

    def state(self) -> str:
        with self._guard:
            try:
                return state_name(self._client().get().state)
            except Exception:
                self.close()
                raise

    def collect(self, *, wake: bool) -> tuple[dict[str, Any], str]:
        with self._guard:
            try:
                sandbox = self._client()
                state = state_name(sandbox.get().state)
                if state != "Running":
                    if not wake:
                        raise OttoAsleep(state)
                    sandbox.ensure_running(timeout=600)
                return collect(sandbox), state
            except OttoAsleep:
                raise
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        with self._guard:
            stack, self._stack, self._sandbox = self._stack, None, None
            if stack is not None:
                stack.close()


def default_label(automatic: bool = False) -> str:
    return ("Auto " if automatic else "Capture ") + time.strftime("%H:%M:%S")


class Viewer:
    """State behind the local viewer: the saved steps, one capture at a time and optional auto-capture."""

    def __init__(
        self, directory: Path, capturer: Capturer | None = None, *, source: str = "live",
        snapshots: list[dict[str, Any]] | None = None, clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.capturer = capturer
        self.source = source
        self.fixed = snapshots
        self.clock = clock
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.busy = False
        self.auto_seconds = 0
        self.sandbox_state = ""
        self.state_error = ""
        self.state_checked = 0.0
        self.last: dict[str, Any] = {}
        self._last_auto = 0.0

    @property
    def live(self) -> bool:
        return self.capturer is not None

    def snapshots(self) -> list[dict[str, Any]]:
        return self.fixed if self.fixed is not None else load_snapshots(self.directory)

    def timeline(self) -> dict[str, Any]:
        return assemble_timeline(self.snapshots(), source=self.source)

    def page(self) -> str:
        return render_html(self.timeline(), live=self.live)

    def status(self) -> dict[str, Any]:
        return {
            "live": self.live, "busy": self.busy, "count": len(self.snapshots()), "sandboxState": self.sandbox_state,
            "stateError": self.state_error, "stateCheckedAt": int(self.state_checked), "auto": self.auto_seconds,
            "autoChoices": list(AUTO_CHOICES), "last": self.last, "now": int(self.clock()),
        }

    def set_auto(self, seconds: int) -> None:
        if seconds not in AUTO_CHOICES:
            raise ValueError(f"Auto-capture interval must be one of {AUTO_CHOICES}.")
        self.auto_seconds = seconds
        self._last_auto = self.clock()

    def rename(self, snapshot_id: str, label: str) -> bool:
        return self.fixed is None and rename_snapshot(self.directory, snapshot_id, label)

    def start_capture(self, label: str, *, wake: bool) -> bool:
        """Begin a capture in the background; False when another capture is already running."""
        if self.capturer is None:
            raise RuntimeError("This viewer has no live connection to Otto.")
        if not self.lock.acquire(blocking=False):
            return False
        self.busy = True
        threading.Thread(target=self._run, args=(label, wake, False), daemon=True).start()
        return True

    def _run(self, label: str, wake: bool, automatic: bool) -> None:
        # The caller already holds the lock; it is released here.
        try:
            self.last = self._capture(label, wake=wake, automatic=automatic)
        except OttoAsleep as error:
            self.sandbox_state = error.state
            self.last = {"ok": False, "asleep": True, "message": str(error), "at": int(self.clock())}
        except Exception as error:  # noqa: BLE001 - shown to the presenter; never fatal for the viewer
            self.last = {"ok": False, "message": f"{type(error).__name__}: {str(error)[:300]}", "at": int(self.clock())}
        finally:
            self.busy = False
            self.lock.release()

    def _capture(self, label: str, *, wake: bool, automatic: bool) -> dict[str, Any]:
        assert self.capturer is not None
        raw, state = self.capturer.collect(wake=wake)
        self.sandbox_state = "Running"
        self.state_checked = self.clock()
        previous = self.snapshots()
        snapshot = build_snapshot(
            raw, label=label.strip() or default_label(automatic), snapshot_id=next_snapshot_id(self.directory),
            source="live", sandbox_state=state,
        )
        unchanged = bool(previous) and previous[-1].get("contentHash") == snapshot["contentHash"]
        if automatic and unchanged:
            return {"ok": True, "saved": False, "message": "Nothing changed.", "at": int(self.clock())}
        save_snapshot(self.directory, snapshot)
        message = f"Saved step {snapshot['id']}" + (" (nothing changed since the previous step)." if unchanged else ".")
        return {
            "ok": True, "saved": True, "id": snapshot["id"], "unchanged": unchanged, "message": message,
            "at": int(self.clock()),
        }

    def tick(self) -> None:
        """One pass of the background loop: refresh Otto's state and start an automatic capture when one is due."""
        if self.capturer is None:
            return
        now = self.clock()
        if not self.busy and now - self.state_checked >= STATE_POLL_SECONDS:
            try:
                self.sandbox_state = self.capturer.state()
                self.state_error = ""
            except Exception as error:  # noqa: BLE001
                self.sandbox_state = "Unknown"
                self.state_error = f"{type(error).__name__}: {str(error)[:200]}"
            self.state_checked = self.clock()
        due = self.auto_seconds and now - self._last_auto >= self.auto_seconds
        if due and not self.busy and self.sandbox_state == "Running" and self.lock.acquire(blocking=False):
            self._last_auto = now
            self.busy = True
            threading.Thread(target=self._run, args=("", False, True), daemon=True).start()

    def run_background(self) -> None:
        while not self.stop.wait(1.0):
            self.tick()

    def close(self) -> None:
        self.stop.set()
        if self.capturer is not None:
            self.capturer.close()


def make_handler(viewer: Viewer, allowed_hosts: set[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "OttoViewer"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _trusted_host(self) -> bool:
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._json(421, {"error": "Unexpected host."})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._trusted_host():
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, viewer.page().encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/timeline":
                self._send(200, json_for_script(viewer.timeline()).encode("utf-8"), "application/json; charset=utf-8")
            elif path == "/api/status":
                self._json(200, viewer.status())
            else:
                self._json(404, {"error": "Not found."})

        def _body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._json(400, {"error": "Invalid length."})
                return None
            if length > MAX_BODY:
                self._json(413, {"error": "Request too large."})
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"error": "Invalid JSON."})
                return None
            if not isinstance(body, dict):
                self._json(400, {"error": "Expected an object."})
                return None
            return body

        def do_POST(self) -> None:  # noqa: N802
            if not self._trusted_host():
                return
            host = self.headers.get("Host", "")
            if self.headers.get("Origin", "") != f"http://{host}":
                return self._json(403, {"error": "Cross-origin requests are not accepted."})
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                return self._json(415, {"error": "Send application/json."})
            body = self._body()
            if body is None:
                return
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/capture":
                    if not viewer.live:
                        return self._json(409, {"error": "This viewer shows saved data only."})
                    label = str(body.get("label") or "")[:120]
                    if not viewer.start_capture(label, wake=body.get("wake") is True):
                        return self._json(409, {"error": "A capture is already running."})
                    return self._json(202, {"accepted": True})
                if path == "/api/label":
                    renamed = viewer.rename(str(body.get("id") or ""), str(body.get("label") or ""))
                    return self._json(200 if renamed else 404, {"renamed": renamed})
                if path == "/api/auto":
                    if not viewer.live:
                        return self._json(409, {"error": "This viewer shows saved data only."})
                    viewer.set_auto(int(body.get("seconds", 0)))
                    return self._json(200, {"auto": viewer.auto_seconds})
            except (TypeError, ValueError) as error:
                return self._json(400, {"error": str(error)})
            return self._json(404, {"error": "Not found."})

    return Handler


def make_server(viewer: Viewer, port: int) -> ThreadingHTTPServer:
    """Bind to the loopback interface only and accept requests that name that interface as their host."""
    allowed: set[str] = set()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(viewer, allowed))
    server.daemon_threads = True
    bound = server.server_address[1]
    allowed.update({f"127.0.0.1:{bound}", f"localhost:{bound}"})
    return server


def serve(viewer: Viewer, *, port: int, open_browser: bool) -> None:
    server = make_server(viewer, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=viewer.run_background, daemon=True).start()
    print(f"Otto memory viewer ({'live' if viewer.live else viewer.source}) at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        viewer.close()


def sample_timeline() -> list[dict[str, Any]]:
    from scripts.learning_view_sample import sample_snapshots

    return sample_snapshots()


def load_timeline(directory: Path, *, sample: bool) -> dict[str, Any]:
    if sample:
        return assemble_timeline(sample_timeline(), source="sample")
    return assemble_timeline(load_snapshots(directory), source="live")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--directory", type=Path, default=TIMELINE_DIR, help="Folder of saved steps.")
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture", help="Read Otto's learned state once and save it as a step.")
    capture.add_argument("--label", default="")
    capture.add_argument("--note", default="")
    capture.add_argument("--wake", action="store_true", help="Wake a suspended Otto instead of refusing.")
    capture.add_argument("--from-raw", type=Path, help="Build the step from an earlier raw capture; no Azure access.")

    serving = commands.add_parser("serve", help="Run the local viewer.")
    serving.add_argument("--port", type=int, default=DEFAULT_PORT)
    serving.add_argument("--sample", action="store_true", help="Serve synthetic rehearsal data; no Azure access.")
    serving.add_argument(
        "--auto", type=int, default=0, choices=AUTO_CHOICES, help="Initial auto-capture interval in seconds (0 = off).",
    )
    serving.add_argument("--open", action="store_true", help="Open the viewer in the default browser.")

    render = commands.add_parser("render", help="Write a static single-file viewer.")
    render.add_argument("--sample", action="store_true")
    render.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)

    summary = commands.add_parser("summary", help="Print an overview of the saved steps.")
    summary.add_argument("--sample", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "capture":
        if args.from_raw:
            raw, state, source = json.loads(args.from_raw.read_text(encoding="utf-8")), "", "import"
        else:
            capturer = SandboxCapturer()
            try:
                raw, state = capturer.collect(wake=args.wake)
            except OttoAsleep as error:
                print(f"{error} Re-run with --wake to proceed.")
                return 2
            finally:
                capturer.close()
            source = "live"
        snapshot = build_snapshot(
            raw, label=args.label or default_label(), snapshot_id=next_snapshot_id(args.directory),
            source=source, sandbox_state=state, note=args.note,
        )
        print(f"Saved {save_snapshot(args.directory, snapshot)}")
    elif args.command == "serve":
        if args.sample:
            viewer = Viewer(args.directory, None, source="sample", snapshots=sample_timeline())
        else:
            viewer = Viewer(args.directory, SandboxCapturer(), source="live")
            viewer.set_auto(args.auto)
        serve(viewer, port=args.port, open_browser=args.open)
    elif args.command == "render":
        timeline = load_timeline(args.directory, sample=args.sample)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_html(timeline, live=False), encoding="utf-8")
        print(f"Wrote {args.out}")
    elif args.command == "summary":
        print(timeline_summary(load_timeline(args.directory, sample=args.sample)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
