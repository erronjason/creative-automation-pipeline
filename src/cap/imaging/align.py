"""Make a generative outpaint line up with the approved source before it is re-composited.

Image models without a real mask (OpenRouter's Images API, and in practice any model that
"extends" a picture) redraw the whole frame. Their centre drifts a few percent in scale and
position and shifts in tone, and pasting the original pixels back then leaves a visible seam.
Two cheap, deterministic corrections remove most of it:

  align()      estimate the affine drift of the model's centre against the source (ECC) and undo it
  harmonize()  shift the generated margins toward the source's colour at the seam, fading with distance

Both are best-effort: if registration fails or looks implausible the input is returned unchanged.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

MAX_SCALE_DRIFT = 0.15  # beyond this the model recomposed the scene; do not "fix" it
MAX_SHIFT = 0.12  # fraction of the source's short side


def _box(canvas: Image.Image, src: Image.Image) -> tuple[int, int]:
    return (canvas.width - src.width) // 2, (canvas.height - src.height) // 2


def align(out: Image.Image, src: Image.Image) -> tuple[Image.Image, str]:
    """Warp `out` so its centre region registers with `src`. Returns (image, human-readable note)."""
    x, y = _box(out, src)
    a = cv2.cvtColor(np.asarray(src), cv2.COLOR_RGB2GRAY).astype(np.float32)
    b = cv2.cvtColor(np.asarray(out.crop((x, y, x + src.width, y + src.height))), cv2.COLOR_RGB2GRAY)
    b = b.astype(np.float32)
    f = 512 / max(a.shape)  # register on a thumbnail: drift is low-frequency, and it is 20x faster
    a_s, b_s = (
        cv2.resize(a, None, fx=f, fy=f, interpolation=cv2.INTER_AREA),
        cv2.resize(b, None, fx=f, fy=f, interpolation=cv2.INTER_AREA),
    )
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
        cv2.findTransformECC(a_s, b_s, warp, cv2.MOTION_AFFINE, crit, None, 5)
    except cv2.error:
        return out, "alignment skipped: registration did not converge"

    improved = cv2.warpAffine(b_s, warp, b_s.shape[::-1], flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    if np.abs(a_s - improved).mean() > 0.9 * np.abs(a_s - b_s).mean():
        return out, "alignment skipped: no measurable improvement"

    lin, t = warp[:, :2].astype(np.float64), warp[:, 2].astype(np.float64) / f  # translation back to full-res px
    drift = float(np.abs(np.linalg.svd(lin, compute_uv=False) - 1).max())
    if drift > MAX_SCALE_DRIFT or np.abs(t).max() > MAX_SHIFT * min(src.size):
        return out, f"alignment skipped: implausible drift ({drift:.0%} scale, {np.abs(t).max():.0f}px)"

    # ECC maps source coords -> model coords inside the crop. Lift that to whole-canvas coordinates.
    origin = np.array([x, y], dtype=np.float64)
    m = np.hstack([lin, (t + origin - lin @ origin)[:, None]])
    aligned = cv2.warpAffine(
        np.asarray(out), m, out.size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT
    )
    return Image.fromarray(aligned), f"aligned model centre (scale drift {drift:.1%}, shift {np.abs(t).max():.0f}px)"


def harmonize(out: Image.Image, src: Image.Image, falloff: float = 0.25) -> Image.Image:
    """Nudge the margins' colour toward the source at the seam, fading over `falloff` x the canvas short side."""
    cw, ch = out.size
    x, y = _box(out, src)
    sigma = max(3.0, min(src.size) * 0.03)
    s = cv2.GaussianBlur(np.asarray(src).astype(np.float32), (0, 0), sigma)
    m = cv2.GaussianBlur(np.asarray(out.crop((x, y, x + src.width, y + src.height))).astype(np.float32), (0, 0), sigma)
    delta = s - m  # low-frequency colour error, measured across the source's whole footprint
    field = cv2.copyMakeBorder(delta, y, ch - src.height - y, x, cw - src.width - x, cv2.BORDER_REPLICATE)

    px, py = np.arange(cw)[None, :], np.arange(ch)[:, None]
    dx = np.maximum(np.maximum(x - px, px - (x + src.width - 1)), 0)
    dy = np.maximum(np.maximum(y - py, py - (y + src.height - 1)), 0)
    weight = np.exp(-np.hypot(dx, dy) / (falloff * min(cw, ch)))[..., None].astype(np.float32)
    fixed = np.asarray(out).astype(np.float32) + field * weight
    return Image.fromarray(np.clip(fixed, 0, 255).astype(np.uint8))
