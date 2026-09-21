"""Offline, deterministic provider.

Lets anyone clone the repo and run the full pipeline with zero credentials and zero cost, and
makes tests hermetic. Output is intentionally stylized (and watermarked) so nobody mistakes it
for real creative.
"""

from __future__ import annotations

import colorsys
import hashlib
import random

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .base import ImageProvider, ProviderStatus, open_image, to_png


class MockProvider(ImageProvider):
    name = "mock"
    model = "mock-v1"
    supports_expand = True
    hero_size = (1024, 1024)

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "offline placeholder renderer, no API key needed")

    def generate(self, prompt: str, size: tuple[int, int]) -> bytes:
        self._tick()
        seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:12], 16)
        rnd = random.Random(seed)
        w, h = size
        hue = rnd.random()

        def col(dh=0.0, s=0.55, v=0.9):
            r, g, b = colorsys.hsv_to_rgb((hue + dh) % 1.0, s, v)
            return int(r * 255), int(g * 255), int(b * 255)

        # Soft two-tone gradient backdrop.
        top, bottom = col(0.0, 0.35, 0.98), col(0.08, 0.6, 0.75)
        img = Image.new("RGB", size)
        px = ImageDraw.Draw(img)
        for y in range(h):
            t = y / max(1, h - 1)
            px.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))

        # Floating bokeh circles.
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for _ in range(14):
            r = rnd.randint(w // 30, w // 7)
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(*col(rnd.uniform(-0.1, 0.1), 0.25, 1.0), 70))
        layer = layer.filter(ImageFilter.GaussianBlur(w / 80))
        img = Image.alpha_composite(img.convert("RGBA"), layer)

        # A generic "product" silhouette: shadow, body, highlight.
        d = ImageDraw.Draw(img)
        cx, cy = w // 2, int(h * 0.52)
        pw, ph = int(w * 0.22), int(h * 0.46)
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse(
            [cx - pw * 0.8, cy + ph * 0.45, cx + pw * 0.8, cy + ph * 0.6], fill=(0, 0, 0, 90)
        )
        img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(w / 60)))
        d = ImageDraw.Draw(img)
        body = col(0.5, 0.65, 0.55)
        d.rounded_rectangle([cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2], radius=pw // 5, fill=body)
        d.rounded_rectangle(
            [cx - pw / 2, cy - ph * 0.1, cx + pw / 2, cy + ph * 0.25], radius=4, fill=col(0.0, 0.2, 0.97)
        )
        d.rounded_rectangle(
            [cx - pw * 0.35, cy - ph * 0.45, cx - pw * 0.25, cy + ph * 0.4], radius=6, fill=(255, 255, 255, 60)
        )

        # Honest watermark.
        try:
            font = ImageFont.load_default(size=max(12, w // 64))
        except TypeError:  # Pillow < 10.1
            font = ImageFont.load_default()
        d.text((w - 12, h - 10), "MOCK RENDER", fill=(255, 255, 255, 150), font=font, anchor="rd")
        return to_png(img.convert("RGB"))

    def expand(self, image: bytes, canvas: tuple[int, int], prompt: str) -> bytes:
        """Fake outpaint: stretch the border pixels outward and blur them, so backgrounds continue."""
        self._tick()
        src = np.asarray(open_image(image))
        cw, ch = canvas
        h, w = src.shape[:2]
        top, left = (ch - h) // 2, (cw - w) // 2
        ext = cv2.copyMakeBorder(src, top, ch - h - top, left, cw - w - left, cv2.BORDER_REPLICATE)
        blurred = cv2.GaussianBlur(ext, (0, 0), max(cw, ch) / 60)
        blurred[top : top + h, left : left + w] = src
        return to_png(Image.fromarray(blurred))
