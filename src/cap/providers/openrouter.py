"""OpenRouter image provider (unified Images API, `POST /api/v1/images`).

One key and one wire format reach many image models (OpenAI GPT Image, Gemini, FLUX, Seedream...),
so this is the cheapest way to try the pipeline live and to compare models: change
OPENROUTER_IMAGE_MODEL, nothing else.

Differences from the OpenAI adapter that matter here:
  * There is no mask parameter. Outpainting sends the source centred on a canvas whose margins are a
    blurred edge-extension of it, as an `input_references` image, and asks for those margins to be
    replaced with real detail. The model may re-render the centre slightly; imaging/align.py undoes
    that drift and the pipeline re-composites the original pixels over it.
  * Size is expressed as an aspect ratio from a fixed enum, not WxH. We pick the nearest supported
    ratio and cover-crop the result to the exact canvas.
  * Responses carry `usage.cost`, so reported spend is real billing data, not an estimate.
"""

from __future__ import annotations

import base64
import io
import os
import threading

import cv2
import httpx
import numpy as np
from PIL import Image

from .base import ImageProvider, ProviderError, ProviderStatus, open_image, to_png, with_retries

API = "https://openrouter.ai/api/v1"
MAX_EDGE = 2048  # reference images are billed by input tokens; the pipeline rescales afterwards
# Ratios accepted by GPT Image 2, FLUX.2 and Gemini image models alike.
RATIOS: dict[str, float] = {
    "1:1": 1.0,
    "3:2": 3 / 2,
    "2:3": 2 / 3,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "21:9": 21 / 9,
}


def nearest_ratio(size: tuple[int, int]) -> str:
    target = size[0] / size[1]
    return min(RATIOS, key=lambda k: abs(RATIOS[k] / target - 1))


def cover_crop(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale to cover `size`, then center-crop. Never stretches (nearest-ratio results are off by a few %)."""
    w, h = size
    k = max(w / img.width, h / img.height)
    big = img.resize((max(w, round(img.width * k)), max(h, round(img.height * k))), Image.LANCZOS)
    left, top = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((left, top, left + w, top + h))


LAYOUT_TAG = "layout=blur"  # part of the outpaint cache key: change it if blurred_layout() changes


def blurred_layout(src: Image.Image, canvas: tuple[int, int]) -> Image.Image:
    """The reference image for outpainting: `src` centred, margins a heavily blurred edge-extension of it.

    The soft margins continue the source's colours and tones outward, so the model has something to
    sharpen instead of an empty hole. This beat transparent margins and reflected margins when tried
    on generated heroes: those left visible seams or moved the subject.
    """
    cw, ch = canvas
    x, y = (cw - src.width) // 2, (ch - src.height) // 2
    arr = np.asarray(src.convert("RGB"))
    pad = cv2.copyMakeBorder(arr, y, ch - src.height - y, x, cw - src.width - x, cv2.BORDER_REPLICATE)
    out = Image.fromarray(cv2.GaussianBlur(pad, (0, 0), max(cw, ch) / 12))
    out.paste(src, (x, y))
    return out.convert("RGBA")


class OpenRouterProvider(ImageProvider):
    name = "openrouter"
    supports_expand = True

    def __init__(self, client: httpx.Client | None = None):
        self.key = os.getenv("OPENROUTER_API_KEY", "")
        self.model = os.getenv("OPENROUTER_IMAGE_MODEL", "openai/gpt-image-2")
        # Only sent when set: not every model exposes `quality` (Gemini, FLUX do not).
        self.quality = os.getenv("OPENROUTER_IMAGE_QUALITY", "")
        self.est_cost_per_image = float(os.getenv("OPENROUTER_EST_COST_PER_IMAGE", "0.05"))
        side = int(os.getenv("OPENROUTER_HERO_SIZE", "1024"))
        self.hero_size = (side, side)
        self.http = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0))
        self._spent = 0.0
        self._priced_calls = 0
        self._spend_lock = threading.Lock()
        super().__init__()

    @property
    def cache_tag(self) -> str:  # type: ignore[override]
        return LAYOUT_TAG + (f";quality={self.quality}" if self.quality else "")

    @property
    def gen_tag(self) -> str:  # type: ignore[override]
        return f"quality={self.quality}" if self.quality else ""

    def status(self) -> ProviderStatus:
        if not self.key:
            return ProviderStatus(False, "set OPENROUTER_API_KEY in .env")
        q = f", quality {self.quality}" if self.quality else ""
        return ProviderStatus(True, f"model {self.model}{q}")

    def actual_cost_usd(self) -> float | None:
        with self._spend_lock:
            return round(self._spent, 4) if self._priced_calls else None

    def avg_cost_per_call(self) -> float:
        with self._spend_lock:
            return self._spent / self._priced_calls if self._priced_calls else self.est_cost_per_image

    def _headers(self) -> dict:
        if not self.key:
            raise ProviderError("OPENROUTER_API_KEY is not set")
        return {
            "Authorization": f"Bearer {self.key}",
            "X-Title": "creative-automation-pipeline",  # optional attribution shown in OpenRouter's dashboard
        }

    def _post(self, body: dict) -> bytes:
        def call():
            r = self.http.post(f"{API}/images", json=body, headers=self._headers())
            r.raise_for_status()
            return r

        resp = with_retries(call)
        try:
            payload = resp.json()
            if "error" in payload:  # OpenRouter can report upstream failures inside a 200
                raise ProviderError(f"upstream error: {str(payload['error'])[:300]}")
            b64 = payload["data"][0]["b64_json"]
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError(f"unexpected response: {resp.text[:300]}") from e
        cost = (payload.get("usage") or {}).get("cost")
        if isinstance(cost, int | float):
            with self._spend_lock:
                self._spent += float(cost)
                self._priced_calls += 1
        return to_png(open_image(base64.b64decode(b64)))

    def _body(self, prompt: str, ratio: str) -> dict:
        body = {"model": self.model, "prompt": prompt, "aspect_ratio": ratio, "n": 1, "output_format": "png"}
        if self.quality:
            body["quality"] = self.quality
        return body

    def generate(self, prompt: str, size: tuple[int, int]) -> bytes:
        self._tick()
        out = open_image(self._post(self._body(prompt, nearest_ratio(size))))
        return to_png(out) if out.size == size else to_png(cover_crop(out, size))

    def expand(self, image: bytes, canvas: tuple[int, int], prompt: str) -> bytes:
        """Outpaint without a mask: source centered in a reference layout, margins to be filled."""
        self._tick()
        src = open_image(image)
        cw, ch = canvas
        k = min(1.0, MAX_EDGE / max(cw, ch))
        cw, ch = round(cw * k), round(ch * k)
        sw, sh = min(cw, round(src.width * k)), min(ch, round(src.height * k))
        src = src.resize((sw, sh), Image.LANCZOS)
        layout = blurred_layout(src, (cw, ch))
        buf = io.BytesIO()
        layout.save(buf, "PNG")
        ref = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        body = self._body(
            "The reference image has a sharp photograph in the centre and soft blurred placeholder margins to replace "
            "with real detail. "
            "Extend the scene outward to fill the whole frame: continue the background, surface, lighting and "
            "depth of field seamlessly. Keep the centre photograph exactly as it is: same size, same position. "
            f"Add no text, logos, people or new objects. {prompt}",
            nearest_ratio((cw, ch)),
        )
        body["input_references"] = [{"type": "image_url", "image_url": {"url": ref}}]
        out = open_image(self._post(body))
        return to_png(cover_crop(out, (cw, ch)))
