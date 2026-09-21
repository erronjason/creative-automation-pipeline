"""Offline, deterministic provider.

Lets anyone clone the repo and run the full pipeline with zero credentials and zero cost, and
makes tests hermetic.

For the demo briefs it replays real output: the heroes and outpaints that gpt-image-2 produced (via
OpenRouter) when those briefs were run for real, recorded by scripts/record_mock_fixtures.py into
providers/recorded/. Anything it has no recording for gets a stylized placeholder, watermarked so nobody
mistakes it for real creative.
"""

from __future__ import annotations

import colorsys
import functools
import hashlib
import json
import random
from importlib import resources

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .base import ImageProvider, ProviderStatus, open_image, to_png


def prompt_id(prompt: str) -> str:
    """How a recorded hero is found: the exact prompt that generated it."""
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def pixel_id(img: Image.Image) -> str:
    """How a recorded outpaint is found: the pixels of the image it extended (not its file bytes, which vary)."""
    return hashlib.sha256(img.convert("RGB").tobytes()).hexdigest()[:16]


class Recorded:
    """Real generations recorded from an OpenRouter run (see scripts/record_mock_fixtures.py)."""

    def __init__(self) -> None:
        self.root = resources.files("cap.providers").joinpath("recorded")
        try:
            self.index = json.loads(self.root.joinpath("index.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.index = {"heroes": {}, "expansions": {}}

    def _load(self, folder: str, name: str | None) -> Image.Image | None:
        return open_image(self.root.joinpath(folder, name).read_bytes()) if name else None

    def hero(self, prompt: str) -> Image.Image | None:
        return self._load("heroes", self.index["heroes"].get(prompt_id(prompt)))

    def outpaint(self, source: Image.Image, canvas: tuple[int, int]) -> Image.Image | None:
        return self._load("expansions", self.index["expansions"].get(f"{pixel_id(source)}-{canvas[0]}x{canvas[1]}"))


@functools.lru_cache(maxsize=1)
def recorded() -> Recorded:
    return Recorded()


class MockProvider(ImageProvider):
    name = "mock"
    model = "mock-v2"  # v2: replays recorded gpt-image-2 output for the demo briefs
    supports_expand = True
    hero_size = (1024, 1024)

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "offline renderer that replays recorded images, no API key needed")

    def generate(self, prompt: str, size: tuple[int, int]) -> bytes:
        self._tick()
        real = recorded().hero(prompt)
        if real is not None:
            return to_png(real if real.size == size else real.resize(size, Image.LANCZOS))
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
        """Replay the recorded outpaint if this exact image was outpainted for real; else fake one by stretching
        the border pixels outward and blurring them, so backgrounds continue."""
        self._tick()
        source = open_image(image)
        real = recorded().outpaint(source, canvas)
        if real is not None:
            return to_png(real if real.size == canvas else real.resize(canvas, Image.LANCZOS))
        src = np.asarray(source)
        cw, ch = canvas
        h, w = src.shape[:2]
        top, left = (ch - h) // 2, (cw - w) // 2
        ext = cv2.copyMakeBorder(src, top, ch - h - top, left, cw - w - left, cv2.BORDER_REPLICATE)
        blurred = cv2.GaussianBlur(ext, (0, 0), max(cw, ch) / 60)
        blurred[top : top + h, left : left + w] = src
        return to_png(Image.fromarray(blurred))
