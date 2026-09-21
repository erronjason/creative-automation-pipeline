"""Text fitting and color math (WCAG contrast)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import ImageFont, features

RTL_LANGS = {"ar", "he", "fa", "ur"}
HAS_RAQM = features.check("raqm")
# Vowel marks (harakat) are dropped, as is normal for ad copy: without libraqm nothing would position them.
_reshape = arabic_reshaper.ArabicReshaper().reshape


def direction(locale: str) -> str:
    return "rtl" if locale.split("-")[0] in RTL_LANGS else "ltr"


@lru_cache(maxsize=256)
def font(path: str, size: int, rtl: bool = False) -> ImageFont.FreeTypeFont:
    # Right-to-left text is shaped and reordered by `shape` below, so it must not be laid out a second time by
    # libraqm (which would reverse it back, and is not installed everywhere). Left-to-right keeps libraqm's kerning.
    layout = ImageFont.Layout.RAQM if HAS_RAQM and not rtl else ImageFont.Layout.BASIC
    return ImageFont.truetype(path, size, layout_engine=layout)


def shape(text: str, rtl: bool) -> str:
    """What to hand to the renderer: the text as it looks left to right on screen.

    Arabic letters change form by position (joined, initial, final), and right-to-left runs are reordered around
    any Latin words and digits. Both are done here in pure Python, so every platform draws the same pixels.
    """
    return get_display(_reshape(text)) if rtl else text


def missing_glyphs(f: ImageFont.FreeTypeFont, text: str) -> str:
    """Characters of `text` the font has no glyph for (it would draw an empty box for each)."""

    def ink(ch: str):
        mask = f.getmask(ch)
        return mask.size, bytes(mask)

    blank = ink("􏿾")  # a code point no font maps: whatever it draws is the font's "missing" box
    return "".join(sorted({c for c in text if not c.isspace() and ink(c) == blank}))


@dataclass
class Fitted:
    lines: list[str]
    size: int
    font: ImageFont.FreeTypeFont
    line_height: int
    truncated: bool
    rtl: bool = False

    @property
    def height(self) -> int:
        return self.line_height * len(self.lines)

    @property
    def display(self) -> list[str]:
        """The lines as drawn (shaped and reordered when right to left); `lines` stays in reading order."""
        return [shape(line, self.rtl) for line in self.lines]

    def width(self) -> int:
        return int(max(self.font.getlength(line) for line in self.display)) if self.lines else 0


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float, rtl: bool = False) -> list[str] | None:
    """Greedy word wrap. None if a single word is wider than the box at this size."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if f.getlength(shape(word, rtl)) > max_w:
            return None
        trial = f"{cur} {word}".strip()
        if f.getlength(shape(trial, rtl)) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit(
    text: str, font_path: str, max_w: int, max_lines: int, max_size: int, min_size: int, rtl: bool = False
) -> Fitted:
    """Largest font size where `text` wraps into <= max_lines within max_w (binary search)."""
    lo, hi, best = min_size, max_size, None
    while lo <= hi:
        mid = (lo + hi) // 2
        f = font(font_path, mid, rtl)
        lines = wrap(text, f, max_w, rtl)
        if lines is not None and len(lines) <= max_lines:
            best = Fitted(lines, mid, f, int(mid * 1.12), False, rtl)
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best
    # Does not fit even at min size: hard-wrap and ellipsize so the failure is visible and flagged.
    f = font(font_path, min_size, rtl)
    lines = wrap(text, f, max_w, rtl) or [text]
    lines = lines[:max_lines]
    last = lines[-1]
    while last and f.getlength(shape(last + "…", rtl)) > max_w:
        last = last[:-1]
    lines[-1] = last.rstrip() + "…"
    return Fitted(lines, min_size, f, int(min_size * 1.12), True, rtl)


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
