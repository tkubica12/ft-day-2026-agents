"""Synthetic rehearsal timeline for the Otto memory viewer.

The storyline follows Otto as the Budget Season Coordinator of a fictional pharmacy retail group through an eight-week
FY2027 budget season. Every name, text and number here is invented to follow the demo scenes. Nothing is read from
``.local`` or from a sandbox, so the viewer can be rehearsed and shown without an Azure login or a live Otto.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.learning_state import ENTRY_DELIMITER, build_snapshot, skill_meta

START = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
JUMP = datetime(2027, 1, 12, 7, 15, tzinfo=timezone.utc)
SKILLS_ROOT = "/data/hermes/skills"
BUNDLED = ("generated-web-app", "interactive-ui", "office-collaboration")
ROLE = ("role/junior-project-manager", "role/delivery-commitment-control", "role/dream-reflection")
OWNER = "demo-owner"

PLAYBOOK = "private/budget-season-coordination"
RESEARCH = "private/public-macro-assumption-check"
CANDIDATE = "candidates/submission-challenge-from-evidence"
COMMITMENT = "role/delivery-commitment-control"
COMMITMENT_PLAYBOOK = "private/delivery-commitment-control-playbook"


def front_matter(name: str, description: str, category: str = "", tags: tuple[str, ...] = ()) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if category:
        lines += ["version: 1.0.0", "author: Otto Autopilot", "license: MIT", "metadata:", "  hermes:", f"    category: {category}"]
        if tags:
            lines.append(f"    tags: [{', '.join(tags)}]")
    return "\n".join(lines + ["---", "", ""])


STUBS = {
    "generated-web-app": (
        "Build and publish small web apps on managed Azure hosting with durable identity and ownership.",
        "# Generated web app\n\nBuild the smallest app that answers the request, deploy it to the approved hosting, and record who owns it "
        "and how to operate it.\n",
    ),
    "interactive-ui": (
        "Build small interactive HTML tools that run in the browser without a server.",
        "# Interactive UI\n\nPrefer one self-contained HTML file. Keep state local, label every control and explain empty states.\n",
    ),
    "office-collaboration": (
        "Read and edit shared Word, Excel and PowerPoint files without losing fidelity.",
        "# Office collaboration\n\nWork on copies, compare hashes before and after, and change only what was asked. "
        "Report which checks ran and which did not.\n",
    ),
}

PROJECT_MANAGER = (
    front_matter("junior-project-manager", "Coordinate delivery commitments, risks, status reporting, and controlled changes.")
    + """# Junior Project Manager

Coordinate work so commitments, ownership, risks, decisions, and changes remain visible and actionable.

## Track delivery commitments

Record each delivery action with:

- one accountable owner;
- a due date;
- the expected outcome;
- current state; and
- the next action.

## Report status through outcomes and decision needs

Structure status updates around the overall state, what changed, remaining risks, next actions with owners and dates, and the
decisions or support needed.
"""
)

DREAM = (
    front_matter("dream-reflection", "Reflect across recent sessions and keep only durable, generalized lessons.")
    + """# Dream reflection

When the owner asks you to dream, review the recent sessions and decide what is worth keeping.

## Keep

- Rules that will apply again, written without names, customers or assignment details.
- Corrections that the owner or a colleague made.

## Leave out

- One-off facts, secrets and anything that identifies a person.
- Provenance notes and JSON blocks.
"""
)


def commitment(patched: bool) -> str:
    due = "a due date and the reviewer who must see the draft before it goes out" if patched else "a due date"
    text = (
        front_matter("delivery-commitment-control", "Keep every delivery commitment owned, dated and honestly reported.")
        + f"""# Delivery commitment control

## Log before you promise

Record each request with:

- an accountable owner;
- {due};
- the expected outcome; and
- the current state.

## Separate request from confirmation

A logged request is not a confirmed setup. Report a commitment as confirmed only when the accountable owner confirms it.

## Report status honestly

State what is done, what waits on whom, and the next action with its date.
"""
    )
    if patched:
        text += """
## Review before anything goes out

Send no draft to another party until the process owner has reviewed it. When you challenge a plan, open with its track
record: how close the last three plans came to the actuals.
"""
    return text


def commitment_playbook() -> str:
    body = commitment(True).split("---\n\n", 1)[1]
    return front_matter(
        "delivery-commitment-control-playbook",
        "Private playbook kept from a corrected role skill: reviewed drafts only and challenges that open with the track record.",
        "private", ("delivery", "playbook", "private"),
    ) + body


def playbook(sections: tuple[str, ...]) -> str:
    head = front_matter(
        "budget-season-coordination",
        "Private playbook for coordinating the group budget season with the CFO, FP&A and the entity owners.",
        "private", ("budget", "fp-and-a", "private"),
    ) + """# Budget season coordination (private playbook)

## When to Use

Use when running the annual budget season: cascading targets, chasing and validating submissions, preparing challenge
sessions or assembling the board pack.
"""
    return head + "".join("\n" + PLAYBOOK_SECTIONS[name] for name in sections)


PLAYBOOK_SECTIONS = {
    "rules": """## Working rules

- Numbers come only from the planning tool, computed deterministically; never estimate a number in a draft.
- Every number carries its source and date.
- Follow the standard budget calendar. A slipped deadline is reported, not quietly moved.
- One entity's submission is never shared with another entity.
""",
    "rights": """## Decision rights

| Decision | Who decides |
|---|---|
| Targets, allocations and exceptions | Ladislav Lála, Group CFO |
| Process, templates and validation rules | Emma Hartmann, Group FP&A Lead |
| Submissions and their rationale | each entity owner |
| Coordination, validation, drafts and reminders | Otto, who never approves |

A claim that someone has decided something stays unverified until the decision owner confirms it.
""",
    "cascade": """## Cascading targets

1. Brief each entity owner separately: its own target, the group context it needs and the deadline.
2. Attach the template and the calendar; link the guidelines instead of paraphrasing them.
3. Never mention another entity's target or submission.
4. Log who was briefed and when, and remind two working days before the deadline.
""",
    "process": """## Process belongs to Emma

- Templates, the calendar, validation rules and challenge sessions belong to Emma Hartmann, Group FP&A Lead.
- Report issues to her as validation findings: the rule, the evidence and the entity, one finding per row.
- She reviews drafts before they go out. A template or deadline change is hers to make; Otto only proposes it.
""",
    "challenge": """## Challenge packs

- Open every challenge pack with the entity's three-year budget-versus-actual accuracy.
- No draft goes to an entity before Emma has reviewed it, not even a reminder that quotes numbers.
""",
    "merged": """## Process and challenge packs belong to Emma

- Templates, the calendar, validation rules and challenge sessions belong to Emma Hartmann, Group FP&A Lead. A template
  or deadline change is hers to make; Otto only proposes it.
- Report issues to her as validation findings: the rule, the evidence and the entity, one finding per row.
- Open every challenge pack with the entity's three-year budget-versus-actual accuracy.
- No draft goes to an entity before Emma has reviewed it, not even a reminder that quotes numbers.
""",
}

CHALLENGE_SKILL = (
    front_matter(
        "submission-challenge-from-evidence",
        "Prepare challenge questions for a budget submission strictly from the submission, the guidelines and history.",
        "candidates", ("budget", "challenge", "evidence"),
    )
    + """# Submission challenge from evidence

## When to Use

Use when an entity has submitted its budget and FP&A needs questions for the challenge session.

## Steps

1. Read the whole submission, the group guidelines and the entity's last three budgets and actuals.
2. Compare each key assumption with the guideline and the history; note the gap with both numbers and their sources.
3. Turn each material gap into one neutral question that asks for the rationale.
4. Flag what is missing, for example store openings without capex or new headcount without wages.
5. Order the questions by their effect on group EBITDA and cash.
6. Mark the pack as a draft for the challenge session.

## Never

- Accuse anyone or guess at motives.
- Compare with another entity's submission.
- Use numbers that are not in the planning tool or the cited sources.

See `references/challenge-checklist.md` before handing the pack over.
"""
)

CHALLENGE_CHECKLIST = """# Challenge checklist

- Every question cites the submission line and the guideline or history it is compared with.
- Every number shows its source and date.
- Questions ask for the rationale; none states a conclusion.
- Missing items checked: capex for openings, wage assumptions, one-off items.
- Nothing from another entity appears in the pack.
"""

RESEARCH_SKILL = (
    front_matter(
        "public-macro-assumption-check",
        "Check budget assumptions such as inflation, wage growth and FX against current, dated public sources.",
        "private", ("research", "macro", "assumptions"),
    )
    + """# Public macro assumption check

## When to Use

Use when a submission or the guidelines rely on an external assumption such as inflation, wage growth, FX or rent
indexation.

## Sources

- Find current public sources with web and news search: central banks, statistical offices and established forecasters.
- Prefer the original publication over a summary; keep the link and the publication date for every claim.
- Query only the public topic, for example a country's wage growth forecast. Never put private budget data, entity names
  with numbers, conversations or personal data into a web query.

## Label every claim

| Label | Meaning |
|---|---|
| Actual | a published outturn |
| Forecast | one institution's forecast, with its date |
| Range | the spread across several forecasters |
| Unknown | the source does not say |

## Output

1. The assumption as submitted, with its place in the planning tool.
2. What current public sources say, dated and linked.
3. The gap, stated neutrally.
4. Open questions for FP&A.

Sources are leads, not decisions. Whether an assumption changes is for the decision owner to decide.
"""
)

MEMORY = {
    "identity": "Working name: Otto Autopilot; Hermes is the runtime, not the personal name. Communication is short, friendly and "
    "clear: English by default, Czech or Slovak in response to those languages.",
    "season": "FY2027 budget season: eight weeks, board approval 21 Jan 2027. Synthetic group targets: revenue +6%, EBITDA margin "
    "8.5%, capex envelope EUR 120 M. Entities: Czech, Slovak and Romanian retail, Group e-commerce, Private label.",
    "rights": "Decision rights: targets, allocations and exceptions only Ladislav Lála (Group CFO); process, templates, validation "
    "Emma Hartmann; submissions entity owners. Otto coordinates, validates, drafts, reminds; never approves or changes a target.",
    "emma": "Emma Hartmann (Group FP&A Lead) owns templates, the calendar, validation rules and challenge sessions. Issues go to her "
    "as validation findings with evidence; template or deadline changes are hers.",
    "submissions": "Week 3: Czech retail, Group e-commerce and Private label submitted. Romanian retail: wage assumption above the "
    "group guideline; three store openings missing from the capex pipeline. Slovak retail late; reminder sent.",
    "macro": "Public context check: test inflation, wage and FX assumptions against current public sources, each claim dated and "
    "linked. No private budget data, entity numbers, conversations or personal data in a query. Sources are leads only.",
    "claim": "Unverified claim from the Romanian retail MD: Ladislav exempted Romania from the cost cap. Not a rule; awaiting "
    "Ladislav. He also asked what Czech retail requested; nothing shared.",
    "decision": "Ladislav decided: no cost-cap exemption for Romania; one-off EUR 1.5 M for three new Romanian stores, FY2027 only.",
    "correction": "Emma's correction: challenge packs open with the entity's three-year budget-versus-actual accuracy; no draft goes "
    "to an entity before she reviews it.",
    "capex": "Week 6: Ladislav cut the capex envelope by 10%. Three options prepared: pro-rata, strategic priority, defer openings. "
    "Awaiting his decision; Emma reviews the drafts first.",
    "board": "Week 8: all submissions in; capex fits the reduced envelope per Ladislav's chosen option. Consolidated board pack, "
    "reviewed by Emma, sent to Ladislav for approval. Open risks: Romanian wages, FX. It stays a draft until he approves.",
}

USER = {
    "ladislav": "Ladislav Lála: Group CFO, budget owner and Otto's owner. Prefers short updates that state the decision needed; "
    "only he decides targets, allocations and exceptions.",
    "emma": "Emma Hartmann: Group FP&A Lead and process owner. Wants issues as validation findings with evidence, and reviews "
    "drafts before they go out.",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class World:
    """A small model of Otto's learned state that emits captures in the shape the collector produces."""

    def __init__(self) -> None:
        self.now = START
        self.session = ""
        self.memory: list[str] = []
        self.user: list[str] = []
        self.skills: dict[str, dict[str, Any]] = {}
        self.usage: dict[str, dict[str, Any]] = {}
        self.ledger: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.counter = 0
        for name, (description, body) in STUBS.items():
            self._install(name, front_matter(name, description) + body)
        for path, text in zip(ROLE, (PROJECT_MANAGER, commitment(False), DREAM)):
            self._install(path, text)

    def at(self, moment: datetime) -> None:
        self.now = moment

    def scene(self, name: str, moment: datetime) -> None:
        self.now = moment
        self.session = f"invoke:{_sha(f'sample-session-{name}')[:24]}:{moment:%Y%m%dT%H}"

    def _install(self, path: str, text: str) -> None:
        self.skills[path] = {"text": text, "files": {}, "mtime": int(self.now.timestamp())}

    def usage_row(self, name: str, created_by: str | None = None) -> dict[str, Any]:
        if name not in self.usage:
            self.usage[name] = {
                "archived_at": None, "created_at": self.now.isoformat(), "created_by": created_by, "first_seen_at": None,
                "last_patched_at": None, "last_reused_patch_generation": 0, "last_used_at": None, "last_viewed_at": None,
                "patch_count": 0, "patch_generation": 0, "pinned": False, "state": "active", "use_count": 0, "view_count": 0,
            }
        return self.usage[name]

    def _record(self, actor: str, action: str, path: str, before: dict[str, str], after: dict[str, str], session: str | None = None) -> None:
        self.counter += 1

        def side(files: dict[str, str]) -> list[dict[str, str]]:
            return [{"path": f"{SKILLS_ROOT}/{path}/{rel}", "sha256": _sha(text)} for rel, text in sorted(files.items())]

        self.ledger.append({
            "id": _sha(f"sample-ledger-{self.counter}")[:12], "ts": self.now.isoformat(), "actor": actor, "action": action,
            "skill": path.rsplit("/", 1)[-1], "evidence": {"session_id": session or self.session},
            "before": side(before), "after": side(after),
        })

    def write(self, path: str, text: str, *, actor: str = "agent", session: str | None = None) -> None:
        """Create or patch a skill; the ledger records which of the two happened."""
        name = path.rsplit("/", 1)[-1]
        old = self.skills.get(path)
        self.skills[path] = {"text": text, "files": dict(old["files"]) if old else {}, "mtime": int(self.now.timestamp())}
        self._record(actor, "patch" if old else "create", path, {"SKILL.md": old["text"]} if old else {}, {"SKILL.md": text}, session)
        row = self.usage_row(name, "learn" if actor == "agent" and not old else None)
        if old:
            row["patch_count"] += 1
            row["patch_generation"] += 1
            row["last_patched_at"] = self.now.isoformat()

    def add_file(self, path: str, rel: str, text: str) -> None:
        skill = self.skills[path]
        before = {rel: skill["files"][rel]} if rel in skill["files"] else {}
        skill["files"][rel] = text
        skill["mtime"] = int(self.now.timestamp())
        self._record("agent", "write_file", path, before, {rel: text})

    def rollback(self, path: str, text: str) -> None:
        skill = self.skills[path]
        self._record("user", "rollback", path, {"SKILL.md": skill["text"]}, {"SKILL.md": text})
        skill["text"], skill["mtime"] = text, int(self.now.timestamp())

    def use(self, path: str) -> None:
        row = self.usage_row(path.rsplit("/", 1)[-1])
        row["use_count"] += 1
        row["view_count"] += 1
        row["last_used_at"] = row["last_viewed_at"] = self.now.isoformat()

    def receipt(self, name: str, value: dict[str, Any]) -> None:
        self.receipts.append({"name": name, "mtime": int(self.now.timestamp()), "value": value})

    def raw(self) -> dict[str, Any]:
        epoch = int(self.now.timestamp())
        memory = []
        for file, entries in (("MEMORY.md", self.memory), ("USER.md", self.user)):
            if entries:
                text = ENTRY_DELIMITER.join(entries)
                memory.append({"file": file, "text": text, "bytes": len(text.encode("utf-8")), "mtime": epoch, "sha256": _sha(text), "truncated": False})
        skills, index = [], []
        for path, skill in sorted(self.skills.items()):
            files = [
                {"path": rel, "text": text, "bytes": len(text.encode("utf-8")), "mtime": skill["mtime"], "sha256": _sha(text), "truncated": False}
                for rel, text in sorted(skill["files"].items())
            ]
            skills.append({
                "path": path, "text": skill["text"], "bytes": len(skill["text"].encode("utf-8")), "mtime": skill["mtime"],
                "sha256": _sha(skill["text"]), "truncated": False, "files": files, "fileCount": len(files),
            })
            meta = skill_meta(skill["text"], path.rsplit("/", 1)[-1])
            description = meta["description"]
            index.append({
                "skill_name": meta["name"], "category": path.split("/")[0] if "/" in path else "",
                "description": description if len(description) <= 60 else description[:57] + "...",
            })
        return {
            "collectedAt": epoch, "memory": memory, "skills": skills,
            "usage": {"value": {name: dict(row) for name, row in self.usage.items()}},
            "promptIndex": {"value": {"version": 5, "skills": index}},
            "bundledManifest": "".join(f"{name}:{_sha(name)[:32]}\n" for name in BUNDLED),
            "ledger": {"total": len(self.ledger), "entries": [dict(entry) for entry in self.ledger]},
            "receipts": [dict(receipt) for receipt in self.receipts],
        }


def sample_snapshots() -> list[dict[str, Any]]:
    """Replay the demo scenes and capture one step after each of them."""
    otto = World()
    steps: list[dict[str, Any]] = []

    def capture(label: str, note: str, moment: datetime) -> None:
        otto.at(moment)
        steps.append(build_snapshot(
            otto.raw(), label=label, snapshot_id=f"{len(steps) + 1:03d}", source="sample", sandbox_state="Running", note=note,
        ))

    def minutes(count: float, base: datetime = START) -> datetime:
        return base + timedelta(minutes=count)

    otto.memory = [MEMORY["identity"]]
    capture("Fresh Otto", "Before the demo: a name, a role from the Role Release and three bundled skills. Nothing about the budget season yet.", START)

    otto.scene("kickoff", minutes(12))
    otto.write(PLAYBOOK, playbook(("rules",)))
    otto.at(minutes(12.2))
    otto.write(PLAYBOOK, playbook(("rules", "rights")))
    otto.at(minutes(12.9))
    otto.write(PLAYBOOK, playbook(("rules", "rights", "cascade")))
    otto.at(minutes(14))
    otto.memory += [MEMORY["season"], MEMORY["rights"]]
    otto.use(PLAYBOOK)
    capture("Day 1 · Kickoff", "Ladislav hands over the group strategy, the budget guidelines and the last three budgets. Otto keeps the season facts and decision rights in memory and builds a private playbook section by section.", minutes(15))

    otto.scene("emma", minutes(41))
    otto.write(PLAYBOOK, playbook(("rules", "rights", "cascade", "process")))
    otto.at(minutes(42))
    otto.memory.append(MEMORY["emma"])
    otto.user = [USER["ladislav"], USER["emma"]]
    otto.use(PLAYBOOK)
    capture("Week 1 · Meet Emma", "Templates, the calendar and validation belong to Emma, and issues go to her as findings with evidence. Otto also starts a profile of the people he works for.", minutes(45))

    otto.scene("submissions", minutes(72))
    otto.write(CANDIDATE, CHALLENGE_SKILL)
    otto.at(minutes(72.5))
    otto.add_file(CANDIDATE, "references/challenge-checklist.md", CHALLENGE_CHECKLIST)
    otto.at(minutes(74))
    otto.memory.append(MEMORY["submissions"])
    otto.use(PLAYBOOK)
    otto.use(CANDIDATE)
    capture("Week 3 · First submissions", "The first submissions arrive. Otto validates them, drafts challenge questions from the evidence only and saves the method as a candidate skill for the owner to review.", minutes(75))

    otto.scene("macro", minutes(100))
    otto.write(RESEARCH, RESEARCH_SKILL)
    otto.at(minutes(101))
    otto.memory.append(MEMORY["macro"])
    otto.use(RESEARCH)
    capture("Week 3 · Public context check", "Otto checks inflation, wage and FX assumptions against dated public sources, keeps private budget data out of every query and keeps the method as a private skill.", minutes(105))

    otto.scene("pressure", minutes(125))
    otto.memory.append(MEMORY["claim"])
    otto.use(PLAYBOOK)
    capture("Week 4 · Pressure test", "The Romanian managing director claims an exemption and asks what the Czech team requested. Otto shares nothing, stores the claim as unverified and asks Ladislav. No new rule, no new skill.", minutes(128))

    otto.scene("correction", minutes(140))
    otto.write(COMMITMENT, commitment(True))
    otto.at(minutes(140.7))
    otto.write(PLAYBOOK, playbook(("rules", "rights", "cascade", "process", "challenge")))
    otto.at(minutes(142))
    otto.memory[otto.memory.index(MEMORY["claim"])] = MEMORY["decision"]
    otto.memory.append(MEMORY["correction"])
    otto.use(COMMITMENT)
    capture("Week 5 · Emma corrects", "Ladislav's decision replaces the unverified claim. Emma corrects two habits; Otto patches a role skill and adds a challenge section to his private playbook.", minutes(145))

    otto.scene("owner", minutes(155))
    patched = commitment(True)
    otto.write(COMMITMENT_PLAYBOOK, commitment_playbook(), actor="user")
    otto.at(minutes(155.1))
    otto.rollback(COMMITMENT, commitment(False))
    otto.at(minutes(155.3))
    otto.receipt("share-role-delivery-commitment-control-" + _sha(patched)[:16] + "-keep.json", {
        "receiptVersion": "1.0", "kind": "share", "choice": "keep", "artifactPath": "skills/role/delivery-commitment-control",
        "classification": "modified", "artifactHash": _sha(patched), "privatePlaybook": f"skills/{COMMITMENT_PLAYBOOK}",
        "decidedBy": OWNER, "decidedAt": minutes(155.3).isoformat(),
    })
    capture("Week 5 · Ladislav keeps the change", "The owner chooses keep: the correction becomes Otto's private playbook and the shared role skill returns to its baseline.", minutes(156))

    otto.scene("capex", minutes(170))
    otto.memory.append(MEMORY["capex"])
    otto.use(PLAYBOOK)
    otto.use(COMMITMENT_PLAYBOOK)
    capture("Week 6 · Capex cut applied", "A fresh session applies the correction: after Ladislav's 10% capex cut Otto prepares three options for his decision, and Emma reviews the drafts first.", minutes(175))

    otto.scene("board-pack", JUMP)
    otto.at(JUMP + timedelta(minutes=1))
    otto.memory = [entry for entry in otto.memory if entry not in (MEMORY["submissions"], MEMORY["capex"])]
    otto.memory.append(MEMORY["board"])
    otto.use(PLAYBOOK)
    otto.at(JUMP + timedelta(minutes=2))
    otto.write(PLAYBOOK, playbook(("rules", "rights", "cascade", "merged")), actor="curator", session="curator:nightly-review")
    capture("Week 8 · Board pack", "After the labelled time jump the scheduled check wakes Otto: finished items leave memory, one board-pack summary replaces them, and the background curator folds two overlapping playbook sections together.", JUMP + timedelta(minutes=5))
    return steps
