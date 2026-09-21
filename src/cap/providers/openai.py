"""OpenAI GPT Image provider (Images API over plain HTTPS).

Uses httpx directly rather than the SDK: one less dependency, identical error handling to the
Firefly adapter, and every request field is visible in this file.
"""

from __future__ import annotations

import base64
import io
import os

import httpx
from PIL import Image

from .base import ImageProvider, ProviderError, ProviderStatus, open_image, to_png, with_retries

API = "https://api.openai.com/v1"
MAX_EDGE = 2048


class OpenAIProvider(ImageProvider):
    name = "openai"
    supports_expand = True

    def __init__(self, client: httpx.Client | None = None):
        self.key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
        self.quality = os.getenv("OPENAI_IMAGE_QUALITY", "medium")
        self.est_cost_per_image = float(os.getenv("OPENAI_EST_COST_PER_IMAGE", "0.05"))
        side = int(os.getenv("OPENAI_HERO_SIZE", "1024"))
        self.hero_size = (side, side)
        self.http = client or httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0))
        super().__init__()

    def status(self) -> ProviderStatus:
        if not self.key:
            return ProviderStatus(False, "set OPENAI_API_KEY in .env")
        return ProviderStatus(True, f"model {self.model}, quality {self.quality}")

    def _headers(self) -> dict:
        if not self.key:
            raise ProviderError("OPENAI_API_KEY is not set")
        return {"Authorization": f"Bearer {self.key}"}

    @staticmethod
    def _decode(resp: httpx.Response) -> bytes:
        try:
            b64 = resp.json()["data"][0]["b64_json"]
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError(f"unexpected response: {resp.text[:300]}") from e
        return to_png(open_image(base64.b64decode(b64)))

    def generate(self, prompt: str, size: tuple[int, int]) -> bytes:
        self._tick()
        body = {
            "model": self.model,
            "prompt": prompt,
            "size": f"{size[0]}x{size[1]}",
            "quality": self.quality,
            "n": 1,
            "output_format": "png",
        }

        def call():
            r = self.http.post(f"{API}/images/generations", json=body, headers=self._headers())
            r.raise_for_status()
            return r

        return self._decode(with_retries(call))

    def expand(self, image: bytes, canvas: tuple[int, int], prompt: str) -> bytes:
        """Outpaint via the edits endpoint: transparent margins in the mask are regenerated."""
        self._tick()
        src = open_image(image)
        cw, ch = canvas
        # Keep within the model's size envelope; the pipeline rescales afterwards anyway.
        # Dimensions must be multiples of 16; round the canvas up so it always contains the source.
        k = min(1.0, (MAX_EDGE - 16) / max(cw, ch))
        cw, ch = -(-int(cw * k) // 16) * 16, -(-int(ch * k) // 16) * 16
        sw, sh = min(cw, int(src.width * k)), min(ch, int(src.height * k))
        src = src.resize((sw, sh), Image.LANCZOS)

        rgba = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        rgba.paste(src, ((cw - sw) // 2, (ch - sh) // 2))
        # Mask: alpha 0 = "edit here", alpha 255 = "keep". The canvas itself doubles as the mask.
        files = {
            "image": ("canvas.png", _png(rgba), "image/png"),
            "mask": ("mask.png", _png(rgba), "image/png"),
        }
        data = {
            "model": self.model,
            "prompt": prompt,
            "size": f"{cw}x{ch}",
            "quality": self.quality,
            "n": "1",
        }

        def call():
            r = self.http.post(f"{API}/images/edits", data=data, files=files, headers=self._headers())
            r.raise_for_status()
            return r

        return self._decode(with_retries(call))


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
