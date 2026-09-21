"""GenAI image provider interface.

Two operations cover the creative need:
  * generate(prompt, size)        -> a new hero image when the brief has no approved asset
  * expand(image, canvas, prompt) -> outpaint an image onto a larger canvas (aspect-ratio reframing)

Providers declare capabilities instead of the pipeline special-casing vendors, so adding
Firefly, Gemini, or an internal model is one new file.
"""

from __future__ import annotations

import io
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from PIL import Image


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderStatus:
    ready: bool
    detail: str


class RateLimiter:
    """Minimum spacing between calls, shared across worker threads (e.g. Firefly: 4 req/min/org)."""

    def __init__(self, per_minute: float | None):
        self.interval = 60.0 / per_minute if per_minute else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)


def with_retries(fn, *, attempts: int = 4, base_delay: float = 2.0):
    """Retry transient failures (network, 429, 5xx) with exponential backoff. 4xx fails fast."""
    for i in range(attempts):
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code != 429 and code < 500 or i == attempts - 1:
                body = e.response.text[:300]
                raise ProviderError(f"HTTP {code}: {body}") from e
        except httpx.TransportError as e:
            if i == attempts - 1:
                raise ProviderError(f"network error: {e}") from e
        time.sleep(base_delay * (2**i))


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def open_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


class ImageProvider(ABC):
    name: str = "base"
    model: str = ""
    supports_expand: bool = False
    hero_size: tuple[int, int] = (1024, 1024)
    est_cost_per_image: float = 0.0  # USD, rough planning figure only; override via env
    requests_per_minute: float | None = None
    cache_tag: str = ""  # extra settings that change output, folded into expand cache keys

    def __init__(self) -> None:
        self.calls = 0
        self._limiter = RateLimiter(self.requests_per_minute)
        self._count_lock = threading.Lock()

    def _tick(self) -> None:
        self._limiter.wait()
        with self._count_lock:
            self.calls += 1

    def actual_cost_usd(self) -> float | None:
        """Billed spend when the API reports it; None means "use calls x est_cost_per_image"."""
        return None

    def avg_cost_per_call(self) -> float:
        return self.est_cost_per_image

    @abstractmethod
    def status(self) -> ProviderStatus: ...

    @abstractmethod
    def generate(self, prompt: str, size: tuple[int, int]) -> bytes:
        """Return PNG bytes."""

    def expand(self, image: bytes, canvas: tuple[int, int], prompt: str) -> bytes:
        """Return PNG bytes of `canvas` size with `image` centered and the margins outpainted."""
        raise NotImplementedError(f"{self.name} does not support expand")
