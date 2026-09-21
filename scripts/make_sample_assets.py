"""Generate the fictional 'Tidewell' brand kit: the logos.

The logos are synthetic and reproducible from this script, so the repo carries no third-party imagery.
Run: python scripts/make_sample_assets.py

The product packshot (assets/sparkling-yuzu/hero.png) is not made here: it is a real gpt-image-2 generation,
installed by scripts/record_mock_fixtures.py.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand" / "tidewell"
FONTS = BRAND / "fonts"
TEAL, SUN, CORAL, DEEP, WHITE = (11, 122, 117), (242, 193, 78), (247, 129, 84), (14, 42, 47), (255, 255, 255)


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Poppins-{weight}.ttf"), size)


def logo(fg, wave_bg, path: Path) -> None:
    s = 4  # supersample for crisp edges
    w, h = 1200 * s, 300 * s
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = 130 * s
    cx, cy = 150 * s, h // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fg)
    # two waves cut through the disc
    for k, off in enumerate((-10, 45)):
        xs = range(cx - r - s, cx + r + 2 * s, s)
        mid = [(x, cy + off * s + math.sin((x - cx) / r * math.pi * 1.6 + k) * 22 * s) for x in xs]
        half = 13 * s
        poly = [(x, y - half) for x, y in mid] + [(x, y + half) for x, y in reversed(mid)]
        d.polygon(poly, fill=wave_bg)
    d.text((320 * s, cy), "TIDEWELL", font=font("Bold", 170 * s), fill=fg, anchor="lm")
    img = img.resize((w // s, h // s), Image.LANCZOS)
    img = img.crop(img.getbbox())
    img.save(path)


if __name__ == "__main__":
    BRAND.mkdir(parents=True, exist_ok=True)
    logo(WHITE, (0, 0, 0, 0), BRAND / "logo-light.png")  # for dark backgrounds
    logo(TEAL, (0, 0, 0, 0), BRAND / "logo-dark.png")  # for light backgrounds
    print("wrote brand kit")
