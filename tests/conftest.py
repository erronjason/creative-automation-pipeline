from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cap.pipeline import RunOptions

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway copy of the example project (briefs, brand, legal, assets)."""
    for d in ("briefs", "brand", "legal", "assets"):
        shutil.copytree(ROOT / d, tmp_path / d)
    return tmp_path


@pytest.fixture
def opts(repo: Path) -> RunOptions:
    return RunOptions(
        provider="mock",
        assets=str(repo / "assets"),
        output=str(repo / "output"),
        legal_rules=str(repo / "legal" / "prohibited_words.yaml"),
        cache_dir=str(repo / ".cache"),
        translator="none",
        workers=2,
    )


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    """Tests never touch real APIs, even if the developer has keys in their shell."""
    for k in ("OPENAI_API_KEY", "FIREFLY_CLIENT_ID", "FIREFLY_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
