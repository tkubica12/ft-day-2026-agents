"""Build standalone html-docs artifacts from bounded, plain-text content."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BRAND = json.loads((ROOT / "brand.json").read_text(encoding="utf-8"))
MAX_SPEC_BYTES = 64 * 1024
PROHIBITED = re.compile(
    r"<\s*/?\s*[a-z!]|(?:https?://|www\.|file:|data:|javascript:|//)|\bon[a-z]+\s*=",
    re.IGNORECASE,
)


def text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be nonempty text of at most {limit} characters")
    if PROHIBITED.search(value) or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError(f"{field} contains markup, an event handler, or an external reference")
    return value.strip()


def validate(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"kind", "title", "subtitle", "closing", "source", "sections"}:
        raise ValueError("spec must contain exactly kind, title, subtitle, closing, source, and sections")
    kind = value["kind"]
    if not isinstance(kind, str) or kind not in {"report", "slides", "brief"}:
        raise ValueError("kind must be report, slides, or brief")
    sections = value["sections"]
    maximum = 4 if kind == "brief" else 8
    if not isinstance(sections, list) or not 1 <= len(sections) <= maximum:
        raise ValueError(f"sections must contain 1 to {maximum} entries")
    result = {
        "kind": kind,
        "title": text(value["title"], "title", 100),
        "subtitle": text(value["subtitle"], "subtitle", 200),
        "closing": text(value["closing"], "closing", 150),
        "source": text(value["source"], "source", 200),
        "sections": [],
    }
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"heading", "body", "cues"}:
            raise ValueError("each section must contain exactly heading, body, and cues")
        cues = section["cues"]
        if not isinstance(cues, list) or len(cues) > 3:
            raise ValueError("each section has at most three cues")
        clean_cues = [text(cue, "cue", 90) for cue in cues]
        if any(len(cue.split()) > 10 for cue in clean_cues):
            raise ValueError("each cue has at most ten words")
        result["sections"].append({
            "heading": text(section["heading"], "heading", 90),
            "body": text(section["body"], "body", 300 if kind == "brief" else 1200),
            "cues": clean_cues,
        })
    return result


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def article_body(spec: dict) -> str:
    sections = []
    for index, section in enumerate(spec["sections"], 1):
        cues = "".join(f'<li class="frag">{esc(cue)}</li>' for cue in section["cues"])
        sections.append(
            f'<article class="card{" card--lead" if index == 1 else ""}" id="card-{index}"'
            f'{" data-open" if index == 1 else ""}>'
            f'<div class="slide-content"><h2 class="slide-title">{esc(section["heading"])}</h2>'
            f'<ul class="slide-points">{cues}</ul></div>'
            f'<h3 class="card-head"><button type="button" class="card-toggle" aria-expanded="{str(index == 1).lower()}">'
            f'<span class="card-num" aria-hidden="true"></span><span class="card-titles">'
            f'<span class="card-title">{esc(section["heading"])}</span></span>'
            f'<span class="card-mark" aria-hidden="true"></span></button></h3>'
            f'<div class="card-body"><p>{esc(section["body"])}</p></div></article>'
        )
    return f"""
<div class="controls" role="group" aria-label="Document controls">
  <button type="button" class="ctrl" data-action="expand-all">Expand all</button>
  <button type="button" class="ctrl" data-action="collapse-all">Collapse all</button>
  <button type="button" class="ctrl" data-action="toggle-slides" aria-pressed="false">Slides</button>
  <button type="button" class="ctrl" data-action="print">PDF</button>
  <button type="button" class="ctrl" data-action="toggle-print-theme">PDF theme: Light</button>
  <button type="button" class="ctrl" data-action="toggle-theme">Dark</button>
  <button type="button" class="ctrl" data-action="toggle-accent">Accent: Green</button>
</div>
<div class="doc">
  <header class="doc-header" id="opening">
    <div class="slide-content slide-content--title">
      <img class="brand-logo" src="assets/javorna-group-logo.svg" alt="Javorna Group">
      <p class="slide-eyebrow">{esc(BRAND["disclaimer"])}</p>
      <h2 class="slide-title">{esc(spec["title"])}</h2>
      <p class="slide-lead">{esc(spec["subtitle"])}</p>
    </div>
    <img class="brand-logo" src="assets/javorna-group-logo.svg" alt="Javorna Group">
    <p class="brand-disclaimer">{esc(BRAND["disclaimer"])}</p>
    <h1>{esc(spec["title"])}</h1><p class="subtitle">{esc(spec["subtitle"])}</p>
  </header>
  <main><section class="chapter" id="ch-summary">
    <h2 class="chapter-label">Overview</h2>{''.join(sections)}
  </section></main>
  <section class="takeaway" id="closing">
    <p>{esc(spec["closing"])}</p>
    <div class="slide-content slide-content--end"><p class="slide-eyebrow">Takeaway</p>
      <h2 class="slide-title">{esc(spec["closing"])}</h2></div>
  </section>
  <footer class="doc-footer">{esc(spec["source"])} · {esc(BRAND["disclaimer"])}</footer>
</div>"""


def sheet_body(spec: dict) -> str:
    blocks = "".join(
        f'<section class="sheet-block"><h3>{esc(s["heading"])}</h3><p>{esc(s["body"])}</p></section>'
        for s in spec["sections"]
    )
    return f"""
<div class="controls" role="group" aria-label="Document controls">
  <button type="button" class="ctrl" data-action="print">PDF</button>
  <button type="button" class="ctrl" data-action="toggle-print-theme">PDF theme: Light</button>
  <button type="button" class="ctrl" data-action="toggle-theme">Dark</button>
  <button type="button" class="ctrl" data-action="toggle-accent">Accent: Green</button>
</div>
<section class="sheet" aria-label="{esc(spec["title"])}"><article class="sheet-page" id="page-summary">
  <header class="sheet-head"><img class="brand-logo" src="assets/javorna-group-logo.svg" alt="Javorna Group">
    <p class="brand-disclaimer">{esc(BRAND["disclaimer"])}</p>
    <h1 class="sheet-title">{esc(spec["title"])}</h1><p class="sheet-lead">{esc(spec["subtitle"])}</p>
  </header>
  <div class="sheet-grid">{blocks}</div>
  <p class="sheet-close">{esc(spec["closing"])}</p>
  <footer class="sheet-notes"><p>{esc(spec["source"])}</p></footer>
</article></section>"""


def source_document(spec: dict) -> str:
    spec = validate(spec)
    logo = ROOT / "assets" / "javorna-group-logo.svg"
    if hashlib.sha256(logo.read_bytes()).hexdigest() != BRAND["logoSha256"]:
        raise ValueError("The approved Javorna Group logo does not match its verified hash")
    sheet = spec["kind"] == "brief"
    template = (ROOT / ("sheet.template.html" if sheet else "article.template.html")).read_text(encoding="utf-8")
    template = template.replace('data-default-accent="blue"', 'data-default-accent="green"')
    template = template.replace("SHEET TITLE" if sheet else "ARTICLE TITLE", esc(spec["title"]), 1)
    template = template.replace("ONE SENTENCE SUMMARY", esc(spec["subtitle"]), 1)
    template = template.replace('content="SLUG"', f'content="otto-{hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]}"', 1)
    style = """
<style>
.brand-logo { display:block; width:64px; max-width:40%; height:auto; margin:0 0 16px; }
.brand-disclaimer { font:600 12px/1.35 system-ui,"DejaVu Sans",sans-serif; color:var(--text-muted); }
[data-view="slides"] .slide-content .brand-logo { width:72px; height:auto; }
[data-view="slides"] .slide-content .slide-eyebrow { font-size:clamp(11px,1vw,15px); letter-spacing:normal; text-transform:none; }
</style>"""
    template = template.replace("</head>", style + "\n</head>", 1)
    if spec["kind"] == "slides":
        template = template.replace("</head>", '<script>document.documentElement.dataset.view="slides";</script>\n</head>', 1)
    body = sheet_body(spec) if sheet else article_body(spec)
    template = template[:template.index("<body>")] + "<body>\n" + body + "\n</body>\n</html>\n"
    license_text = (ROOT / "assets" / "LICENSE").read_text(encoding="utf-8")
    template = "<!--\n" + license_text + "\n-->\n" + template
    return template


def expected_document(spec: dict) -> bytes:
    """Reconstruct the trusted bundle independently for gateway verification."""
    document = source_document(spec)

    def css(match: re.Match) -> str:
        filename = match.group(1)
        if filename not in {"article.css", "slides.css", "sheet.css"}:
            raise ValueError("Unexpected stylesheet")
        return "<style>\n" + (ROOT / "assets" / filename).read_bytes().decode("utf-8").strip() + "\n</style>"

    def script(match: re.Match) -> str:
        filename = match.group(1)
        if filename not in {"article.js", "slides.js", "sheet.js"}:
            raise ValueError("Unexpected script")
        code = (ROOT / "assets" / filename).read_bytes().decode("utf-8").strip()
        return ("<script>\n(function () {\n  function boot() {\n" + code +
                "\n  }\n  if (document.readyState === \"loading\") {\n" +
                "    document.addEventListener(\"DOMContentLoaded\", boot);\n" +
                "  } else { boot(); }\n})();\n</script>")

    document = re.sub(r'<link rel="stylesheet" href="assets/([^"]+)">', css, document)
    document = re.sub(r'<script src="assets/([^"]+)" defer></script>', script, document)
    logo = base64.b64encode((ROOT / "assets" / "javorna-group-logo.svg").read_bytes()).decode("ascii")
    document = document.replace('src="assets/javorna-group-logo.svg"', 'src="data:image/svg+xml;base64,' + logo + '"')
    return document.encode("utf-8")


def build(spec: dict, output: Path) -> Path:
    template = source_document(spec)
    with tempfile.TemporaryDirectory(prefix="otto-html-docs-") as directory:
        scratch = Path(directory)
        shutil.copytree(ROOT / "assets", scratch / "assets")
        source = scratch / "document.html"
        standalone = scratch / "standalone.html"
        source.write_bytes(template.encode("utf-8"))
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required to bundle html-docs")
        env = {"PATH": os.environ.get("PATH", "")}
        if os.name == "nt" and "SystemRoot" in os.environ:
            env["SystemRoot"] = os.environ["SystemRoot"]
        subprocess.run([node, str(scratch / "assets" / "bundle.js"), str(source), str(standalone)],
                       check=True, capture_output=True, timeout=30, env=env)
        if standalone.read_bytes() != expected_document(spec):
            raise ValueError("html-docs bundle differs from the trusted expected output")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(standalone.read_bytes())
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = args.spec.read_bytes()
    if len(data) > MAX_SPEC_BYTES:
        raise ValueError("spec exceeds 64 KiB")
    build(json.loads(data), args.output)


if __name__ == "__main__":
    main()
