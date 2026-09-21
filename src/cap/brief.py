"""Campaign brief schema.

The brief is the contract between marketers and the pipeline. It is validated up front so a
malformed brief fails in milliseconds, before any paid GenAI call is made.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
SUPPORTED_RATIOS = {"1:1": (1080, 1080), "9:16": (1080, 1920), "16:9": (1920, 1080), "4:5": (1080, 1350)}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Copy(Strict):
    """Pre-approved (transcreated) copy for one locale. Always wins over machine translation."""

    message: str = Field(min_length=1, max_length=120)
    cta: str | None = Field(default=None, max_length=30)
    disclaimer: str | None = Field(default=None, max_length=160)


class Market(Strict):
    code: str = Field(description="Market code, e.g. US, MX, CA-QC")
    locale: str = Field(description="BCP-47 locale used for copy, e.g. es-MX")

    @field_validator("locale")
    @classmethod
    def _locale(cls, v: str) -> str:
        if not LOCALE.match(v):
            raise ValueError(f"'{v}' is not a locale like 'en-US' or 'fr-CA'")
        return v


class Target(Strict):
    region: str
    markets: list[Market] = Field(min_length=1)
    audience: str = Field(min_length=3)


class ProductAssets(Strict):
    hero: str | None = Field(default=None, description="Path (relative to the assets root) of an approved hero image")
    focus: tuple[float, float] | None = Field(
        default=None, description="Normalized (x, y) focal point used when cropping; overrides saliency"
    )

    @field_validator("focus")
    @classmethod
    def _focus(cls, v):
        if v is not None and not all(0.0 <= c <= 1.0 for c in v):
            raise ValueError("focus coordinates must be between 0 and 1")
        return v


class Product(Strict):
    id: str
    name: str
    description: str = Field(min_length=3)
    scene: str | None = Field(default=None, description="Art direction for generated heroes")
    prompt: str | None = Field(default=None, description="Full prompt override for generated heroes")
    assets: ProductAssets = Field(default_factory=ProductAssets)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError(f"product id '{v}' must be a lowercase slug (a-z, 0-9, dashes)")
        return v


class Campaign(Strict):
    id: str
    name: str
    brand: str = Field(description="Path to brand guidelines YAML (relative to the brief)")
    source_locale: str = "en-US"
    message: str = Field(min_length=1, max_length=120)
    cta: str | None = Field(default=None, max_length=30)
    disclaimer: str | None = Field(default=None, max_length=160)
    visual_direction: str | None = None

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError(f"campaign id '{v}' must be a lowercase slug (a-z, 0-9, dashes)")
        return v


class Brief(Strict):
    campaign: Campaign
    target: Target
    products: list[Product] = Field(min_length=2, description="At least two products")
    aspect_ratios: list[str] = Field(default_factory=lambda: ["1:1", "9:16", "16:9"], min_length=1)
    localized_copy: dict[str, Copy] = Field(
        default_factory=dict, description="Approved copy keyed by locale; source locale is implied"
    )

    @field_validator("aspect_ratios")
    @classmethod
    def _ratios(cls, v: list[str]) -> list[str]:
        bad = [r for r in v if r not in SUPPORTED_RATIOS]
        if bad:
            raise ValueError(f"unsupported aspect ratio(s) {bad}; supported: {sorted(SUPPORTED_RATIOS)}")
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _consistency(self) -> Brief:
        ids = [p.id for p in self.products]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate product ids: {sorted(dupes)}")
        locales = {m.locale for m in self.target.markets}
        stray = set(self.localized_copy) - locales
        if stray:
            raise ValueError(f"localized_copy has locales not targeted by any market: {sorted(stray)}")
        return self

    @property
    def locales(self) -> list[str]:
        return list(dict.fromkeys(m.locale for m in self.target.markets))


def load_brief(path: str | Path) -> tuple[Brief, Path]:
    """Load a brief from YAML or JSON. Returns the brief and its directory (for relative paths)."""
    p = Path(path)
    return parse_brief(p.read_text(encoding="utf-8")), p.resolve().parent


def parse_brief(text: str) -> Brief:
    data = yaml.safe_load(text)  # YAML is a superset of JSON, so this handles both
    if not isinstance(data, dict):
        raise ValueError("brief must be a mapping at the top level")
    return Brief.model_validate(data)
