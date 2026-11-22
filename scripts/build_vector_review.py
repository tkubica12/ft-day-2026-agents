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
CATEGORY_ICONS = {
    "home": "assets/javorna/category-home.svg",
    "workplace": "assets/javorna/category-workplace.svg",
    "artistic": "assets/javorna/category-artistic.svg",
}


def inline_svg_images(markup):
    def embed(match):
        src = match[1]
        if src not in (LOGO, REVERSE, *CATEGORY_ICONS.values()):
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


def commission_types():
    categories = (
        ("home", "Bydlení a domácnosti", "Nábytek a interiéry na míru."),
        ("workplace", "Firmy a veřejné prostory", "Kanceláře, hotely a reprezentativní interiéry."),
        ("artistic", "Umělecké a speciální zakázky", "Kostely, restaurace, film a restaurování."),
    )
    return (
        '<section class="commission-widget" aria-labelledby="commission-heading">'
        '<p class="commission-kicker">Nová zakázka · 01</p>'
        '<h3 id="commission-heading">Pro jaký prostor tvoříme?</h3>'
        '<p class="commission-intro">Vyberte typ zakázky. Podrobnosti doplníme v dalším kroku.</p>'
        '<div class="commission-options" role="group" aria-label="Typ zakázky">'
        + "".join(
            '<div class="commission-option">'
            f'<img src="{icon}" width="64" height="64" alt="">'
            f'<strong>{title}</strong><span>{description}</span>'
            '</div>'
            for key, title, description in categories
            for icon in (CATEGORY_ICONS[key],)
        )
        + '</div><p class="commission-caption">Formulářová navigace, ne další logo</p></section>'
    )


def widget_file(markup):
    css = (ROOT / "docs/assets/logo-review.css").read_text(encoding="utf-8")
    css += (ROOT / "docs/assets/javorna-brand.css").read_text(encoding="utf-8")
    css += (ROOT / "docs/assets/vector-review.css").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'img-src data:; style-src \'unsafe-inline\'">'
        '<title>Javorna widget preview</title><style>'
        + css + 'body{max-width:704px;box-sizing:border-box;margin:0 auto;padding:12px;background:#EEEBDD}.widget{max-width:680px;margin:auto}.commission-widget{max-width:680px;margin:auto}'
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
        '<section class="review-direction" id="widgets"><h2>Widgety: logo, data a klid.</h2>'
        '<p>Rozpočtová čísla a rozhodnutí potřebují přehledné záhlaví, ne dekoraci navíc.</p><div class="widget-gallery">'
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
    commission = widget_file(commission_types())
    sizes["commission-types"] = len(commission.encode("utf-8"))
    (ROOT / "docs/widgets/commission-types.html").write_bytes(commission.encode("utf-8"))
    gallery += (
        '</div><p class="widget-limits">Samostatné offline náhledy. Rozpočtové widgety používají jen logo; '
        'akce jsou vizuální makety. Nasazení v živých Teams teprve následuje.</p></section>'
        '<section class="review-direction" id="commission-types"><h2>Zakázky: ikona vysvětluje volbu</h2>'
        '<p>Jemné jednobarevné obrysy označují kategorii ve formuláři — nikdy nesoutěží s barevným křeslem Javorna.</p>'
        f'<figure class="widget-shot">{commission_types()}'
        '<figcaption>Výběr typu zakázky · malý SVG symbol + čitelný popisek</figcaption></figure>'
        '<p class="widget-limits">Ikony jsou záměrně jiné než logo: tenký obrys, jedna barva, žádná židle ani trůn.</p>'
        '</section></main><footer class="doc-footer"><span>Javorna Group · fiktivní společnost · paleta C · '
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
