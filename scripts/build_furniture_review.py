"""Build three local logo/palette comparisons and six real 16:9 slide studies."""
import argparse
import json
from pathlib import Path

from build_brand_review import LABELS, load_images
from build_docs import ROOT, esc, head

COLORS = ("wood", "deep", "paper", "muted", "leaf", "pine")


def theme(palette):
    return ";".join(f"--{key}:{palette[key]}" for key in COLORS)


def lockup(image, subtitle=False):
    return (
        '<div class="sample-lockup">'
        f'<img class="sample-mark" src="{esc(image)}" width="2048" height="2048" alt="Javorna furniture logo concept">'
        '<div class="sample-wordmark">JAVORNA'
        + ('<small>BESPOKE WOODWORK</small>' if subtitle else '')
        + '</div></div>'
    )


def study(palette, images, hero, content=False):
    letter, name = palette["id"], palette["name"]
    logo = images[palette["image"]]
    opening = f'<div class="study-frame{" sample-content" if content else ""}" style="{theme(palette)}">'
    if not content:
        return (
            opening
            + f'<img class="title-photo" src="{esc(hero)}" width="2048" height="2048" '
            'alt="AI-generated fictional woodworking atelier">'
            '<div class="title-copy">' + lockup(logo, True)
            + '<h2 class="title-heading">Craft takes people.<br>Coordination<br>takes <em>Otto.</em></h2>'
            '<p class="title-sub">One budget season.<br>Four countries. Clear boundaries.</p>'
            '<p class="title-bottom"><strong>Tomáš Kubica · Microsoft</strong><br>Frontier Day 2026</p></div>'
            f'<p class="sample-tag">{esc(letter)} · {esc(name)}</p></div>'
        )
    return (
        opening
        + '<div class="content-top">' + lockup(logo)
        + '<p class="sample-kicker">Week three · same goal</p></div>'
        '<h2 class="content-heading">A goal outlives a conversation.</h2>'
        '<div class="country-grid">'
        '<div class="country-card"><h3>Submitted</h3><p>Czechia<br>Poland · Hungary</p></div>'
        '<div class="country-card"><h3>Follow up</h3><p>Slovakia<br>Submission overdue</p></div>'
        '<div class="country-card"><h3>Next step</h3><p>Emma prepares<br>challenge sessions</p></div></div>'
        '<p class="sample-note"><strong>Otto coordinates.</strong> People keep the decision rights.</p>'
        '<p class="sample-foot">Javorna Group · fictional scenario</p>'
        f'<p class="sample-tag">{esc(letter)} · {esc(name)}</p></div>'
    )


def build(palettes, images, hero):
    stylesheet = '<link rel="stylesheet" href="assets/furniture-review.css">'
    gallery = head("article.template.html", "Javorna — nábytek, dřevo a zeleň", "ft2026-furniture-review")
    gallery = gallery.replace('<html lang="en"', '<html lang="cs"').replace("</head>", stylesheet + "</head>")
    gallery += (
        '<div class="controls" role="group" aria-label="Vzhled galerie">'
        '<button type="button" class="ctrl" data-action="print">PDF</button>'
        '<button type="button" class="ctrl" data-action="toggle-print-theme">PDF theme: Light</button>'
        '<button type="button" class="ctrl" data-action="toggle-theme">Dark</button>'
        '<button type="button" class="ctrl" data-action="toggle-accent">Accent: Green</button></div>'
        '<div class="doc"><header class="doc-header">'
        '<p class="eyebrow">JAVORNA · NOVÉ NÁVRHY ZNAČKY</p>'
        '<h1>Nábytek jako podpis.</h1>'
        '<p class="subtitle">Tři loga. Tři palety dřeva a zeleně. Stejný ateliér.</p>'
        '<p>Vyber logo a barevnost nezávisle — například logo B s paletou A. '
        'Návrhy zatím nejsou aplikované do hlavní prezentace.</p></header>'
        '<main><div class="furniture-options">'
    )
    for palette in palettes:
        letter = palette["id"]
        gallery += (
            f'<section class="furniture-option" id="option-{letter}">'
            f'<img class="option-mark" src="{images[palette["image"]]}" width="2048" height="2048" '
            f'alt="Varianta {letter} — {esc(palette["label"])}">'
            '<div class="palette-strip" aria-hidden="true">'
            + ''.join(f'<span style="background:{palette[key]}"></span>' for key in ("deep", "wood", "paper", "leaf", "pine"))
            + f'</div><div class="option-copy"><h2>{letter} · {esc(palette["label"])}</h2>'
            f'<p>{esc(palette["description"])}</p><a href="#study-{letter}">Prohlédnout slidy</a></div></section>'
        )
    gallery += '</div>'
    deck = head("deck.template.html", "Javorna — furniture and palette studies", "ft2026-furniture-slides")
    deck = deck.replace("</head>", stylesheet + "</head>") + '<main class="deck-stage">'
    for palette in palettes:
        letter = palette["id"]
        gallery += (
            f'<section class="review-direction" id="study-{letter}">'
            f'<h2>{letter} · {esc(palette["label"])}</h2><p>{esc(palette["description"])}</p>'
            '<div class="palette-list">'
        )
        for key, label in (("wood", "Dřevo"), ("deep", "Hloubka"), ("paper", "Slonová kost"), ("leaf", "Listí"), ("pine", "Jehličí")):
            gallery += f'<span><i style="background:{palette[key]}" aria-hidden="true"></i>{label} <code>{palette[key]}</code></span>'
        gallery += '</div>'
        for content, label, suffix in ((False, "Úvodní slide", "title"), (True, "Obsahový slide", "content")):
            markup = study(palette, images, hero, content)
            gallery += (
                f'<div class="study-caption"><h3>{label}</h3>'
                f'<a href="furniture-slides.html#s-{letter}-{suffix}">Otevřít jako prezentaci</a></div>'
                + markup
            )
            deck_markup = markup
            if letter == palettes[0]["id"] and not content:
                deck_markup = markup.replace('<h2 class="title-heading">', '<h1 class="title-heading">').replace('</h2>', '</h1>')
            deck += f'<section class="slide furniture-slide" id="s-{letter}-{suffix}">{deck_markup}</section>'
        gallery += '</section>'
    gallery += (
        '</main><footer class="doc-footer"><span>Koncepty ke schválení · '
        'MAI 2.7 · původní PNG 2048 × 2048 · fiktivní společnost</span></footer></div></body></html>'
    )
    return gallery, deck + '</main></body></html>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--atelier-manifest", type=Path, required=True)
    args = parser.parse_args()
    palettes = json.loads((ROOT / "brand/furniture-palettes.json").read_text(encoding="utf-8"))
    manifest, images = load_images(args.manifest, {p["image"] for p in palettes})
    atelier, old_images = load_images(args.atelier_manifest, {*LABELS, "workshop-hero"})
    gallery, deck = build(palettes, images, old_images["workshop-hero"])
    for name, text in (("furniture-review.html", gallery), ("furniture-slides.html", deck)):
        (ROOT / "docs" / name).write_text(text, encoding="utf-8")
        print(ROOT / "docs" / name)
    (ROOT / ".local/furniture-review-lineage.json").write_text(json.dumps({
        "run_id": manifest["run_id"], "atelier_run_id": atelier["run_id"],
        "images": images, "atelier": old_images["workshop-hero"],
        "status": "awaiting-user-selection", "presentation_modified": False,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
