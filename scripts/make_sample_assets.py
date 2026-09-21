"""Generate the fictional 'Tidewell' brand kit and one approved product packshot.

Everything in brand/ and assets/ is synthetic and reproducible from this script, so the repo
carries no third-party imagery. Run: python scripts/make_sample_assets.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


def packshot(path: Path) -> None:
    W, H = 1600, 1200
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(0, 1, W)[None, :]
    top, bot = np.array([255, 236, 190]), np.array([250, 196, 150])
    grad = top * (1 - y[..., None]) + bot * y[..., None]
    glow = np.exp(-(((x - 0.62) / 0.35) ** 2 + ((y - 0.35) / 0.4) ** 2))[..., None] * 40
    bg = np.clip(grad + glow, 0, 255).astype(np.uint8)
    img = Image.fromarray(np.broadcast_to(bg, (H, W, 3)).copy(), "RGB").convert("RGBA")

    # yuzu fruit
    fruit = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fruit)
    for fx, fy, fr in [(530, 870, 120), (1080, 900, 95), (1210, 830, 70)]:
        fd.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=(236, 205, 60, 255))
        fd.ellipse([fx - fr * 0.55, fy - fr * 0.7, fx - fr * 0.05, fy - fr * 0.25], fill=(255, 240, 150, 200))
        fd.ellipse([fx - 8, fy - fr - 6, fx + 8, fy - fr + 8], fill=(110, 140, 50, 255))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([560, 930, 1040, 1010], fill=(80, 40, 20, 110))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(22)))
    img = Image.alpha_composite(img, fruit)

    # can body with cylindrical shading
    cx, cw, top_y, bot_y = 800, 300, 250, 960
    body = np.zeros((bot_y - top_y, cw, 4), np.uint8)
    u = np.linspace(-1, 1, cw)
    shade = 0.55 + 0.45 * np.sqrt(np.clip(1 - u**2, 0, 1)) + 0.25 * np.exp(-((u + 0.45) / 0.12) ** 2)
    base = np.array(TEAL, float)
    body[..., :3] = np.clip(base[None, None, :] * shade[None, :, None], 0, 255).astype(np.uint8)
    body[..., 3] = 255
    can = Image.fromarray(body, "RGBA")
    cd = ImageDraw.Draw(can)
    band_top, band_h = 250, 230
    band = np.zeros((band_h, cw, 4), np.uint8)
    band[..., :3] = np.clip(np.array(SUN, float)[None, None, :] * shade[None, :, None], 0, 255).astype(np.uint8)
    band[..., 3] = 255
    can.alpha_composite(Image.fromarray(band, "RGBA"), (0, band_top))
    cd.text((cw // 2, 120), "TIDEWELL", font=font("Bold", 46), fill=WHITE, anchor="mm")
    cd.text((cw // 2, band_top + 80), "SPARKLING", font=font("SemiBold", 34), fill=DEEP, anchor="mm")
    cd.text((cw // 2, band_top + 135), "YUZU", font=font("Bold", 64), fill=DEEP, anchor="mm")
    for i in range(18):  # condensation droplets
        dx, dy = (i * 73) % (cw - 40) + 20, (i * 131) % (bot_y - top_y - 60) + 30
        cd.ellipse([dx, dy, dx + 7, dy + 10], fill=(255, 255, 255, 120))
    mask = Image.new("L", (cw, bot_y - top_y), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw - 1, bot_y - top_y - 1], radius=28, fill=255)
    can.putalpha(mask)
    img.alpha_composite(can, (cx - cw // 2, top_y))
    d = ImageDraw.Draw(img)
    d.ellipse([cx - cw // 2 + 6, top_y - 26, cx + cw // 2 - 6, top_y + 26], fill=(205, 212, 214))
    d.ellipse([cx - cw // 2 + 30, top_y - 16, cx + cw // 2 - 30, top_y + 14], fill=(172, 180, 184))
    d.rounded_rectangle([cx - 40, top_y - 12, cx + 40, top_y + 4], radius=8, fill=(150, 158, 162))
    img.convert("RGB").save(path, quality=92)


if __name__ == "__main__":
    BRAND.mkdir(parents=True, exist_ok=True)
    logo(WHITE, (0, 0, 0, 0), BRAND / "logo-light.png")  # for dark backgrounds
    logo(TEAL, (0, 0, 0, 0), BRAND / "logo-dark.png")  # for light backgrounds
    out = ROOT / "assets" / "sparkling-yuzu"
    out.mkdir(parents=True, exist_ok=True)
    packshot(out / "hero.jpg")
    print("wrote brand kit and packshot")
