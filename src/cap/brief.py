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
LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?$")
SUPPORTED_RATIOS = {"1:1": (1080, 1080), "9:16": (1080, 1920), "16:9": (1920, 1080), "4:5": (1080, 1350)}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Copy(Strict):
    """Pre-approved (transcreated) copy for one locale. Always wins over machine translation."""

    message: str = Field(min_length=1, max_length=120, description="Headline on the post, in this locale.")
    cta: str | None = Field(
        default=None, max_length=30, description="Button text. Omit to reuse the campaign's `cta` untranslated."
    )
    disclaimer: str | None = Field(
        default=None, max_length=160, description="Small print. Omit to reuse the campaign's `disclaimer` untranslated."
    )


class Market(Strict):
    code: str = Field(description="Market code, e.g. US, MX, CA-QC. Shown in the report; does not affect rendering.")
    locale: str = Field(
        description="BCP-47 locale that selects the copy, e.g. en-US, es-MX, fr-CA, es-419, zh-Hans-CN. "
        "Two markets sharing a locale share one set of variants."
    )

    @field_validator("locale")
    @classmethod
    def _locale(cls, v: str) -> str:
        if not LOCALE.match(v):
            raise ValueError(f"'{v}' is not a locale like 'en-US', 'fr-CA', 'es-419' or 'zh-Hans-CN'")
        return v


class Target(Strict):
    region: str = Field(description="Where the campaign runs, e.g. 'North America'. Used in prompts and the report.")
    markets: list[Market] = Field(
        min_length=1, description="One entry per market. Every distinct locale becomes a variant per product and ratio."
    )
    audience: str = Field(
        min_length=3, description="Who the campaign is for. Used in image prompts and as translation context."
    )


class ProductAssets(Strict):
    hero: str | None = Field(
        default=None,
        description="Approved hero image, relative to the assets root. Omit to use "
        "assets/<product id>/hero.png|jpg|jpeg|webp; if none exists a hero is generated.",
    )
    focus: tuple[float, float] | None = Field(
        default=None,
        description="Focal point (x, y), each 0-1, used when cropping to a new ratio. Overrides automatic saliency.",
    )

    @field_validator("focus")
    @classmethod
    def _focus(cls, v):
        if v is not None and not all(0.0 <= c <= 1.0 for c in v):
            raise ValueError("focus coordinates must be between 0 and 1")
        return v


class Product(Strict):
    id: str = Field(description="Lowercase slug (a-z, 0-9, dashes). Names the asset folder and the output folder.")
    name: str = Field(description="Product name as it should appear in image prompts.")
    description: str = Field(min_length=3, description="What the product looks like. Goes into the hero image prompt.")
    scene: str | None = Field(
        default=None,
        description="Setting for generated images, e.g. 'bright modern office desk by a window'. "
        "Falls back to the campaign's `visual_direction`.",
    )
    prompt: str | None = Field(
        default=None,
        description="Advanced. A complete prompt for the hero image, replacing the built-in template "
        "(name, description, scene, audience, brand style). The 'no text or logos' rule is still appended.",
    )
    assets: ProductAssets = Field(
        default_factory=ProductAssets, description="Optional hints about this product's image."
    )

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError(f"product id '{v}' must be a lowercase slug (a-z, 0-9, dashes)")
        return v


class Campaign(Strict):
    id: str = Field(description="Lowercase slug (a-z, 0-9, dashes). Names the output folder.")
    name: str = Field(description="Display name for reports.")
    brand: str = Field(description="Path to the brand guidelines YAML, relative to this brief.")
    source_locale: str = Field(
        default="en-US",
        description="Locale of the copy below. Markets in other locales use `localized_copy`, "
        "else machine translation, else this copy.",
    )
    message: str = Field(min_length=1, max_length=120, description="The campaign headline shown on every post.")
    cta: str | None = Field(default=None, max_length=30, description="Button text, e.g. 'Find it near you'. Optional.")
    disclaimer: str | None = Field(default=None, max_length=160, description="Small print under the button. Optional.")
    visual_direction: str | None = Field(
        default=None, description="Default setting for generated images; a product's own `scene` wins."
    )

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError(f"campaign id '{v}' must be a lowercase slug (a-z, 0-9, dashes)")
        return v


class Brief(Strict):
    campaign: Campaign = Field(description="Identity, brand and copy of the campaign.")
    target: Target = Field(description="Where and to whom the campaign is aimed.")
    products: list[Product] = Field(min_length=2, description="At least two products.")
    aspect_ratios: list[str] = Field(
        default_factory=lambda: ["1:1", "9:16", "16:9"],
        min_length=1,
        description="Formats to produce. Supported: 1:1, 9:16, 16:9, 4:5.",
    )
    localized_copy: dict[str, Copy] = Field(
        default_factory=dict,
        description="Approved copy keyed by locale. It always wins over machine translation. "
        "Keys must be locales of a market in `target.markets`.",
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
