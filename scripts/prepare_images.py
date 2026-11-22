"""Merge local authorized Azure settings with a public creative brief, without inference."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
brief = yaml.safe_load((ROOT / "brand/brief.yaml").read_text(encoding="utf-8"))
brief["azure"] = json.loads((ROOT / ".local/image-settings.json").read_text(encoding="utf-8"))
brief["output_dir"] = str(ROOT / ".local/image-output")
target = ROOT / ".local/image-brief.json"
target.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
print(target)
