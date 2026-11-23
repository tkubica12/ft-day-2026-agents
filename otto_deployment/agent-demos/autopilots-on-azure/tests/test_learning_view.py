import contextlib
import http.client
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from scripts import learning_view
from scripts.learning_state import (
    COLLECT_SCRIPT, ENTRY_DELIMITER, MEMORY_LIMITS, VIEWER_DIR, VIEWER_SHELL, assemble_timeline, build_snapshot,
    content_hash, decode_collected, digest, load_snapshots, next_snapshot_id, parse_front_matter, redact,
    rename_snapshot, render_html, save_snapshot, skill_meta, split_entries, timeline_summary,
)
from scripts.learning_view import MAX_BODY, OttoAsleep, Viewer, make_server
from scripts.learning_view_sample import sample_snapshots


SKILL_TEXT = """---
name: demo-playbook
description: >
  Keep demo notes short
  and dated.
version: 1.2.0
author: Otto
license: MIT
metadata:
  hermes:
    category: private
    tags: [demo, notes]
---

# Demo playbook

Body text.
"""


def raw_state(version: int = 0, *, memory_text: str | None = None) -> dict:
    """A capture in the shape the collector produces."""
    memory_text = memory_text if memory_text is not None else f"First note\n§\nSecond note {version}"
    return {
        "collectedAt": 1_800_000_000 + version,
        "memory": [
            {"file": "MEMORY.md", "text": memory_text, "bytes": len(memory_text.encode()), "mtime": 1, "sha256": "x", "truncated": False},
            {"file": "OTHER.md", "text": "ignored", "bytes": 7, "mtime": 1, "sha256": "x", "truncated": False},
        ],
        "skills": [
            {
                "path": "private/demo-playbook", "text": SKILL_TEXT, "bytes": len(SKILL_TEXT.encode()), "mtime": 5, "sha256": "x",
                "truncated": False, "fileCount": 2,
                "files": [
                    {"path": "references/a.md", "text": "# A\n", "bytes": 4, "mtime": 5, "sha256": "x", "truncated": False},
                    {"path": "assets/logo.bin", "bytes": 900, "mtime": 5, "sha256": "x", "binary": True},
                ],
            },
            {
                "path": "role/junior-project-manager", "text": "---\nname: junior-project-manager\ndescription: Coordinate.\n---\n# JPM\n",
                "bytes": 60, "mtime": 5, "sha256": "x", "truncated": False, "files": [], "fileCount": 0,
            },
            {
                "path": "generated-web-app", "text": "---\nname: generated-web-app\n---\n# Web\n", "bytes": 40, "mtime": 5, "sha256": "x",
                "truncated": False, "files": [], "fileCount": 0,
            },
            {
                "path": "scratch-pad", "text": "# Scratch\n", "bytes": 10, "mtime": 5, "sha256": "x", "truncated": False, "files": [],
                "fileCount": 0,
            },
        ],
        "usage": {"value": {"demo-playbook": {"use_count": 3, "patch_count": 1, "state": "active", "created_by": "learn", "secret_field": "no"}}},
        "promptIndex": {"value": {"version": 5, "skills": [{"skill_name": "demo-playbook", "category": "private", "description": "Keep notes"}]}},
        "bundledManifest": "generated-web-app:abc\n",
        "ledger": {
            "total": 2,
            "entries": [
                {
                    "id": "aaa", "ts": "2026-11-24T08:00:00+00:00", "actor": "agent", "action": "create", "skill": "demo-playbook",
                    "evidence": {"session_id": "invoke:1"}, "before": [],
                    "after": [{"path": "/data/hermes/skills/private/demo-playbook/SKILL.md", "sha256": "x"}],
                },
                {"id": "bbb", "ts": "2026-11-24T08:05:00+00:00", "actor": "curator", "action": "patch", "skill": "/x/demo-playbook"},
                {"no": "id"},
            ],
        },
        "receipts": [
            {"name": "share-a.json", "mtime": 2, "value": {"kind": "share", "choice": "keep"}},
            {"name": "huge.json", "mtime": 3, "value": {"blob": "x" * 5000}},
        ],
    }


def make_snapshot(version: int = 0, **options) -> dict:
    options.setdefault("snapshot_id", f"{version + 1:03d}")
    return build_snapshot(raw_state(version), label=options.pop("label", f"Step {version}"), **options)


class StateTests(unittest.TestCase):
    def test_front_matter_parses_the_skill_subset(self):
        front = parse_front_matter(SKILL_TEXT)
        self.assertEqual(front["name"], "demo-playbook")
        self.assertEqual(front["description"], "Keep demo notes short and dated.")
        self.assertEqual(front["metadata"]["hermes"]["tags"], ["demo", "notes"])
        meta = skill_meta(SKILL_TEXT, "fallback")
        self.assertEqual(
            meta,
            {
                "name": "demo-playbook", "description": "Keep demo notes short and dated.", "version": "1.2.0", "author": "Otto",
                "license": "MIT", "tags": ["demo", "notes"], "category": "private",
            },
        )

    def test_front_matter_is_optional_and_never_raises(self):
        self.assertEqual(parse_front_matter("# Just a heading\n"), {})
        self.assertEqual(parse_front_matter("---\nunterminated: true\n"), {})
        self.assertEqual(skill_meta("# No front matter", "from-path")["name"], "from-path")
        self.assertEqual(skill_meta("---\n\n---\nBody", "x")["description"], "")
        self.assertEqual(parse_front_matter("---\r\nname: crlf\r\n---\r\nBody")["name"], "crlf")

    def test_entries_split_on_the_section_sign(self):
        self.assertEqual(split_entries("one\n§\ntwo"), ["one", "two"])
        self.assertEqual(split_entries("one\r\n§\r\ntwo\r\n"), ["one", "two"])
        self.assertEqual(split_entries("§\none\n§\n\n§\ntwo\n§"), ["one", "two"])
        self.assertEqual(split_entries("only"), ["only"])
        self.assertEqual(split_entries("  \n"), [])
        self.assertEqual(split_entries("price is 5 § not a delimiter"), ["price is 5 § not a delimiter"])

    def test_redaction_hides_credential_shapes_and_keeps_ordinary_text(self):
        secrets = [
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36",
            "sk-abcdefghijklmnopqrstuvwxyz123456",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "xoxb-1234567890-abcdefghij",
            "AKIAABCDEFGHIJKLMNOP",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
            'api_key = "AbCdEf0123456789AbCdEf0123456789"',
            "password: Sup3rSecretValue1234567890",
            "AccountKey=abcdefghijklmnopqrstuvwxyz0123456789==",
        ]
        for secret in secrets:
            text, count = redact(f"before {secret} after")
            self.assertGreaterEqual(count, 1, secret)
            self.assertIn("[hidden]", text, secret)
            self.assertTrue(text.startswith("before ") and text.endswith(" after"), secret)
        ordinary = "FY2027 capex review: three stores, EUR 1.5 M, 09:45–10:15, sha 0123456789abcdef, password policy applies, token budget 20."
        self.assertEqual(redact(ordinary), (ordinary, 0))

    def test_snapshot_normalises_a_collector_capture(self):
        snapshot = make_snapshot(2, source="live", sandbox_state="Running", note="  A note. ")
        self.assertEqual(snapshot["schema"], "otto-learning-snapshot/1")
        self.assertEqual((snapshot["id"], snapshot["label"], snapshot["note"]), ("003", "Step 2", "A note."))
        self.assertEqual(snapshot["capturedAt"], 1_800_000_002)
        notes = snapshot["memory"]["memory"]
        self.assertEqual(notes["entries"], ["First note", "Second note 2"])
        self.assertEqual(notes["chars"], len(ENTRY_DELIMITER.join(notes["entries"])))
        self.assertNotIn("user", snapshot["memory"])
        self.assertEqual([skill["path"] for skill in snapshot["skills"]], sorted(skill["path"] for skill in snapshot["skills"]))
        namespaces = {skill["path"]: skill["namespace"] for skill in snapshot["skills"]}
        self.assertEqual(
            namespaces,
            {
                "generated-web-app": "bundled", "private/demo-playbook": "private", "role/junior-project-manager": "role",
                "scratch-pad": "local",
            },
        )
        playbook = next(skill for skill in snapshot["skills"] if skill["name"] == "demo-playbook")
        self.assertEqual(snapshot["blobs"][playbook["sha"]], SKILL_TEXT)
        self.assertEqual(playbook["fileCount"], 2)
        reference, binary = playbook["files"]
        self.assertEqual(snapshot["blobs"][reference["sha"]], "# A\n")
        self.assertTrue(binary["binary"] and binary["sha"] is None)
        self.assertEqual(set(playbook["usage"]), {"use_count", "patch_count", "state", "created_by"})
        self.assertEqual(playbook["meta"]["category"], "private")
        self.assertEqual(snapshot["promptIndex"], [{"name": "demo-playbook", "category": "private", "description": "Keep notes"}])

    def test_ledger_and_receipts_are_mapped_and_capped(self):
        snapshot = make_snapshot()
        first, second = snapshot["ledger"]
        self.assertEqual((first["origin"], second["origin"]), ("foreground", "background"))
        self.assertEqual(first["files"], ["private/demo-playbook/SKILL.md"])
        self.assertEqual(first["session"], "invoke:1")
        self.assertEqual(second["skill"], "demo-playbook")
        self.assertEqual(snapshot["ledgerTotal"], 2)
        self.assertEqual([receipt["name"] for receipt in snapshot["receipts"]], ["share-a.json"])

    def test_snapshot_redacts_before_storing_anything(self):
        raw = raw_state(memory_text="Use Bearer abcdefghijklmnopqrstuvwxyz0123456789 for the API\n§\nfine")
        raw["skills"][0]["text"] += '\napi_key = "AbCdEf0123456789AbCdEf0123456789"\n'
        snapshot = build_snapshot(raw, label="Secrets", snapshot_id="001")
        dump = json.dumps(snapshot)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", dump)
        self.assertNotIn("AbCdEf0123456789", dump)
        self.assertGreaterEqual(snapshot["redactions"], 2)
        playbook = next(skill for skill in snapshot["skills"] if skill["name"] == "demo-playbook")
        self.assertIn("[hidden]", snapshot["blobs"][playbook["sha"]])
        self.assertEqual(playbook["sha"], digest(snapshot["blobs"][playbook["sha"]]))

    def test_content_hash_follows_what_the_viewer_shows(self):
        base = make_snapshot()
        later = build_snapshot({**raw_state(), "collectedAt": 1_900_000_000}, label="Later", snapshot_id="002")
        self.assertEqual(base["contentHash"], later["contentHash"])
        self.assertEqual(base["contentHash"], content_hash(base))
        self.assertNotEqual(base["contentHash"], make_snapshot(1)["contentHash"])
        used = raw_state()
        used["usage"]["value"]["demo-playbook"]["use_count"] = 4
        self.assertNotEqual(base["contentHash"], build_snapshot(used, label="Used", snapshot_id="003")["contentHash"])

    def test_timeline_merges_blobs_and_remembers_when_a_ledger_entry_first_appeared(self):
        first = make_snapshot(0)
        second_raw = raw_state(1)
        second_raw["ledger"]["entries"].append({"id": "ccc", "ts": "2026-11-24T09:00:00+00:00", "actor": "user", "action": "rollback", "skill": "x"})
        second = build_snapshot(second_raw, label="Step 1", snapshot_id="002")
        timeline = assemble_timeline([second, first], source="live")
        self.assertEqual(timeline["schema"], "otto-learning-timeline/1")
        self.assertEqual([step["id"] for step in timeline["snapshots"]], ["001", "002"])
        self.assertEqual(timeline["limits"], MEMORY_LIMITS)
        for step in timeline["snapshots"]:
            self.assertFalse({"blobs", "ledger", "file", "schema"} & set(step))
        self.assertEqual(set(timeline["blobs"]), set(first["blobs"]) | set(second["blobs"]))
        self.assertEqual({entry["id"]: entry["seen"] for entry in timeline["ledger"]}, {"aaa": 0, "bbb": 0, "ccc": 1})
        self.assertEqual([entry["id"] for entry in timeline["ledger"]], ["aaa", "bbb", "ccc"])
        self.assertIn("live timeline: 2 snapshot(s)", timeline_summary(timeline))

    def test_snapshots_round_trip_through_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "timeline"
            self.assertEqual(load_snapshots(folder), [])
            self.assertEqual(next_snapshot_id(folder), "001")
            first = save_snapshot(folder, make_snapshot(0, label="Rehearsal: after redeploy!"))
            self.assertEqual(first.name, "001-rehearsal-after-redeploy.json")
            self.assertEqual(next_snapshot_id(folder), "002")
            save_snapshot(folder, make_snapshot(1))
            (folder / "003-broken.json").write_text("{not json", encoding="utf-8")
            (folder / "004-foreign.json").write_text('{"schema": "other"}', encoding="utf-8")
            (folder / "notes.json").write_text("{}", encoding="utf-8")
            self.assertEqual([item["id"] for item in load_snapshots(folder)], ["001", "002"])
            self.assertTrue(rename_snapshot(folder, "002", "  After scene 1  "))
            self.assertFalse(rename_snapshot(folder, "999", "Nope"))
            self.assertEqual(
                sorted(path.name for path in folder.glob("0*.json") if "broken" not in path.name and "foreign" not in path.name),
                ["001-rehearsal-after-redeploy.json", "002-after-scene-1.json"],
            )
            self.assertEqual(load_snapshots(folder)[1]["label"], "After scene 1")
            self.assertEqual(list(folder.glob(".*.tmp")), [])


class StubParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


class RenderTests(unittest.TestCase):
    def make_viewer_dir(self, directory: str, *, shell: str | None = None, script: str = "window.ok = 1;") -> Path:
        folder = Path(directory)
        folder.joinpath(VIEWER_SHELL).write_text(
            shell
            or "<!doctype html><head><!-- @inline css viewer.css --></head><body>"
            "<!-- @inline data --><!-- @inline js core.js --><!-- @inline js app.js --></body>",
            encoding="utf-8",
        )
        folder.joinpath("viewer.css").write_text("body { color: red; }", encoding="utf-8")
        folder.joinpath("core.js").write_text(script, encoding="utf-8")
        folder.joinpath("app.js").write_text("window.app = 2;", encoding="utf-8")
        return folder

    def test_data_is_escaped_so_it_cannot_close_the_script_or_open_markup(self):
        hostile = "</script><script>alert(1)</script><!-- & \u2028 \u2029"
        timeline = {"source": "live", "snapshots": [], "blobs": {"k": hostile}, "ledger": [], "note": hostile}
        with tempfile.TemporaryDirectory() as directory:
            html = render_html(timeline, live=True, viewer_dir=self.make_viewer_dir(directory))
        parser = StubParser()
        parser.feed(html)
        self.assertEqual([item.get("id") for item in parser.scripts], ["otto-data", None, None])
        self.assertEqual(parser.scripts[0]["type"], "application/json")
        payload =  re.search(r'<script id="otto-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        for raw in ("<", ">", "&", "\u2028", "\u2029"):
            self.assertNotIn(raw, payload)
        data = json.loads(payload)
        self.assertEqual(data, {"live": True, "timeline": timeline})
        self.assertIn("<style>\nbody { color: red; }\n</style>", html)
        self.assertNotIn("@inline", html)

    def test_inline_markers_are_required_and_scripts_must_be_inlinable(self):
        with tempfile.TemporaryDirectory() as directory:
            few = self.make_viewer_dir(directory, shell="<head><!-- @inline css viewer.css --></head><!-- @inline data -->")
            with self.assertRaisesRegex(ValueError, "markers"):
                render_html({}, viewer_dir=few)
        for bad in ("var s = '</script>';", "var s = '</SCRIPT >';", "/* <!-- */"):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, "cannot be inlined"):
                    render_html({}, viewer_dir=self.make_viewer_dir(directory, script=bad))

    def test_the_real_viewer_shell_renders_the_sample(self):
        self.assertTrue((VIEWER_DIR / VIEWER_SHELL).is_file(), "memory-viewer shell is missing")
        timeline = assemble_timeline(sample_snapshots(), source="sample")
        html = render_html(timeline, live=False)
        self.assertNotIn("@inline", html)
        self.assertEqual(html.count('id="otto-data"'), 1)
        payload = re.search(r'<script id="otto-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        data = json.loads(payload)
        self.assertFalse(data["live"])
        self.assertEqual(len(data["timeline"]["snapshots"]), 10)
        self.assertIn('name="doc-id" content="otto-memory-viewer"', html)
        self.assertIn("MIT License", html)
        page = re.sub(r'<script id="otto-data".*?</script>', "", html, flags=re.S)
        self.assertIn("default-src 'none'", page)
        self.assertNotRegex(page, r"""(?i)\b(?:src|href|action)\s*=\s*["']?(?:https?:)?//""")
        self.assertNotRegex(page, r"""(?i)@import|url\(\s*["']?(?:https?:)?//""")
        self.assertNotRegex(page, r"(?i)\bfetch\(\s*[\"'`]https?:|XMLHttpRequest|WebSocket|EventSource|sendBeacon")


class SampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = sample_snapshots()

    def test_sample_replays_ten_steps_of_the_demo(self):
        labels = [step["label"] for step in self.steps]
        self.assertEqual(len(labels), 10)
        self.assertEqual(labels[0], "Fresh Otto")
        self.assertIn("Week 3 · Public context check", labels)
        self.assertEqual([step["id"] for step in self.steps], [f"{number:03d}" for number in range(1, 11)])
        self.assertTrue(all(step["source"] == "sample" and step["note"] for step in self.steps))
        self.assertEqual(len({step["contentHash"] for step in self.steps}), 10)
        times = [step["capturedAt"] for step in self.steps]
        self.assertEqual(times, sorted(times))

    def test_sample_memory_stays_within_the_hermes_limits(self):
        for step in self.steps:
            for slot, limit in MEMORY_LIMITS.items():
                item = step["memory"].get(slot)
                if item:
                    self.assertLessEqual(item["chars"], limit, f"{step['label']} {slot}")
                    self.assertEqual(item["chars"], len(ENTRY_DELIMITER.join(item["entries"])))
        self.assertNotIn("user", self.steps[0]["memory"])
        self.assertEqual(len(self.steps[2]["memory"]["user"]["entries"]), 2)

    def test_sample_tells_the_governance_story(self):
        namespaces = [{skill["namespace"] for skill in step["skills"]} for step in self.steps]
        self.assertEqual(namespaces[0], {"bundled", "role"})
        self.assertIn("candidates", namespaces[3])
        by_step = [{skill["path"]: skill for skill in step["skills"]} for step in self.steps]
        commitment = "role/delivery-commitment-control"
        self.assertNotEqual(by_step[5][commitment]["fp"], by_step[6][commitment]["fp"])
        self.assertEqual(by_step[5][commitment]["fp"], by_step[7][commitment]["fp"], "the owner restores the baseline")
        self.assertIn("private/delivery-commitment-control-playbook", by_step[7])
        self.assertEqual(len(self.steps[7]["receipts"]), 1)
        final = self.steps[-1]
        actors = {entry["actor"] for entry in final["ledger"]}
        self.assertEqual(actors, {"agent", "user", "curator"})
        curator = [entry for entry in final["ledger"] if entry["actor"] == "curator"]
        self.assertEqual([(entry["origin"], entry["action"]) for entry in curator], [("background", "patch")])
        self.assertTrue(curator[0]["session"].startswith("curator:"))
        self.assertEqual(len({entry["id"] for entry in final["ledger"]}), len(final["ledger"]))

    def test_sample_usage_rows_only_exist_for_skills_that_were_touched(self):
        baseline = {skill["path"]: skill for skill in self.steps[0]["skills"]}
        self.assertTrue(all(skill["usage"] is None for skill in baseline.values()))
        final = {skill["path"]: skill for skill in self.steps[-1]["skills"]}
        playbook = final["private/budget-season-coordination"]
        self.assertEqual(playbook["usage"]["created_by"], "learn")
        self.assertGreaterEqual(playbook["usage"]["patch_count"], 5)
        self.assertIsNone(final["role/delivery-commitment-control"]["usage"]["created_by"])

    def test_sample_contains_nothing_that_looks_like_a_credential(self):
        self.assertEqual(sum(step["redactions"] for step in self.steps), 0)


COLLECT_ENV = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
SECRET = "TOP-SECRET-MUST-NOT-BE-COLLECTED"
# Stand-in for symbolic links on Windows machines without the symlink privilege: the collector's own checks still run.
SIMULATED_LINKS = (
    "import pathlib\n_is_link = pathlib.Path.is_symlink\n"
    "pathlib.Path.is_symlink = lambda self: self.name.startswith('link-') or self.name == '.curator_ledger.jsonl' or _is_link(self)\n"
)


class CollectorTests(unittest.TestCase):
    """Runs the sandbox collector for real against a fake Hermes home on this machine."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / "hermes"
        self.home.mkdir()

    def put(self, relative: str, content: str | bytes = "x") -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def collect(self, prelude: str = "") -> dict:
        self.assertIn('"/data/hermes"', COLLECT_SCRIPT)
        script = COLLECT_SCRIPT.replace('"/data/hermes"', repr(str(self.home)))
        self.assertNotEqual(script, COLLECT_SCRIPT)
        self.assertNotIn("/data/hermes", script.replace("/data/hermes/skills", ""))
        result = subprocess.run(
            [sys.executable, "-c", prelude + script], capture_output=True, text=True, timeout=120, env=COLLECT_ENV,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        collected = decode_collected(result.stdout)
        self.assertNotIn(SECRET, json.dumps(collected))
        return collected

    def test_collector_source_is_read_only(self):
        for forbidden in (
            "os.remove", "unlink", "rmdir", "rmtree", "shutil", "subprocess", "os.system", "write_text", "write_bytes", ".write(",
            "mkdir", "rename", "chmod", "socket", "urllib", "http", "requests", "environ", "getenv",
        ):
            self.assertNotIn(forbidden, COLLECT_SCRIPT, forbidden)
        for opened in re.findall(r"open\(([^)]*)\)", COLLECT_SCRIPT):
            self.assertIn('"rb"', opened)

    def test_empty_home_yields_an_empty_capture(self):
        collected = self.collect()
        self.assertEqual((collected["memory"], collected["skills"], collected["receipts"]), ([], [], []))
        self.assertEqual(collected["ledger"], {"total": 0, "truncated": False, "entries": []})
        self.assertIsNone(collected["usage"])
        self.assertEqual(collected["bundledManifest"], "")
        self.assertIsNone(collected["identity"])
        self.assertIsInstance(collected["collectedAt"], int)

    def test_only_allowlisted_files_are_read(self):
        for name in (".env", "config.yaml", "state.db", "auth.json", "sessions/log.jsonl", "logs/gateway.log", "learning/other.json"):
            self.put(name, SECRET)
        self.put("memories/MEMORY.md", "one\n§\ntwo")
        self.put("memories/USER.md", "profile")
        self.put("memories/notes.txt", SECRET)
        self.put("memories/.hidden.md.bak", SECRET)
        self.put("SOUL.md", SECRET)
        self.put("skills/private/alpha/SKILL.md", "# Alpha\n")
        self.put("skills/private/alpha/references/guide.md", "guide")
        self.put("skills/private/alpha/node_modules/pkg/index.js", SECRET)
        self.put("skills/private/alpha/__pycache__/x.pyc", SECRET)
        self.put("skills/.hub/SKILL.md", SECRET)
        self.put("skills/.hub/cache/SKILL.md", SECRET)
        self.put("skills/private/no-skill-file/README.md", SECRET)
        self.put("skills/.bundled_manifest", "generated-web-app:abc\n")
        collected = self.collect()
        self.assertEqual([item["file"] for item in collected["memory"]], ["MEMORY.md", "USER.md"])
        self.assertEqual([skill["path"] for skill in collected["skills"]], ["private/alpha"])
        self.assertEqual([item["path"] for item in collected["skills"][0]["files"]], ["references/guide.md"])
        self.assertEqual(collected["bundledManifest"], "generated-web-app:abc\n")
        self.assertEqual(set(collected["identity"]), {"bytes", "mtime", "sha256"})

    def test_skill_tree_is_walked_with_limits(self):
        self.put("skills/role/beta/SKILL.md", "# Beta\n")
        for number in range(15):
            self.put(f"skills/role/beta/references/{number:02d}.md", f"file {number}")
        self.put("skills/role/beta/assets/logo.bin", b"\x00\x01\x02" * 100)
        self.put("skills/role/beta/a/b/c/deep.md", "deep")
        self.put("skills/role/beta/a/b/c/d/too-deep.md", "too deep")
        self.put("skills/bundled-one/SKILL.md", "# Bundled\n")
        self.put("skills/role/beta/nested-skill/SKILL.md", "# Nested stays part of beta\n")
        collected = self.collect()
        skills = {skill["path"]: skill for skill in collected["skills"]}
        self.assertEqual(set(skills), {"role/beta", "bundled-one"})
        beta = skills["role/beta"]
        # deep.md, logo.bin, the nested SKILL.md and 15 references; nothing below the third level
        self.assertEqual(beta["fileCount"], 18)
        self.assertEqual(len(beta["files"]), 12)
        self.assertNotIn("a/b/c/d/too-deep.md", [item["path"] for item in beta["files"]])
        self.assertEqual(skills["bundled-one"]["files"], [])
        asset = next((item for item in beta["files"] if item["path"] == "assets/logo.bin"), None)
        self.assertIsNotNone(asset, "assets sort before references")
        self.assertTrue(asset["binary"] and "text" not in asset and asset["sha256"])
        self.assertIn("nested-skill/SKILL.md", [item["path"] for item in beta["files"]])

    def test_large_files_are_truncated_or_reported_as_oversize(self):
        self.put("memories/MEMORY.md", "m" * (100 * 1024))
        self.put("skills/private/big/SKILL.md", "---\nname: big\n---\n" + "s" * (100 * 1024))
        self.put("skills/private/big/references/long.md", "r" * (70 * 1024))
        self.put("skills/private/big/references/huge.bin", b"h" * (2 * 1024 * 1024 + 1))
        self.put("skills/private/huge-skill/SKILL.md", b"x" * (2 * 1024 * 1024 + 1))
        collected = self.collect()
        memory = collected["memory"][0]
        self.assertTrue(memory["truncated"])
        self.assertEqual(len(memory["text"]), 96 * 1024)
        self.assertEqual(memory["bytes"], 100 * 1024)
        self.assertEqual([skill["path"] for skill in collected["skills"]], ["private/big"])
        big = collected["skills"][0]
        self.assertTrue(big["truncated"] and len(big["text"]) == 96 * 1024)
        files = {item["path"]: item for item in big["files"]}
        self.assertTrue(files["references/long.md"]["truncated"])
        self.assertEqual(len(files["references/long.md"]["text"]), 64 * 1024)
        self.assertEqual(files["references/huge.bin"], {
            "bytes": 2 * 1024 * 1024 + 1, "mtime": files["references/huge.bin"]["mtime"], "oversize": True, "path": "references/huge.bin",
        })
        snapshot = build_snapshot(collected, label="Large", snapshot_id="001")
        huge = next(item for item in snapshot["skills"][0]["files"] if item["path"] == "references/huge.bin")
        self.assertTrue(huge["binary"] and huge["sha"] is None)
        self.assertTrue(snapshot["skills"][0]["truncated"] and snapshot["memory"]["memory"]["truncated"])

    def test_symbolic_links_are_never_followed(self):
        outside = Path(self.directory.name) / "outside.md"
        outside.write_text(SECRET, encoding="utf-8")
        outside_dir = Path(self.directory.name) / "outside-skills"
        (outside_dir / "stolen").mkdir(parents=True)
        (outside_dir / "stolen" / "SKILL.md").write_text(SECRET, encoding="utf-8")
        self.put("memories/MEMORY.md", "real")
        self.put("skills/private/safe/SKILL.md", "# Safe\n")
        self.put("skills/private/safe/references/real.md", "real")
        prelude = ""
        try:
            os.symlink(outside, self.home / "memories" / "USER.md")
            os.symlink(outside, self.home / "skills" / "private" / "safe" / "references" / "link-out.md")
            os.symlink(outside_dir / "stolen", self.home / "skills" / "private" / "link-stolen", target_is_directory=True)
            os.symlink(outside, self.home / "skills" / ".curator_ledger.jsonl")
        except OSError:
            self.put("memories/link-user.md", SECRET)
            self.put("skills/private/safe/references/link-out.md", SECRET)
            self.put("skills/private/link-stolen/SKILL.md", SECRET)
            self.put("skills/.curator_ledger.jsonl", json.dumps({"id": SECRET}) + "\n")
            prelude = SIMULATED_LINKS
        collected = self.collect(prelude)
        self.assertEqual([item["file"] for item in collected["memory"]], ["MEMORY.md"])
        self.assertEqual([skill["path"] for skill in collected["skills"]], ["private/safe"])
        self.assertEqual([item["path"] for item in collected["skills"][0]["files"]], ["references/real.md"])
        self.assertEqual(collected["ledger"]["total"], 0)

    def test_usage_prompt_index_and_receipts_are_loaded_with_limits(self):
        self.put("skills/.usage.json", json.dumps({"alpha": {"use_count": 2}}))
        self.put(".skills_prompt_snapshot.json", json.dumps({"version": 5, "skills": []}))
        self.put("learning/receipts/old.json", json.dumps({"n": 0}))
        self.put("learning/receipts/new.json", json.dumps({"n": 1}))
        self.put("learning/receipts/huge.json", json.dumps({"pad": "x" * (70 * 1024)}))
        self.put("learning/receipts/broken.json", "{oops")
        self.put("learning/receipts/readme.txt", SECRET)
        os.utime(self.home / "learning" / "receipts" / "old.json", (1_000_000, 1_000_000))
        collected = self.collect()
        self.assertEqual(collected["usage"]["value"], {"alpha": {"use_count": 2}})
        self.assertEqual(collected["promptIndex"]["value"]["version"], 5)
        self.assertEqual([item["name"] for item in collected["receipts"]], ["old.json", "new.json"])
        self.put(".skills_prompt_snapshot.json", "{oops")
        self.put("skills/.usage.json", "[1,")
        collected = self.collect()
        self.assertIsNone(collected["usage"])
        self.assertIsNone(collected["promptIndex"])

    def test_only_the_last_forty_receipts_are_kept(self):
        for number in range(45):
            path = self.put(f"learning/receipts/r{number:02d}.json", json.dumps({"n": number}))
            os.utime(path, (2_000_000 + number, 2_000_000 + number))
        names = [item["name"] for item in self.collect()["receipts"]]
        self.assertEqual(len(names), 40)
        self.assertEqual((names[0], names[-1]), ("r05.json", "r44.json"))

    def test_small_ledger_is_read_whole_and_bad_lines_are_skipped(self):
        lines = [json.dumps({"id": f"e{number}", "action": "patch"}) for number in range(3)]
        self.put("skills/.curator_ledger.jsonl", "\n".join(lines[:2] + ["{broken", lines[2]]) + "\n")
        ledger = self.collect()["ledger"]
        self.assertEqual((ledger["total"], ledger["truncated"]), (4, False))
        self.assertEqual([entry["id"] for entry in ledger["entries"]], ["e0", "e1", "e2"])

    def test_ledger_keeps_the_last_five_hundred_entries(self):
        self.put("skills/.curator_ledger.jsonl", "\n".join(json.dumps({"id": f"e{number:04d}"}) for number in range(600)))
        ledger = self.collect()["ledger"]
        self.assertEqual((ledger["total"], ledger["truncated"], len(ledger["entries"])), (600, False, 500))
        self.assertEqual((ledger["entries"][0]["id"], ledger["entries"][-1]["id"]), ("e0100", "e0599"))

    def test_ledger_tail_skips_the_partial_first_line_of_a_huge_file(self):
        padding = "p" * 180
        count = 12_000
        self.put(
            "skills/.curator_ledger.jsonl",
            "\n".join(json.dumps({"id": f"e{number:06d}", "pad": padding}) for number in range(count)) + "\n",
        )
        size = (self.home / "skills" / ".curator_ledger.jsonl").stat().st_size
        tail = 2 * 1024 * 1024
        self.assertGreater(size, tail)
        self.assertEqual(size % count, 0, "every line has the same length")
        line_length = size // count
        ledger = self.collect()["ledger"]
        self.assertTrue(ledger["truncated"])
        # The line that straddles the tail boundary is dropped whole; every later line is counted.
        self.assertEqual(ledger["total"], count - ((size - tail) // line_length + 1))
        self.assertEqual(len(ledger["entries"]), 500)
        self.assertEqual(ledger["entries"][-1]["id"], f"e{count - 1:06d}")
        self.assertEqual(int(ledger["entries"][0]["id"][1:]), count - 500)

    def test_a_real_collection_builds_a_snapshot_the_viewer_can_show(self):
        self.put("memories/MEMORY.md", "Note one\n§\nNote two")
        self.put("skills/.bundled_manifest", "interactive-ui:abc\n")
        self.put("skills/interactive-ui/SKILL.md", "---\nname: interactive-ui\ndescription: UI.\n---\n# UI\n")
        self.put("skills/private/mine/SKILL.md", SKILL_TEXT)
        self.put("skills/private/mine/references/a.md", "# A\n")
        self.put("skills/.usage.json", json.dumps({"demo-playbook": {"use_count": 7, "patch_count": 2, "state": "active"}}))
        entry = {
            "id": "abc123", "ts": "2026-11-24T08:00:00+00:00", "actor": "agent", "action": "create", "skill": "demo-playbook",
            "evidence": {"session_id": "invoke:1"}, "before": [],
            "after": [{"path": "/data/hermes/skills/private/mine/SKILL.md", "sha256": "x"}],
        }
        self.put("skills/.curator_ledger.jsonl", json.dumps(entry) + "\n")
        snapshot = build_snapshot(self.collect(), label="Fake home", snapshot_id="001")
        self.assertEqual(snapshot["memory"]["memory"]["entries"], ["Note one", "Note two"])
        self.assertEqual({skill["path"]: skill["namespace"] for skill in snapshot["skills"]}, {"interactive-ui": "bundled", "private/mine": "private"})
        mine = next(skill for skill in snapshot["skills"] if skill["path"] == "private/mine")
        self.assertEqual(mine["usage"]["use_count"], 7)
        self.assertEqual(mine["files"][0]["path"], "references/a.md")
        self.assertEqual(snapshot["ledger"][0]["files"], ["private/mine/SKILL.md"])


class FakeCapturer:
    def __init__(self) -> None:
        self.calls: list[bool] = []
        self.version = 0
        self.live_state = "Running"
        self.asleep = False
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.closed = False

    def state(self) -> str:
        return self.live_state

    def collect(self, *, wake: bool):
        self.calls.append(wake)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if self.asleep and not wake:
            raise OttoAsleep(self.live_state)
        was = self.live_state
        if wake:
            self.asleep, self.live_state = False, "Running"
        return raw_state(self.version), was

    def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def wait_idle(viewer: Viewer) -> None:
    deadline = time.time() + 10
    while (viewer.busy or viewer.lock.locked()) and time.time() < deadline:
        time.sleep(0.01)
    assert not viewer.busy and not viewer.lock.locked(), "the capture did not finish"


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name) / "timeline"
        self.capturer = FakeCapturer()
        self.clock = Clock()
        self.viewer = Viewer(self.folder, self.capturer, clock=self.clock)

    def test_capture_saves_a_step_with_the_given_label(self):
        self.assertTrue(self.viewer.live)
        self.assertTrue(self.viewer.start_capture("After scene 1", wake=False))
        wait_idle(self.viewer)
        self.assertEqual(self.viewer.last["id"], "001")
        self.assertTrue(self.viewer.last["saved"] and not self.viewer.last["unchanged"])
        self.assertEqual(self.capturer.calls, [False])
        saved = load_snapshots(self.folder)
        self.assertEqual((saved[0]["label"], saved[0]["source"], saved[0]["sandboxState"]), ("After scene 1", "live", "Running"))
        self.assertEqual(self.viewer.status()["count"], 1)
        self.assertEqual(self.viewer.timeline()["source"], "live")

    def test_manual_capture_of_unchanged_content_is_saved_but_says_so(self):
        self.viewer.start_capture("One", wake=False)
        wait_idle(self.viewer)
        self.viewer.start_capture("", wake=False)
        wait_idle(self.viewer)
        self.assertTrue(self.viewer.last["saved"] and self.viewer.last["unchanged"])
        self.assertIn("nothing changed", self.viewer.last["message"])
        self.assertTrue(load_snapshots(self.folder)[1]["label"].startswith("Capture "))

    def test_only_one_capture_runs_at_a_time(self):
        self.capturer.gate = threading.Event()
        self.assertTrue(self.viewer.start_capture("First", wake=False))
        self.assertTrue(self.capturer.entered.wait(5))
        self.assertTrue(self.viewer.busy)
        self.assertFalse(self.viewer.start_capture("Second", wake=False))
        self.capturer.gate.set()
        wait_idle(self.viewer)
        self.assertEqual(len(load_snapshots(self.folder)), 1)
        self.assertTrue(self.viewer.start_capture("Third", wake=False))
        wait_idle(self.viewer)

    def test_auto_capture_skips_unchanged_content(self):
        self.viewer.set_auto(30)
        with self.assertRaises(ValueError):
            self.viewer.set_auto(45)
        self.viewer.tick()
        wait_idle(self.viewer)
        self.assertEqual(self.viewer.sandbox_state, "Running")
        self.assertEqual(len(load_snapshots(self.folder)), 0, "not due yet")
        for expected_saved, version in ((True, 0), (False, 0), (True, 1)):
            self.clock.now += 31
            self.capturer.version = version
            self.viewer.tick()
            wait_idle(self.viewer)
            self.assertEqual(self.viewer.last["saved"], expected_saved, self.viewer.last)
        self.assertEqual(len(load_snapshots(self.folder)), 2)
        self.assertTrue(load_snapshots(self.folder)[0]["label"].startswith("Auto "))
        self.assertEqual(self.capturer.calls, [False, False, False], "automatic captures never wake Otto")

    def test_auto_capture_never_runs_while_otto_is_not_running(self):
        self.capturer.live_state = "Suspended"
        self.viewer.set_auto(30)
        self.clock.now += 100
        self.viewer.tick()
        wait_idle(self.viewer)
        self.assertEqual(self.viewer.sandbox_state, "Suspended")
        self.assertEqual(self.capturer.calls, [])

    def test_a_sleeping_otto_is_not_woken_unless_asked(self):
        self.capturer.asleep, self.capturer.live_state = True, "Suspended"
        self.viewer.start_capture("Quiet", wake=False)
        wait_idle(self.viewer)
        last = self.viewer.last
        self.assertTrue(last["asleep"] and not last["ok"])
        self.assertIn("idle timer", last["message"])
        self.assertEqual(self.viewer.sandbox_state, "Suspended")
        self.assertEqual(load_snapshots(self.folder), [])
        self.viewer.start_capture("Wake", wake=True)
        wait_idle(self.viewer)
        self.assertTrue(self.viewer.last["ok"])
        self.assertEqual(self.capturer.calls, [False, True])
        self.assertEqual(load_snapshots(self.folder)[0]["sandboxState"], "Suspended")
        self.assertEqual(self.viewer.sandbox_state, "Running")

    def test_failures_are_reported_and_do_not_stop_the_viewer(self):
        with patch.object(self.capturer, "collect", side_effect=RuntimeError("boom " * 200)):
            self.viewer.start_capture("Fails", wake=False)
            wait_idle(self.viewer)
        self.assertFalse(self.viewer.last["ok"])
        self.assertTrue(self.viewer.last["message"].startswith("RuntimeError: boom"))
        self.assertLessEqual(len(self.viewer.last["message"]), 330)
        self.assertTrue(self.viewer.start_capture("Retry", wake=False))
        wait_idle(self.viewer)
        self.assertTrue(self.viewer.last["ok"])

    def test_state_errors_are_surfaced(self):
        with patch.object(self.capturer, "state", side_effect=OSError("offline")):
            self.viewer.tick()
        self.assertEqual(self.viewer.sandbox_state, "Unknown")
        self.assertIn("OSError: offline", self.viewer.status()["stateError"])
        self.viewer.tick()
        self.assertEqual(self.viewer.sandbox_state, "Unknown", "polled at most every 20 seconds")
        self.clock.now += 21
        self.viewer.tick()
        self.assertEqual((self.viewer.sandbox_state, self.viewer.state_error), ("Running", ""))

    def test_sample_viewer_is_read_only(self):
        sample = Viewer(self.folder, None, source="sample", snapshots=sample_snapshots())
        self.assertFalse(sample.live)
        with self.assertRaises(RuntimeError):
            sample.start_capture("No", wake=False)
        self.assertFalse(sample.rename("001", "Nope"))
        self.assertEqual(len(sample.timeline()["snapshots"]), 10)
        self.assertEqual(sample.status()["count"], 10)
        sample.tick()
        sample.close()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.capturer = FakeCapturer()
        self.viewer = Viewer(Path(self.directory.name), self.capturer, clock=Clock())
        patcher = patch.object(learning_view, "render_html", return_value="<!doctype html><title>stub</title>")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.port = self.serve(self.viewer)

    def serve(self, viewer: Viewer) -> int:
        server = make_server(viewer, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(5)

        self.addCleanup(stop)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        return server.server_address[1]

    def call(self, method: str, path: str, body=None, *, port: int | None = None, headers: dict | None = None, raw: bytes | None = None):
        port = port or self.port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            sent = dict(headers or {})
            if method == "POST":
                sent.setdefault("Origin", f"http://127.0.0.1:{port}")
                sent.setdefault("Content-Type", "application/json")
            connection.request(method, path, body=payload, headers=sent)
            response = connection.getresponse()
            data = response.read()
            try:
                parsed = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data.decode()
            except ValueError:
                parsed = data.decode()
            return response.status, response, parsed
        finally:
            connection.close()

    def test_page_and_data_endpoints_carry_the_security_headers(self):
        status, response, body = self.call("GET", "/")
        self.assertEqual((status, response.getheader("Content-Type")), (200, "text/html; charset=utf-8"))
        self.assertIn("stub", body)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
        policy = response.getheader("Content-Security-Policy")
        for directive in ("default-src 'none'", "script-src 'unsafe-inline'", "connect-src 'self'", "frame-ancestors 'none'"):
            self.assertIn(directive, policy)
        status, _, timeline = self.call("GET", "/api/timeline?x=1")
        self.assertEqual((status, timeline["schema"]), (200, "otto-learning-timeline/1"))
        status, _, state = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(
            {"live", "busy", "count", "sandboxState", "stateError", "auto", "autoChoices", "last", "now"} - set(state), set(),
        )
        self.assertTrue(state["live"])
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("DELETE", "/")[0], 501)

    def test_only_the_loopback_host_names_are_accepted(self):
        self.assertEqual(self.call("GET", "/", headers={"Host": f"localhost:{self.port}"})[0], 200)
        for host in ("evil.example", f"127.0.0.1:{self.port + 1}", "127.0.0.1", f"[::1]:{self.port}"):
            self.assertEqual(self.call("GET", "/api/status", headers={"Host": host})[0], 421, host)
            self.assertEqual(self.call("POST", "/api/capture", {}, headers={"Host": host})[0], 421, host)
        self.assertEqual(self.capturer.calls, [])

    def test_posts_need_the_same_origin_json_and_a_small_body(self):
        origin = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.call("POST", "/api/capture", {}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/capture", {}, headers={"Origin": f"http://localhost:{self.port}"})[0], 403)
        self.assertEqual(self.call("POST", "/api/capture", {}, headers={"Origin": "null"})[0], 403)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", "/api/capture", body=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(connection.getresponse().status, 403, "a missing Origin is not accepted")
        connection.close()
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            self.assertEqual(self.call("POST", "/api/capture", {}, headers={"Origin": origin, "Content-Type": content_type})[0], 415)
        status, _, error = self.call("POST", "/api/capture", {"label": "x" * (MAX_BODY + 10)})
        self.assertEqual(status, 413, error)
        self.assertEqual(self.call("POST", "/api/capture", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/capture", raw=b"[1, 2]")[0], 400)
        self.assertEqual(self.call("POST", "/api/capture", headers={"Content-Length": "abc"}, raw=b"{}")[0], 400)
        self.assertEqual(self.call("POST", "/api/unknown", {})[0], 404)
        self.assertEqual(self.call("POST", "/api/capture", {}, headers={"Content-Type": "Application/JSON; charset=utf-8"})[0], 202)
        wait_idle(self.viewer)
        self.assertEqual(self.capturer.calls, [False])

    def test_capture_is_accepted_once_and_a_second_request_conflicts(self):
        self.capturer.gate = threading.Event()
        status, _, body = self.call("POST", "/api/capture", {"label": "Scene 1"})
        self.assertEqual((status, body), (202, {"accepted": True}))
        self.assertTrue(self.capturer.entered.wait(5))
        self.assertEqual(self.call("GET", "/api/status")[2]["busy"], True)
        status, _, body = self.call("POST", "/api/capture", {"label": "Scene 2"})
        self.assertEqual(status, 409)
        self.assertIn("already running", body["error"])
        self.capturer.gate.set()
        wait_idle(self.viewer)
        state = self.call("GET", "/api/status")[2]
        self.assertEqual((state["busy"], state["count"], state["last"]["saved"]), (False, 1, True))
        self.assertEqual(self.call("GET", "/api/timeline")[2]["snapshots"][0]["label"], "Scene 1")

    def test_wake_must_be_the_json_boolean_true(self):
        for value in ("true", 1, "yes", None, [True]):
            self.assertEqual(self.call("POST", "/api/capture", {"wake": value})[0], 202)
            wait_idle(self.viewer)
        self.assertEqual(self.capturer.calls, [False] * 5)
        self.assertEqual(self.call("POST", "/api/capture", {"wake": True})[0], 202)
        wait_idle(self.viewer)
        self.assertEqual(self.capturer.calls[-1], True)

    def test_labels_are_limited_to_120_characters(self):
        self.call("POST", "/api/capture", {"label": "L" * 300})
        wait_idle(self.viewer)
        self.assertEqual(len(self.call("GET", "/api/timeline")[2]["snapshots"][0]["label"]), 120)

    def test_a_sleeping_otto_is_reported_and_woken_only_on_request(self):
        self.capturer.asleep, self.capturer.live_state = True, "Suspended"
        self.assertEqual(self.call("POST", "/api/capture", {"label": "Peek"})[0], 202)
        wait_idle(self.viewer)
        state = self.call("GET", "/api/status")[2]
        self.assertTrue(state["last"]["asleep"])
        self.assertFalse(state["last"]["ok"])
        self.assertEqual(state["sandboxState"], "Suspended")
        self.assertEqual(state["count"], 0)
        self.assertEqual(self.call("POST", "/api/capture", {"label": "Wake", "wake": True})[0], 202)
        wait_idle(self.viewer)
        state = self.call("GET", "/api/status")[2]
        self.assertTrue(state["last"]["ok"])
        self.assertEqual((state["count"], state["sandboxState"]), (1, "Running"))
        self.assertEqual(self.capturer.calls, [False, True])

    def test_label_and_auto_endpoints(self):
        self.call("POST", "/api/capture", {"label": "Before"})
        wait_idle(self.viewer)
        status, _, body = self.call("POST", "/api/label", {"id": "001", "label": "After rename"})
        self.assertEqual((status, body), (200, {"renamed": True}))
        self.assertEqual(self.call("GET", "/api/timeline")[2]["snapshots"][0]["label"], "After rename")
        self.assertEqual(self.call("POST", "/api/label", {"id": "042", "label": "Missing"})[0], 404)
        self.assertEqual(self.call("POST", "/api/auto", {"seconds": 60}), (200, unittest.mock.ANY, {"auto": 60}))
        self.assertEqual(self.call("GET", "/api/status")[2]["auto"], 60)
        for value in (45, "x", None, [1]):
            self.assertEqual(self.call("POST", "/api/auto", {"seconds": value})[0], 400, value)
        self.assertEqual(self.call("POST", "/api/auto", {"seconds": 0})[2], {"auto": 0})

    def test_a_saved_data_viewer_refuses_live_actions(self):
        sample = Viewer(Path(self.directory.name), None, source="sample", snapshots=sample_snapshots())
        port = self.serve(sample)
        status, _, body = self.call("POST", "/api/capture", {}, port=port)
        self.assertEqual(status, 409)
        self.assertIn("saved data", body["error"])
        self.assertEqual(self.call("POST", "/api/auto", {"seconds": 30}, port=port)[0], 409)
        self.assertEqual(self.call("POST", "/api/label", {"id": "001", "label": "x"}, port=port)[0], 404)
        status, _, state = self.call("GET", "/api/status", port=port)
        self.assertEqual((status, state["live"], state["count"]), (200, False, 10))
        timeline = self.call("GET", "/api/timeline", port=port)[2]
        self.assertEqual((timeline["source"], len(timeline["snapshots"])), ("sample", 10))


class CommandLineTests(unittest.TestCase):
    def run_main(self, *argv: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = learning_view.main(list(argv))
        return code, output.getvalue()

    def test_summary_of_the_sample_needs_no_azure(self):
        code, text = self.run_main("summary", "--sample")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("sample timeline: 10 snapshot(s)"))
        self.assertIn("Week 3 · Public context check", text)

    def test_render_writes_a_standalone_page(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "viewer.html"
            code, text = self.run_main("render", "--sample", "--out", str(target))
            self.assertEqual(code, 0)
            self.assertIn("Wrote", text)
            html = target.read_text(encoding="utf-8")
        self.assertIn('id="otto-data"', html)
        self.assertNotIn("@inline", html)

    def test_capture_from_a_raw_file_and_render_the_saved_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_file = Path(directory) / "raw.json"
            raw_file.write_text(json.dumps(raw_state()), encoding="utf-8")
            folder = Path(directory) / "steps"
            code, text = self.run_main(
                "--directory", str(folder), "capture", "--from-raw", str(raw_file), "--label", "Baseline", "--note", "Start.",
            )
            self.assertEqual(code, 0)
            self.assertIn("001-baseline.json", text)
            saved = load_snapshots(folder)
            self.assertEqual((saved[0]["source"], saved[0]["note"], saved[0]["sandboxState"]), ("import", "Start.", ""))
            self.assertIn("live timeline: 1 snapshot(s)", self.run_main("--directory", str(folder), "summary")[1])
            target = Path(directory) / "out.html"
            self.assertEqual(self.run_main("--directory", str(folder), "render", "--out", str(target))[0], 0)
            self.assertIn("Baseline", target.read_text(encoding="utf-8"))

    def test_capture_refuses_to_wake_a_sleeping_otto_without_the_flag(self):
        with patch.object(learning_view, "SandboxCapturer") as capturer_type:
            capturer_type.return_value.collect.side_effect = OttoAsleep("Suspended")
            with tempfile.TemporaryDirectory() as directory:
                code, text = self.run_main("--directory", directory, "capture")
                self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(code, 2)
            self.assertIn("--wake", text)
            capturer_type.return_value.collect.assert_called_once_with(wake=False)
            capturer_type.return_value.close.assert_called_once()

    def test_capture_with_wake_passes_the_flag_on(self):
        with patch.object(learning_view, "SandboxCapturer") as capturer_type:
            capturer_type.return_value.collect.return_value = (raw_state(), "Suspended")
            with tempfile.TemporaryDirectory() as directory:
                code, _ = self.run_main("--directory", directory, "capture", "--wake", "--label", "Woken")
                saved = load_snapshots(Path(directory))
            self.assertEqual(code, 0)
            capturer_type.return_value.collect.assert_called_once_with(wake=True)
            self.assertEqual((saved[0]["label"], saved[0]["source"], saved[0]["sandboxState"]), ("Woken", "live", "Suspended"))


if __name__ == "__main__":
    unittest.main()
