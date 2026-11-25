---
name: teams-html-widget
description: Generate a structured or interactive HTML widget inside a personal Teams message, with a bounded selection sent back to Otto.
version: 1.0.0
platforms: linux
metadata:
  hermes:
    category: productivity
    tags: [Teams, HTML, widgets, genUI]
---

# Inline generated UI

When enabled for the current personal Teams chat, use a branded structured widget
for substantive answers: explanations, budget/status summaries, comparisons,
recommendations and options. This is also the default for substantive follow-up
answers after a widget selection. Plain text is only for brief clarification
questions, brief answers or acknowledgments, not lengthy explanations.
Rule of thumb: more than about three short sentences, or any figures such as
revenue, EBITDA, capex, targets, deadlines, owners or next steps, means a widget.
This includes kickoff and progress confirmations. Show key figures as KPI tiles
and explain each point in its own short section.
Put the full answer in the widget with clear headings, short sections, lists or
tables. A widget need not be a calculator or form: use `actions: []` and omit
JavaScript behavior when a structured explanation is enough. Do not invent
buttons or interactions merely to use a widget. Preserve existing governed
approval cards and their server-side controls; do not convert them to widgets.
Use standalone
visual-artifacts for a requested report, presentation or PDF file, not this
widget. A full application with hosting is a separate capability.

Return a concise useful Czech plain-text fallback summary, not a duplicate of
the full explanation, then exactly one JSON request:

```text
<HTML_WIDGET_REQUEST>
{
  "title": "Scenario explorer",
  "html": "<h2>Explore a scenario</h2><label>Illustrative investment <input type=\"number\" id=\"investment\" data-widget-field=\"investment\" value=\"5\"></label><p id=\"estimate\"></p><button type=\"button\" data-hermes-action=\"discuss\">Discuss this scenario with Otto</button>",
  "css": "h2{margin-top:0}label{display:block;margin:16px 0}button{padding:10px 16px;border:1px solid #008a00;border-radius:6px;background:white;color:#191919}input{font:inherit}",
  "javascript": "const investment=document.getElementById('investment'); const refresh=()=>document.getElementById('estimate').textContent='Illustrative input: '+Number(investment.value)+' EUR million; not an approved allocation'; investment.addEventListener('input',refresh);refresh();",
  "actions": [{"id":"discuss","label":"Discuss this scenario with Otto"}]
}
</HTML_WIDGET_REQUEST>
```

## Authoring

- Return the request inline; creating a scratch file is not required. If you test
  calculation logic with tools, use only the permitted runtime workspace and
  report actual failures honestly. Do not invent paths or change write restrictions.
- Write a real HTML fragment, CSS and JavaScript. No document/head/body, script,
  style, meta, iframe, object, external URL or inline event-handler attributes.
  Register event listeners in `javascript`. Do not close script/style elements.
- The gateway executes your script only after the Teams host handshake. Use the
  DOM immediately; do not wait for DOMContentLoaded. It supplies resizing.
- All scripts/styles/data stay inline. No network, CDN, imports, remote images,
  nested frames, secrets, credentials or background effects. Do not ask for host
  permissions. Local SVG/charts, filtering, sorting, input validation, tabs and
  calculations are appropriate. State when numbers are illustrative.
- Write all content in Czech. Use the Javorna Group palette: dark wood text
  (`#332E27`), oak secondary text (`#6B5A45`), paper surfaces (`#EEEBDD` or
  white), muted borders (`#CFCCB6`), pine green (`#4C6138`) as the one UI accent
  and leaf green (`#BCCB7A`) only for highlights on dark wood. Use system fonts
  and readable spacing. The gateway inserts the approved Javorna Group chair
  mark and the fictional-data disclaimer. Do not duplicate or redraw the logo,
  load it remotely, or present the data as a real company's. Do not style, remove or replace gateway host elements.
- Use semantic headings, visible labels and keyboard-accessible buttons. Avoid
  fixed heights or viewport widths; the widget sizes to its content.
- Do not use element IDs beginning with `autopilot-`; those belong to the gateway.
- Limits: title 100 characters; HTML 24,000; CSS 8,000; JavaScript 12,000; the full
  JSON request at most 48,000 UTF-8 bytes. Keep it smaller when practical.
- Do not mix this request with Adaptive Card, standalone file or generated-app
  request markers. Attachment/private turns, notifications, group chats and
  background scheduled messages are not supported widget delivery paths.

## Buttons and forms

Local interactions stay in JavaScript and do not call the model.

For a choice that should continue the same Otto conversation, declare at most
five `actions` with unique lowercase IDs and visible labels (80 characters).
Use `<button type="button" data-hermes-action="the-id">Visible label</button>`.
The gateway sends that visible action label, not a hidden instruction.
Never use widget selections for budget approvals, purchases, deletion or access
grants, or claim an external change was made; those require the existing
server-side governed flow. An "approved" label or a widget click grants no rights.

Fields marked `data-widget-field="field-name"` are included in the submission.
At most ten fields, names at most 60 characters, string values at most 600
characters each. Alternatively call `await hermes.submit("the-id", {field:"value"})`
from a user click handler and show errors. Do not call it automatically on load.

The gateway accepts one selection per widget. Duplicate calls do not run the model
again. After acceptance Otto replies in the original personal chat. A substantive
explanation must use a fresh branded widget; only a brief clarification or
acknowledgment stays plain text. Buttons expire after 30 minutes and become invalid after gateway
restart. Keep a useful fallback so the user can continue if Teams cannot render it.
Activity acceptance is not proof of rendering or a successful external action.

Do not implement MCP Apps initialization, postMessage, tools/call, tokens, routing,
or model-context updates. The fixed gateway bootstrap owns those. `domain`,
client-supplied metadata and a model-visible gateway key are not proof of identity
or approval authority; only gateway-issued selections are accepted.
