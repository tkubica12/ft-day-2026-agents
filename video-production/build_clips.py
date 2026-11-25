"""Build edited demo clips from the raw recordings according to edl.json.

Raw recordings are only read. Each segment is re-encoded once at original resolution; accelerated waits get
an on-screen badge so the time compression is visible to the audience.
"""
import json
import argparse
import math
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "video-raw"
RAW_FALLBACK = ROOT / ".local" / "raw-videos"
OUT = ROOT / "docs" / "video"
FONT = "C\\:/Windows/Fonts/seguisb.ttf"


def source_path(src: str) -> Path:
    primary = RAW / f"{src}.mp4"
    if primary.exists():
        return primary
    fallback = RAW_FALLBACK / f"{src}.mp4"
    if fallback.exists():
        return fallback
    return primary


def validate(edl: dict) -> None:
    ids = set()
    for clip in edl["clips"]:
        cid = clip["id"]
        if not re.fullmatch(r"[0-9]{2}", cid) or cid in ids:
            raise ValueError(f"Clip ID must be unique and two digits: {cid}")
        ids.add(cid)
        if not clip["segments"]:
            raise ValueError(f"{cid}: no segments")
        for seg in clip["segments"]:
            if not re.fullmatch(r"[0-9]{2}(?:-[0-9a-z]+)?", seg["src"]):
                raise ValueError(f"{cid}: invalid source ID")
            values = [seg["from"], seg["to"], seg.get("speed", 1), seg.get("hold", 0)]
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError(f"{cid}: segment values must be finite numbers")
            if not 0 <= seg["from"] < seg["to"] or seg.get("speed", 1) <= 0 or seg.get("hold", 0) < 0:
                raise ValueError(f"{cid}: invalid segment bounds, speed or hold")
            if seg.get("speed", 1) != 1 and not seg.get("badge"):
                raise ValueError(f"{cid}: accelerated footage requires a visible badge")


def probe_size(src: str) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(source_path(src))],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


def build(clip: dict, edl: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{clip['id']}.mp4"
    base_w, base_h = probe_size(clip["segments"][0]["src"])
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    chains = []
    for i, seg in enumerate(clip["segments"]):
        args += ["-ss", f"{seg['from']:.3f}", "-to", f"{seg['to']:.3f}", "-i", str(source_path(seg["src"]))]
        f = ["setpts=PTS-STARTPTS"]
        speed = seg.get("speed", 1)
        if speed != 1:
            f.append(f"setpts=PTS/{speed}")
        f.append(f"fps={edl['fps']}")
        w, h = probe_size(seg["src"])
        if (w, h) != (base_w, base_h):
            f.append(f"scale={base_w}:{base_h}:flags=lanczos")
        if seg.get("badge"):
            text = seg["badge"].replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:")
            f.append(
                f"drawtext=fontfile='{FONT}':text='{text}':fontsize=h*0.03:fontcolor=white:"
                f"box=1:boxcolor=0x1b1b1f@0.82:boxborderw=18:x=(w-tw)/2:y=h*0.012")
        f += ["setsar=1", "format=yuv420p"]
        if seg.get("hold"):
            f.append(f"tpad=stop_mode=clone:stop_duration={seg['hold']}")
        chains.append(f"[{i}:v]{','.join(f)}[s{i}]")
    n = len(clip["segments"])
    chains.append("".join(f"[s{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(";\n".join(chains))
        script = fh.name
    args += ["-filter_complex_script", script, "-map", "[v]", "-an",
             "-c:v", "libx264", "-crf", str(edl["crf"]), "-preset", edl["preset"], "-pix_fmt", "yuv420p",
             "-r", str(edl["fps"]), "-movflags", "+faststart", str(target)]
    try:
        subprocess.run(args, check=True)
    finally:
        Path(script).unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--check", action="store_true", help="validate EDL structure without rendering")
    options = parser.parse_args()
    edl = json.loads((Path(__file__).parent / "edl.json").read_text(encoding="utf-8"))
    validate(edl)
    only = set(options.ids)
    unknown = only - {clip["id"] for clip in edl["clips"]}
    if unknown:
        parser.error(f"Unknown clip IDs: {', '.join(sorted(unknown))}")
    if options.check:
        print(f"EDL valid: {len(edl['clips'])} clips; recordings pending." if not edl["clips"]
              else f"EDL valid: {len(edl['clips'])} clips.")
        return
    if not edl["clips"]:
        parser.error("No recordings configured. Add inspected source ranges to edl.json first.")
    for clip in edl["clips"]:
        if only and clip["id"] not in only:
            continue
        path = build(clip, edl)
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], check=True, capture_output=True, text=True).stdout.strip()
        print(f"{clip['id']}: {float(dur):.1f}s {path.stat().st_size / 1e6:.1f} MB -> {path}", flush=True)


if __name__ == "__main__":
    main()
