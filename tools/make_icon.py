"""Generate the app icon: static/icon.ico (multi-size) and static/icon-*.png.

Run with the dev requirements installed:  python tools/make_icon.py
Drawn at 1024 px and downsampled, so edges stay smooth at every size.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static"
S = 1024
TOP, BOTTOM = (22, 190, 165), (9, 120, 106)  # teal gradient (matches the UI accent)
WHITE = (255, 255, 255, 255)


def gradient_square() -> Image.Image:
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    for y in range(S):
        t = y / (S - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=True)) + (255,)
        for x in range(S):
            px[x, y] = color
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=230, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def dot(draw: ImageDraw.ImageDraw, x: float, y: float, r: float) -> None:
    draw.ellipse((x - r, y - r, x + r, y + r), fill=WHITE)


def point(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    rad = math.radians(degrees)
    return cx + radius * math.cos(rad), cy + radius * math.sin(rad)


def draw_glyph(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    cx, cy, r, w = S / 2, 670, 340, 116
    start, end = 200, 340  # Pillow angles: clockwise from 3 o'clock, so this is the top arc
    d.arc((cx - r, cy - r, cx + r, cy + r), start=start, end=end, fill=WHITE, width=w)
    mid = r - w / 2  # round caps on the arc ends
    for angle in (start, end):
        dot(d, *point(cx, cy, mid, angle), w / 2)
    # Needle pointing up-right, with round tip and hub.
    tip = point(cx, cy, 268, 297)
    d.line((cx, cy, *tip), fill=WHITE, width=92)
    dot(d, *tip, 46)
    dot(d, cx, cy, 84)


def main() -> None:
    img = gradient_square()
    draw_glyph(img)
    OUT.mkdir(exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    img.save(OUT / "icon.ico", sizes=[(s, s) for s in sizes])
    for size in (64, 192, 512):
        img.resize((size, size), Image.Resampling.LANCZOS).save(OUT / f"icon-{size}.png", optimize=True)
    print("wrote", OUT / "icon.ico", "and icon-{64,192,512}.png")


if __name__ == "__main__":
    main()
