from __future__ import annotations

import asyncio
import html
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp
from microsoft_agents.activity import Activity, Attachment

from bridge.attachments import _PinnedResolver
from bridge.document_cards import create_action_token, decode_action_token

START = "<VISUAL_ARTIFACT_REQUEST>"
END = "</VISUAL_ARTIFACT_REQUEST>"
MAX_BYTES = 48_000
TTL = 600
MAX_PENDING = 20


def extract_artifact_request(response: str) -> tuple[str, dict[str, Any] | None]:
    if START not in response and END not in response:
        return response, None
    if response.count(START) != 1 or response.count(END) != 1:
        raise ValueError("Invalid visual artifact delimiters.")
    start, end = response.index(START), response.index(END)
    if end < start:
        raise ValueError("Invalid visual artifact request order.")
    raw = response[start + len(START):end].strip()
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Visual artifact request is too large.")
    spec = json.loads(raw)
    if not isinstance(spec, dict):
        raise ValueError("Visual artifact must be an object.")
    title = spec.get("title")
    sections = spec.get("sections")
    language = spec.get("language", "en")
    if language != "en":
        raise ValueError("Artifact language must be English.")
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise ValueError("Artifact title must contain 1 to 120 characters.")
    if not isinstance(sections, list) or not 1 <= len(sections) <= 8:
        raise ValueError("Artifact must contain 1 to 8 sections.")
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("Artifact section must be an object.")
        for field, limit in (("heading", 120), ("body", 3000)):
            text = section.get(field)
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError(f"Artifact {field} must contain 1 to {limit} characters.")
        cues = section.get("cues", [])
        if (not isinstance(cues, list) or len(cues) > 3 or
            any(not isinstance(cue, str) or not cue.strip() or len(cue) > 100 or len(cue.split()) > 10 for cue in cues)):
            raise ValueError("Slide cues must contain at most three short strings of up to ten words.")
    return (response[:start] + response[end + len(END):]).strip(), {
        "title": title, "language": language,
        "sections": [{"heading": s["heading"], "body": s["body"], "cues": s.get("cues", [])} for s in sections]
    }


def render_html(spec: dict[str, Any]) -> bytes:
    title = html.escape(spec["title"])
    language = "en"
    read, slides, previous, following, fictional = (
        "Read", "Slides", "Previous", "Next", "Fictional Medivia demo data — not Dr.Max data"
    )
    sections = "".join(
        f'<section class="page"><h2>{html.escape(s["heading"])}</h2>'
        f'<p class="body">{html.escape(s["body"]).replace(chr(10), "<br>")}</p>'
        f'<ul class="cues">{"".join(f"<li>{html.escape(c)}</li>" for c in s.get("cues", []))}</ul></section>'
        for s in spec["sections"]
    )
    document = f"""<!doctype html><html lang="{language}"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7f5;color:#182019;font:17px/1.6 system-ui,sans-serif}}main{{max-width:960px;margin:auto;padding:3rem 1.5rem}}small{{color:#286827;letter-spacing:.08em;text-transform:uppercase}}h1{{font-size:clamp(2.4rem,6vw,4.7rem);line-height:1.04;max-width:16ch}}h2{{font-size:clamp(1.6rem,4vw,2.6rem);line-height:1.15}}.page{{background:white;border:1px solid #dfe7df;border-top:4px solid #008a00;border-radius:12px;padding:2rem;margin:1.2rem 0}}.cover{{padding:3rem 2rem}}button{{padding:.6rem 1rem;margin-right:.5rem;border:1px solid #518251;border-radius:6px;background:white;color:#174e17;font:inherit;cursor:pointer}}nav,.cues{{display:none}}.js nav{{display:block}}.slides .page{{display:none;min-height:60vh}}.slides .page.active{{display:block}}.slides .body{{display:none}}.slides .cues{{display:block}}#nav{{margin:1rem 0}}@media print{{body{{background:white;font-size:12pt}}main{{padding:0}}nav{{display:none!important}}.page{{break-inside:avoid;padding:1rem;margin:.5rem 0}}h1{{font-size:28pt;max-width:none}}h2{{font-size:18pt;break-after:avoid}}.slides .page{{display:block;break-after:page}}}}
</style></head><body><main><header class="page cover"><small>{fictional}</small><h1>{title}</h1></header>
<nav><button id="mode" type="button">{slides}</button><span id="nav" hidden><button id="prev" type="button">{previous}</button><button id="next" type="button">{following}</button><span id="count"></span></span></nav>
{sections}</main><script>
const pages=[...document.querySelectorAll('.page')]; let index=0, slides=false;
document.documentElement.classList.add('js');
function draw(){{document.body.classList.toggle('slides',slides);pages.forEach((p,i)=>p.classList.toggle('active',i===index));document.getElementById('nav').hidden=!slides;document.getElementById('mode').textContent=slides?{json.dumps(read)}:{json.dumps(slides)};document.getElementById('count').textContent=(index+1)+' / '+pages.length;}}
document.getElementById('mode').onclick=()=>{{slides=!slides;draw()}};
document.getElementById('prev').onclick=()=>{{index=Math.max(0,index-1);draw()}};
document.getElementById('next').onclick=()=>{{index=Math.min(pages.length-1,index+1);draw()}};
draw();
</script></body></html>"""
    return document.encode("utf-8")


@dataclass
class Pending:
    content: bytes
    user_id: str
    conversation_id: str
    expires: float


pending: dict[str, Pending] = {}


def consent_activity(spec: dict[str, Any], visible: str, *, user_id: str, conversation_id: str) -> Activity:
    now = time.time()
    for key in list(pending):
        if pending[key].expires <= now:
            del pending[key]
    if len(pending) >= MAX_PENDING:
        raise RuntimeError("Too many pending artifact offers; try again later.")
    if not user_id or user_id == "unknown-user" or not conversation_id:
        raise ValueError("A personal Teams recipient and conversation are required.")
    operation = secrets.token_hex(12)
    content = render_html(spec)
    expires = now + TTL
    token = create_action_token(operation_id=operation, operation_scope="visual-artifact",
                                user_id=user_id, conversation_id=conversation_id, expires_at_unix=expires)
    pending[operation] = Pending(content, user_id, conversation_id, expires)
    asyncio.get_running_loop().call_later(TTL, pending.pop, operation, None)
    context = {"visualArtifactToken": token}
    return Activity(type="message", text=visible or "Accept to receive your HTML report.",
                    attachments=[Attachment(
                        content_type="application/vnd.microsoft.teams.card.file.consent",
                        name="medivia-report.html",
                        content={"description": "Standalone HTML report; accept to save it to your OneDrive.",
                                 "sizeInBytes": len(content), "acceptContext": context, "declineContext": context},
                    )])


def take_consent(value: object, *, user_id: str, conversation_id: str) -> tuple[str, Pending, dict]:
    if not isinstance(value, dict) or value.get("action") not in {"accept", "decline"}:
        raise ValueError("Invalid file consent response.")
    context = value.get("context")
    if not isinstance(context, dict) or not isinstance(context.get("visualArtifactToken"), str):
        raise ValueError("Missing visual artifact consent token.")
    token = decode_action_token(context["visualArtifactToken"], user_id=user_id, conversation_id=conversation_id)
    if token.get("operationScope") != "visual-artifact":
        raise ValueError("Invalid visual artifact scope.")
    operation = str(token["operationId"])
    item = pending.get(operation)
    if item is None or item.expires <= time.time() or item.user_id != user_id or item.conversation_id != conversation_id:
        raise ValueError("Artifact offer expired or is no longer available.")
    info = value.get("uploadInfo") or {}
    if value["action"] == "accept" and not isinstance(info, dict):
        raise ValueError("Invalid upload information.")
    del pending[operation]
    return value["action"], item, info


def microsoft_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Missing Microsoft file URL.")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
        or not any(host.endswith(suffix) for suffix in (".sharepoint.com", ".sharepoint-df.com"))):
        raise ValueError("File URL is not an allowed Microsoft HTTPS host.")
    return value


async def upload_consent_file(item: Pending, info: dict) -> Activity:
    upload_url = microsoft_url(info.get("uploadUrl"))
    content_url = microsoft_url(info.get("contentUrl"))
    unique_id = info.get("uniqueId")
    if not isinstance(unique_id, str) or not unique_id or len(unique_id) > 256:
        raise ValueError("Missing file identifier.")
    resolver = _PinnedResolver()
    await asyncio.to_thread(resolver.pin, upload_url)
    connector = aiohttp.TCPConnector(resolver=resolver)
    size = len(item.content)
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=60), trust_env=False) as session:
        async with session.put(upload_url, data=item.content, allow_redirects=False, headers={
            "Content-Type": "application/octet-stream", "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}",
        }) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"File upload failed with HTTP {response.status}.")
    return Activity(type="message", text="Your HTML report is ready.", attachments=[Attachment(
        content_type="application/vnd.microsoft.teams.card.file.info", content_url=content_url,
        name="medivia-report.html", content={"uniqueId": unique_id, "fileType": "html"},
    )])
