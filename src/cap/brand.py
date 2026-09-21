"""Brand guidelines: colors, logo, typography, voice, layout safe zones."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class Palette(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str
    secondary: str
    accent: str
    dark: str = "#111111"
    light: str = "#FFFFFF"

    @field_validator("*")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX.match(v):
            raise ValueError(f"'{v}' is not a #RRGGBB color")
        return v.upper()

    def as_rgb(self) -> dict[str, tuple[int, int, int]]:
        return {k: hex_to_rgb(v) for k, v in self.model_dump().items()}


class Fonts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str
    body: str


class SafeZone(BaseModel):
    """Insets as fractions of canvas size. 9:16 reserves room for platform UI (Stories/Reels)."""

    model_config = ConfigDict(extra="forbid")
    top: float = 0.06
    bottom: float = 0.06
    left: float = 0.06
    right: float = 0.06


DEFAULT_SAFE_ZONES = {
    "1:1": SafeZone(),
    "4:5": SafeZone(),
    "16:9": SafeZone(top=0.07, bottom=0.07, left=0.05, right=0.05),
    "9:16": SafeZone(top=0.14, bottom=0.22, left=0.07, right=0.07),
}


class Brand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    palette: Palette
    logo: str = Field(description="Logo for dark backgrounds (PNG with alpha)")
    logo_on_light: str | None = Field(default=None, description="Logo variant for light backgrounds")
    fonts: Fonts
    voice: str = Field(description="Tone-of-voice guidance, fed to the translator")
    visual_style: str = Field(description="Art direction appended to every generation prompt")
    min_palette_coverage: float = 0.02
    safe_zones: dict[str, SafeZone] = Field(default_factory=dict)

    root: Path = Field(default=Path("."), exclude=True)

    def path(self, rel: str) -> Path:
        return (self.root / rel).resolve()

    def safe_zone(self, ratio: str) -> SafeZone:
        return self.safe_zones.get(ratio) or DEFAULT_SAFE_ZONES.get(ratio, SafeZone())


def load_brand(path: str | Path) -> Brand:
    p = Path(path).resolve()
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    brand = Brand.model_validate(data)
    brand.root = p.parent
    for rel in [brand.logo, brand.logo_on_light, brand.fonts.headline, brand.fonts.body]:
        if rel and not brand.path(rel).exists():
            raise FileNotFoundError(f"brand file not found: {brand.path(rel)}")
    return brand
