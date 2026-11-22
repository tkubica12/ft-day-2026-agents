"""Merge local authorized Azure settings with a public creative brief, without inference."""
import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief", type=Path, nargs="?", default=ROOT / "brand/brief.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/image-brief.json")
    args = parser.parse_args()
    brief = yaml.safe_load(args.brief.read_text(encoding="utf-8"))
    brief["azure"] = json.loads((ROOT / ".local/image-settings.json").read_text(encoding="utf-8"))
    brief["output_dir"] = str(ROOT / ".local/image-output")
    args.output.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
