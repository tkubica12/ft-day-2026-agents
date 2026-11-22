"""Verify immutable originals and build a local approval gallery; never publish."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image
from build_docs import head, esc

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "joinery-mark": ("A", "Precision joinery", "A dovetail-inspired J. Contemporary, precise and architectural."),
    "growth-mark": ("B", "Living grain", "Growth rings and continuity. A quieter, organic atelier identity."),
    "chisel-mark": ("C", "Sculpted form", "Faceted planes. Artistic commissions and expressive craftsmanship."),
}


def load_images(manifest_path, expected):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["summary"]["failed"]:
        raise ValueError("The generation run contains failures; do not show it as a complete set.")
    records = manifest["records"]
    if {r["lineage"]["image"] for r in records} != set(expected) or len(records) != len(expected):
        raise ValueError("Expected exactly the authored candidates.")
    images = {}
    for record in records:
        name = record["lineage"]["image"]
        source = (manifest_path.parent.parent / record["original"]).resolve()
        source.relative_to(manifest_path.parent.parent.resolve())
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["image_sha256"]:
            raise ValueError(f"{name}: original hash mismatch")
        with Image.open(source) as image:
            if image.size != (2048, 2048) or image.format != "PNG":
                raise ValueError(f"{name}: wrong native size or format")
            image.verify()
        relative = Path("brand-candidates") / f"{name}-{record['image_sha256'][:12]}.png"
        destination = ROOT / "docs" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() != data:
            raise ValueError("An existing candidate was modified; refusing to overwrite it.")
        destination.write_bytes(data)
        images[name] = relative.as_posix()
        print(f"{name}: {destination} ({len(data)} bytes; 2048x2048)")
    return manifest, images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest, images = load_images(args.manifest, {*LABELS, "workshop-hero"})

    text = head("article.template.html", "Javorna — brand directions", "ft2026-brand-review")
    style = """<style>
    .brand-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}
    .brand-option{border:1px solid var(--border);border-radius:var(--radius);padding:24px;background:var(--surface)}
    .brand-option img{display:block;width:100%;height:240px;object-fit:contain;border-radius:8px}
    .wordmark{font-size:28px;letter-spacing:.15em;text-align:center;margin:18px 0}
    .brand-option h2{font-size:24px}.brand-option p{color:var(--text-muted)}
    .label{color:var(--accent);font:600 13px var(--font-sans);letter-spacing:.12em;text-transform:uppercase}
    .study{margin:36px 0 64px;padding:4%;display:grid;grid-template-columns:1fr 1fr;gap:4%;
      border:1px solid var(--border);border-radius:var(--radius);background:var(--surface)}
    .study .copy{align-self:center}.study .mark{width:100px;height:100px;object-fit:contain}
    .study .hero{width:100%;height:auto;align-self:center;object-fit:contain}
    .study h2{font-size:clamp(26px,3vw,44px);line-height:1.08;margin:24px 0}
    .study .wordmark{text-align:left;font-size:19px}.study p{color:var(--text-muted)}
    @media(max-width:900px){.brand-grid{gap:12px}.brand-option{padding:14px}
      .brand-option h2{font-size:18px}.brand-option p{font-size:14px}
      .brand-option img{height:190px}.brand-option .wordmark{font-size:18px;letter-spacing:.1em}}
    @media(max-width:600px){.brand-grid{grid-template-columns:1fr}.study{grid-template-columns:1fr}}
    </style>"""
    text = text.replace("</head>", style + "</head>")
    text += (
        '<div class="controls" role="group" aria-label="Appearance controls">'
        '<button type="button" class="ctrl" data-action="print">PDF</button>'
        '<button type="button" class="ctrl" data-action="toggle-print-theme">PDF theme: Light</button>'
        '<button type="button" class="ctrl" data-action="toggle-theme">Dark</button>'
        '<button type="button" class="ctrl" data-action="toggle-accent">Accent: Green</button></div>'
        '<div class="doc"><header class="doc-header"><p class="eyebrow">JAVORNA · BRAND REVIEW</p>'
        '<h1>Three ways to express the craft.</h1>'
        '<p class="subtitle">Original MAI 2.7 concepts for a fictional luxury woodworking group.</p>'
        '<p>Native 2048 × 2048 PNG originals. No resizing, cropping or upscaling of the source files. '
        'Concepts only — no direction has been approved.</p></header><main>'
        '<div class="brand-grid">'
    )
    for name, (letter, title, description) in LABELS.items():
        text += (
            f'<section class="brand-option" id="direction-{letter}"><span class="label">Direction {letter}</span>'
            f'<h2>{esc(title)}</h2><img src="{images[name]}" width="2048" height="2048" '
            f'alt="{esc(title)} logo concept"><div class="wordmark">JAVORNA</div>'
            f'<p>{esc(description)}</p></section>'
        )
    text += '</div><h2>In a title-slide composition</h2><p>Three studies, one unchanged atelier image. Text remains editable HTML.</p>'
    for name, (letter, title, _) in LABELS.items():
        text += (
            f'<section class="study" aria-label="Direction {letter} title study"><div class="copy">'
            f'<p class="label">Direction {letter} · {esc(title)}</p>'
            f'<img class="mark" src="{images[name]}" alt="{esc(title)} concept">'
            '<div class="wordmark">JAVORNA</div><h2>Craft takes people.<br>Coordination takes Otto.</h2>'
            '<p>One budget season.<br>Four countries. Clear boundaries.</p>'
            '<p>Tomáš Kubica · Microsoft<br>Frontier Day 2026</p></div>'
            f'<img class="hero" src="{images["workshop-hero"]}" width="2048" height="2048" '
            'alt="AI-generated fictional woodworking atelier, not a real customer photograph"></section>'
        )
    text += '</main><footer class="doc-footer"><span>Local approval gallery · not applied to the presentation</span></footer></div></body></html>'
    (ROOT / "docs/brand-review.html").write_text(text, encoding="utf-8")
    (ROOT / ".local/brand-review-lineage.json").write_text(
        json.dumps({"run_id": manifest["run_id"], "images": images, "status": "awaiting-user-selection"}, indent=2),
        encoding="utf-8")


if __name__ == "__main__":
    main()
