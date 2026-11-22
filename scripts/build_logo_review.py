"""Build the local review of three final furniture logos, palette C slide studies and Teams widget mockups."""
import argparse
import json
from pathlib import Path

from build_brand_review import LABELS, load_images
from build_docs import ROOT, esc, head
from build_furniture_review import study

FINALISTS = (
    ("r2-s1-ribbon", "1", "Stuha", "Zjednodušeno — bez opěrky",
     "Jedna plynulá skořepina z kouřového dubu se světlou vnitřní plochou. Nejblíž původnímu C.",
     "Nejčitelnější i ve 32 px.", "Spíš designová ikona než luxusní kus."),
    ("r2-s2-planks-pine", "2", "Kapradí", "Zjednodušeno — bez opěrky",
     "Dubový rám a čalounění v jehličnaté zeleni. Logo samo nese barvu palety.",
     "Zadní noha plynule přechází v opěradlo, přední nese sedák.", "Ve 32 px tmavý rám splývá."),
    ("r2-d2-cushion", "3", "Křeslo", "Opěrka zachována — 3D",
     "Tříčtvrteční pohled, přesně čtyři nohy. Opěrky nesou přední nohy a zadní sloupky.",
     "Nejluxusnější, konstrukčně poctivé.", "Ve 32 px hodně detailů; favicon by chtěl zjednodušení."),
)
ROUNDS = (
    ("Kolo 1", (
        ("r1-s1-ribbon", "Dobrý směr, ale čte se jako lehátko."),
        ("r1-s2-planks", "Dobré — dopracováno v kole 2."),
        ("r1-s3-shell", "Čte se jako krabice nebo pohovka."),
        ("r1-d1-frame", "Pohled zezadu, jen jedna opěrka."),
        ("r1-d2-cushion", "Konstrukce sedí, ale přidal zelený pruh podlahy."),
        ("r1-d3-sideframe", "Nakloněná krabice."))),
    ("Kolo 2", (
        ("r2-s1-ribbon", "Finalista 1."),
        ("r2-s2-planks-oak", "Boční lišta nedosedá na zadní nohu."),
        ("r2-s2-planks-pine", "Finalista 2."),
        ("r2-d2-cushion", "Finalista 3."),
        ("r2-d2-wood", "Nepřehledná konstrukce."),
        ("r2-d2-sculpted", "Baňaté, nefunkční nohy."))),
    ("Kolo 3", (
        ("r3-d2-oak", "Zkřížené opěrky, chaos."),
        ("r3-d2-bold", "Rozbité proporce."),
        ("r3-s1-pine", "Abstraktní, zelený blok podlahy."),
        ("r3-s1-threequarter", "Čisté 3D bez opěrky — náhradník, ale generický."))),
)


def mark(image, css="w-mark"):
    dimensions = 'width="357" height="367.25"' if image.endswith(".svg") else 'width="2048" height="2048"'
    return f'<img class="{css}" src="{esc(image)}" {dimensions} alt="Javorna logo">'


def band(image, kicker):
    return (f'<div class="w-band">{mark(image)}<span class="w-word">JAVORNA</span>'
            f'<span class="w-kicker">{esc(kicker)}</span></div>')


def widget_document(image):
    rows = (("PL-CELL line", "€0.45 M", "€0.60 M"),
            ("Other Poland capex", "as submitted", "rebalance"),
            ("Poland capex cap", "€1.10 M", "€1.10 M"))
    return (
        '<article class="widget widget-doc">' + band(image, "FY2027 · Poland · private")
        + '<div class="w-body"><div><h3 class="w-title">PL-CELL capex decision</h3>'
        '<p class="w-meta">Decided by Adam Vávra, Group CFO · recorded by Otto · 23 Nov 2026</p></div>'
        '<div class="w-kpis">'
        '<div class="w-kpi"><b>€0.60 M</b><span>PL-CELL approved</span></div>'
        '<div class="w-kpi"><b>€1.10 M</b><span>Cap unchanged</span></div>'
        '<div class="w-kpi"><b>FY2027</b><span>Only this year</span></div></div>'
        '<table class="w-table"><thead><tr><th>Item</th><th>Before</th><th>Now</th></tr></thead><tbody>'
        + ''.join(f'<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>' for a, b, c in rows)
        + '</tbody></table>'
        '<p class="w-text"><strong>Next:</strong> Piotr replaces the line and rebalances within the cap. '
        'Emma validates the revised submission.</p>'
        '<p class="w-note"><strong>No blanket exemption.</strong> Replaces the €0.45 M line — not added to it.</p>'
        '<div class="w-actions"><span class="w-btn">Open Poland submission</span>'
        '<span class="w-btn ghost">Ask Otto</span></div></div></article>'
    )


def widget_square(image):
    countries = (("CZ", "Czechia", "Submitted", ""), ("PL", "Poland", "Submitted", ""),
                 ("HU", "Hungary", "Submitted", ""), ("SK", "Slovakia", "Overdue", " open"))
    return (
        '<article class="widget widget-square"><div class="sq-top">' + mark(image)
        + '<span class="w-word">JAVORNA</span><span class="w-kicker">Week 3</span></div>'
        '<p class="sq-big">3<small>/4</small></p><p class="sq-label">countries submitted their FY2027 budget</p>'
        '<div class="sq-countries">'
        + ''.join(f'<div class="sq-country{cls}"><b>{code}</b><span>{esc(name)}</span><em>{state}</em></div>'
                  for code, name, state, cls in countries)
        + '</div><p class="sq-next"><b>Next</b> Validation · Challenge sessions 30 Nov – 3 Dec</p></article>'
    )


def widget_announcement(image, hero):
    owners = (("Adam Vávra", "Decides exceptions"), ("Emma Hartmann", "Runs the process"),
              ("Otto", "Coordinates and follows up"))
    visual = f'<img class="photo" src="{esc(hero)}" width="2048" height="2048" alt="">' if hero else ""
    return (
        '<article class="widget widget-announce"><div class="w-photo">'
        + (visual if hero else "")
        + '<div class="w-photo-copy"><div class="sq-top">' + mark(image)
        + '<span class="w-word">JAVORNA</span></div>'
        + '<h3 class="w-title">FY2027 budget season starts today.</h3></div></div>'
        '<div class="w-body"><ul class="w-owners">'
        + ''.join(f'<li><b>{esc(n)}</b><span>{esc(r)}</span></li>' for n, r in owners)
        + '</ul><div class="w-timeline"><span><b>20 Nov</b>Submissions</span><span><b>Week 3</b>Validation</span>'
        '<span><b>30 Nov</b>Challenge sessions</span></div>'
        '<div class="w-actions"><span class="w-btn">Open calendar</span><span class="w-btn ghost">Ask Otto</span>'
        '</div></div></article>'
    )


def build(palette, images, hero):
    stylesheets = ('<link rel="stylesheet" href="assets/furniture-review.css">'
                   '<link rel="stylesheet" href="assets/logo-review.css">')
    gallery = head("article.template.html", "Javorna — tři finální loga", "ft2026-logo-review")
    gallery = gallery.replace('<html lang="en"', '<html lang="cs"').replace("</head>", stylesheets + "</head>")
    gallery += (
        '<div class="controls" role="group" aria-label="Vzhled stránky">'
        '<button type="button" class="ctrl" data-action="print">PDF</button>'
        '<button type="button" class="ctrl" data-action="toggle-theme">Dark</button></div>'
        '<div class="doc"><header class="doc-header">'
        '<p class="eyebrow">JAVORNA · LOGO · FINÁLNÍ VÝBĚR</p>'
        '<h1>Tři židle, které stojí.</h1>'
        '<p class="subtitle">16 návrhů ve 3 kolech. Konstrukce bez visících dílů. Paleta C.</p></header>'
        '<main><div class="logo-finalists">'
    )
    for name, number, title, path, body, plus, minus in FINALISTS:
        image = images[name]
        sizes = ''.join(f'<span class="size"><img src="{esc(image)}" width="2048" height="2048" '
                        f'style="width:{px}px;height:{px}px" alt="">{px}</span>' for px in (128, 64, 32))
        gallery += (
            f'<section class="logo-card" id="logo-{number}">'
            f'<img class="logo-hero" src="{esc(image)}" width="2048" height="2048" alt="Logo {number} — {esc(title)}">'
            f'<div class="logo-copy"><p class="logo-path">{esc(path)}</p><h2>{number} · {esc(title)}</h2>'
            f'<p>{esc(body)}</p><p class="logo-pm"><span>+ {esc(plus)}</span><span>− {esc(minus)}</span></p>'
            f'<div class="size-row">{sizes}</div>'
            f'<div class="size-row dark">{sizes}</div>'
            f'<a href="#study-{number}">Na slidech</a></div></section>'
        )
    gallery += '</div>'
    deck = head("deck.template.html", "Javorna — logo finalists on palette C", "ft2026-logo-slides")
    deck = deck.replace("</head>", stylesheets + "</head>") + '<main class="deck-stage">'
    for name, number, title, *_ in FINALISTS:
        variant = {**palette, "id": number, "name": title, "image": name}
        gallery += f'<section class="review-direction" id="study-{number}"><h2>{number} · {esc(title)}</h2>'
        for content, label, suffix in ((False, "Úvodní slide", "title"), (True, "Obsahový slide", "content")):
            markup = study(variant, images, hero, content)
            gallery += (f'<div class="study-caption"><h3>{label}</h3>'
                        f'<a href="logo-slides.html#s-{number}-{suffix}">Otevřít jako prezentaci</a></div>' + markup)
            if number == "1" and not content:
                markup = markup.replace('<h2 class="title-heading">', '<h1 class="title-heading">').replace('</h2>', '</h1>')
            deck += f'<section class="slide furniture-slide" id="s-{number}-{suffix}">{markup}</section>'
        gallery += '</section>'
    widget_logo = images[FINALISTS[0][0]]
    gallery += (
        '<section class="review-direction widget-review" id="widgets"><h2>Widgety v Teams</h2>'
        '<ul class="widget-rules">'
        '<li><strong>Dokument</strong> — z obsahového slidu. Brief, rozhodnutí, podklady.</li>'
        '<li><strong>Čtverec</strong> — z úvodního slidu. Stav, souhrn, jedno velké číslo.</li>'
        '<li><strong>Vizuály</strong> — malá schválená knihovna. Vkládá je brána podle ID, '
        'jen jako dekoraci záhlaví. Nikdy místo dat.</li></ul>'
        '<div class="widget-gallery">'
        f'<figure class="widget-shot">{widget_document(widget_logo)}<figcaption>Dokument · A4</figcaption></figure>'
        f'<figure class="widget-shot">{widget_square(widget_logo)}<figcaption>Infografika · čtverec</figcaption></figure>'
        f'<figure class="widget-shot">{widget_announcement(widget_logo, hero)}'
        '<figcaption>Oznámení · vizuál z knihovny</figcaption></figure></div>'
        '<p class="widget-limits">Jen osobní chat s Otto · odpověď modelu max. 48 KB · obrázky jen <code>data:</code> '
        '· vizuál = malá zmenšená kopie, výslednou velikost zprávy ověříme v Teams.</p></section>'
        '<section class="review-direction" id="iterations"><h2>Všech 16 iterací</h2>'
        '<details class="iteration-log"><summary>Zobrazit vyřazené varianty</summary>'
    )
    for round_name, entries in ROUNDS:
        gallery += f'<h3>{esc(round_name)}</h3><div class="iteration-grid">'
        for name, verdict in entries:
            chosen = " chosen" if verdict.startswith("Finalista") else ""
            gallery += (f'<figure class="iteration{chosen}"><img src="{esc(images[name])}" width="2048" height="2048" '
                        f'loading="lazy" alt="{esc(name)}"><figcaption><code>{esc(name)}</code>{esc(verdict)}'
                        '</figcaption></figure>')
        gallery += '</div>'
    gallery += (
        '</details></section></main><footer class="doc-footer"><span>Koncepty ke schválení · MAI 2.7 · '
        'původní PNG 2048 × 2048 · finální logo bude vektorové a průhledné</span></footer></div></body></html>'
    )
    return gallery, deck + '</main></body></html>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", type=Path, nargs=3, help="logo round 1, 2 and 3 run manifests")
    parser.add_argument("--atelier-manifest", type=Path, required=True)
    args = parser.parse_args()
    palette = next(p for p in json.loads((ROOT / "brand/furniture-palettes.json").read_text(encoding="utf-8"))
                   if p["id"] == "C")
    images, runs = {}, []
    for manifest_path, (_, entries) in zip(args.manifests, ROUNDS):
        manifest, loaded = load_images(manifest_path, {name for name, _ in entries})
        images.update(loaded)
        runs.append(manifest["run_id"])
    atelier, old = load_images(args.atelier_manifest, {*LABELS, "workshop-hero"})
    gallery, deck = build(palette, images, old["workshop-hero"])
    for name, text in (("logo-review.html", gallery), ("logo-slides.html", deck)):
        (ROOT / "docs" / name).write_text(text, encoding="utf-8")
        print(ROOT / "docs" / name)
    (ROOT / ".local/logo-review-lineage.json").write_text(json.dumps({
        "runs": runs, "atelier_run_id": atelier["run_id"], "palette": "C",
        "finalists": {name: images[name] for name, *_ in FINALISTS},
        "status": "awaiting-user-selection", "presentation_modified": False,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
