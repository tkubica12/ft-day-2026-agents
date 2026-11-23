---
name: visual-artifacts
description: Prepare an offline html-docs report, presentation, or brief from fictional Medivia data.
---

# Visual artifacts

Write all artifact content in English, including titles, source descriptions,
body text, and slide cues, even when the request is in another language.
Create a UTF-8 JSON object with exactly `kind`, `title`, `subtitle`, `closing`,
`source`, and `sections`. `kind` is `report` (Read and Slides), `slides`
(opens in Slides), or `brief` (one-page PDF). Each section has exactly
`heading`, `body`, and `cues` (zero to three strings of at most ten words).
Use one to eight sections for HTML and one to four for PDF; limit brief body
text to 300 characters per section. The builder checks actual page fit.
For HTML, the closing is displayed at title size: aim for at most 8 words and
60 characters, while preserving essential qualifications. Keep section
headings and cues concise too. These are guidance, not a guarantee of fit;
fonts and word lengths affect the layout. If rendering reports an overflowing
surface, shorten that surface and request a new artifact.
Use factual, dated source descriptions and label synthetic data explicitly.
Text is plain text only: never include markup, event handlers, or URLs.
The builder validates these limits and escapes content.

The html-docs runtime uses a green accent, system fonts and an unchanged
Dr.Max Group logo sourced from https://www.drmax.eu/assets/img/logo.svg.
This is fictional Medivia data, not Dr.Max financial data. The notice is
included in each document. Call `m365-collaboration`'s
`prepare_visual_artifact` with the current turn's `operation_scope` from the
gateway instructions and the JSON string as `content_json`. Include its exact
returned marker in the final answer. The tool only stages content; the gateway
renders and shares it with the current authenticated requester. Never choose a
recipient or claim delivery before the gateway confirms it.
