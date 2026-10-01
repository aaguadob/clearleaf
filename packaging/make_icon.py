"""Draws the app icon (the leaf mark on a dark tile) as icon.png, icon.icns
(macOS, needs iconutil) and icon.ico (Windows)."""

import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
SIZE = 1024
SS = 4  # drawn larger, then shrunk, for smooth edges


def leaf(draw, box, r_small, r_big, fill):
    """The clearleaf mark: a rectangle whose top-left and bottom-right corners
    are nearly square and whose other two are rounded deep, like a leaf."""
    x0, y0, x1, y1 = box
    corners = [  # centre of each corner's arc, its radius, and the angle it starts from
        ((x0 + r_small, y0 + r_small), r_small, 180),  # top left
        ((x1 - r_big, y0 + r_big), r_big, 270),  # top right
        ((x1 - r_small, y1 - r_small), r_small, 0),  # bottom right
        ((x0 + r_big, y1 - r_big), r_big, 90),  # bottom left
    ]
    points = []
    for (cx, cy), r, start in corners:
        for k in range(33):
            t = math.radians(start + 90 * k / 32)
            points.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    draw.polygon(points, fill=fill)


def draw() -> Image.Image:
    s = SIZE * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # macOS icon grid: an 824 px tile centred in 1024, corner radius ~185
    m = 100 * SS
    d.rounded_rectangle((m, m, s - m, s - m), radius=185 * SS, fill=(22, 25, 29, 255))
    w, h = 300 * SS, 390 * SS
    cx, cy = s / 2, s / 2
    leaf(d, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), 16 * SS, 170 * SS, (255, 255, 255, 255))
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    img = draw()
    img.save(HERE / "icon.png")
    img.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if shutil.which("iconutil"):
        iconset = HERE / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for size in (16, 32, 128, 256, 512):
            img.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            img.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")], check=True)
        shutil.rmtree(iconset)


if __name__ == "__main__":
    main()
