# Frontier Day 2026: Otto at Javorna

Private preparation for the 26 November 2026 presentation. Do not make this
repository public until the owner explicitly requests it after the event.

**Javorna Group is fictional.** It makes bespoke luxury furniture, sculptural
woodwork and architectural joinery for homes, representative spaces and offices,
with specialist commissions for churches, heritage restoration and film sets.
Its four country units are Czechia, Slovakia, Poland and Hungary. All financial
amounts, projects and process events in this repository are synthetic.

## What exists

- `scenario/demo.json`: cast, synthetic scenario, prompts and recording outcomes.
- `docs/presentation.html`: slide-first presentation skeleton, with recording slots.
- `docs/demo-guide.html`: concise operator guide; prompts and expected results.
- `video-production/`: EDL-based FFmpeg cutter; raw recordings remain immutable.
- `.github/skills/`: portable HTML/image authoring skills and local recording/playbook skills.
- `brand/`: image briefs and the selected identity record. Unapproved generated candidates and their manifests stay local.

There are **no recordings, screenshots, provisioned accounts or running agent
services** in this foundation. Recording slots are placeholders, not evidence of
an executed demo. Generated brand imagery is illustrative, not a customer photo.

## Cast

| Person | Role | Proposed UPN |
|---|---|---|
| Adam Vávra | Group CFO; decisions and learning approval | adam.vavra@tomasonline.net |
| Piotr Nowak | Managing Director, Poland | piotr.nowak@tomasonline.net |
| Emma Hartmann | Group FP&A Lead; process and calendar | emma.hartmann@tomasonline.net |
| Otto Autopilot | Digital budget-season coordinator | Existing bot identity, binding pending |

UPNs are the desired demo mapping, not a claim that users exist. Verify Emma's
existing identity before binding it; do not rename an existing account implicitly.
Adam and Piotr are fictional personas, not depictions of specific real people.

## Author and build

Requires Python 3.12+, Node.js, and FFmpeg/ffprobe for recordings. Document building
and recording orchestration use the standard library. The vendored image skill
has its own dependency manifest and lockfile; do not copy environments or secrets.

```powershell
python scripts\build_docs.py
python -m unittest discover -s tests -v
New-Item -ItemType Directory -Force dist | Out-Null
node docs\assets\bundle.js docs\presentation.html dist\presentation.html
node docs\assets\bundle.js docs\demo-guide.html dist\demo-guide.html
python video-production\build_clips.py --check
```

Edit the JSON scenario and rebuild the two HTML documents. Read the recording
skill before capturing new footage. Add verified trim ranges to
`video-production/edl.json`, then run the cutter with the clip IDs to build.
Presentation playback integration follows after new recordings exist; an empty
EDL intentionally refuses a render rather than reporting a successful empty build.

## Brand review

Image preparation: author `brand/brief.yaml`, put verified resource settings in
ignored `.local/image-settings.json`, then run `python scripts\prepare_images.py`.
Run the local image-generator skill's offline plan and discovery before inference.
The current brief uses explicitly approved MAI 2.7 at native 2048x2048. A single
user-authorized 3840x2160 experiment was rejected by the API (maximum 4,194,304
pixels). No upscaling or 4K claim is permitted for these candidates.

`python scripts\build_brand_review.py <run-manifest.json>` checks each original's
dimensions and hash, copies it unchanged to ignored `docs/brand-candidates/`,
and builds the local `docs/brand-review.html` gallery with title-slide studies.
Candidates remain unapproved and outside the presentation until selected.

The second review round uses furniture silhouettes and explicitly requested
wood/foliage brand palettes, without changing the shared document runtime:
`brand/furniture-brief.yaml` and `brand/furniture-palettes.json`.
Prepare that brief with
`python scripts\prepare_images.py brand\furniture-brief.yaml --output .local\furniture-brief.json`.
After approved generation, run
`python scripts\build_furniture_review.py <new-run.json> --atelier-manifest <original-run.json>`.
This preserves the approved atelier image and creates local
`docs/furniture-review.html` (three comparisons) and `docs/furniture-slides.html`
(six full-size title/content studies). Logos and palettes can be selected
independently. All candidates and their original bytes remain local and
unapproved; `docs/presentation.html` is unchanged.

Palette C was chosen. The logo round iterates the chair so its construction is
plausible: either no armrest, or a three-quarter view where four legs carry the
arms. Briefs `brand/logo-r1-brief.yaml` to `brand/logo-r3-brief.yaml` produced 16
candidates; prepare each like the furniture brief, then run
`python scripts\build_logo_review.py <r1.json> <r2.json> <r3.json> --atelier-manifest <original-run.json>`.
It builds local `docs/logo-review.html` (three finalists at 128/64/32 px, A4,
square and announcement Teams widget mockups, and all 16 iterations) and
`docs/logo-slides.html`. Check it with `node tests\check_logo_browser.cjs`.
These galleries remain a historical record of the selection process.

### Selected identity: chair 3, palette C

The selected chair now uses transparent SVG instead of a beige square:
`docs/assets/javorna/chair.svg` for pale surfaces and `chair-on-dark.svg` for
dark oak. Both have identical geometry in five colour layers (7,932 bytes
each); the reverse version lightens the materials, not the background.
The untouched approved PNG and atelier photo are preserved alongside them.
`brand/identity.json` records selection, hashes and adaptation review status.

Keep the mark free of tiles, frames and shadows, with clear space around it.
Use 64 px or larger when possible; 32 px stays recognizable but loses detail.
The main presentation and guide use the selected brand profile. The shared
document runtime and canonical UI tokens are unchanged.

```powershell
python scripts\build_docs.py
python scripts\build_vector_review.py
node tests\check_vector_browser.cjs
```

This creates `docs/vector-review.html`, `docs/vector-slides.html` and three
self-contained offline budget-widget previews plus one commission-type
selection mockup under `docs/widgets/`. The budget widgets
contain only SVG data images, readable HTML data, CSS and visual action
placeholders: no photos, scripts or live actions. The growth-ring motif has
been removed from the budget widget headers. The three category icons
(home, workplace and carving tool) appear only where the form asks what type
of commission this is; they use a restrained single-colour outline, unlike the
multicolour Javorna chair mark. All previews are offline and contain no scripts
or live actions; live gateway/Teams delivery is not implemented or verified
by this design work. The budget widgets include the compact logo but no
additional header art; their complete offline HTML files are about 25–26 KB.
The commission-type form mockup is a separate fourth example, with three
64px line icons (home, workplace desk, carving tool) at about 17 KB total.
Those illustrations appear only beside their explanatory category labels, not
in budget summaries.

Optional trace reproduction (requires existing Pillow, NumPy and OpenCV):
`python scripts\vectorize_chair.py docs\assets\javorna\chair-original.png`.
The source hash is checked before tracing. The fidelity test renders the SVG
at the original size and requires over 99% silhouette overlap, including
transparent joinery openings. The independent visual judge accepted the light
redraw and, after a contrast correction, the dark variant at 64 px and larger.
The vector adaptation remains available for the user's visual sign-off.

## Runtime boundary

The intended architecture remains Teams -> gateway -> Hermes -> Foundry, with
budget tools and WebIQ through an MCP toolbox. This foundation does not deploy
or port those services. Carry over reviewed implementation fixes separately,
with explicit environment bindings and fresh state.

Use a separate team/channel, role mapping, agent memory, learning proposals,
decisions, jobs and application access list. Never reset or reuse another demo's
state as a shortcut. A country owner's assertion is not a CFO decision.
Only the CFO approves reusable learning; one-off investment decisions are not
converted into permanent exemptions.

Before recording: provision and sign in the two users; bind verified Entra IDs;
verify private/public routing, country confidentiality and learning approval;
seed week 1 and a separate explicit week-3 snapshot; rehearse app access and a
confirmed Poland booking. Generated apps use Azure Container Apps sandbox
terminology, not an unavailable integration.

## Publication and licenses

Do not commit raw videos, browser profiles, conversations, credentials, tenant
bindings or image-generation manifests. Before future public release, inspect
every tracked file and approved media item; switching visibility is a separate
user decision. No old demo history or media was imported.

The vendored `html-docs` and `image-generator` skills retain their MIT licenses
and notices. Repository-specific content has no public redistribution license
selected yet.
