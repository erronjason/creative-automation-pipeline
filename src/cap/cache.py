"""Content-addressed cache for GenAI outputs.

Key = sha256 of everything that influences the output (provider, model, operation, prompt, size,
input image hash). Re-running a brief after a copy tweak re-renders overlays in seconds and
spends nothing on image generation. That is the single biggest cost lever in the pipeline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_key(**parts) -> str:
    return sha256(json.dumps(parts, sort_keys=True, default=str).encode())[:32]


class Cache:
    def __init__(self, root: str | Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled
        self.hits = 0

    def _p(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.png"

    def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        p = self._p(key)
        if p.exists():
            self.hits += 1
            return p.read_bytes()
        return None

    def put(self, key: str, data: bytes) -> None:
        if not self.enabled:
            return
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
