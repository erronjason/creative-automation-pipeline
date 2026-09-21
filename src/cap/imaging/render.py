"""Compose the final post: scrim, logo, headline, CTA, disclaimer, all inside platform safe zones.

Layout is computed first, then a scrim is fitted to it adaptively: its opacity is raised only as
far as needed for the headline to reach WCAG contrast against the actual pixels behind it. Bright
heroes get a stronger scrim, dark heroes keep more of the photo.

The renderer returns both the image and a Layout describing where everything landed, so the
compliance stage can verify placement instead of trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..brand import Brand, hex_to_rgb
from .saliency import saliency_map
from .typography import Fitted, best_text_color, contrast, direction, fit, font, luminance

Box = tuple[int, int, int, int]
SCRIM_STEPS = (0.45, 0.55, 0.65, 0.75, 0.85, 0.92)
TARGET_CONTRAST = 4.5
LOGO_CONTRAST = 3.0  # WCAG 1.4.11 non-text contrast: the mark must stand off the pixels behind it
# A mark is recognisable by its edges, and busy backdrops (foliage, bokeh, fabric) drown them even when
# contrast is fine. Mean Sobel gradient of the luminance behind the logo, on a 0-1 scale. Measured on
# generated heroes: match >= 0.92 at <= 0.04, 0.84 near 0.07, lost (0.71-0.76) at >= 0.145. 0.05 keeps headroom.
LOGO_CLUTTER_MAX = 0.05
LOGO_PLATE_STEPS = (0.0, 0.18, 0.28, 0.4, 0.52, 0.65)


@dataclass
class Layout:
    safe_box: Box
    logo_box: Box | None = None
    logo_plate: float = 0.0  # opacity of the soft plate behind the logo (0 = none needed)
    headline_box: Box | None = None
    cta_box: Box | None = None
    disclaimer_box: Box | None = None
    headline_color: tuple[int, int, int] = (255, 255, 255)
    headline_size: int = 0
    min_legible_size: int = 0
    truncated: bool = False
    text_side: str = "bottom"
    scrim_alpha: float = 0.0
    subject_overlap: float = 0.0  # share of the subject's saliency covered by copy
    backdrop: Image.Image | None = field(default=None, repr=False)  # pixels behind the text, pre-text

    def text_boxes(self) -> list[Box]:
        return [b for b in (self.headline_box, self.cta_box, self.disclaimer_box) if b]


def _scrim(size: tuple[int, int], side: str, start: int, solid: int, color, alpha: float) -> Image.Image:
    """Gradient from transparent at `start` to `alpha` at `solid`, flat beyond (toward `side`)."""
    w, h = size
    n = h if side == "bottom" else w
    pos = np.arange(n, dtype=np.float32)
    if side in ("bottom", "right"):
        t = np.clip((pos - start) / max(1, solid - start), 0, 1)
    else:  # left
        t = np.clip((start - pos) / max(1, start - solid), 0, 1)
    ramp = ((t * t * (3 - 2 * t)) * alpha * 255).astype(np.uint8)  # smoothstep: no visible edge
    a = np.repeat(ramp[:, None], w, axis=1) if side == "bottom" else np.repeat(ramp[None, :], h, axis=0)
    layer = Image.new("RGBA", size, (*color, 0))
    layer.putalpha(Image.fromarray(a, "L"))
    return layer


def _clutter(img: Image.Image, box: Box) -> float:
    """Mean edge strength (Sobel, luminance 0-1) inside `box`: how much texture the mark must stand out from."""
    g = cv2.cvtColor(np.asarray(img.convert("RGB").crop(box)), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    return float(np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)).mean())


def _plate(size: tuple[int, int], mask: Image.Image, color, alpha: float) -> Image.Image:
    layer = Image.new("RGBA", size, (*color, 0))
    layer.putalpha(mask.point(lambda v: int(v * alpha)))
    return layer


def _brightest(img: Image.Image, box: Box) -> np.ndarray:
    px = np.asarray(img.convert("RGB").crop(box)).reshape(-1, 3).astype(np.float32)
    lum = px @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return px[lum >= np.percentile(lum, 90)].mean(axis=0)


def compose(
    frame: Image.Image,
    ratio: str,
    brand: Brand,
    message: str,
    cta: str | None,
    disclaimer: str | None,
    locale: str,
) -> tuple[Image.Image, Layout]:
    W, H = frame.size
    short = min(W, H)
    sz = brand.safe_zone(ratio)
    L, T, R, B = int(W * sz.left), int(H * sz.top), int(W * (1 - sz.right)), int(H * (1 - sz.bottom))
    lay = Layout(safe_box=(L, T, R, B))
    pal = brand.palette.as_rgb()
    rtl = direction(locale) == "rtl"
    landscape = W > H * 1.2
    sal = saliency_map(frame)
    sal_img = np.asarray(Image.fromarray((sal / sal.max() * 255).astype(np.uint8)).resize((W, H)), np.float32)

    # --- 1. Decide where copy goes. Landscape: the side column with less subject saliency.
    if landscape:
        col_w = int((R - L) * 0.44)
        left_mass = sal_img[:, L : L + col_w].sum()
        right_mass = sal_img[:, R - col_w : R].sum()
        lay.text_side = "left" if left_mass <= right_mass else "right"
        x0, x1 = (L, L + col_w) if lay.text_side == "left" else (R - col_w, R)
    else:
        col_w, x0, x1 = R - L, L, R
    align_right = rtl or lay.text_side == "right"
    anchor_x, anchor = (x1, "ra") if align_right else (x0, "la")
    dir_kw = {"direction": "rtl"} if rtl else {}
    hfont, bfont = str(brand.path(brand.fonts.headline)), str(brand.path(brand.fonts.body))

    # --- 2. Measure the text block bottom-up (no drawing yet).
    gap = int(short * 0.025)
    y = B
    dfit = None
    if disclaimer:
        dfit = fit(disclaimer, bfont, x1 - x0, 3, int(short * 0.022), max(12, int(short * 0.016)))
        y -= dfit.height + gap
        disc_y = y + gap
    cta_geom = None
    if cta:
        cf = font(bfont, int(short * 0.034))
        ph, pw = int(cf.size * 0.7), int(cf.size * 1.1)
        bw, bh = int(cf.getlength(cta)) + 2 * pw, cf.size + 2 * ph
        y -= bh
        cx0 = x1 - bw if align_right else x0
        cta_geom = (cf, (cx0, y, cx0 + bw, y + bh))
        y -= int(gap * 1.2)
    min_size = max(14, int(short * 0.04))
    max_size = int(short * (0.085 if landscape else 0.078))
    hfit = fit(message, hfont, x1 - x0, 3, max_size, min_size)
    y -= hfit.height
    head_y = y
    w_head = hfit.width()
    hx0 = x1 - w_head if align_right else x0
    lay.headline_box = (hx0, head_y, hx0 + w_head, head_y + hfit.height)
    lay.headline_size, lay.min_legible_size, lay.truncated = hfit.size, min_size, hfit.truncated
    lay.headline_color = hex_to_rgb(brand.palette.light)
    block_top = head_y

    # --- 3. Adaptive scrim: the weakest one that makes the headline legible.
    base = frame.convert("RGBA")
    # Light top fade so the logo reads on any background.
    grad = np.clip(1 - np.arange(H, dtype=np.float32) / max(1, T + short * 0.2), 0, 1) ** 1.5 * 0.32 * 255
    top_fade = Image.new("RGBA", base.size, (*pal["dark"], 0))
    top_fade.putalpha(Image.fromarray(np.repeat(grad.astype(np.uint8)[:, None], W, axis=1), "L"))
    base = Image.alpha_composite(base, top_fade)
    if landscape:
        side = lay.text_side
        start = x1 + int(W * 0.28) if side == "left" else x0 - int(W * 0.28)
        solid = x1 if side == "left" else x0
        # Landscape also gets a soft bottom lift so CTA/disclaimer sit on something consistent.
        extra = _scrim(base.size, "bottom", block_top, B, pal["dark"], 0.35)
    else:
        side = "bottom"
        start, solid = block_top - int(short * 0.22), block_top + int(hfit.line_height * 0.6)
        extra = None
    for alpha in SCRIM_STEPS:
        img = Image.alpha_composite(base, _scrim(base.size, side, start, solid, pal["dark"], alpha))
        if extra is not None:
            img = Image.alpha_composite(img, extra)
        if contrast(lay.headline_color, _brightest(img, lay.headline_box)) >= TARGET_CONTRAST:
            break
    lay.scrim_alpha = alpha

    # --- 4. Logo: top corner of the safe zone. Variant by local luminance, then the weakest soft plate
    #        that lifts the mark to LOGO_CONTRAST against the worst pixels behind it (busy or bright heroes).
    logo_w = int(short * 0.24)
    lx = R - logo_w if rtl else L
    region = img.crop((lx, T, lx + logo_w, T + logo_w // 3)).convert("RGB")
    bg_lum = luminance(np.asarray(region).reshape(-1, 3).mean(axis=0))
    rel = brand.logo_on_light if (bg_lum > 0.45 and brand.logo_on_light) else brand.logo
    logo = Image.open(brand.path(rel)).convert("RGBA")
    logo = logo.resize((logo_w, int(logo.height * logo_w / logo.width)), Image.LANCZOS)
    lay.logo_box = (lx, T, lx + logo.width, T + logo.height)
    mark = np.asarray(logo).astype(np.float32)
    weight = mark[..., 3]
    # Alpha-weighted mean colour; a fully transparent logo falls back to white rather than NaN.
    total = weight.sum()
    mark_rgb = (mark[..., :3] * weight[..., None]).sum(axis=(0, 1)) / total if total else np.full(3, 255.0)
    mark_is_light = luminance(mark_rgb) > 0.5
    plate_color = pal["dark"] if mark_is_light else pal["light"]
    pad = int(logo.height * 0.45)
    plate_box = (lx - pad, T - pad, lx + logo.width + pad, T + logo.height + pad)
    plate = Image.new("L", img.size, 0)
    ImageDraw.Draw(plate).rounded_rectangle(plate_box, radius=pad, fill=255)
    plate = plate.filter(ImageFilter.GaussianBlur(pad * 0.7))
    for a in LOGO_PLATE_STEPS:
        lit = img if a == 0 else Image.alpha_composite(img, _plate(img.size, plate, plate_color, a))
        px = np.asarray(lit.convert("RGB").crop(lay.logo_box)).reshape(-1, 3).astype(np.float32)
        lum = px @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        worst = px[lum >= np.percentile(lum, 90)] if mark_is_light else px[lum <= np.percentile(lum, 10)]
        # The mark is only ever seen against backdrop pixels, so measure it against the hardest 10% of them.
        worst = worst.mean(axis=0)
        calm = _clutter(lit, lay.logo_box) <= LOGO_CLUTTER_MAX
        if calm and contrast(tuple(int(c) for c in mark_rgb), worst) >= LOGO_CONTRAST:
            break
    img = lit
    lay.logo_plate = a
    img.alpha_composite(logo, (lx, T))
    lay.backdrop = img.copy()

    # --- 5. Draw copy.
    draw = ImageDraw.Draw(img)
    _draw_lines(draw, hfit, anchor_x, head_y, lay.headline_color, anchor, dir_kw)
    if cta_geom:
        cf, (a, b, c, d) = cta_geom
        fill = pal["primary"]
        draw.rounded_rectangle([a, b, c, d], radius=(d - b) // 2, fill=fill)
        draw.text(((a + c) // 2, (b + d) // 2), cta, font=cf, fill=best_text_color(fill), anchor="mm", **dir_kw)
        lay.cta_box = (a, b, c, d)
    if dfit:
        lay.disclaimer_box = _draw_lines(draw, dfit, anchor_x, disc_y, (232, 232, 232), anchor, dir_kw)

    # --- 6. How much of the subject does the copy cover?
    mask = np.zeros((H, W), bool)
    for bx in lay.text_boxes():
        mask[bx[1] : bx[3], bx[0] : bx[2]] = True
    hot = sal_img >= np.percentile(sal_img, 90)
    lay.subject_overlap = float((hot & mask).sum() / max(1, hot.sum()))
    return img.convert("RGB"), lay


def _draw_lines(draw, f: Fitted, x, y, color, anchor, dir_kw) -> Box:
    for i, line in enumerate(f.lines):
        draw.text((x, y + i * f.line_height), line, font=f.font, fill=color, anchor=anchor, **dir_kw)
    w = f.width()
    x0 = x - w if anchor.startswith("r") else x
    return (x0, y, x0 + w, y + f.height)
