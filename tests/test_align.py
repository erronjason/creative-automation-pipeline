import cv2
import numpy as np
from PIL import Image

from cap.imaging.align import align, harmonize
from cap.imaging.reframe import recomposite
from cap.providers.openrouter import build_layout


def texture(size=(320, 320), seed=1) -> Image.Image:
    """Smooth, feature-rich test image (blurred noise) so registration has something to lock onto."""
    rng = np.random.default_rng(seed)
    a = cv2.GaussianBlur(rng.random((size[1], size[0], 3)).astype(np.float32), (0, 0), 6)
    a = (a - a.min()) / (a.max() - a.min())
    return Image.fromarray((a * 255).astype(np.uint8))


def canvas_with(src: Image.Image, canvas=(320, 560)) -> Image.Image:
    out = Image.new("RGB", canvas, (120, 120, 120))
    out.paste(src, ((canvas[0] - src.width) // 2, (canvas[1] - src.height) // 2))
    return out


def centre_mae(out: Image.Image, src: Image.Image) -> float:
    x, y = (out.width - src.width) // 2, (out.height - src.height) // 2
    crop = np.asarray(out.crop((x, y, x + src.width, y + src.height))).astype(float)
    return float(np.abs(crop - np.asarray(src).astype(float)).mean())


def test_align_undoes_scale_and_shift_drift():
    src = texture()
    drifted = canvas_with(src)
    m = np.float32([[1.02, 0, -2], [0, 1.02, 3]])  # ~2% drift: what a model that "redraws the centre" does
    drifted = Image.fromarray(cv2.warpAffine(np.asarray(drifted), m, drifted.size, borderMode=cv2.BORDER_REFLECT))
    before = centre_mae(drifted, src)
    fixed, note = align(drifted, src)
    assert "aligned" in note and centre_mae(fixed, src) < before * 0.4


def test_align_skips_when_nothing_matches():
    src = texture()
    unrelated = canvas_with(texture(seed=99))
    out, note = align(unrelated, src)
    assert "skipped" in note and out is unrelated  # declines rather than warping on noise


def test_harmonize_pulls_margin_colour_toward_source_at_the_seam():
    # The model redrew the whole frame 20 levels darker, centre and margins alike (continuous across the seam).
    src = Image.new("RGB", (200, 200), (250, 240, 200))
    out = Image.new("RGB", (200, 400), (230, 220, 180))
    fixed = np.asarray(harmonize(out, src)).astype(float)
    seam, far = fixed[95, 100], fixed[5, 100]  # just above the source vs. the frame's far edge
    target, orig = np.array([250, 240, 200.0]), np.array([230, 220, 180.0])
    assert np.abs(seam - target).max() < 3  # matches the approved pixels where they meet (5px out: ~90% correction)
    assert np.abs(far - target).sum() > np.abs(seam - target).sum()  # correction fades with distance
    assert np.abs(far - orig).sum() < np.abs(far - target).sum()  # ...and stays partial far away


def test_recomposite_keeps_source_pixels_and_has_no_border_notches():
    src = texture((300, 300))
    expanded = Image.new("RGB", (300, 520), (10, 200, 10))
    out = np.asarray(recomposite(expanded, src)).astype(int)
    y0 = (520 - 300) // 2
    s = np.asarray(src).astype(int)
    assert np.abs(out[y0 + 30 : y0 + 270, :] - s[30:270, :]).max() == 0  # interior + left/right border sides pristine
    # Corner columns touching the canvas border must blend like their neighbours, not jut out as a strip.
    row = out[y0 + 2]  # inside the top feather, where a leftover border strip used to show
    assert abs(int(row[1].sum()) - int(row[40].sum())) < 300


def test_layout_builders_keep_source_centered_and_fill_margins():
    src = texture((200, 200))
    t = build_layout(src, (200, 360), "transparent")
    assert t.getpixel((5, 5))[3] == 0 and t.getpixel((100, 180))[3] == 255
    for mode in ("blur", "mirror"):
        lay = build_layout(src, (200, 360), mode)
        assert lay.size == (200, 360) and lay.getpixel((5, 5))[3] == 255  # margins are opaque placeholders
        assert lay.crop((0, 80, 200, 280)).convert("RGB").tobytes() == src.tobytes()  # source untouched
