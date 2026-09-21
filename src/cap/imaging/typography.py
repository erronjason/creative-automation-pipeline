"""Text fitting and color math (WCAG contrast)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PIL import ImageFont, features

RTL_LANGS = {"ar", "he", "fa", "ur"}
HAS_RAQM = features.check("raqm")


def direction(locale: str) -> str:
    return "rtl" if locale.split("-")[0] in RTL_LANGS else "ltr"


@lru_cache(maxsize=256)
def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    layout = ImageFont.Layout.RAQM if HAS_RAQM else ImageFont.Layout.BASIC
    return ImageFont.truetype(path, size, layout_engine=layout)


@dataclass
class Fitted:
    lines: list[str]
    size: int
    font: ImageFont.FreeTypeFont
    line_height: int
    truncated: bool

    @property
    def height(self) -> int:
        return self.line_height * len(self.lines)

    def width(self) -> int:
        return int(max(self.font.getlength(line) for line in self.lines)) if self.lines else 0


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> list[str] | None:
    """Greedy word wrap. None if a single word is wider than the box at this size."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if f.getlength(word) > max_w:
            return None
        trial = f"{cur} {word}".strip()
        if f.getlength(trial) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit(text: str, font_path: str, max_w: int, max_lines: int, max_size: int, min_size: int) -> Fitted:
    """Largest font size where `text` wraps into <= max_lines within max_w (binary search)."""
    lo, hi, best = min_size, max_size, None
    while lo <= hi:
        mid = (lo + hi) // 2
        f = font(font_path, mid)
        lines = wrap(text, f, max_w)
        if lines is not None and len(lines) <= max_lines:
            best = Fitted(lines, mid, f, int(mid * 1.12), False)
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best
    # Does not fit even at min size: hard-wrap and ellipsize so the failure is visible and flagged.
    f = font(font_path, min_size)
    lines = wrap(text, f, max_w) or [text]
    lines = lines[:max_lines]
    last = lines[-1]
    while last and f.getlength(last + "…") > max_w:
        last = last[:-1]
    lines[-1] = last.rstrip() + "…"
    return Fitted(lines, min_size, f, int(min_size * 1.12), True)


# --- color --------------------------------------------------------------------------------------
def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def best_text_color(bg, light=(255, 255, 255), dark=(17, 17, 17)):
    return light if contrast(light, bg) >= contrast(dark, bg) else dark
