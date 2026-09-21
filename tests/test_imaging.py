import numpy as np
from PIL import Image, ImageDraw

from cap.brand import load_brand
from cap.cache import Cache
from cap.compliance.visual import LogoDetector, check_variant
from cap.imaging.reframe import expand_canvas, recomposite, reframe
from cap.imaging.render import compose
from cap.imaging.saliency import best_crop
from cap.imaging.typography import contrast, fit
from cap.providers import ProviderError
from cap.providers.mock import MockProvider


def subject_at(x_frac, size=(1600, 900)):
    img = Image.new("RGB", size, (235, 230, 220))
    d = ImageDraw.Draw(img)
    cx = int(size[0] * x_frac)
    d.rectangle([cx - 90, 300, cx + 90, 700], fill=(10, 120, 115))
    return img, cx


def test_saliency_crop_follows_off_center_subject():
    img, cx = subject_at(0.8)
    (x0, _, x1, _), kept = best_crop(img, 1.0)
    assert x0 < cx - 90 and cx + 90 < x1, "subject must stay inside the crop"
    assert kept > 0.5


def test_focus_point_overrides_saliency():
    img, _ = subject_at(0.8)
    (x0, _, x1, _), _ = best_crop(img, 1.0, focus=(0.0, 0.5))
    assert x0 == 0


def test_expand_canvas_contains_source_and_hits_ratio():
    for size, r in [((1000, 1000), 16 / 9), ((1000, 1000), 9 / 16), ((1600, 1200), 9 / 16)]:
        cw, ch = expand_canvas(size, r)
        assert cw >= size[0] and ch >= size[1]
        assert abs(cw / ch - r) < 0.01


def test_recomposite_keeps_original_pixels():
    src = Image.fromarray(np.random.default_rng(0).integers(0, 255, (200, 300, 3), dtype=np.uint8))
    model_out = Image.new("RGB", (300, 534), (0, 0, 0))  # a "model" that ruined everything
    merged = np.asarray(recomposite(model_out, src))
    y = (534 - 200) // 2
    inner = merged[y + 10 : y + 190, 10:290]
    assert np.array_equal(inner, np.asarray(src)[10:190, 10:290])


class BrokenExpand(MockProvider):
    def expand(self, *a, **k):
        raise ProviderError("HTTP 500: upstream down")


def test_failed_outpaint_degrades_to_crop(tmp_path):
    img, _ = subject_at(0.5, (1000, 1000))
    rf = reframe(img, (1920, 1080), strategy="expand", provider=BrokenExpand(), cache=Cache(tmp_path), prompt="p")
    assert rf.method == "crop" and "expand failed" in rf.detail
    assert rf.image.size == (1920, 1080)


def test_text_fit_shrinks_then_flags_truncation(repo):
    font = str(repo / "brand/tidewell/fonts/Poppins-Bold.ttf")
    short = fit("Summer, freshly poured.", font, 800, 3, 120, 20)
    assert not short.truncated and len(short.lines) <= 3 and short.width() <= 800
    long = fit("word " * 80, font, 300, 2, 60, 30)
    assert long.truncated and long.lines[-1].endswith("…")


def test_contrast_math():
    assert round(contrast((255, 255, 255), (0, 0, 0)), 1) == 21.0
    assert round(contrast((119, 119, 119), (255, 255, 255)), 1) == 4.5


def test_compose_is_legible_on_bright_background_and_detects_logo(repo):
    brand = load_brand(repo / "brand/tidewell/brand.yaml")
    frame = Image.new("RGB", (1080, 1920), (250, 248, 240))  # worst case: near-white hero
    img, lay = compose(frame, "9:16", brand, "Summer, freshly poured.", "Find it near you", None, "en-US")
    checks = {c.id: c for c in check_variant(img, lay, brand, LogoDetector(brand), 1.0)}
    assert checks["layout.text_contrast"].status == "pass"
    assert checks["layout.safe_zone"].status == "pass"
    assert checks["brand.logo_present"].status == "pass"
    assert LogoDetector(brand).score(frame) < 0.5  # and does not hallucinate a logo


def busy_bright(size=(1080, 1920), seed=3) -> Image.Image:
    """Sunlit foliage: mostly mid-tone, with bright sky gaps (where a white logo loses contrast)."""
    rng = np.random.default_rng(seed)
    noise = rng.random((size[1] // 24, size[0] // 24)).astype(np.float32)
    big = np.asarray(Image.fromarray((noise * 255).astype(np.uint8)).resize(size, Image.BICUBIC)).astype(np.float32)
    rgb = np.stack([90 + big * 0.6, 120 + big * 0.55, 60 + big * 0.7], axis=-1)
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def test_logo_gets_a_backing_plate_only_when_the_backdrop_needs_it(repo):
    brand = load_brand(repo / "brand/tidewell/brand.yaml")
    dark = Image.new("RGB", (1080, 1920), (20, 40, 45))
    _, calm = compose(dark, "9:16", brand, "Summer, freshly poured.", "Find it near you", None, "en-US")
    assert calm.logo_plate == 0.0  # white mark on a dark hero needs no help

    img, lay = compose(busy_bright(), "9:16", brand, "Summer, freshly poured.", "Find it near you", None, "en-US")
    assert lay.logo_plate > 0  # busy, bright pixels behind the mark: the plate steps in
    assert LogoDetector(brand).score(img) >= 0.8  # and the mark still reads as the brand logo


def test_logo_plate_also_answers_texture_not_just_contrast(repo):
    """Dark, busy foliage: a white mark clears 3:1 contrast, but the texture would still shred its edges."""
    brand = load_brand(repo / "brand/tidewell/brand.yaml")
    rng = np.random.default_rng(7)
    noise = rng.random((1920 // 6, 1080 // 6)).astype(np.float32)
    big = np.asarray(Image.fromarray((noise * 255).astype(np.uint8)).resize((1080, 1920), Image.BICUBIC))
    frame = Image.fromarray(np.stack([15 + big * 0.22, 30 + big * 0.28, 15 + big * 0.2], axis=-1).astype(np.uint8))
    _, lay = compose(frame, "9:16", brand, "Summer, freshly poured.", "Find it near you", None, "en-US")
    assert lay.logo_plate > 0


def test_compose_survives_a_logo_with_no_opaque_pixels(repo):
    """A faint logo (all alpha <= 200) used to produce a NaN colour and crash the renderer."""
    brand = load_brand(repo / "brand/tidewell/brand.yaml")
    for name in (brand.logo, brand.logo_on_light):
        path = brand.path(name)
        rgba = np.asarray(Image.open(path).convert("RGBA")).copy()
        rgba[..., 3] = (rgba[..., 3] * 0.6).astype(np.uint8)
        Image.fromarray(rgba).save(path)
    img, lay = compose(Image.new("RGB", (1080, 1920), (90, 120, 60)), "9:16", brand, "Hello", "Go", None, "en-US")
    assert img.size == (1080, 1920) and lay.logo_box
