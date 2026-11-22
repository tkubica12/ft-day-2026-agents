"""Build static English demo surfaces from the authored scenario and vendored templates."""
import html
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".github" / "skills" / "html-docs"
DOCS = ROOT / "docs"


def esc(value):
    return html.escape(str(value), quote=True)


def head(template, title, doc_id):
    text = (SKILL / template).read_text(encoding="utf-8").split("<body>", 1)[0]
    text = re.sub(r"<title>.*?</title>", f"<title>{esc(title)}</title>", text)
    text = re.sub(r'(<meta name="doc-id" content=")[^"]*', rf"\g<1>{doc_id}", text)
    text = text.replace('data-default-accent="blue"', 'data-default-accent="green"')
    return text + "<body>\n"


def slide(identity, title, body, note=""):
    return (
        f'<section class="slide" id="s-{esc(identity)}">'
        f'<div class="slide-head"><h2 class="slide-title">{esc(title)}</h2></div>'
        f'<div class="slide-body">{body}</div>'
        f'<p class="slide-note">{esc(note)}</p></section>\n'
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
    deck = head("deck.template.html", "Otto at Javorna — Frontier Day 2026", "ft2026-deck")
    deck += '<main class="deck-stage">\n'
    deck += (
        '<section class="slide slide--title" id="s-title">'
        '<p class="slide-meta">FRONTIER DAY 2026 · JAVORNA GROUP</p>'
        '<h1>Craft takes people.<br>Coordination takes Otto.</h1>'
        '<p class="slide-sub">One budget season. Four countries. A digital colleague with clear boundaries.</p>'
        f'<p class="slide-meta">{esc(data["presenter"])}</p>'
        '<p class="slide-note">Fictional company and data · recording skeleton</p></section>\n'
    )
    deck += slide("company", "Bespoke work needs shared planning",
                  '<p>Luxury furniture. Architectural joinery. Artistic woodwork.</p>'
                  '<p>Homes, offices and representative spaces; church, restoration and film commissions.</p>'
                  '<p>Czechia · Slovakia · Poland · Hungary</p>', "Javorna Group · fictional scenario")
    cast_rows = "".join(
        f'<tr><td>{esc(p["name"])}</td><td>{esc(p["role"])}</td></tr>' for p in data["cast"]
    )
    deck += slide("cast", "Four roles, different decision rights",
                  '<table><thead><tr><th>Person</th><th>Responsibility</th></tr></thead>'
                  f'<tbody>{cast_rows}</tbody></table>')
    chapters = list(dict.fromkeys(s["chapter"] for s in data["scenes"]))
    for scene in data["scenes"]:
        sid = scene["id"]
        if sid == "09":
            deck += (
                '<section class="slide slide--section" id="s-week-three">'
                '<h2>Two weeks later</h2><p class="slide-lead">The same goal. New work.</p>'
                '<p class="slide-note">Explicit simulated time jump to week 3</p></section>\n'
            )
        deck += slide(f"scene-{sid}", scene["title"],
                      f'<p class="slide-lead">{esc(scene["cue"])}</p>', scene["actor"])
        deck += slide(f"recording-{sid}", f'Recording {sid}',
                      '<div class="callout note"><span class="callout-label">Not recorded yet</span>'
                      '<p>New Javorna footage will go here.</p></div>',
                      scene["record"])
    deck += slide("recap", "A digital colleague works within authority",
                  '<ul class="arrow-list"><li>A goal outlives a conversation.</li>'
                  '<li>Coordination crosses people, channels and weeks.</li>'
                  '<li>Decisions and learning retain human ownership.</li></ul>')
    deck += (
        '<section class="slide slide--end" id="s-end">'
        '<p>Delegate the work.<br>Keep the authority.</p></section>\n</main></body></html>\n'
    )

    guide = head("article.template.html", "Javorna — demo recording guide", "ft2026-guide")
    guide += (
        '<div class="controls" role="group" aria-label="Guide controls">'
        '<button type="button" class="ctrl" data-action="expand-all">Expand all</button>'
        '<button type="button" class="ctrl" data-action="collapse-all">Collapse all</button>'
        '<button type="button" class="ctrl" data-action="print">PDF</button>'
        '<button type="button" class="ctrl" data-action="toggle-print-theme">PDF theme: Light</button>'
        '<button type="button" class="ctrl" data-action="toggle-theme">Dark</button>'
        '<button type="button" class="ctrl" data-action="toggle-accent">Accent: Green</button></div>'
        '<div class="doc"><header class="doc-header" id="opening">'
        '<p class="eyebrow">FRONTIER DAY 2026 · RECORDING GUIDE</p>'
        '<h1>Otto runs Javorna’s budget season</h1>'
        '<p class="subtitle">Copy the messages. Record the real outcomes. Keep country figures private.</p>'
        '<p>Fictional company, people and financial data. New recordings and screenshots are pending.</p>'
        '</header><nav class="toc" aria-label="Contents"><ol>'
    )
    guide += '<li><a href="#ch-setup">Before recording</a></li>'
    guide += "".join(f'<li><a href="#ch-{i}">{esc(c)}</a></li>' for i, c in enumerate(chapters))
    guide += '</ol></nav><main><section class="chapter" id="ch-setup"><h2 class="chapter-label">Before recording</h2>'
    identities = "".join(
        f'<li>{esc(p["name"])} · {esc(p["role"])} · '
        f'{esc(p["upn"] or "verify the existing bot identity")}</li>' for p in data["cast"]
    )
    guide += card("cast", "Use the right identity for each scene", f'<ul>{identities}</ul>'
                  '<p>Verify these proposed UPNs against the live directory. Do not create or rename users implicitly.</p>')
    guide += card("setup", "Start from an isolated week-1 state",
                  '<p>Fresh Teams space, role bindings, agent memory and planning data. No earlier demo history.</p>'
                  '<p>The first scene uses the simulated 9 November kickoff. The submission deadline is '
                  '20 November; the week-3 snapshot is 23 November. Challenge sessions are 30 November–3 December, '
                  '09:00–12:00 Europe/Prague.</p>'
                  '<p>Verify both human logins, routing, learning approval and app access before recording.</p>')
    guide += '</section>'
    for i, chapter in enumerate(chapters):
        guide += f'<section class="chapter" id="ch-{i}"><h2 class="chapter-label">{esc(chapter)}</h2>'
        if any(s["id"] == "09" and s["chapter"] == chapter for s in data["scenes"]):
            guide += '<p>Show an explicit two-week time jump. Load the prepared week-3 snapshot without losing the decision or approved learning.</p>'
        for scene in (s for s in data["scenes"] if s["chapter"] == chapter):
            body = f'<p>{esc(scene["actor"])}</p>'
            body += f'<blockquote>{esc(scene["prompt"])}</blockquote>' if scene["prompt"] else '<p>No message to send.</p>'
            body += f'<p><strong>Look for:</strong> {esc(scene["expected"])}</p>'
            body += f'<p><strong>Capture:</strong> {esc(scene["record"])}</p>'
            body += '<p class="media-status">Recording and screenshot pending.</p>'
            guide += card(f'scene-{scene["id"]}', scene["title"], body)
        guide += '</section>'
    guide += (
        '</main><section class="takeaway" id="closing"><p>Delegate the work. Keep the authority.</p></section>'
        '<footer class="doc-footer"><span>Javorna Group · fictional demo · no recordings included</span></footer>'
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
