"""Build static Czech demo surfaces from the authored scenario and vendored templates."""
import html
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "html-docs"
DOCS = ROOT / "docs"


def esc(value):
    return html.escape(str(value), quote=True)


def presenter_line(presenters):
    organisations = list(dict.fromkeys(p["organisation"] for p in presenters))
    if len(organisations) == 1:
        return " · ".join([p["name"] for p in presenters] + organisations)
    return " · ".join(f'{p["name"]}, {p["organisation"]}' for p in presenters)


def head(template, title, doc_id, lang="en"):
    text = (SKILL / template).read_text(encoding="utf-8").split("<body>", 1)[0]
    if lang != "en":
        text = text.replace('<html lang="en"', f'<html lang="{esc(lang)}"', 1)
    text = re.sub(r"<title>.*?</title>", f"<title>{esc(title)}</title>", text)
    text = re.sub(r'(<meta name="doc-id" content=")[^"]*', rf"\g<1>{doc_id}", text)
    text = text.replace('data-default-accent="blue"', 'data-default-accent="green"')
    return text + "<body>\n"


def slide(identity, title, body, note=""):
    return (
        f'<section class="slide" id="s-{esc(identity)}">{brand_lockup()}'
        f'<div class="slide-head"><h2 class="slide-title">{esc(title)}</h2></div>'
        f'<div class="slide-body">{body}</div>'
        f'<p class="slide-note">{esc(note)}</p></section>\n'
    )


def brand_lockup(dark=False):
    image = "chair-on-dark.svg" if dark else "chair.svg"
    return (
        '<div class="j-lockup">'
        f'<img class="j-mark" src="assets/javorna/{image}" width="357" height="367.25" alt="Křeslo Javorna">'
        '<span>JAVORNA</span></div>'
    )


def card(identity, title, body):
    return (
        f'<article class="card" id="card-{esc(identity)}" data-open>'
        '<h3 class="card-head"><button type="button" class="card-toggle" aria-expanded="true">'
        '<span class="card-num" aria-hidden="true"></span><span class="card-titles">'
        f'<span class="card-title">{esc(title)}</span></span>'
        '<span class="card-mark" aria-hidden="true"></span></button></h3>'
        f'<div class="card-body">{body}</div></article>\n'
    )


def build(data):
    from build_furniture_review import study

    palette = next(p for p in json.loads((ROOT / "brand/furniture-palettes.json").read_text(encoding="utf-8"))
                   if p["id"] == "C")
    palette = {**palette, "image": "chair"}
    styles = ('<link rel="stylesheet" href="assets/furniture-review.css">'
              '<link rel="stylesheet" href="assets/javorna-brand.css">')
    deck = head("deck.template.html", "Otto ve skupině Javorna — Frontier Day 2026", "ft2026-deck", lang="cs")
    deck = deck.replace("</head>", styles + "</head>")
    deck += '<main class="deck-stage javorna">\n'
    title = study(palette, {"chair": "assets/javorna/chair-on-dark.svg"},
                  "assets/javorna/atelier.png", approved=True)
    title = title.replace("AI-generated fictional woodworking atelier", "AI vygenerovaný fiktivní truhlářský ateliér")
    title = title.replace("Javorna furniture logo", "Logo nábytku Javorna")
    title = title.replace("BESPOKE WOODWORK", "ZAKÁZKOVÉ TRUHLÁŘSTVÍ")
    title = title.replace(
        "Craft takes people.<br>Coordination<br>takes <em>Otto.</em>",
        "Řemeslo patří lidem.<br>Koordinaci<br>řídí <em>Otto.</em>",
    )
    title = title.replace(
        "One budget season.<br>Four countries. Clear boundaries.",
        "Jedna rozpočtová sezóna.<br>Čtyři země. Jasná pravidla.",
    )
    title = title.replace("Tomáš Kubica · Microsoft", esc(presenter_line(data["presenters"])))
    title = title.replace('<h2 class="title-heading">', '<h1 class="title-heading">').replace('</h2>', '</h1>')
    deck += f'<section class="slide furniture-slide" id="s-title">{title}</section>\n'
    deck += slide("company", "Zakázková výroba potřebuje sdílené plánování",
                  '<p>Luxusní nábytek. Architektonické truhlářství. Umělecká práce se dřevem.</p>'
                  '<p>Bydlení, kanceláře a reprezentativní prostory; zakázky pro kostely, památky a film.</p>'
                  '<p>Česko · Slovensko · Polsko · Maďarsko</p>', "Javorna Group · fiktivní scénář")
    cast_rows = "".join(
        f'<tr><td>{esc(p["name"])}</td><td>{esc(p["role"])}</td></tr>' for p in data["cast"]
    )
    deck += slide("cast", "Čtyři role, různá rozhodovací práva",
                  '<table><thead><tr><th>Osoba</th><th>Odpovědnost</th></tr></thead>'
                  f'<tbody>{cast_rows}</tbody></table>')
    chapters = list(dict.fromkeys(s["chapter"] for s in data["scenes"]))
    for scene in data["scenes"]:
        sid = scene["id"]
        if sid == "10":
            deck += (
                f'<section class="slide slide--section" id="s-week-three">{brand_lockup(dark=True)}'
                '<h2>O dva týdny později</h2><p class="slide-lead">Stejný cíl. Nová práce.</p>'
                '<p class="slide-note">Explicitní simulovaný časový skok do 3. týdne</p></section>\n'
            )
        deck += slide(f"scene-{sid}", scene["title"],
                      f'<p class="slide-lead">{esc(scene["cue"])}</p>', scene["actor"])
        deck += slide(f"recording-{sid}", f'Záznam {sid}',
                      '<div class="callout note"><span class="callout-label">Zatím nenahráno</span>'
                      '<p>Sem přijde nový záznam z dema Javorna.</p></div>',
                      scene["record"])
    deck += slide("recap", "Digitální kolega pracuje v rámci pravomocí",
                  '<ul class="arrow-list"><li>Cíl přežije jednu konverzaci.</li>'
                  '<li>Koordinace propojuje lidi, kanály i týdny.</li>'
                  '<li>Rozhodnutí i učení zůstávají pod kontrolou lidí.</li></ul>')
    deck += (
        f'<section class="slide slide--end" id="s-end">{brand_lockup(dark=True)}'
        '<p>Delegujte práci.<br>Pravomoc si ponechte.</p></section>\n</main></body></html>\n'
    )

    guide = head("article.template.html", "Javorna — návod k nahrávání dema", "ft2026-guide", lang="cs")
    guide = guide.replace("</head>", '<link rel="stylesheet" href="assets/javorna-brand.css"></head>')
    guide += (
        '<div class="controls" role="group" aria-label="Ovládání návodu">'
        '<button type="button" class="ctrl" data-action="expand-all">Rozbalit vše</button>'
        '<button type="button" class="ctrl" data-action="collapse-all">Sbalit vše</button>'
        '<button type="button" class="ctrl" data-action="print">PDF</button>'
        '<button type="button" class="ctrl" data-action="toggle-print-theme">PDF theme: Light</button>'
        '<button type="button" class="ctrl" data-action="toggle-theme">Dark</button>'
        '<button type="button" class="ctrl" data-action="toggle-accent">Accent: Green</button></div>'
        f'<div class="doc javorna"><header class="doc-header" id="opening">{brand_lockup()}'
        '<p class="eyebrow">FRONTIER DAY 2026 · NÁVOD K NAHRÁVÁNÍ</p>'
        '<h1>Otto řídí rozpočtovou sezónu Javorny</h1>'
        '<p class="subtitle">Zkopírujte zprávy v češtině. Nahrajte skutečné výstupy. Čísla zemí držte v soukromí.</p>'
        '<p>Fiktivní společnost, osoby i finanční údaje. Nové nahrávky a snímky obrazovky čekají na pořízení.</p>'
        '</header><nav class="toc" aria-label="Obsah"><ol>'
    )
    guide += '<li><a href="#ch-setup">Před nahráváním</a></li>'
    guide += "".join(f'<li><a href="#ch-{i}">{esc(c)}</a></li>' for i, c in enumerate(chapters))
    guide += '</ol></nav><main><section class="chapter" id="ch-setup"><h2 class="chapter-label">Před nahráváním</h2>'
    identities = "".join(
        f'<li>{esc(p["name"])} · {esc(p["role"])} · '
        f'{esc(p["upn"] or "stávající identita bota (otto@tomasonline.net)")}</li>' for p in data["cast"]
    )
    guide += card("cast", "Pro každou scénu použijte správnou identitu", f'<ul>{identities}</ul>'
                  '<p>Pro každou lidskou roli mějte otevřený vlastní profil prohlížeče (Adam, Emma, Piotr). '
                  'Přihlašování a hesla nepatří do záznamu.</p>')
    guide += card("setup", "Začněte z čistého stavu 1. týdne",
                  '<p>Čisté prostředí Teams, resetovaná paměť agenta, nulová předchozí historie a rozpočtová sezóna v týdnu 0 '
                  '(připraveno k zahájení v 1. týdnu).</p>'
                  '<p>První scéna zahajuje sezónu (simulovaný start 9. listopadu, termín odevzdání 20. listopadu). '
                  'Před scénou 10 proveďte explicitní časový skok do 3. týdne (<code>season set-week 3</code>, snímek k 23. listopadu). '
                  'Challenge schůzky probíhají 30. listopadu – 3. prosince, 09:00–12:00 Europe/Prague.</p>'
                  '<p>Surové MP4 nahrávky ukládejte do <code>video-raw/</code> (případně <code>.local/raw-videos/</code>) '
                  'a mapujte je přes <code>video-production/edl.json</code> na výsledné klipy <code>docs/video/01.mp4</code> až <code>14.mp4</code>.</p>')
    guide += '</section>'
    for i, chapter in enumerate(chapters):
        guide += f'<section class="chapter" id="ch-{i}"><h2 class="chapter-label">{esc(chapter)}</h2>'
        if any(s["id"] == "10" and s["chapter"] == chapter for s in data["scenes"]):
            guide += ('<p><strong>Časový skok před scénou 10:</strong> Posuňte stav sezóny do 3. týdne '
                      '(<code>uv run --frozen python -m scripts.budget_season_ops season set-week 3</code>) — '
                      'rozhodnutí i schválené poučení zůstanou zachované.</p>')
        for scene in (s for s in data["scenes"] if s["chapter"] == chapter):
            body = f'<p><strong>Scéna {esc(scene["id"])} · {esc(scene["actor"])}</strong></p>'
            body += f'<blockquote>{esc(scene["prompt"])}</blockquote>' if scene["prompt"] else '<p>Žádná zpráva k odeslání — přepněte profil a nahrávejte doručený výsledek.</p>'
            body += f'<p><strong>Co ověřit:</strong> {esc(scene["expected"])}</p>'
            body += f'<p><strong>Co nahrát:</strong> {esc(scene["record"])}</p>'
            body += '<p class="media-status">Záznam a snímek obrazovky čeká na nahrání.</p>'
            guide += card(f'scene-{scene["id"]}', f'{scene["id"]} · {scene["title"]}', body)
        guide += '</section>'
    guide += (
        '</main><section class="takeaway" id="closing"><p>Delegujte práci. Pravomoc si ponechte.</p></section>'
        '<footer class="doc-footer"><span>Javorna Group · fiktivní demo · zatím bez vložených nahrávek</span></footer>'
        '</div></body></html>\n'
    )
    return deck, guide


def main():
    data = json.loads((ROOT / "scenario" / "demo.json").read_text(encoding="utf-8"))
    deck, guide = build(data)
    for name, text in (("presentation.html", deck), ("demo-guide.html", guide)):
        (DOCS / name).write_text(text, encoding="utf-8")
    subprocess.run(["node", str(DOCS / "assets" / "sync-head.js"),
                    str(DOCS / "presentation.html"), str(DOCS / "demo-guide.html")], check=True)
    print(f"Built {len(data['scenes'])} recording slots, {deck.count('<section class=')} slides, and a guide.")


if __name__ == "__main__":
    main()
