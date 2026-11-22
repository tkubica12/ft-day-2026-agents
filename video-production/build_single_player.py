"""Build single-file copies of docs/player.html.

  python video-production\\build_single_player.py         e-mail copy: clips re-encoded (CRF 28, max 1920 px)
  python video-production\\build_single_player.py --full  presentation copy: edited clips embedded unchanged

Images referenced as assets/... (src attributes and CSS url()) are embedded as data URIs.
Clips come from docs/video/NN.mp4; slides without a clip still show "Recording pending".
Outputs go to ignored dist/. Re-run after rebuilding the player or changing any clip.
"""
import argparse
import base64
import mimetypes
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SRC = DOCS / "player.html"
DIST = ROOT / "dist"
OUT_EMAIL = DIST / "Javorna-demo-player-single-file.html"
OUT_FULL = DIST / "Javorna-demo-player-presentation.html"
WIDTH, CRF = 1920, 28

HELPER = """<script>
window.__clip = src => {
  const el = document.querySelector('script[data-clip="' + src + '"]');
  if (!el) return null;
  const bin = atob(el.textContent.trim());
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: 'video/mp4' }));
};
</script>
"""


def encode(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vf", f"scale='min({WIDTH},iw)':-2:flags=lanczos", "-c:v", "libx264",
         "-preset", "veryslow", "-crf", str(CRF), "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", "-an", str(dst)],
        check=True)


def data_uri(relative: str) -> str:
    path = (DOCS / relative).resolve()
    if DOCS.resolve() not in path.parents:
        raise ValueError(f"asset outside docs/: {relative}")
    mime = "image/svg+xml" if path.suffix == ".svg" else mimetypes.guess_type(path.name)[0]
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def inline_assets(html: str) -> str:
    html = re.sub(r'src="(assets/[^"]+)"', lambda m: f'src="{data_uri(m.group(1))}"', html)
    return re.sub(r'url\("(assets/[^"]+)"\)', lambda m: f'url("{data_uri(m.group(1))}")', html)


def package(html: str, clips: list[Path], full: bool) -> str:
    old = "    v.src = src;"
    new = ("    const url = window.__clip(src);\n"
           "    if (!url) { s.classList.add('missing'); return; }\n"
           "    v.src = url;")
    if html.count(old) != 1 or html.count("<script>") != 1:
        raise SystemExit("player structure changed; update build_single_player.py")
    html = inline_assets(html.replace(old, new))

    blocks = []
    with tempfile.TemporaryDirectory() as tmp:
        for clip in clips:
            small = Path(tmp) / clip.name
            if full:
                shutil.copyfile(clip, small)
            else:
                encode(clip, small)
            data = base64.b64encode(small.read_bytes()).decode("ascii")
            blocks.append(f'<script type="application/octet-stream" data-clip="video/{clip.name}">{data}</script>\n')
            print(f"{clip.name}: {clip.stat().st_size // 1024} KB -> {small.stat().st_size // 1024} KB")
    return html.replace("<script>", "".join(blocks) + HELPER + "<script>", 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="embed edited clips without re-encoding")
    full = ap.parse_args().full
    out = OUT_FULL if full else OUT_EMAIL
    clips = sorted(p for p in (DOCS / "video").glob("*.mp4") if re.fullmatch(r"[0-9]{2}\.mp4", p.name))
    html = package(SRC.read_text(encoding="utf-8"), clips, full)
    DIST.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"{out.relative_to(ROOT)}: {len(clips)} clips, {out.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
