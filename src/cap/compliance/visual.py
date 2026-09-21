"""Visual compliance checks on the final rendered pixels.

These verify the output, not the intent: the logo detector searches the finished image rather
than trusting the renderer's coordinates, and contrast is measured against the actual pixels
behind the headline.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from ..brand import Brand
from ..imaging.render import Layout
from ..imaging.typography import contrast
from ..manifest import CheckResult

# Calibrated: present >= 0.87 on real generated photos (0.92-0.98 on smooth renders), absent <= 0.70.
LOGO_THRESHOLD = 0.80


class LogoDetector:
    """Multi-scale template matching on edge maps (background-independent)."""

    def __init__(self, brand: Brand):
        self.templates = []
        for rel in filter(None, [brand.logo, brand.logo_on_light]):
            rgba = np.asarray(Image.open(brand.path(rel)).convert("RGBA"))
            # Composite on mid-gray so the alpha outline produces edges on any background.
            a = rgba[..., 3:4] / 255.0
            gray = (rgba[..., :3] * a + 128 * (1 - a)).mean(axis=2).astype(np.uint8)
            self.templates.append(gray)

    def score(self, img: Image.Image) -> float:
        g = np.asarray(img.convert("L"))
        k = 720 / max(g.shape)  # normalize working resolution for speed and stable thresholds
        g = cv2.resize(g, (int(g.shape[1] * k), int(g.shape[0] * k)), interpolation=cv2.INTER_AREA)
        edges = _soft_edges(g)
        short = min(g.shape)
        best = 0.0
        for tpl in self.templates:
            for frac in np.linspace(0.12, 0.40, 15):  # logo width as a fraction of the short side
                tw = int(short * frac)
                th = max(4, int(tpl.shape[0] * tw / tpl.shape[1]))
                if th >= g.shape[0] or tw >= g.shape[1]:
                    continue
                t = _soft_edges(cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA))
                if t.max() == 0:
                    continue
                res = cv2.matchTemplate(edges, t, cv2.TM_CCOEFF_NORMED)
                # Flat windows make normalized correlation unstable; require real edge energy.
                energy = cv2.matchTemplate(edges * edges, np.ones_like(t), cv2.TM_CCORR)
                res[energy < 0.25 * float((t * t).sum())] = 0
                best = max(best, float(np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0).max()))
        return best


def _soft_edges(gray: np.ndarray) -> np.ndarray:
    """Canny edges, blurred so 1px misalignment between scales does not destroy the match."""
    e = cv2.Canny(gray, 60, 160).astype(np.float32)
    return cv2.GaussianBlur(e, (0, 0), 1.2)


def _palette_coverage(img: Image.Image, brand: Brand, max_de: float = 22.0) -> float:
    small = img.convert("RGB").resize((160, int(160 * img.height / img.width)))
    lab = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    lab[:, 0] *= 100 / 255.0  # OpenCV 8-bit Lab -> CIE ranges
    lab[:, 1:] -= 128
    pal = brand.palette.as_rgb()
    refs = np.array([pal[k] for k in ("primary", "secondary", "accent")], dtype=np.uint8).reshape(1, -1, 3)
    rlab = cv2.cvtColor(refs, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    rlab[:, 0] *= 100 / 255.0
    rlab[:, 1:] -= 128
    de = np.linalg.norm(lab[:, None, :] - rlab[None, :, :], axis=2).min(axis=1)  # CIE76 delta E
    return float((de < max_de).mean())


def _inside(box, outer) -> bool:
    return box[0] >= outer[0] and box[1] >= outer[1] and box[2] <= outer[2] and box[3] <= outer[3]


def check_variant(
    img: Image.Image, lay: Layout, brand: Brand, detector: LogoDetector, upscale: float
) -> list[CheckResult]:
    out: list[CheckResult] = []

    s = detector.score(img)
    out.append(
        CheckResult(
            id="brand.logo_present",
            category="brand",
            value=round(s, 3),
            status="pass" if s >= LOGO_THRESHOLD else "fail",
            detail=f"logo match score {s:.2f} (threshold {LOGO_THRESHOLD})",
        )
    )

    cov = _palette_coverage(img, brand)
    out.append(
        CheckResult(
            id="brand.palette_presence",
            category="brand",
            value=round(cov, 4),
            status="pass" if cov >= brand.min_palette_coverage else "warn",
            detail=f"{cov:.1%} of pixels within ΔE<22 of brand palette (min {brand.min_palette_coverage:.0%})",
        )
    )

    if lay.headline_box and lay.backdrop is not None:
        region = np.asarray(lay.backdrop.convert("RGB").crop(lay.headline_box)).reshape(-1, 3)
        lum = region @ np.array([0.2126, 0.7152, 0.0722])
        # Worst case: the brightest 10% of backdrop pixels behind light text.
        worst_px = region[lum >= np.percentile(lum, 90)].mean(axis=0)
        cr = contrast(lay.headline_color, worst_px)
        status = "pass" if cr >= 4.5 else "warn" if cr >= 3.0 else "fail"
        out.append(
            CheckResult(
                id="layout.text_contrast",
                category="layout",
                value=round(cr, 2),
                status=status,
                detail=f"WCAG contrast {cr:.1f}:1 vs brightest backdrop behind headline (AA: large ≥3, body ≥4.5)",
            )
        )

    boxes = [b for b in [lay.logo_box, *lay.text_boxes()] if b]
    outside = [b for b in boxes if not _inside(b, lay.safe_box)]
    out.append(
        CheckResult(
            id="layout.safe_zone",
            category="layout",
            value=len(outside),
            status="fail" if outside else "pass",
            detail="logo and copy inside platform safe zone"
            if not outside
            else f"{len(outside)} element(s) outside safe zone",
        )
    )

    ov = lay.subject_overlap
    out.append(
        CheckResult(
            id="layout.subject_clear",
            category="layout",
            value=round(ov, 3),
            status="pass" if ov <= 0.15 else "warn",
            detail=f"copy covers {ov:.0%} of the most salient area"
            + ("" if ov <= 0.15 else " (product partly behind text; consider expand or a focal point)"),
        )
    )

    status = "fail" if lay.truncated else "warn" if lay.headline_size <= lay.min_legible_size else "pass"
    out.append(
        CheckResult(
            id="layout.text_fit",
            category="layout",
            value=lay.headline_size,
            status=status,
            detail="headline truncated: shorten copy for this format"
            if lay.truncated
            else f"headline set at {lay.headline_size}px (min legible {lay.min_legible_size}px)",
        )
    )

    out.append(
        CheckResult(
            id="asset.resolution",
            category="asset",
            value=round(upscale, 2),
            status="pass" if upscale <= 2.0 else "warn",
            detail=f"source upscaled {upscale:.2f}x"
            + (" (may look soft; supply a larger asset)" if upscale > 2.0 else ""),
        )
    )
    return out
