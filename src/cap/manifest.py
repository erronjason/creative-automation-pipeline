"""Run manifest: the machine-readable record of everything a run produced and why.

It doubles as the analytics join key: every variant has a stable id and tags (product, market,
locale, ratio, asset source, reframe method, copy source) so ad-platform performance data can be
joined back to learn which creative choices drive CTR and conversion.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

SEVERITY = {"pass": 0, "warn": 1, "fail": 2}


def worst(statuses) -> str:
    return max(statuses, key=lambda s: SEVERITY[s], default="pass")


class CheckResult(BaseModel):
    id: str
    category: str  # legal | brand | layout | localization | asset
    status: str  # pass | warn | fail
    detail: str
    value: float | None = None


class Review(BaseModel):
    state: str = "pending"  # pending | approved | rejected
    note: str = ""
    by: str = ""
    at: str = ""


class Variant(BaseModel):
    id: str
    product_id: str
    ratio: str
    locale: str
    market: str
    path: str  # relative to the campaign output folder
    width: int
    height: int
    sha256: str
    copy_text: dict = Field(default_factory=dict)
    copy_source: str
    reframe: dict = Field(default_factory=dict)
    checks: list[CheckResult] = Field(default_factory=list)
    status: str = "pass"
    review: Review = Field(default_factory=Review)


class ProductResult(BaseModel):
    id: str
    name: str
    asset_source: str  # reused | generated | cached
    hero_path: str
    prompt: str | None = None
    detail: str = ""


class Stats(BaseModel):
    variants: int = 0
    passed: int = 0
    warned: int = 0
    failed: int = 0
    heroes_reused: int = 0
    heroes_generated: int = 0
    genai_calls: int = 0
    cache_hits: int = 0
    est_cost_usd: float = 0.0
    cost_billed: bool = False  # True when est_cost_usd is provider-reported billing, not calls x estimate
    est_saved_usd: float = 0.0
    duration_s: float = 0.0
    stage_seconds: dict[str, float] = Field(default_factory=dict)


class Manifest(BaseModel):
    schema_version: int = 1
    run_id: str
    campaign_id: str
    campaign_name: str
    brand: str
    region: str
    audience: str
    message: str
    started_at: str
    finished_at: str = ""
    provider: str
    model: str
    reframe_strategy: str
    translator: str
    pipeline_version: str
    brief_sha256: str
    storage: str
    aspect_ratios: list[str]
    locales: list[str]
    products: list[ProductResult] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    stats: Stats = Field(default_factory=Stats)
    warnings: list[str] = Field(default_factory=list)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
