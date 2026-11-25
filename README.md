# Frontier Day 2026: Otto ve skupině Javorna Group

Materiály, scénář, návod a přehrávač k českému demu **Otto Autopilot ve skupině Javorna Group** (26. listopadu 2026).

> **Javorna Group je fiktivní společnost** (zakázkový luxusní nábytek a umělecké truhlářství; pobočky Česko, Slovensko, Polsko, Maďarsko). Všechny osoby, projekty i finanční údaje jsou fiktivní.

## Kde najít přehrávač a prezentaci

| Soubor | Popis |
|---|---|
| `docs/player.html` | **Hlavní demo přehrávač** (34 slidů + 14 sestříhaných videí z `docs/video/01.mp4`–`14.mp4`). Ovládání: `←`/`→` posun, `Mezerník` přehrát/pauza, `R` znovu, `F` celá obrazovka. |
| `dist/Javorna-demo-player-presentation.html` | Jednosouborový přehrávač v plné kvalitě (CRF 16) pro promítání *(lokální build, mimo Git)*. |
| `dist/Javorna-demo-player-single-file.html` | Zmenšený jednosouborový přehrávač pro poslání e-mailem *(lokální build, mimo Git)*. |
| `docs/presentation.html` | Statická kostra prezentace (34 slidů, `html-docs` deck). |
| `docs/demo-guide.html` | Návod k nahrávání dema se všemi 14 českými prompty a očekávanými výsledky. |
| `scenario/demo.json` | Zdrojový scénář (obsazení, čísla, prompty a scény `01`–`14`). |

## Sestavení a střih videí

Vyžaduje Python 3.12+, Node.js a FFmpeg/ffprobe (pro střih videí):

```powershell
python scripts\build_docs.py
python scripts\build_player.py
python video-production\build_clips.py                 # sestříhá docs/video/01.mp4..14.mp4 dle edl.json
python video-production\build_single_player.py --full  # prezentační kopie do dist/
python video-production\build_single_player.py         # e-mailová kopie do dist/
python -m unittest discover -s tests -v
```

## Agent skills

Potřebné lokální skilly pro správu scénáře, dokumentů a nahrávání jsou ve standardní cestě `.agents/skills/`:

- `.agents/skills/budget-season` — pravidla scénáře a pravomocí Javorna Group
- `.agents/skills/demo-recording` — pravidla nahrávání, střihu (EDL) a balení přehrávače
- `.agents/skills/html-docs` — šablony a runtime pro HTML prezentaci a návod
- `.agents/skills/image-generator` — volitelná příprava brandových vizuálů

## Poznámka k soukromým datům

Surová a vyrenderovaná videa (`.local/`, `video-raw/`, `docs/video/`, `dist/`), přihlašovací údaje a tenantová konfigurace nejsou součástí Gitu. Repozitář zůstává soukromý, dokud vlastník nerozhodne jinak.

