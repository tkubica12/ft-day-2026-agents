import copy
import importlib.util
import json
import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.media = [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag in ("img", "video", "source"):
            self.media.append(attrs)


class FoundationTests(unittest.TestCase):
    def test_chair_is_compact_transparent_vector_with_identical_reverse_geometry(self):
        from PIL import Image
        import numpy as np

        assets = ROOT / "docs/assets/javorna"
        variants = [(assets / name).read_bytes() for name in ("chair.svg", "chair-on-dark.svg")]
        trees = [ET.fromstring(svg) for svg in variants]
        for svg, tree in zip(variants, trees):
            self.assertLess(len(svg), 10_000)
            self.assertEqual({el.tag.split("}")[-1] for el in tree.iter()}, {"svg", "title", "path"})
            self.assertNotIn(b"data:", svg)
        self.assertEqual([p.attrib["d"] for p in trees[0] if p.tag.endswith("path")],
                         [p.attrib["d"] for p in trees[1] if p.tag.endswith("path")])
        source = assets / "chair-original.png"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         "f0f04939aed657e9d996b9c8e578ee3369967dec05b4c29925ae67bcd48887df")
        subprocess.run(["node", str(ROOT / "tests/render_chair.cjs")], check=True)
        raster = np.asarray(Image.open(source).convert("RGB"))
        vector = np.asarray(Image.open(ROOT / ".local/vector-shots/vector-native.png").convert("RGBA"))
        original_mask = raster.min(axis=2) < 190
        vector_mask = vector[:, :, 3] > 127
        overlap = (original_mask & vector_mask).sum() / (original_mask | vector_mask).sum()
        self.assertGreater(overlap, .99, "silhouette including the open joinery must match the approved source")
        self.assertEqual(int(vector[0, 0, 3]), 0, "no rectangular background")
        self.assertEqual(int(vector[860, 1390, 3]), 0, "armrest opening must remain transparent")

    def test_widget_exports_embed_only_svg_and_fit_html_budget(self):
        builder = load("vector_review", "scripts/build_vector_review.py")
        for name, markup in builder.widgets():
            with self.subTest(widget=name):
                document = builder.widget_file(markup)
                self.assertLess(len(document.encode("utf-8")), 48_000)
                media = Document(document).media
                self.assertGreater(len(media), 0)
                self.assertTrue(all(m["src"].startswith("data:image/svg+xml;base64,") for m in media))
                self.assertNotIn("<script", document)
                self.assertIn("img-src data:", document)
                self.assertNotIn("growth-rings", document)
                if name in {"decision", "status", "kickoff"}:
                    self.assertIn("chair-on-dark.svg", markup)
        commission = builder.widget_file(builder.commission_types())
        self.assertLess(len(commission.encode("utf-8")), 48_000)
        self.assertEqual(commission.count('class="commission-option"'), 3)
        self.assertEqual(commission.count("data:image/svg+xml;base64,"), 3)
        self.assertIn("Bydlení a domácnosti", commission)
        self.assertIn("Firmy a veřejné prostory", commission)
        self.assertIn("Umělecké a speciální zakázky", commission)
        with self.assertRaisesRegex(ValueError, "only approved local SVG"):
            builder.inline_svg_images('<img src="assets/javorna/atelier.png">')

    def test_furniture_studies_keep_six_static_slides_and_readable_brand_pairs(self):
        builder = load("furniture", "scripts/build_furniture_review.py")
        palettes = json.loads((ROOT / "brand/furniture-palettes.json").read_text(encoding="utf-8"))
        images = {p["image"]: f"brand-candidates/{p['image']}.png" for p in palettes}
        gallery, deck = builder.build(palettes, images, "brand-candidates/atelier.png")
        parsed = Document(deck)
        self.assertEqual(len(parsed.ids), 6)
        self.assertEqual(len(set(parsed.ids)), 6)
        self.assertEqual(len(parsed.media), 9)
        self.assertIn("logo B s paletou A", gallery)
        self.assertEqual(deck.count("A goal outlives a conversation."), 3)
        self.assertNotIn("<iframe", gallery)

        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
            return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))

        for palette in palettes:
            for foreground, background in (("paper", "wood"), ("muted", "wood"), ("leaf", "wood"),
                                           ("wood", "paper"), ("paper", "pine"), ("pine", "paper"),
                                           ("paper", "deep")):
                with self.subTest(palette=palette["id"], foreground=foreground, background=background):
                    values = sorted((luminance(palette[foreground]), luminance(palette[background])))
                    self.assertGreaterEqual((values[1] + .05) / (values[0] + .05), 4.5)

    def test_logo_review_marks_three_finalists_and_keeps_widgets_offline_and_readable(self):
        builder = load("logo_review", "scripts/build_logo_review.py")
        palette = next(p for p in json.loads((ROOT / "brand/furniture-palettes.json").read_text(encoding="utf-8"))
                       if p["id"] == "C")
        names = [name for _, entries in builder.ROUNDS for name, _ in entries]
        self.assertEqual(len(names), 16)
        self.assertEqual(len(set(names)), 16)
        finalists = {name for name, *_ in builder.FINALISTS}
        chosen = {name for _, entries in builder.ROUNDS for name, verdict in entries if verdict.startswith("Finalista")}
        self.assertEqual(chosen, finalists)
        gallery, deck = builder.build(palette, {n: f"brand-candidates/{n}.png" for n in names}, "brand-candidates/hero.png")
        parsed = Document(deck)
        self.assertEqual(len(parsed.ids), 6)
        self.assertEqual(len(set(parsed.ids)), 6)
        self.assertEqual(gallery.count('class="logo-card"'), 3)
        self.assertEqual(gallery.count('<figure class="iteration'), 16)
        self.assertEqual(gallery.count('<article class="widget '), 3)
        widgets = gallery[gallery.index('id="widgets"'):gallery.index('id="iterations"')]
        self.assertNotIn("http", widgets)
        self.assertTrue(all(src.startswith("brand-candidates/") for src in
                            (m["src"] for m in Document(widgets).media)))

        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
            return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))

        css = (ROOT / "docs/assets/logo-review.css").read_text(encoding="utf-8")
        tokens = dict(re.findall(r"--(wood|deep|paper|muted|leaf|pine|oak):(#[0-9A-F]{6})", css))
        for foreground, background in (("oak", "paper"), ("pine", "paper"), ("paper", "pine"),
                                       ("leaf", "wood"), ("muted", "wood"), ("paper", "wood")):
            with self.subTest(foreground=foreground, background=background):
                values = sorted((luminance(tokens[foreground]), luminance(tokens[background])))
                self.assertGreaterEqual((values[1] + .05) / (values[0] + .05), 4.5)

    def test_review_originals_are_verified_before_copying(self):
        from PIL import Image
        from unittest.mock import patch
        import build_brand_review as review

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runs = root / "output/runs"
            runs.mkdir(parents=True)
            source = root / "output/original.png"
            Image.new("RGB", (2048, 2048), "white").save(source)
            original = source.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            manifest = {"run_id": "test", "summary": {"failed": 0}, "records": [{
                "lineage": {"image": "chair"}, "original": "original.png", "image_sha256": digest
            }]}
            path = runs / "test.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(review, "ROOT", root):
                _, images = review.load_images(path, {"chair"})
                self.assertEqual((root / "docs" / images["chair"]).read_bytes(), original)
                with self.assertRaises(ValueError):
                    review.load_images(path, {"chair", "missing"})
                source.write_bytes(b"corrupted")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    review.load_images(path, {"chair"})
                self.assertEqual((root / "docs" / images["chair"]).read_bytes(), original)

    def test_cutter_preserves_source_and_encodes_real_segments(self):
        cutter = load("cutter_integration", "video-production/build_clips.py")
        with tempfile.TemporaryDirectory() as temp:
            cutter.RAW = Path(temp) / "raw"
            cutter.OUT = Path(temp) / "out"
            cutter.RAW.mkdir()
            raw = cutter.RAW / "01.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "testsrc2=size=320x180:rate=30", "-t", "2", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(raw)], check=True)
            original = hashlib.sha256(raw.read_bytes()).hexdigest()
            edl = {"fps": 30, "crf": 16, "preset": "fast", "clips": [{
                "id": "01", "segments": [
                    {"src": "01", "from": 0, "to": 1},
                    {"src": "01", "from": 1, "to": 2, "speed": 2, "badge": "Wait 2x faster", "hold": 0.4}
                ]}]}
            cutter.validate(edl)
            output = cutter.build(edl["clips"][0], edl)
            result = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
                check=True, capture_output=True, text=True).stdout)
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), original)
            self.assertEqual((result["streams"][0]["width"], result["streams"][0]["height"]), (320, 180))
            self.assertAlmostEqual(float(result["format"]["duration"]), 1.9, delta=0.07)

    def test_scenario_renders_all_prompts_without_old_media(self):
        builder = load("builder", "scripts/build_docs.py")
        data = json.loads((ROOT / "scenario/demo.json").read_text(encoding="utf-8"))
        deck, guide = builder.build(data)
        for scene in data["scenes"]:
            self.assertIn(f's-recording-{scene["id"]}', deck)
            if scene["prompt"]:
                self.assertIn(builder.esc(scene["prompt"]), guide)
        for text in (deck, guide):
            parsed = Document(text)
            self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
            self.assertTrue(all(m["src"].startswith("assets/javorna/") for m in parsed.media))
            self.assertNotIn("<video", text)
            self.assertNotIn("brand-candidates/", text)
            self.assertIn("Not recorded yet" if text == deck else "pending", text)

    def test_edl_rejects_unsafe_or_misleading_segments(self):
        cutter = load("cutter", "video-production/build_clips.py")
        valid = {"clips": [{"id": "01", "segments": [{"src": "01", "from": 0, "to": 1}]}]}
        cutter.validate(valid)
        for patch in ({"src": "../secret"}, {"to": -1}, {"speed": 20}, {"hold": -1}, {"to": float("nan")}):
            with self.subTest(patch=patch):
                invalid = copy.deepcopy(valid)
                invalid["clips"][0]["segments"][0].update(patch)
                with self.assertRaises(ValueError):
                    cutter.validate(invalid)
        invalid = copy.deepcopy(valid)
        invalid["clips"].append(copy.deepcopy(valid["clips"][0]))
        with self.assertRaises(ValueError):
            cutter.validate(invalid)


if __name__ == "__main__":
    unittest.main()
