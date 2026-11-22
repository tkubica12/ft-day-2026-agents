import copy
import importlib.util
import json
import hashlib
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
            self.assertEqual(parsed.media, [])
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
