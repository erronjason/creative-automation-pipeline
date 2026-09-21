"""Right-to-left copy (Arabic, Hebrew): shaped and ordered in Python, drawn with a brand font that covers the script."""

import numpy as np
import yaml
from PIL import Image

from cap.brand import load_brand
from cap.imaging.render import compose
from cap.imaging.typography import direction, fit, font, missing_glyphs, shape
from cap.pipeline import Pipeline

ARABIC = "الصيف، مُقدَّم طازجًا."
HEBREW = "הקיץ, מוגש טרי."


def test_arabic_letters_are_joined_and_hebrew_is_reordered():
    assert direction("ar-AE") == "rtl" and direction("he-IL") == "rtl" and direction("fr-CA") == "ltr"
    shaped = shape("بنكهة", rtl=True)
    assert len(shaped) == 5 and all(ord(c) > 0xFB00 for c in shaped)  # each letter swapped for its joined form
    assert shape("אבג", rtl=True) == "גבא"  # Hebrew has no joining, only reordering
    assert shape("Tidewell", rtl=True) == "Tidewell", "a Latin word stays readable inside right-to-left text"
    assert shape(ARABIC, rtl=False) == ARABIC  # left-to-right copy is never touched


def test_brand_fonts_cover_arabic_and_hebrew_but_poppins_does_not(repo):
    brand = load_brand(repo / "brand/tidewell/brand.yaml")
    mixed = "Tidewell 2026 - " + ARABIC + " " + HEBREW
    for locale, own in (("ar-AE", ARABIC), ("he-IL", HEBREW)):
        head, _ = brand.fonts.for_locale(locale)
        f = font(str(brand.path(head)), 40, rtl=True)
        assert missing_glyphs(f, "Tidewell 2026 - " + own) == ""
    assert brand.fonts.for_locale("en-US") == (brand.fonts.headline, brand.fonts.body)
    poppins = font(str(brand.path(brand.fonts.headline)), 40, rtl=True)
    assert missing_glyphs(poppins, mixed) != ""


def test_rtl_layout_mirrors_and_wraps_in_reading_order(repo):
    brand = load_brand(repo / "brand/tidewell/brand.yaml")
    head, _ = brand.fonts.for_locale("ar-AE")
    text = "الصيف، مُقدَّم طازجًا بنكهة اليوسفي الياباني المنعشة على شرفة مشمسة"
    fitted = fit(text, str(brand.path(head)), 500, 3, 80, 20, rtl=True)
    assert " ".join(fitted.lines) == text  # lines are kept in reading order; only the drawn form is reversed
    assert fitted.display != fitted.lines and fitted.width() <= 500

    frame = Image.new("RGB", (1080, 1350), (90, 140, 150))
    args = (frame, "4:5", brand, ARABIC, "اعثر عليه بالقرب منك", "لا يحتوي على سكر مضاف.")
    rtl_img, rtl = compose(*args, "ar-AE")
    ltr_img, ltr = compose(*args, "en-US")
    assert rtl.missing_glyphs == ""
    assert rtl.logo_box[0] > 540 > ltr.logo_box[0]  # the logo moves to the right-hand corner
    assert rtl.headline_box[2] > 540 and ltr.headline_box[0] < 200  # copy is right-aligned, not left
    assert not np.array_equal(np.asarray(rtl_img), np.asarray(ltr_img))


def test_a_language_without_a_covering_font_fails_the_check_instead_of_drawing_boxes(repo, opts):
    brief = yaml.safe_load((repo / "briefs/summer-refresh.yaml").read_text(encoding="utf-8"))
    brief["target"]["markets"] = [{"code": "AE", "locale": "ar-AE"}]
    brief["localized_copy"] = {"ar-AE": {"message": ARABIC, "cta": "اعثر عليه", "disclaimer": "لا يحتوي على سكر."}}
    (repo / "briefs/rtl.yaml").write_text(yaml.safe_dump(brief, allow_unicode=True), encoding="utf-8")
    opts.only_ratios = ["1:1"]

    def run(campaign_id):
        manifest = Pipeline(opts).run(repo / "briefs/rtl.yaml")
        assert manifest.campaign_id == campaign_id
        return {c.id: c for v in manifest.variants for c in v.checks}["layout.font_coverage"]

    assert run("summer-refresh-2026").status == "pass"

    brand_file = repo / "brand/tidewell/brand.yaml"
    brand = yaml.safe_load(brand_file.read_text(encoding="utf-8"))
    del brand["fonts"]["by_language"]
    brand_file.write_text(yaml.safe_dump(brand), encoding="utf-8")
    check = run("summer-refresh-2026")
    assert check.status == "fail" and "by_language" in check.detail
