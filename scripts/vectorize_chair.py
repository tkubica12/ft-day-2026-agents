"""Trace the approved chair into compact, transparent, layered SVG paths."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "f0f04939aed657e9d996b9c8e578ee3369967dec05b4c29925ae67bcd48887df"
PALETTE = ("#392F25", "#A17A52", "#7F5936", "#435A46", "#314A38")
DARK_PALETTE = ("#D6C4A8", "#B79468", "#96734C", "#83997F", "#647C64")


def path_data(contour, epsilon):
    points = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2) / 4
    if len(points) < 3:
        return ""

    def xy(point):
        return " ".join(f"{n:.1f}".rstrip("0").rstrip(".") for n in point)

    midpoints = (points + np.roll(points, -1, axis=0)) / 2
    commands = ["M" + xy(midpoints[-1])]
    for i, point in enumerate(points):
        a, b = points[i - 1] - point, points[(i + 1) % len(points)] - point
        cosine = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
        # Keep the joinery's corners; smooth only gently curving upholstery/rails.
        if cosine > -.65:
            commands.extend(("L" + xy(point), "L" + xy(midpoints[i])))
        else:
            commands.append("Q" + xy(point) + " " + xy(midpoints[i]))
    return "".join(commands) + "Z"


def trace(mask, epsilon, min_area=70):
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    return "".join(path_data(c, epsilon) for c in contours if cv2.contourArea(c) >= min_area)


def build(source, epsilon=2.3):
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA:
        raise ValueError("Source is not the approved finalist 3 original")
    image = Image.open(source).convert("RGB")
    if image.size != (2048, 2048):
        raise ValueError("Expected the untouched 2048 x 2048 original")
    pixels = np.asarray(image).astype(np.float32)
    foreground = pixels.min(axis=2) < 190
    palette = np.array([tuple(bytes.fromhex(c[1:])) for c in PALETTE], dtype=np.float32)
    labels = np.square(pixels[:, :, None, :] - palette).sum(axis=3).argmin(axis=2)
    ys, xs = np.where(foreground)
    x, y = xs.min() / 4 - 5, ys.min() / 4 - 5
    width, height = (xs.max() - xs.min()) / 4 + 10, (ys.max() - ys.min()) / 4 + 10
    silhouette = trace(foreground, epsilon)
    paths = [f'<path fill="{PALETTE[0]}" fill-rule="evenodd" d="{silhouette}"/>']
    for index, color in enumerate(PALETTE[1:], 1):
        mask = (labels == index) & foreground
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        data = trace(mask, epsilon)
        paths.append(f'<path fill="{color}" d="{data}" fill-rule="evenodd"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:g} {y:g} {width:g} {height:g}" '
           'role="img" aria-label="Javorna armchair"><title>Javorna armchair</title>'
           + "".join(paths) + "</svg>\n")
    return svg, {
        "source_sha256": digest, "source_dimensions": [2048, 2048],
        "view_box": [x, y, width, height], "palette": PALETTE,
        "epsilon_source_pixels": epsilon, "svg_bytes": len(svg.encode("utf-8")),
        "method": "Five flat colours; fitted quadratic paths; transparent background and openings.",
        "original_modified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--epsilon", type=float, default=2.3)
    args = parser.parse_args()
    svg, report = build(args.source, args.epsilon)
    output = ROOT / "docs/assets/javorna"
    output.mkdir(parents=True, exist_ok=True)
    (output / "chair.svg").write_bytes(svg.encode("utf-8"))
    reversed_svg = svg
    for light, dark in zip(PALETTE, DARK_PALETTE):
        reversed_svg = reversed_svg.replace(light, dark)
    (output / "chair-on-dark.svg").write_bytes(reversed_svg.encode("utf-8"))
    report["svg_sha256"] = hashlib.sha256(svg.encode()).hexdigest()
    report["on_dark_palette"] = DARK_PALETTE
    (ROOT / ".local/chair-vector-lineage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
