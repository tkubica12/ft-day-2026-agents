from __future__ import annotations

import base64
import html.parser
import io
import os
import re
import shutil
import signal
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from .visual_artifact_staging import builder, read, staged_path


MAX_OUTPUT = 10 * 1024 * 1024
SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
PREFLIGHT = """<script>
window.addEventListener('load', () => {
  const e = document.querySelector('.sheet-page'), b = e.getBoundingClientRect();
  const bad = [...e.querySelectorAll('*')].some(x => {
    const r = x.getBoundingClientRect();
    return r.left < b.left - 0.5 || r.right > b.right + 0.5 || r.bottom > b.bottom + 0.5;
  });
  document.body.setAttribute('data-otto-layout', !bad && e.scrollHeight <= e.clientHeight ? 'ok' : 'overflow');
});
</script>"""
ARTICLE_PREFLIGHT = """<script>
window.addEventListener('load', () => {
  const root = document.documentElement;
  const toggle = document.querySelector('[data-action="toggle-slides"]');
  if (root.dataset.view !== 'slides') toggle.click();
  document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'Home', bubbles:true}));
  const count = document.querySelectorAll('.doc-header > .slide-content, .chapter > .chapter-label, .card > .slide-content, .takeaway > .slide-content').length;
  const seen = new Set();
  let bad = count === 0;
  let badSurface = '';
  for (let i=0; i<count; i++) {
    const node = document.querySelector('[data-slide-current]');
    const surface = node && node.querySelector(':scope > .slide-content, :scope > .chapter-label');
    if (!node || !surface || seen.has(node.id)) { bad = true; break; }
    seen.add(node.id);
    const box = surface.getBoundingClientRect();
    let overflow = surface.scrollHeight > surface.clientHeight + 1 || surface.scrollWidth > surface.clientWidth + 1;
    for (const child of surface.querySelectorAll('*')) {
      const r = child.getBoundingClientRect();
      if (r.left < box.left - 1 || r.right > box.right + 1 || r.bottom > box.bottom + 1) overflow = true;
    }
    if (overflow) { bad = true; if (!badSurface) badSurface = node.id; }
    document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'PageDown', bubbles:true}));
  }
  document.body.setAttribute('data-otto-layout', bad ? 'overflow' : 'ok');
  document.body.setAttribute('data-otto-surface', badSurface);
});
</script>"""


class LayoutParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.layout = ""
        self.surface = ""

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.layout = dict(attrs).get("data-otto-layout", "")
            self.surface = dict(attrs).get("data-otto-surface", "")


def chrome(scratch: Path, arguments: list[str], *, timeout: int = 30) -> bytes:
    if os.name != "posix":
        raise RuntimeError("Sandboxed runtime PDF rendering requires Linux")
    unshare = shutil.which("unshare")
    browser_file = Path("/etc/hermes/chromium-executable-path")
    if not unshare or not browser_file.is_file():
        raise RuntimeError("Chromium or Linux network namespace support is unavailable")
    browser = browser_file.read_text(encoding="utf-8").strip()
    if not Path(browser).is_file():
        raise RuntimeError("Configured Chromium executable is unavailable")
    command = [
        unshare, "--user", "--map-current-user", "--net", browser,
        "--headless=new", "--disable-gpu", "--disable-dev-shm-usage",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=3000", "--disable-background-networking",
        "--disable-component-update", "--disable-sync", "--no-first-run",
        "--no-default-browser-check", "--proxy-server=127.0.0.1:9",
        "--proxy-bypass-list=<-loopback>", *arguments,
    ]
    env = {"PATH": os.defpath, "HOME": str(scratch), "XDG_CONFIG_HOME": str(scratch),
           "XDG_CACHE_HOME": str(scratch), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, cwd=scratch, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise RuntimeError("Sandboxed Chromium timed out") from exc
    if process.returncode:
        raise RuntimeError(f"Sandboxed Chromium failed ({process.returncode}): {stderr.decode('utf-8', 'replace')[-1000:]}")
    return stdout


def render_pdf(spec: dict, document: bytes, scratch: Path) -> bytes:
    from pypdf import PdfReader

    static = SCRIPT.sub("", document.decode("utf-8"))
    check = scratch / "check.html"
    check.write_text(static.replace("</body>", PREFLIGHT + "</body>"), encoding="utf-8")
    parser = LayoutParser()
    parser.feed(chrome(scratch, ["--dump-dom", check.as_uri()]).decode("utf-8"))
    if parser.layout != "ok":
        raise ValueError(f"PDF sheet layout did not fit ({parser.layout or 'no layout result'})")
    source = scratch / "sheet.html"
    source.write_text(static, encoding="utf-8")
    target = scratch / "brief.pdf"
    chrome(scratch, [f"--print-to-pdf={target}", source.as_uri()])
    data = target.read_bytes()
    if not data.startswith(b"%PDF-") or len(data) > MAX_OUTPUT:
        raise ValueError("Invalid or oversized rendered PDF")
    pdf = PdfReader(io.BytesIO(data))
    if len(pdf.pages) != 1:
        raise ValueError("The PDF brief must have exactly one page")
    # PDF font extraction can retain ligatures such as U+FB01 instead of "fi".
    actual = "".join(unicodedata.normalize("NFKC", pdf.pages[0].extract_text() or "").split())
    required = [spec["title"], spec["subtitle"], spec["closing"], spec["source"],
                builder().BRAND["disclaimer"]]
    required.extend(value for section in spec["sections"] for value in (section["heading"], section["body"]))
    if any("".join(unicodedata.normalize("NFKC", value).split()) not in actual for value in required):
        raise ValueError("PDF text is missing or clipped")
    return data


def validate_article(document: bytes, scratch: Path) -> None:
    check = scratch / "article-check.html"
    check.write_text(document.decode("utf-8").replace("</body>", ARTICLE_PREFLIGHT + "</body>"), encoding="utf-8")
    parser = LayoutParser()
    parser.feed(chrome(scratch, ["--window-size=1440,900", "--dump-dom", check.as_uri()]).decode("utf-8"))
    if parser.layout != "ok":
        if parser.layout == "overflow" and re.fullmatch(r"opening|ch-summary|card-[1-8]|closing", parser.surface):
            field = {"opening": "title or subtitle", "ch-summary": "chapter label", "closing": "closing"}.get(
                parser.surface, f"section {parser.surface.removeprefix('card-')} heading or cues"
            )
            raise ValueError(f"HTML slide layout did not fit ({parser.surface}); shorten the {field} and request a new artifact")
        raise ValueError(f"HTML slide layout did not fit ({parser.layout or 'no layout result'})")


def render(scope: str) -> dict:
    path = staged_path(scope)
    try:
        spec = read(scope)
        with tempfile.TemporaryDirectory(prefix="render-", dir=path.parent) as directory:
            scratch = Path(directory)
            output = scratch / "document.html"
            builder().build(spec, output)
            document = output.read_bytes()
            if spec["kind"] == "brief":
                data = render_pdf(spec, document, scratch)
            else:
                validate_article(document, scratch)
                data = document
    finally:
        path.unlink(missing_ok=True)
    if len(data) > MAX_OUTPUT:
        raise ValueError("Visual artifact exceeds the delivery size limit")
    return {"scope": scope, "spec": spec, "contentBase64": base64.b64encode(data).decode("ascii")}
