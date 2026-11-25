"""Build the recorded-demo player from the authored scenario.

  python scripts\\build_player.py

Writes docs/player.html: one text slide (who sends what) and one video slide per scene,
plus title, cast, season, time-jump, recap and closing slides. Clips load from
docs/video/NN.mp4 (scene IDs); a slide whose clip does not exist shows "Recording pending".
Package single-file copies with video-production/build_single_player.py.
"""
import json
from pathlib import Path

from build_docs import esc

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "player.html"

PEOPLE = {
    "Adam": ("av", "Skupinový CFO. Vlastní cíle, výjimky i to, co se Otto smí naučit."),
    "Emma": ("eh", "Vedoucí skupinového FP&A. Vlastní proces a harmonogram."),
    "Piotr": ("pn", "Výkonný ředitel, Polsko. Vlastní rozpočet Polska."),
    "Otto": ("oa", "Koordinátor rozpočtové sezóny. Digitální junior v Teams."),
}
CAST_ORDER = ("Adam", "Emma", "Piotr", "Otto")

# Player copy per scene: kicker, who (person, arrow?, text), video title. The message itself comes
# from the scenario; scenes without a message use "watch" or "heading"/"lead".
STEPS = {
    "01": {"kicker": "Zahájení", "who": ("Adam", True, "Otto, soukromý chat"),
           "video": "Adam zadá Ottovi cíl",
           "sub": "Otto zkontroluje plán, kontaktuje Emmu a pošle shrnutí"},
    "02": {"kicker": "Předání vlastníkovi procesu", "who": ("Emma", False, "otevře chat s Ottem"),
           "watch": "Emmě ještě nikdo nepsal. Otto jí už sám předal úkoly a harmonogram.",
           "video": "Emma dostává předání od Otta"},
    "03": {"kicker": "Informovat země", "who": ("Adam", True, "Otto"),
           "video": "Otto zveřejní uvítání v kanálu a potvrdí ho"},
    "04": {"kicker": "Týmový kanál", "who": ("Piotr", False, "vidí novou zmínku"),
           "watch": "Jedno uvítání pro celý tým v kanálu, bez soukromých čísel zemí.",
           "video": "Piotr otevírá uvítání v Javorna Budget Season"},
    "05": {"kicker": "Dotaz ve vlákně", "who": ("Piotr", True, "odpověď v uvítacím vlákně"),
           "video": "Odpověď ve vlákně, polský brief v soukromém chatu"},
    "06": {"kicker": "Tlak na výjimku", "who": ("Piotr", True, "Otto, soukromý chat"),
           "video": "Tvrzení není rozhodnutí"},
    "07": {"kicker": "CFO rozhoduje", "who": ("Adam", True, "Otto"),
           "video": "Ohraničené rozhodnutí DEC-001 zapsáno a předáno Piotrovi"},
    "08": {"kicker": "Řízené učení", "who": ("Adam", True, "Otto, soukromý chat"),
           "then_label": "Poté v kartě \u201eCo jsem se naučil\u201c", "then": "Schválit",
           "video": "Otto navrhne změnu playbooku; CFO schválí diff"},
    "09": {"kicker": "Zpět u Piotra", "who": ("Piotr", False, "čte rozhodnutí a odpovídá"),
           "video": "Piotr obdrží Adamovo rozhodnutí a potvrdí další krok"},
    "10": {"kicker": "Přehled a schválení pro kanál", "who": ("Emma", True, "Otto, soukromý chat"),
           "then_label": "Poté ve schvalovací kartě", "then": "Confirm",
           "video": "Tři země odevzdaly; Emma schválí zveřejnění do kanálu"},
    "11": {"kicker": "Zveřejněno v kanálu", "who": ("Otto", False, "zveřejnil schválený přehled v General"),
           "watch": "V kanálu General je sdílený přehled 3. týdne — bez soukromých čísel zemí.",
           "video": "Schválený týdenní přehled v kanálu Javorna Budget Season"},
    "12": {"kicker": "Z jedné zprávy vznikne aplikace", "who": ("Emma", True, "Otto, soukromý chat"),
           "video": "Otto vytvoří, otestuje a nasadí rezervační aplikaci"},
    "13": {"kicker": "Otto rozesílá odkaz", "who": ("Emma", True, "Otto, soukromý chat"),
           "video": "Otto potvrdí dvě soukromé zprávy: Piotrovi k rezervaci, Adamovi pro čtení"},
    "14": {"kicker": "Pozvánka dorazí k Piotrovi", "who": ("Piotr", True, "soukromá zpráva od Otta"),
           "heading": "Z pozvánky v Teams ke 45minutovému slotu",
           "lead": "Piotr otevře aplikaci, vybere Polsko a volný termín a potvrdí rezervaci.",
           "video": "Piotr rezervuje challenge schůzku za Polsko"},
}

WEEKS = [
    ("1. týden", "Zahájení, soukromé briefy"), ("2. týden", "Termín odevzdání"),
    ("3. týden", "Validace a dotazy"), ("4. týden", "Challenge schůzky"),
    ("5. týden", "Úpravy CFO, capex review"), ("6. týden", "Upravené návrhy"),
    ("7. týden", "Konsolidace, podklady"), ("8. týden", "Schválení představenstvem"),
]

RECAP = [
    ("Kdo spouští práci", "uživatel se zeptá", "pevný trigger", "jeho uživatel", "cíl zadaný jednou; Otto ho táhne"),
    ("Horizont", "jedna konverzace", "jedna pevná trasa", "úkoly jedné osoby", "osm týdnů, více lidí"),
    ("Výjimky", "odpovídá na dotazy", "spadne nebo eskaluje", "ptá se uživatele",
     "přeplánuje, ptá se toho, kdo rozhoduje"),
    ("V čím zájmu", "tazatele", "autora procesu", "jedné osoby",
     "organizace v rámci rozhodovacích práv"),
    ("Učení", "žádné", "předělání přes IT", "osobní preference", "pracovní postupy schválené šéfem"),
]

CSS = """
  :root {
    --wood: #332E27; --deep: #211F1A; --paper: #EEEBDD; --muted: #CFCCB6; --leaf: #BCCB7A;
    --pine: #4C6138; --oak: #6B5A45; --cherry: #8C4A2F; --card: #F8F6EE; --line: #D9D5C1;
    --serif: Georgia, "Times New Roman", serif;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--deep); color: var(--wood);
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif; overflow: hidden; }
  .slide { position: fixed; inset: 0; display: none; }
  .slide.active { display: flex; }

  /* Javorna mark: transparent vector, never framed */
  .mark { display: block; flex: none; width: clamp(44px, 3.4vw, 80px); aspect-ratio: 357 / 367.25;
    background: url("assets/javorna/chair.svg") center / contain no-repeat; }
  .dark .mark { background-image: url("assets/javorna/chair-on-dark.svg"); }
  .brand { position: absolute; top: 4.5vh; left: 4vw; display: flex; align-items: center; gap: .8em;
    font: 400 clamp(14px, 1.2vw, 24px) var(--serif); letter-spacing: .18em; color: var(--wood); }
  .dark .brand { color: var(--paper); }
  .brand small { display: block; margin-top: .55em; font: 500 .42em "Segoe UI", sans-serif;
    letter-spacing: .22em; color: var(--muted); }

  /* text slides */
  .text { background: var(--paper); align-items: center; justify-content: center; padding: 15vh 8vw 9vh; }
  .card { width: min(1200px, 100%); }
  .kicker { font-size: clamp(14px, 1.5vw, 20px); font-weight: 600; letter-spacing: .1em;
    text-transform: uppercase; color: var(--pine); margin-bottom: 1.4vh; }
  h1, h2 { font-family: var(--serif); font-weight: 400; color: var(--wood); }
  h1 { font-size: clamp(34px, 5vw, 72px); line-height: 1.05; letter-spacing: -.025em; margin: 0 0 2.5vh; }
  h2 { font-size: clamp(28px, 3.6vw, 52px); line-height: 1.12; letter-spacing: -.02em; margin: 0 0 3vh; }
  .lead { font-size: clamp(18px, 2vw, 28px); color: var(--oak); line-height: 1.4; margin: 0; }

  /* title slide */
  .title { background: var(--wood); color: var(--paper); padding: 0; justify-content: flex-start; overflow: hidden; }
  .title-photo { position: absolute; top: 0; right: 0; width: 41vw; height: 100%; object-fit: cover; }
  .title-copy { position: relative; z-index: 1; width: 59vw; height: 100%; padding: 7vh 5vw 8vh 6vw;
    display: flex; flex-direction: column; }
  .title .brand { position: static; font-size: clamp(18px, 1.9vw, 36px); }
  .title .brand .mark { width: clamp(64px, 5vw, 120px); }
  .title-main { margin: auto 0; }
  .title .kicker { color: var(--leaf); }
  .title h1 { font-size: clamp(40px, 5.2vw, 104px); line-height: 1.04; letter-spacing: -.03em;
    color: var(--paper); margin-bottom: 3.2vh; }
  .title h1 em { font-style: normal; color: var(--leaf); }
  .title .lead { color: var(--muted); font-size: clamp(18px, 1.7vw, 32px); max-width: 23em; text-wrap: balance; }
  .presenter b { display: block; font-size: clamp(18px, 1.55vw, 30px); font-weight: 600; color: var(--paper);
    line-height: 1.3; }
  .presenter b:last-of-type { margin-bottom: 1.5vh; }
  .logo-box { display: flex; align-items: center; height: clamp(22px, 2.1vw, 40px); }
  .logo { display: block; height: 100%; width: auto; }

  .who { display: inline-flex; align-items: center; gap: .6em; font-size: clamp(16px, 1.7vw, 24px);
    padding: .4em .95em .4em .45em; border-radius: 999px; background: var(--card); border: 1px solid var(--line);
    margin-bottom: 3vh; }
  .who b { color: var(--wood); }
  .who .arrow { color: var(--pine); font-weight: 700; }
  .avatar { width: 1.8em; height: 1.8em; border-radius: 50%; display: inline-grid; place-items: center; flex: none;
    font-size: .8em; font-weight: 700; color: #fff; background: var(--pine); }
  .avatar.av { background: var(--wood); } .avatar.eh { background: var(--oak); } .avatar.pn { background: var(--cherry); }
  blockquote { margin: 0 0 3vh; padding: 2.2vh 2vw; background: var(--card); border-left: 6px solid var(--pine);
    border-radius: 3px; font-size: clamp(18px, 2vw, 30px); line-height: 1.45; color: var(--wood);
    box-shadow: 0 2px 14px rgba(51,46,39,.07); }
  blockquote.long { font-size: clamp(16px, 1.7vw, 26px); }
  blockquote.xlong { font-size: clamp(13px, 1.4vw, 21px); line-height: 1.36; padding: 1.6vh 1.8vw; }
  .then { font-size: clamp(16px, 1.6vw, 22px); color: var(--oak); margin: -1.5vh 0 3vh; }
  .then b { color: var(--pine); }
  ul.watch { margin: 0; padding: 0; list-style: none; font-size: clamp(17px, 1.8vw, 26px); line-height: 1.5; }
  ul.watch li { padding-left: 1.4em; position: relative; margin: .2em 0; }
  ul.watch li::before { content: "\\25B8"; position: absolute; left: 0; color: var(--pine); }

  .cast { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.5vw; }
  .person { background: var(--card); border: 1px solid var(--line); border-radius: 3px; padding: 2.5vh 1.4vw; }
  .person .avatar { width: 3em; height: 3em; font-size: clamp(14px, 1.3vw, 20px); margin-bottom: 1.5vh; display: grid; }
  .person b { display: block; font: 400 clamp(18px, 1.8vw, 26px) var(--serif); }
  .person span:not(.avatar) { display: block; color: var(--oak); font-size: clamp(14px, 1.3vw, 19px);
    margin-top: .4em; line-height: 1.35; }
  .note { margin: 3vh 0 0; font-size: clamp(13px, 1.9vmin, 30px); color: var(--oak); }

  .weeks { display: grid; grid-template-columns: repeat(8, 1fr); gap: .5vw; margin-top: 2vh; }
  .wk { background: var(--card); border: 1px solid var(--line); border-radius: 3px; padding: 1.6vh .7vw; min-height: 16vh; }
  .wk b { display: block; color: var(--pine); font-size: clamp(13px, 1.2vw, 18px); margin-bottom: .6vh; }
  .wk span { font-size: clamp(12px, 1.05vw, 16px); line-height: 1.3; color: var(--wood); }
  .wk.now { background: var(--pine); border-color: var(--pine); }
  .wk.now b, .wk.now span { color: var(--paper); }

  .jump { background: var(--wood); color: var(--paper); }
  .jump .kicker { color: var(--leaf); } .jump .lead { color: var(--muted); }
  .jump h1 { font-size: clamp(44px, 7vw, 110px); color: var(--paper); }
  .facts { display: flex; flex-wrap: wrap; gap: 1vw; margin-top: 4vh; }
  .fact { background: rgba(238,235,221,.07); border: 1px solid rgba(207,204,182,.22); border-radius: 3px;
    padding: 1.6vh 1.4vw; font-size: clamp(16px, 1.6vw, 24px); color: var(--muted); }
  .fact b { font: 400 1.4em var(--serif); display: block; color: var(--leaf); }

  table.recap { width: 100%; border-collapse: collapse; font-size: clamp(13px, 1.3vw, 20px); background: var(--card); }
  .recap th, .recap td { padding: 1.4vh 1vw; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
  .recap thead th { background: #E4E0CD; font-weight: 600; }
  .recap th.otto, .recap td.otto { background: #E3E8CC; font-weight: 600; color: var(--pine); }
  .recap tbody th { color: var(--oak); font-weight: 600; width: 15%; }

  .text.wide { padding-inline: 5vw; }
  .wide .card { width: 100%; }
  .wide h2 { font-size: clamp(28px, 5.8vmin, 100px); }
  .wide .kicker { font-size: clamp(14px, 2.1vmin, 36px); }
  .wide .person { min-height: 34vh; padding: 3vh 1.8vw; }
  .wide .person .avatar { font-size: clamp(14px, 2.2vmin, 42px); margin-bottom: 2.5vh; }
  .wide .person b { font-size: clamp(16px, 3.2vmin, 60px); line-height: 1.15; }
  .wide .person span:not(.avatar) { font-size: clamp(14px, 2.5vmin, 44px); margin-top: .65em; }
  .wide .weeks { gap: .7vw; }
  .wide .wk { min-height: 30vh; padding: 2.5vh .9vw; }
  .wide .wk b { font-size: clamp(14px, 2.5vmin, 48px); margin-bottom: 1.5vh; }
  .wide .wk span { display: block; font-size: clamp(14px, 2.15vmin, 40px); }
  .wide .recap { font-size: clamp(13px, 2.5vmin, 44px); }
  .wide .recap th, .wide .recap td { padding: 2.3vh 1.15vw; }
  .closing { text-align: center; background: var(--wood); color: var(--paper); }
  .closing .mark { width: clamp(80px, 11vmin, 220px); margin: 0 auto 5vh; }
  .closing .kicker { color: var(--leaf); margin-bottom: 3vh; }
  .closing h1 { font-size: clamp(48px, 17vmin, 330px); margin: 0; color: var(--paper); }

  @media (max-width: 1100px), (max-aspect-ratio: 4/3) {
    .wide .weeks { grid-template-columns: repeat(4, 1fr); gap: 1vh 1vw; }
    .wide .wk { min-height: 0; padding: 1.5vh 1.5vw; }
    .wide .wk b { margin-bottom: .5vh; }
    .wide .recap th, .wide .recap td { padding-block: 1.7vh; }
  }
  @media (max-width: 760px) {
    .wide .cast { grid-template-columns: repeat(2, 1fr); gap: 1.5vh 2vw; }
    .wide .person { min-height: 0; padding: 2vh 3vw; }
    .wide .person .avatar { margin-bottom: 1vh; }
    .wide .recap th, .wide .recap td { padding: 1.3vh .8vw; overflow-wrap: anywhere; }
  }

  /* video slides */
  .video { flex-direction: column; background: var(--deep); }
  .vbar { flex: 0 0 auto; display: flex; align-items: center; gap: 1vw; padding: 1.2vh 1.6vw;
    color: var(--paper); font-size: clamp(14px, 1.4vw, 20px); }
  .vbar .tag { background: var(--pine); color: var(--paper); font-weight: 700; border-radius: 3px; padding: .15em .55em; }
  .vbar .sub { color: #A9A58F; }
  .vbar .mark { margin-left: auto; width: clamp(30px, 2.2vw, 44px); }
  .vwrap { flex: 1 1 auto; min-height: 0; display: flex; align-items: center; justify-content: center;
    padding: 0 1.2vw 5vh; position: relative; }
  video { max-width: 100%; max-height: 100%; width: auto; height: auto; border-radius: 6px; background: #000;
    box-shadow: 0 10px 40px rgba(0,0,0,.5); }
  .pending { display: none; flex-direction: column; align-items: center; justify-content: center; gap: 2vh;
    width: min(1100px, 90%); aspect-ratio: 16 / 9; border: 2px dashed #5C5444; border-radius: 6px; color: #A9A58F;
    font-size: clamp(18px, 2vw, 30px); text-align: center; padding: 4vh 4vw; }
  .pending b { color: var(--paper); font-size: 1.3em; font-weight: 600; }
  .pending span { font-family: var(--serif); color: var(--muted); }
  .slide.missing video { display: none; }
  .slide.missing .pending { display: flex; }
  .ended-hint { position: absolute; right: 2.4vw; bottom: 6.5vh; background: var(--pine); color: var(--paper);
    border-radius: 999px; padding: .5em 1em; font-size: clamp(14px, 1.3vw, 18px); opacity: 0; transition: opacity .3s;
    pointer-events: none; }
  .slide.ended .ended-hint { opacity: .95; }

  /* chrome */
  .progress { position: fixed; left: 0; bottom: 0; height: 5px; background: var(--pine); z-index: 10; transition: width .25s; }
  body.on-dark .progress { background: var(--leaf); }
  .nav { position: fixed; right: 1.2vw; bottom: 1.2vh; z-index: 10; display: flex; align-items: center; gap: .5em;
    font-size: 14px; color: #8C8672; }
  .nav button { background: rgba(140,134,114,.18); color: inherit; border: 0; border-radius: 6px; width: 2.2em;
    height: 2.2em; cursor: pointer; font-size: 14px; }
  .nav button:hover { background: rgba(140,134,114,.35); }
  body.on-dark .nav { background: rgba(33,31,26,.72); color: var(--muted); border-radius: 8px; padding: .3em; }
  .help { position: fixed; left: 1.2vw; bottom: 1.4vh; z-index: 10; font-size: 12px; color: #8C8672; opacity: .85; }
  body.on-video .help { display: none; }
"""

SCRIPT = """
(() => {
  const slides = [...document.querySelectorAll('.slide')];
  const progress = document.getElementById('progress');
  const count = document.getElementById('count');
  let current = 0;

  slides.forEach(s => {
    const src = s.dataset.video;
    if (!src) return;
    const v = s.querySelector('video');
    v.addEventListener('error', () => s.classList.add('missing'));
    v.addEventListener('ended', () => s.classList.add('ended'));
    v.addEventListener('play', () => s.classList.remove('ended'));
    v.src = src;
  });

  function show(i) {
    i = Math.max(0, Math.min(slides.length - 1, i));
    const old = slides[current], next = slides[i];
    const ov = old.querySelector('video');
    if (ov) ov.pause();
    old.classList.remove('active');
    current = i;
    next.classList.add('active');
    next.classList.remove('ended');
    const v = next.querySelector('video');
    if (v && !next.classList.contains('missing')) {
      try { v.currentTime = 0; } catch (e) {}
      v.play().catch(() => {});
    }
    progress.style.width = ((current + 1) / slides.length * 100) + '%';
    document.body.classList.toggle('on-video', !!next.dataset.video);
    document.body.classList.toggle('on-dark', next.classList.contains('dark'));
    count.textContent = (current + 1) + ' / ' + slides.length;
    if (location.hash !== '#' + (current + 1)) history.replaceState(null, '', '#' + (current + 1));
  }

  function toggle() {
    const v = slides[current].querySelector('video');
    if (!v || slides[current].classList.contains('missing')) { show(current + 1); return; }
    if (v.paused) { if (v.ended) v.currentTime = 0; v.play(); } else v.pause();
  }

  document.addEventListener('keydown', e => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    switch (e.key) {
      case 'ArrowRight': case 'ArrowDown': case 'PageDown': case 'n': case 'N': e.preventDefault(); show(current + 1); break;
      case 'ArrowLeft': case 'ArrowUp': case 'PageUp': case 'p': case 'P': e.preventDefault(); show(current - 1); break;
      case ' ': case 'Enter': e.preventDefault(); toggle(); break;
      case 'Home': e.preventDefault(); show(0); break;
      case 'End': e.preventDefault(); show(slides.length - 1); break;
      case 'r': case 'R': { const v = slides[current].querySelector('video'); if (v) { v.currentTime = 0; v.play(); } break; }
      case 'f': case 'F': if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen(); break;
    }
  });
  document.getElementById('prev').onclick = () => show(current - 1);
  document.getElementById('next').onclick = () => show(current + 1);
  document.querySelectorAll('video').forEach(v => v.addEventListener('keydown', e => {
    if ([' ', 'ArrowLeft', 'ArrowRight'].includes(e.key)) e.preventDefault();
  }));

  const start = parseInt(location.hash.slice(1), 10);
  slides[0].classList.add('active');
  show(Number.isFinite(start) ? start - 1 : 0);
})();
"""

BRAND = '<div class="brand"><span class="mark" aria-hidden="true"></span>JAVORNA</div>'
NUMBERS = ("žádná", "jedna", "dvě", "tři", "čtyři", "pět", "šest", "sedm", "osm", "devět", "deset")


def initials(name):
    return "".join(part[0] for part in name.split()[:2]).upper()


def who(people, person, arrow, text):
    css, _ = PEOPLE[person]
    full = people[person]
    tail = f'<span class="arrow">\u2192</span>{esc(text)}' if arrow else f" {esc(text)}"
    return (f'<div class="who"><span class="avatar {css}">{initials(full)}</span>'
            f'<b>{esc(person)}</b>{tail}</div>')


def text_slide(title, body, classes=""):
    dark = " dark" if "jump" in classes else ""
    return (f'<section class="slide text{classes}{dark}" data-title="{esc(title)}">{BRAND}'
            f'<div class="card">{body}</div></section>\n')


def scene_slides(number, scene, people):
    sid, step = scene["id"], STEPS[scene["id"]]
    body = f'<div class="kicker">Krok {number} \u00b7 {esc(step["kicker"])}</div>' + who(people, *step["who"])
    if scene["prompt"]:
        cls = ' class="xlong"' if len(scene["prompt"]) > 550 else (' class="long"' if len(scene["prompt"]) > 300 else "")
        body += f'<blockquote{cls}>{esc(scene["prompt"]).replace(chr(10), "<br>")}</blockquote>'
    elif "watch" in step:
        body += f'<ul class="watch"><li>{esc(step["watch"])}</li></ul>'
    elif "heading" in step:
        body += f'<h2>{esc(step["heading"])}</h2><p class="lead">{esc(step["lead"])}</p>'
    else:
        raise ValueError(f"scene {sid} has no message and no player copy")
    if "then" in step:
        label = step.get("then_label", "Poté v kartě \u201eCo jsem se naučil\u201c")
        body += f'<p class="then">{esc(label)}: <b>{esc(step["then"])}</b></p>'
    sub = f'<span class="sub">\u00b7 {esc(step["sub"])}</span>' if "sub" in step else ""
    video = (
        f'<section class="slide video dark" data-title="{sid} video" data-video="video/{sid}.mp4">'
        f'<div class="vbar"><span class="tag">{sid}</span>{esc(step["video"])}{sub}'
        '<span class="mark" aria-hidden="true"></span></div>'
        '<div class="vwrap"><video muted playsinline preload="auto" controls></video>'
        f'<div class="pending"><b>Čeká na nahrání</b><span>{esc(scene["cue"])}</span></div>'
        '<div class="ended-hint">\u2192 další</div></div></section>\n'
    )
    return f"\n<!-- ============ {sid} ============ -->\n" + text_slide(f"{sid} {step['kicker']}", body) + video


def build(data):
    if set(STEPS) != {s["id"] for s in data["scenes"]}:
        raise ValueError("player steps must match scenario scene IDs")
    people = {p["name"].split()[0]: p["name"] for p in data["cast"]}
    if set(people) != set(PEOPLE):
        raise ValueError("player cast must match scenario cast")
    seed = data["fictional_seed"]
    organisations = {p["organisation"] for p in data["presenters"]}
    if organisations != {"Microsoft"}:
        raise ValueError("title logo expects Microsoft presenters")
    presenters = "".join(f'<b>{esc(p["name"])}</b>' for p in data["presenters"])

    page = (
        '<!doctype html>\n<html lang="cs">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>Otto ve skupině {esc(data["company"])} \u2013 nahraná rozpočtová sezóna</title>\n'
        f'<style>{CSS}</style>\n</head>\n<body>\n'
    )
    page += (
        '\n<!-- ============ OPENING ============ -->\n'
        '<section class="slide text title dark" data-title="Úvod">'
        '<img class="title-photo" src="assets/javorna/atelier-title.jpg" width="1100" height="1502" '
        'alt="AI vygenerovaný fiktivní truhlářský ateliér">'
        '<div class="title-copy"><div class="brand"><span class="mark" aria-hidden="true"></span>'
        '<div>JAVORNA<small>ZAKÁZKOVÉ TRUHLÁŘSTVÍ</small></div></div>'
        f'<div class="title-main"><div class="kicker">{esc(data["event"])} \u00b7 nahrané demo</div>'
        '<h1><em>Otto</em> řídí rozpočtovou sezónu</h1>'
        '<p class="lead">Jeden cíl od skupinového CFO. Digitální junior, který řídí sezónu, '
        'učí se se schválením a postaví aplikaci, když je potřeba.</p></div>'
        f'<div class="presenter">{presenters}<span class="logo-box">'
        '<img class="logo" src="assets/microsoft-on-dark.svg" width="337.6" height="72" alt="Microsoft">'
        '</span></div></div></section>\n'
    )
    cards = "".join(
        f'<div class="person"><span class="avatar {PEOPLE[n][0]}">{initials(people[n])}</span>'
        f'<b>{esc(people[n])}</b><span>{esc(PEOPLE[n][1])}</span></div>' for n in CAST_ORDER
    )
    page += text_slide("Obsazení", '<div class="kicker">Kdo vystupuje v příběhu</div><h2>Tři lidé a Otto</h2>'
                       f'<div class="cast">{cards}</div>'
                       f'<p class="note">{esc(data["company"])}, její lidé i všechna čísla jsou fiktivní.</p>',
                       " wide")
    weeks = "".join(f'<div class="wk{" now" if i == 0 else ""}"><b>{esc(w)}</b><span>{esc(t)}</span></div>'
                    for i, (w, t) in enumerate(WEEKS))
    countries = len(data["countries"])
    page += text_slide("Sezóna", '<div class="kicker">Zadání</div>'
                       f'<h2>Rozpočtová sezóna {esc(seed["fiscal_year"])}: osm týdnů, '
                       f'{NUMBERS[countries]} země</h2>'
                       f'<div class="weeks">{weeks}</div>', " wide")

    for number, scene in enumerate(data["scenes"], 1):
        if scene["id"] == "10":
            facts = [
                (f'{len(seed["week_3_submitted"])} ze {countries}', "odevzdáno"),
                (", ".join(seed["week_3_overdue"]), "stále chybí"),
                ("1", "rozhodnutí CFO v záznamu"),
                ("4. týden", "challenge schůzky"),
            ]
            page += "\n<!-- ============ TIME JUMP ============ -->\n" + text_slide(
                "O dva týdny později",
                '<div class="kicker">Časový skok</div><h1>O dva týdny později</h1>'
                '<p class="lead">3. týden: validace. Příští týden začínají challenge schůzky.</p>'
                '<div class="facts">' + "".join(f'<div class="fact"><b>{esc(b)}</b>{esc(t)}</div>' for b, t in facts)
                + '</div>', " jump")
        page += scene_slides(number, scene, people)

    def cell(tag, i, value):
        otto = ' class="otto"' if i == 3 else ""
        return f"<{tag}{otto}>{esc(value)}</{tag}>"

    head = "".join(cell("th", i, h) for i, h in
                   enumerate(("Chatbot", "Pevné workflow", "Osobní asistent", "Digitální junior (Otto)")))
    rows = "".join("<tr><th>" + esc(r[0]) + "</th>" + "".join(cell("td", i, c) for i, c in enumerate(r[1:]))
                   + "</tr>" for r in RECAP)
    page += "\n<!-- ============ RECAP ============ -->\n" + text_slide(
        "Shrnutí", '<div class="kicker">V čem byl rozdíl</div>'
        '<h2>Chatbot, workflow, asistent \u2013 nebo digitální junior</h2>'
        f'<table class="recap"><thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table>', " wide")
    page += (
        '<section class="slide text wide closing dark" data-title="Závěr"><div class="card">'
        f'<span class="mark" aria-hidden="true"></span><div class="kicker">{esc(data["event"])}</div>'
        '<h1>Děkujeme</h1></div></section>\n\n'
        '<div class="progress" id="progress"></div>\n'
        '<div class="help">\u2190 \u2192 / PgUp PgDn posun \u00b7 Mezerník přehrát/pauza \u00b7 R znovu \u00b7 F celá obrazovka</div>\n'
        '<div class="nav"><button id="prev" title="Předchozí">\u25c0</button><span id="count"></span>'
        '<button id="next" title="Další">\u25b6</button></div>\n\n'
        f'<script>{SCRIPT}</script>\n</body>\n</html>\n'
    )
    return page


def main():
    data = json.loads((ROOT / "scenario" / "demo.json").read_text(encoding="utf-8"))
    page = build(data)
    OUT.write_text(page, encoding="utf-8", newline="\n")
    print(f"Built {OUT.relative_to(ROOT)}: {page.count('<section class=')} slides, "
          f"{page.count('data-video=')} clip slots.")


if __name__ == "__main__":
    main()
