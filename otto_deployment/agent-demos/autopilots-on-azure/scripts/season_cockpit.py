"""Season cockpit for the Otto budget-season demo: one HTML page with the season, entities and governance state.

    render                      capture live state once and write the cockpit
    render --from snapshot.json render a saved snapshot; no Azure access
    watch --interval 30         re-capture continuously; the page reloads itself

Facilitator view: it shows every entity. Otto still applies need-to-know in every conversation.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from scripts import budget_season_ops as ops


OUTPUT = ops.ROOT / "season-cockpit.html"
SNAPSHOTS = ops.ROOT / "cockpit"
WEEKS = ("Kickoff", "Cascade", "Submissions", "Validation", "Challenge", "Top-down", "Revisions", "Consolidation", "Board")
READ_PLAYBOOK = r"""
import json,pathlib,sys
p=pathlib.Path(sys.argv[1])
print(json.dumps({'text':p.read_text(encoding='utf-8') if p.exists() else ''}))
"""


def approved_learnings(playbook: str) -> list[str]:
    match = re.search(r"^## Approved learnings\s*$(.*?)(?=^## |\Z)", playbook, re.M | re.S)
    return [line[2:].strip() for line in (match.group(1) if match else "").splitlines() if line.startswith("- ")]


def capture() -> dict[str, Any]:
    snapshot: dict[str, Any] = {"observedUtc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    snapshot["export"] = ops.season(["export"])
    with ops.sandbox("runtime") as client:
        digest = ops.runtime_api(client, "GET", "/internal/learning/digest?limit=50&markPresented=false")
        snapshot["pendingLearning"] = [
            {key: item.get(key) for key in ("pendingId", "origin", "skill", "summary", "createdAt")}
            for item in digest.get("pending") or []
        ]
        snapshot["jobs"] = ops.runtime_api(client, "GET", "/internal/cron/jobs").get("jobs") or []
        playbook = ops.run_json(client, READ_PLAYBOOK, ops.PLAYBOOK_REMOTE, operation="Reading playbook")["text"]
    snapshot["approvedLearnings"] = approved_learnings(playbook)
    return snapshot


def money(value: Any) -> str:
    return "–" if value is None else f"{float(value):,.1f}"


def clip(text: Any, limit: int = 160) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def entity_rows(export: dict[str, Any]) -> list[dict[str, Any]]:
    state, week = export["state"], int(export["state"]["week"])
    consolidated = {row["entity"]: row for row in export["boardPack"]["consolidated"]["entities"]}
    owners = {msg["entity"]: (msg["to"], msg.get("toTitle", "")) for msg in state["outbox"] if msg.get("kind") == "brief"}
    rows = []
    for code, check in export["validation"].items():
        versions = state["submissions"].get(code) or []
        findings = check.get("findings") or []
        high = sum(1 for finding in findings if finding.get("severity") == "high")
        if not versions:
            status, tone = ("Overdue", "bad") if week >= 3 else ("Waiting", "idle")
        elif high:
            status, tone = "Needs action", "bad"
        elif findings:
            status, tone = "Minor gaps", "warn"
        else:
            status, tone = "Clean", "good"
        owner, title = owners.get(code) or ((versions[-1]["submittedBy"], "") if versions else ("–", ""))
        rows.append({
            "code": code, "name": check.get("name", code), "owner": owner, "title": title, "status": status,
            "tone": tone, "version": len(versions), "late": bool(check.get("late")), "findings": findings,
            "figures": consolidated.get(code, {}),
        })
    return rows


def render(snapshot: dict[str, Any], refresh_seconds: int = 0) -> str:
    export = snapshot["export"]
    state, pack = export["state"], export["boardPack"]
    week = int(state["week"])
    group = pack["consolidated"]
    capex = pack["capex"]
    claims, decisions = state["claims"], state["decisions"]
    unverified = sum(1 for claim in claims if claim.get("status") == "unverified")
    high = sum(1 for rows in pack["openFindings"].values() for finding in rows if finding.get("severity") == "high")
    counts: dict[int, dict[str, int]] = {}
    for event in export["events"]:
        bucket = counts.setdefault(int(event["week"]), {})
        bucket[event["kind"]] = bucket.get(event["kind"], 0) + 1

    def tile(label: str, value: str, note: str, tone: str = "") -> str:
        return (f'<div class="tile {tone}"><div class="label">{escape(label)}</div>'
                f'<div class="value">{escape(value)}</div><div class="note">{escape(note)}</div></div>')

    timeline = "".join(
        f'<li class="{"now" if index == week else "past" if index < week else ""}">'
        f'<b>W{index}</b><span>{escape(name)}</span>'
        f'<small>{escape(" · ".join(f"{n} {k.replace("_", " ")}" for k, n in sorted(counts.get(index, {}).items())))}</small></li>'
        for index, name in enumerate(WEEKS)
    )
    cards = []
    for row in entity_rows(export):
        figures = row["figures"]
        items = "".join(
            f'<li class="{escape(f.get("severity", ""))}">{escape(f.get("title", ""))}'
            f'{"" if f.get("amountEurM") is None else " · EUR " + money(f["amountEurM"]) + " M"}</li>'
            for f in row["findings"]
        ) or "<li class=\"ok\">No open findings</li>"
        badges = (f'<span class="badge">v{row["version"]}</span>' if row["version"] else "") + (
            '<span class="badge late">late</span>' if row["late"] else "")
        cards.append(
            f'<article class="entity {row["tone"]}"><header><h3>{escape(row["name"])}</h3>'
            f'<span class="status">{escape(row["status"])}</span></header>'
            f'<p class="owner">{escape(row["owner"])}{" · " + escape(row["title"]) if row["title"] else ""} {badges}</p>'
            f'<p class="figures">Revenue {money(figures.get("revenueEurM"))} / {money(figures.get("revenueTargetEurM"))}'
            f' · EBITDA {money(figures.get("ebitdaEurM"))} / {money(figures.get("ebitdaTargetEurM"))}</p>'
            f'<ul class="findings">{items}</ul></article>'
        )
    decision_items = "".join(
        f'<li><b>W{d["week"]}</b> {escape(d["subject"])} <small>{escape(d.get("decidedBy", ""))}</small></li>'
        for d in decisions
    ) or "<li class=\"empty\">None yet</li>"
    claim_items = "".join(
        f'<li><span class="chip {escape(c.get("status", ""))}">{escape(c.get("status", ""))}</span> '
        f'<b>W{c["week"]}</b> {escape(c.get("claimant", ""))}: “{escape(clip(c.get("claim")))}”</li>'
        for c in claims
    ) or "<li class=\"empty\">None yet</li>"
    exception_items = "".join(
        f'<li>{escape(e.get("entity", ""))}: {escape(e.get("scope") or e.get("subject") or "")}</li>'
        for e in state.get("exceptions") or []
    ) or "<li class=\"empty\">None granted</li>"
    approved_items = "".join(
        f"<li>{escape(item)}</li>" for item in snapshot.get("approvedLearnings") or []
    ) or "<li class=\"empty\">None yet</li>"
    pending_items = "".join(
        f'<li><span class="chip unverified">awaits CFO</span> {escape(item.get("summary") or item.get("skill") or "")}</li>'
        for item in snapshot.get("pendingLearning") or []
    ) or "<li class=\"empty\">Nothing waiting</li>"
    job_items = "".join(
        f'<li>{escape(job.get("name", ""))} <small>next {escape(str(job.get("nextRunAt", "")))}</small></li>'
        for job in snapshot.get("jobs") or []
    ) or "<li class=\"empty\">None scheduled</li>"
    feed = "".join(
        f'<li><b>W{e["week"]}</b> <span class="kind">{escape(e["kind"].replace("_", " "))}</span> '
        f'{escape(clip(e.get("summary")))} <small>{escape(e.get("actor") or "")}</small></li>'
        for e in list(reversed(export["events"]))[:14]
    )
    refresh = f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">' if refresh_seconds else ""
    envelope = " → ".join(money(step["value"]) for step in capex.get("envelopeHistory") or [])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">{refresh}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Season cockpit · {escape(export["group"])} {escape(export["fiscalYear"])}</title>
<style>
:root{{--bg:#0f1419;--panel:#171e26;--line:#2a3440;--text:#e8edf2;--muted:#8b98a6;--good:#3fb950;--warn:#d29922;--bad:#f85149;--accent:#58a6ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 "Segoe UI",system-ui,sans-serif}}
main{{max-width:1500px;margin:auto;padding:22px 28px 40px}}
h1{{font-size:26px;margin:0}}h2{{font-size:14px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:26px 0 10px}}
h3{{margin:0;font-size:17px}}small,.note,.owner{{color:var(--muted)}}
.top{{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap}}
.tiles{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-top:16px}}
.tile,.entity,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.tile .label{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}.tile .value{{font-size:26px;font-weight:600;margin:4px 0}}
.tile.bad .value{{color:var(--bad)}}.tile.warn .value{{color:var(--warn)}}.tile.good .value{{color:var(--good)}}
.timeline{{display:grid;grid-template-columns:repeat(9,1fr);gap:6px;list-style:none;padding:0;margin:0}}
.timeline li{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-height:86px}}
.timeline li span{{display:block;font-weight:600}}.timeline li small{{display:block;font-size:11px;margin-top:4px}}
.timeline li.past{{opacity:.75}}.timeline li.now{{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent) inset}}
.entities{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}
.entity{{border-top:4px solid var(--line)}}.entity.good{{border-top-color:var(--good)}}.entity.warn{{border-top-color:var(--warn)}}.entity.bad{{border-top-color:var(--bad)}}
.entity header{{display:flex;justify-content:space-between;gap:8px}}.status{{font-size:12px;font-weight:600}}
.entity.good .status{{color:var(--good)}}.entity.warn .status{{color:var(--warn)}}.entity.bad .status{{color:var(--bad)}}
.owner,.figures{{margin:6px 0;font-size:13px}}.findings{{margin:8px 0 0;padding-left:18px;font-size:13px}}
.findings .high{{color:#ffa198}}.findings .medium{{color:#e3b341}}.findings .ok{{color:var(--good);list-style:none;margin-left:-18px}}
.badge{{border:1px solid var(--line);border-radius:999px;padding:0 7px;font-size:11px;margin-left:4px}}.badge.late{{border-color:var(--warn);color:var(--warn)}}
.grid{{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:12px}}.panel h3{{margin-bottom:8px}}
.panel ul{{margin:0;padding-left:18px}}.panel li{{margin:5px 0}}.empty{{color:var(--muted);list-style:none;margin-left:-18px}}
.chip{{font-size:11px;border-radius:999px;padding:1px 7px;border:1px solid var(--line);text-transform:uppercase}}
.chip.unverified{{color:var(--warn);border-color:var(--warn)}}.chip.rejected{{color:var(--bad);border-color:var(--bad)}}.chip.confirmed{{color:var(--good);border-color:var(--good)}}
.feed{{list-style:none;padding:0;margin:0;columns:2;column-gap:24px}}.feed li{{break-inside:avoid;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}}
.kind{{color:var(--accent)}}footer{{margin-top:22px;color:var(--muted);font-size:12px}}
</style></head><body><main>
<div class="top"><h1>{escape(export["group"])} · {escape(export["fiscalYear"])} budget season</h1>
<span class="note">Week {week} of 8 · {escape(WEEKS[min(week, 8)])} · observed {escape(snapshot["observedUtc"][:16].replace("T", " "))} UTC</span></div>
<div class="tiles">
{tile("Revenue, EUR M", money(group.get("revenueEurM")), "target " + money(group.get("revenueTargetEurM")))}
{tile("EBITDA, EUR M", money(group.get("ebitdaEurM")), "target " + money(group.get("ebitdaTargetEurM")))}
{tile("Capex plan, EUR M", money(capex.get("plannedTotalEurM")), "envelope " + envelope,
      "bad" if (capex.get("plannedTotalEurM") or 0) > (capex.get("envelopeEurM") or 0) else "good")}
{tile("High findings", str(high), "open across entities", "bad" if high else "good")}
{tile("Unverified claims", str(unverified), f"{len(claims)} logged, none treated as a decision", "warn" if unverified else "good")}
{tile("Decisions", str(len(decisions)), f"{len(state['outbox'])} messages sent by Otto")}
</div>
<h2>Eight-week season</h2><ol class="timeline">{timeline}</ol>
<h2>Entities</h2><section class="entities">{"".join(cards)}</section>
<h2>What Otto holds, and who decided it</h2>
<section class="grid">
<div class="panel"><h3>Decisions · only from their owner</h3><ul>{decision_items}</ul><h3 style="margin-top:14px">Exceptions</h3><ul>{exception_items}</ul></div>
<div class="panel"><h3>Claims · recorded, never obeyed</h3><ul>{claim_items}</ul></div>
<div class="panel"><h3>Learning · approved by the Group CFO</h3><ul>{approved_items}</ul>
<h3 style="margin-top:14px">Waiting for approval</h3><ul>{pending_items}</ul>
<h3 style="margin-top:14px">Scheduled checks</h3><ul>{job_items}</ul></div>
</section>
<h2>Latest activity</h2><ul class="feed">{feed}</ul>
<footer>Facilitator view of synthetic data. Otto applies need-to-know in every conversation; numbers come from the planning tool.</footer>
</main></body></html>
"""


def write(snapshot: dict[str, Any], out: Path, refresh_seconds: int = 0, save: bool = True) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if save:
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        stamp = snapshot["observedUtc"].replace(":", "").replace("-", "").replace("+0000", "Z")
        (SNAPSHOTS / f"snapshot-{stamp}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    out.write_text(render(snapshot, refresh_seconds), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    rendering = commands.add_parser("render")
    rendering.add_argument("--from", dest="source", type=Path, help="Saved snapshot JSON; no Azure access.")
    rendering.add_argument("--out", type=Path, default=OUTPUT)
    watching = commands.add_parser("watch")
    watching.add_argument("--interval", type=int, default=30)
    watching.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    if args.command == "render":
        snapshot = json.loads(args.source.read_text(encoding="utf-8")) if args.source else capture()
        print(write(snapshot, args.out, save=args.source is None))
        return 0
    while True:
        print(write(capture(), args.out, max(args.interval, 10)), flush=True)
        time.sleep(max(args.interval, 10))


if __name__ == "__main__":
    raise SystemExit(main())
