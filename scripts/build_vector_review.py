"""Preview the selected transparent identity and export small offline SVG widgets."""
import base64
import json
import re
from build_docs import ROOT, head
from build_furniture_review import study
from build_logo_review import widget_document, widget_square, widget_announcement

LOGO = "assets/javorna/chair.svg"
REVERSE = "assets/javorna/chair-on-dark.svg"
HERO = "assets/javorna/atelier.png"
ORIGINAL = "assets/javorna/chair-original.png"


def inline_svg_images(markup):
    def embed(match):
        src = match[1]
        if src not in (LOGO, REVERSE, "assets/javorna/growth-rings.svg"):
            raise ValueError(f"Widgets accept only approved local SVG assets: {src}")
        encoded = base64.b64encode((ROOT / "docs" / src).read_bytes()).decode("ascii")
        return f'src="data:image/svg+xml;base64,{encoded}"'
    return re.sub(r'src="([^"]+)"', embed, markup)


def widgets():
    return [(name, markup.replace('class="widget ', 'class="widget javorna '))
            for name, markup in (
                ("decision", widget_document(REVERSE)),
                ("status", widget_square(REVERSE)),
                ("kickoff", widget_announcement(REVERSE, None)),
            )]


def widget_file(markup):
    css = (ROOT / "docs/assets/logo-review.css").read_text(encoding="utf-8")
    css += (ROOT / "docs/assets/javorna-brand.css").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'img-src data:; style-src \'unsafe-inline\'">'
        '<title>Javorna widget preview</title><style>'
        + css + 'body{max-width:704px;box-sizing:border-box;margin:0 auto;padding:12px;background:#EEEBDD}.widget{max-width:680px;margin:auto}'
        '</style></head><body>' + inline_svg_images(markup) + '</body></html>'
    )


def build():
    palette = next(p for p in json.loads((ROOT / "brand/furniture-palettes.json").read_text(encoding="utf-8"))
                   if p["id"] == "C")
    palette = {**palette, "image": "chair"}
    styles = "".join(f'<link rel="stylesheet" href="assets/{name}.css">'
                     for name in ("furniture-review", "logo-review", "javorna-brand", "vector-review"))
    gallery = head("article.template.html", "Javorna — křeslo bez rámečku", "ft2026-vector-review")
    gallery = gallery.replace('<html lang="en"', '<html lang="cs"').replace("</head>", styles + "</head>")
    gallery += (
        '<div class="controls" role="group" aria-label="Vzhled">'
        '<button type="button" class="ctrl" data-action="print">PDF</button>'
        '<button type="button" class="ctrl" data-action="toggle-print-theme">PDF theme: Light</button>'
        '<button type="button" class="ctrl" data-action="toggle-theme">Dark</button></div>'
        '<div class="doc"><header class="doc-header"><p class="eyebrow">JAVORNA · SCHVÁLENÉ KŘESLO</p>'
        '<h1>Symbol, ne dlaždice.</h1>'
        '<p class="subtitle">Stejné křeslo. Průhledné pozadí. Žádný čtverec ani rámeček.</p></header><main>'
        '<div class="vector-pair">'
    )
    for kind, src, title, description in (
        ("light", LOGO, "Původní barvy na světlé ploše", "Kouřový dub a zelené čalounění. Bez béžového pozadí."),
        ("dark", REVERSE, "Světlá varianta na tmavé ploše", "Stejná geometrie; světlejší dřevo i čalounění drží čitelnost."),
    ):
        gallery += (
            f'<section class="vector-tile {kind}"><div class="vector-lockup">'
            f'<img src="{src}" width="357" height="367.25" alt="Javorna křeslo">'
            '<span>JAVORNA<small>BESPOKE WOODWORK</small></span></div>'
            f'<h2>{title}</h2><p>{description}</p><div class="vector-sizes">'
            + "".join(f'<span><img src="{src}" width="{size}" height="{size * 367.25 / 357:g}" alt="">{size} px</span>'
                      for size in (96, 48, 32))
            + '</div></section>'
        )
    logo_bytes = (ROOT / "docs" / LOGO).stat().st_size
    motif_bytes = (ROOT / "docs/assets/javorna/growth-rings.svg").stat().st_size
    gallery += (
        '</div><p class="vector-facts">'
        f'SVG {logo_bytes:,} B · 5 barevných vrstev · žádná bitmapa · '
        'průhledné mezery mezi konstrukcí · stejný tvar v obou variantách</p>'
        '<details class="vector-original"><summary>Porovnat s původním schváleným návrhem</summary>'
        '<div class="vector-compare">'
        f'<figure><img src="{ORIGINAL}" width="2048" height="2048" alt="Původní schválený návrh"><figcaption>Originál</figcaption></figure>'
        f'<figure><img class="vector-aligned" src="{LOGO}" width="357" height="367.25" alt="Vektorový převod">'
        '<figcaption>SVG · jemné barevné přechody nahrazené plnými barvami</figcaption></figure>'
        '</div></details>'
    )
    deck = head("deck.template.html", "Javorna — transparent identity", "ft2026-vector-slides")
    deck = deck.replace("</head>", styles + "</head>") + '<main class="deck-stage">'
    for content, label, slug in ((False, "Úvodní slide", "title"), (True, "Obsahový slide", "content")):
        markup = study(palette, {"chair": LOGO if content else REVERSE}, HERO, content, approved=True)
        gallery += (f'<section class="review-direction"><div class="study-caption"><h2>{label}</h2>'
                    f'<a href="vector-slides.html#s-{slug}">Prezentovat</a></div>{markup}</section>')
        if not content:
            markup = markup.replace('<h2 class="title-heading">', '<h1 class="title-heading">').replace('</h2>', '</h1>')
        deck += f'<section class="slide furniture-slide" id="s-{slug}">{markup}</section>'
    gallery += (
        '<section class="review-direction" id="widgets"><h2>Widgety: vektor + data, bez fotografií.</h2>'
        f'<p>Jeden dekorativní motiv letokruhů má {motif_bytes} B. '
        'Čísla, tabulky a stav zůstávají čitelným HTML, ne součástí obrázku.</p><div class="widget-gallery">'
    )
    sizes = {}
    for name, markup in widgets():
        standalone = widget_file(markup)
        sizes[name] = len(standalone.encode("utf-8"))
        out = ROOT / "docs/widgets"
        out.mkdir(exist_ok=True)
        (out / f"{name}.html").write_bytes(standalone.encode("utf-8"))
        gallery += (f'<figure class="widget-shot">{markup}<figcaption>{name} · '
                    f'{sizes[name]:,} B včetně CSS a vložených SVG</figcaption></figure>')
    gallery += (
        '</div><p class="widget-limits">Samostatné offline náhledy. SVG jsou vložena jako data: obrázky; '
        'ovládací prvky jsou jen vizuální makety. Nasazení a ověření v živých Teams teprve následuje.</p></section>'
        '</main><footer class="doc-footer"><span>Javorna Group · fiktivní společnost · paleta C · '
        'křeslo 3 · originál zachován</span></footer></div></body></html>'
    )
    (ROOT / ".local/vector-widget-sizes.json").write_text(json.dumps(sizes, indent=2), encoding="utf-8")
    return gallery, deck + "</main></body></html>"


def main():
    gallery, deck = build()
    for name, text in (("vector-review.html", gallery), ("vector-slides.html", deck)):
        (ROOT / "docs" / name).write_text(text, encoding="utf-8")
        print(ROOT / "docs" / name)


if __name__ == "__main__":
    main()
