"""Content-aware cropping.

Spectral-residual saliency (Hou & Zhang, CVPR 2007): the parts of an image's log-amplitude
spectrum that deviate from a smoothed version correspond to "unexpected" regions, which in
product photography is almost always the product. ~15 lines of numpy, no model download,
deterministic, and fast enough to run on every variant.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

WORK = 128  # saliency is computed on a small thumbnail; it only needs to find the subject


def saliency_map(img: Image.Image) -> np.ndarray:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    k = WORK / max(g.shape)
    g = cv2.resize(g, (max(8, int(g.shape[1] * k)), max(8, int(g.shape[0] * k))), interpolation=cv2.INTER_AREA)
    f = np.fft.fft2(g)
    log_amp = np.log(np.abs(f) + 1e-8)
    phase = np.angle(f)
    residual = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), sigmaX=3)
    # Mild center prior: photographers center the subject, and it stabilizes flat backgrounds.
    h, w = sal.shape
    yy, xx = np.mgrid[0:h, 0:w]
    prior = np.exp(-(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2))
    sal = sal / (sal.max() + 1e-8)
    sal = 0.75 * sal + 0.25 * prior
    return sal / sal.sum()


def best_crop(
    img: Image.Image, ratio: float, focus: tuple[float, float] | None = None
) -> tuple[tuple[int, int, int, int], float]:
    """Largest crop of `ratio` that maximizes retained saliency.

    Returns the box in source pixels and the fraction of total saliency retained (0..1), which
    the reframer uses to decide whether cropping is acceptable or outpainting is needed.
    """
    W, H = img.size
    if ratio < W / H:  # too wide: full height, slide horizontally
        cw, ch = round(H * ratio), H
    else:  # too tall: full width, slide vertically
        cw, ch = W, round(W / ratio)
    sal = saliency_map(img)
    sh, sw = sal.shape
    horizontal = cw < W
    profile = sal.sum(axis=0) if horizontal else sal.sum(axis=1)
    n = len(profile)
    win = max(1, round(n * (cw / W if horizontal else ch / H)))
    csum = np.concatenate([[0.0], np.cumsum(profile)])
    scores = csum[win:] - csum[: n - win + 1]

    if focus is not None:
        c = focus[0] if horizontal else focus[1]
        start = int(round(c * n - win / 2))
        start = min(max(0, start), n - win)
    else:
        start = int(np.argmax(scores))
    retained = float(scores[start])

    if horizontal:
        x0 = min(round(start / n * W), W - cw)
        box = (x0, 0, x0 + cw, ch)
    else:
        y0 = min(round(start / n * H), H - ch)
        box = (0, y0, cw, y0 + ch)
    return box, retained
