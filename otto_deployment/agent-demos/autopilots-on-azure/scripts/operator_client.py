from __future__ import annotations

import re
import time
import uuid
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


def invoke_response(
    client: httpx.Client, url: str, *, body: dict[str, Any],
    headers: dict[str, str] | None = None, timeout: float = 900,
) -> httpx.Response:
    payload = dict(body)
    outgoing = dict(headers or {})
    request_id = str(payload.get("requestId") or outgoing.get("Idempotency-Key") or uuid.uuid4().hex)
    if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", request_id):
        raise ValueError("An operator request identity must be bounded visible ASCII.")
    payload["requestId"] = request_id
    outgoing["Idempotency-Key"] = request_id
    parsed = urlsplit(url)
    status_url = urlunsplit((parsed.scheme, parsed.netloc, "/invocations/" + quote(request_id, safe=""), "", ""))
    deadline = time.monotonic() + timeout
    known_admission = False
    submissions = 0

    def submit() -> httpx.Response | None:
        nonlocal submissions, known_admission
        submissions += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Operator request {request_id} exceeded its admission budget.")
        try:
            response = client.post(url, json=payload, headers=outgoing, timeout=min(60, remaining))
        except httpx.TransportError:
            return None
        if response.status_code == 202:
            admitted = response.json()
            if admitted.get("requestId") != request_id or not admitted.get("jobId"):
                raise ValueError("The server did not attest the exact durable operator admission.")
            known_admission = True
            return None
        if response.status_code in {408, 429, 502, 503, 504}:
            return None
        if response.status_code == 200:
            raise RuntimeError("The operator endpoint is synchronous; deploy the durable admission protocol first.")
        return response

    rejected = submit()
    if rejected is not None:
        return rejected
    while time.monotonic() < deadline:
        try:
            response = client.get(status_url, headers=outgoing, timeout=min(60, max(1, deadline - time.monotonic())))
        except httpx.TransportError:
            time.sleep(1)
            continue
        if response.status_code == 200:
            if response.json().get("requestId") != request_id:
                raise ValueError("Operator polling returned another request identity.")
            return response
        if response.status_code == 404:
            if known_admission:
                raise RuntimeError("A confirmed durable operator admission disappeared; restore its verified journal.")
            if submissions >= 3:
                raise RuntimeError(f"Operator admission {request_id} remains unconfirmed; do not generate a new identity.")
            rejected = submit()
            if rejected is not None:
                return rejected
        elif response.status_code not in {202, 408, 429, 502, 503, 504}:
            return response
        time.sleep(1)
    raise TimeoutError(f"Operator request {request_id} remains unfinished. Poll {status_url}; never submit a new identity blindly.")
