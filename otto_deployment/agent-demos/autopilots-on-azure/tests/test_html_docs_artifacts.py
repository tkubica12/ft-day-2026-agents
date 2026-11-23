from __future__ import annotations

import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "runtimes" / "hermes" / "runtime_skills" / "visual-artifacts"
module_spec = importlib.util.spec_from_file_location("otto_html_docs", SKILL / "build.py")
builder = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(builder)


def spec(kind="report"):
    return {
        "kind": kind, "title": "Week 7 budget", "subtitle": "Fictional snapshot",
        "closing": "Decide next steps.", "source": "Snapshot November 12, 2026",
        "sections": [{"heading": "Romania", "body": "EBITDA €44.66m.", "cues": ["EBITDA is below plan"]}],
    }


class HtmlDocsArtifactTests(unittest.TestCase):
    def test_rejects_untrusted_markup_and_urls(self):
        for payload in ("<script>alert(1)</script>", '<img src=x onerror="alert(1)">',
                        "https://example.test/x", "javascript:alert(1)", "www.example.test"):
            with self.subTest(payload=payload):
                value = spec()
                value["sections"][0]["body"] = payload
                with self.assertRaises(ValueError):
                    builder.validate(value)

    def test_builds_self_contained_document(self):
        for kind in ("report", "slides", "brief"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "output.html"
                builder.build(spec(kind), target)
                content = target.read_text(encoding="utf-8")
                logo = base64.b64encode((SKILL / "assets" / "drmax-group-logo.svg").read_bytes()).decode()
                self.assertIn(logo, content)
                self.assertIn(builder.BRAND["disclaimer"], content)
                self.assertNotIn('src="assets/', content)
                self.assertNotIn('href="assets/', content)
                self.assertIn("Permission is hereby granted", content)
                self.assertIn('<html lang="en"', content)
                self.assertIn('aria-label="Document controls"', content)
                self.assertIn("Fictional Medivia demo data — not Dr.Max data", content)
                self.assertIn('data-default-accent="green"', content)
                self.assertIn('data-action="toggle-slides"' if kind != "brief" else "sheet-page", content)
                if kind != "brief":
                    self.assertIn(">Expand all</button>", content)
                    self.assertIn(">Collapse all</button>", content)
                    self.assertIn('class="chapter-label">Overview</h2>', content)
                    self.assertIn('class="slide-eyebrow">Takeaway</p>', content)

    def test_rejects_unexpected_fields_and_oversized_sheet(self):
        value = spec("brief")
        value["recipient"] = "person@example.test"
        with self.assertRaises(ValueError):
            builder.validate(value)
        value = spec("brief")
        value["sections"][0]["body"] = "x" * 301
        with self.assertRaises(ValueError):
            builder.validate(value)
