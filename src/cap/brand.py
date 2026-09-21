"""Brand guidelines: colors, logo, typography, voice, layout safe zones."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class Palette(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str = Field(description="Main brand color, #RRGGBB. Fills the CTA button; counts toward palette presence.")
    secondary: str = Field(description="Second brand color, #RRGGBB. Counts toward the palette-presence check.")
    accent: str = Field(description="Accent color, #RRGGBB. Counts toward the palette-presence check.")
    dark: str = Field(default="#111111", description="Scrim and shadow color, #RRGGBB, laid over bright images.")
    light: str = Field(default="#FFFFFF", description="Headline text color, #RRGGBB.")

    @field_validator("*")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX.match(v):
            raise ValueError(f"'{v}' is not a #RRGGBB color")
        return v.upper()

    def as_rgb(self) -> dict[str, tuple[int, int, int]]:
        return {k: hex_to_rgb(v) for k, v in self.model_dump().items()}


class ScriptFonts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(description="Headline font (TTF/OTF), relative to the brand file.")
    body: str = Field(description="Font (TTF/OTF) for the button text and disclaimer, relative to the brand file.")


class Fonts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(
        description="Headline font (TTF/OTF), relative to the brand file. Must cover the locales' scripts."
    )
    body: str = Field(description="Font (TTF/OTF) for the button text and disclaimer, relative to the brand file.")
    by_language: dict[str, ScriptFonts] = Field(
        default_factory=dict,
        description="Fonts to use instead for a language, keyed by its code (ar, he, ...): for scripts the main "
        "fonts do not cover. Choose fonts that also include Latin letters and digits, since copy often mixes them.",
    )

    def for_locale(self, locale: str) -> tuple[str, str]:
        """(headline, body) font files for a locale such as ar-AE."""
        own = self.by_language.get(locale.split("-")[0])
        return (own.headline, own.body) if own else (self.headline, self.body)


class SafeZone(BaseModel):
    """Insets as fractions of canvas size. 9:16 reserves room for platform UI (Stories/Reels)."""

    model_config = ConfigDict(extra="forbid")
    top: float = Field(default=0.06, description="Inset from the top edge, as a fraction of the height.")
    bottom: float = Field(default=0.06, description="Inset from the bottom edge, as a fraction of the height.")
    left: float = Field(default=0.06, description="Inset from the left edge, as a fraction of the width.")
    right: float = Field(default=0.06, description="Inset from the right edge, as a fraction of the width.")


DEFAULT_SAFE_ZONES = {
    "1:1": SafeZone(),
    "4:5": SafeZone(),
    "16:9": SafeZone(top=0.07, bottom=0.07, left=0.05, right=0.05),
    "9:16": SafeZone(top=0.14, bottom=0.22, left=0.07, right=0.07),
}


class Brand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Brand name, shown in reports.")
    palette: Palette = Field(description="Brand colors.")
    logo: str = Field(description="Logo for dark or busy backgrounds (PNG with alpha), relative to the brand file.")
    logo_on_light: str | None = Field(
        default=None, description="Logo variant for light backgrounds (PNG with alpha). Omit to always use `logo`."
    )
    fonts: Fonts = Field(description="Typography.")
    voice: str = Field(description="Tone-of-voice guidance. Given to the translator when copy is machine-translated.")
    visual_style: str = Field(description="Art direction appended to every image-generation prompt.")
    min_palette_coverage: float = Field(
        default=0.02,
        description="Share of pixels (0-1) that must be near a palette color before `brand.palette_presence` warns.",
    )
    safe_zones: dict[str, SafeZone] = Field(
        default_factory=dict,
        description="Per-ratio overrides of the platform safe zone, keyed '1:1', '9:16', '16:9' or '4:5'. "
        "Defaults keep 9:16 clear of Stories/Reels interface elements.",
    )

    root: SkipJsonSchema[Path] = Field(default=Path("."), exclude=True)  # set by load_brand; not part of the file

    def path(self, rel: str) -> Path:
        return (self.root / rel).resolve()

    def safe_zone(self, ratio: str) -> SafeZone:
        return self.safe_zones.get(ratio) or DEFAULT_SAFE_ZONES.get(ratio, SafeZone())


def load_brand(path: str | Path) -> Brand:
    p = Path(path).resolve()
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    brand = Brand.model_validate(data)
    brand.root = p.parent
    font_files = [brand.fonts.headline, brand.fonts.body]
    for own in brand.fonts.by_language.values():
        font_files += [own.headline, own.body]
    for rel in [brand.logo, brand.logo_on_light, *font_files]:
        if rel and not brand.path(rel).exists():
            raise FileNotFoundError(f"brand file not found: {brand.path(rel)}")
    return brand
